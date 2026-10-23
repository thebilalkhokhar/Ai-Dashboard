"""In-process scheduler for the live dashboard and the event-triggered agents.

Runs as a plain asyncio task inside the FastAPI lifespan - no cron, no worker,
no extra service. Every REFRESH_MINUTES it does one cycle:

    1. incremental ERP refresh            (app/services/refresh.py)
    2. build + store + publish snapshot   (Mongo `snapshots`, Redis)
    3. decide whether the agents run      (hash comparison against the last run)
    4. daily safety net at AGENT_FULL_RUN_HOUR, with the cache bypassed

Agent runs are event-triggered. After every refresh the current stale-quote,
unpaid-invoice and stuck-lead items are built with graph.load_signals_node
(the graph's own code, so the hashes are identical to what the pipeline would
compute) and compared with the hashes stored in the previous agent_runs
document. Only when something is new or changed does the scheduler call the
pipeline once; inside the pipeline the existing 24h cache skips every
unchanged item. No changes -> zero LLM calls.

A Redis lock (`dashboard:scheduler:lock`) makes sure two cycles never overlap,
and a second one (`dashboard:scheduler:leader`, renewed while work runs) means
only ONE worker out of many even tries: with `uvicorn --workers 4` the other
three stand by and take over within a TTL if the leader dies.

Every job has its own try/except: one failure is logged and the loop keeps
running. Only agent failures publish an "error" event.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app import db as app_db
from app.config import settings
from app.services import lead_pulls, metrics, refresh, signals
from app.services.erp_client import ERPClient
from app.services.events import get_event_service
from app.services.redis_client import get_redis

log = logging.getLogger("scheduler")

# Redis keys / channel shared with app/api/live.py
SNAPSHOT_KEY = "dashboard:snapshot"
UPDATES_CHANNEL = "dashboard:updates"
LOCK_KEY = "dashboard:scheduler:lock"
# one worker is the leader and runs the schedule; the others stand by
LEADER_KEY = "dashboard:scheduler:leader"


# ---------------------------------------------------------------------------
# default dependencies (tests inject fakes instead)
# ---------------------------------------------------------------------------


async def _default_pipeline(**kwargs) -> dict:
    """Thin wrapper around the existing agent pipeline entry point."""
    from app.agents.graph import run_pipeline  # noqa: PLC0415 - heavy import

    return await run_pipeline(**kwargs)


class DashboardScheduler:
    """One loop, three jobs, one Redis lock."""

    def __init__(
        self,
        database: Any = None,
        redis: Any = None,
        refresh_func: Optional[Callable] = None,
        snapshot_func: Optional[Callable] = None,
        lead_snapshot_func: Optional[Callable] = None,
        pipeline_func: Optional[Callable] = None,
        interval_seconds: Optional[int] = None,
    ) -> None:
        self.database = database
        self.redis = redis
        self._refresh_func = refresh_func or refresh.run_incremental_refresh
        self._snapshot_func = snapshot_func or metrics.build_snapshot
        self._lead_snapshot_func = lead_snapshot_func or lead_pulls.capture_lead_snapshot
        self._pipeline_func = pipeline_func or _default_pipeline
        self._interval = interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._stop: Optional[asyncio.Event] = None
        self._last_daily_day: Optional[str] = None  # e.g. "2026-10-20"
        # this process' identity in the leader election
        self._leader_token = str(uuid.uuid4())
        self.is_leader = False

    # ----------------------------------------------------------- plumbing

    def _db(self) -> Any:
        # AsyncDatabase objects cannot be truth-tested, so never use `or` here
        return self.database if self.database is not None else app_db.get_db()

    def _redis(self) -> Any:
        return self.redis if self.redis is not None else get_redis()

    def interval_seconds(self) -> int:
        return int(self._interval or max(1, settings.REFRESH_MINUTES * 60))

    def _lock_ttl(self) -> int:
        # Longer than a slow cycle (refresh + an agent run) but short enough
        # that a crashed process does not block the dashboard for long.
        return max(900, self.interval_seconds() * 3)

    def leader_ttl_seconds(self) -> int:
        return max(30, int(settings.SCHEDULER_LEADER_TTL_SECONDS))

    # ------------------------------------------------------- leader election

    async def claim_leadership(self) -> bool:
        """True when this process may run the schedule.

        First call wins the Redis key (SET NX with a TTL). Later calls renew it
        only while we still own it, so a worker that stops renewing is replaced
        by a standby within one TTL.
        """
        ttl = self.leader_ttl_seconds()
        try:
            redis = self._redis()
            if await redis.set(LEADER_KEY, self._leader_token, nx=True, ex=ttl):
                self.is_leader = True
                return True
            held = await redis.get(LEADER_KEY)
            if isinstance(held, bytes):
                held = held.decode()
            if held == self._leader_token:
                await redis.set(LEADER_KEY, self._leader_token, ex=ttl)
                self.is_leader = True
                return True
            self.is_leader = False
            return False
        except Exception:
            # no Redis, no election: nobody runs, which is the safe answer
            log.exception("could not reach Redis for the scheduler leader key")
            self.is_leader = False
            return False

    async def step_down(self) -> None:
        """Give leadership back so a standby takes over immediately."""
        if not self.is_leader:
            return
        self.is_leader = False
        try:
            redis = self._redis()
            held = await redis.get(LEADER_KEY)
            if isinstance(held, bytes):
                held = held.decode()
            if held == self._leader_token:
                await redis.delete(LEADER_KEY)
        except Exception:
            log.debug("could not release scheduler leadership", exc_info=True)

    def leader_renew_seconds(self) -> int:
        """How often the lease is renewed: three chances inside one TTL."""
        return max(1, self.leader_ttl_seconds() // 3)

    async def _renew_while_working(self) -> None:
        """Keep the leader key alive through a long cycle."""
        every = self.leader_renew_seconds()
        while True:
            await asyncio.sleep(every)
            if not await self.claim_leadership():
                log.warning("scheduler lost leadership during a cycle")
                return

    # ---------------------------------------------------------- start/stop

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="dashboard-scheduler")

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.step_down()

    async def _loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            try:
                if await self.claim_leadership():
                    renewal = asyncio.create_task(self._renew_while_working())
                    try:
                        await self.run_cycle()
                    finally:
                        renewal.cancel()
                        with suppress(asyncio.CancelledError):
                            await renewal
                else:
                    log.info("scheduler: another worker leads this dashboard, standing by")
            except asyncio.CancelledError:
                raise
            except Exception:  # a bad cycle must never kill the loop
                log.exception("scheduler cycle failed")
            # fixed cadence: a slow cycle (a 2 minute agent run) must not push
            # the next refresh further out than one interval
            remaining = max(1.0, self.interval_seconds() - (loop.time() - started))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            if self._stop.is_set():
                return

    # --------------------------------------------------------------- lock

    async def _acquire_lock(self, token: str) -> bool:
        try:
            return bool(await self._redis().set(LOCK_KEY, token, nx=True, ex=self._lock_ttl()))
        except Exception:
            log.exception("could not reach Redis for the scheduler lock")
            return False

    async def _release_lock(self, token: str) -> None:
        try:
            current = await self._redis().get(LOCK_KEY)
            if isinstance(current, bytes):
                current = current.decode()
            if current == token:  # only the owner releases it
                await self._redis().delete(LOCK_KEY)
        except Exception:
            log.debug("could not release the scheduler lock", exc_info=True)

    # -------------------------------------------------------------- cycle

    async def run_cycle(self, now: Optional[datetime] = None) -> dict:
        """One refresh + snapshot + agent decision. Never raises."""
        now = now or datetime.now(timezone.utc)
        token = str(uuid.uuid4())
        if not await self._acquire_lock(token):
            log.info("scheduler: a run is already in progress, skipping this tick")
            return {"at": now.isoformat(), "skipped": "locked"}
        try:
            summary = {
                "at": now.isoformat(),
                "refresh": await self._job_refresh(),
                "snapshot": await self._job_snapshot(now),
                "lead_snapshots": await self._job_lead_snapshots(),
                "signals": await self._job_signals(now),
                "agents": await self._job_agents(now),
            }
            return summary
        finally:
            await self._release_lock(token)

    # --------------------------------------------------------- job: refresh

    async def _job_refresh(self) -> dict:
        """One incremental ERP refresh, wrapped in real lifecycle events.

        The task id is fresh per refresh cycle (documented task-id strategy:
        batch runs key on run_id, chat turns on a per-turn uuid, ERP syncs on a
        per-cycle uuid). `task.completed` / `task.failed` are emitted only after
        the refresh call has actually returned, so a sync is never reported as
        finished while it is still running. A Redis/event failure is never fatal
        - emit() swallows it - so the refresh cadence and error handling are
        unchanged.
        """
        service = get_event_service()
        task_id = str(uuid.uuid4())
        await service.emit(
            task_id=task_id, type="task.created",
            payload={"trigger": "erp_refresh", "lookback_days": int(settings.REFRESH_LOOKBACK_DAYS)},
        )
        await service.emit(
            task_id=task_id, type="erp.sync_started",
            payload={"lookback_days": int(settings.REFRESH_LOOKBACK_DAYS)},
        )
        started = time.monotonic()
        try:
            report = await self._refresh_func(database=self._db())
        except Exception as exc:
            log.exception("refresh job failed")
            duration_ms = round((time.monotonic() - started) * 1000)
            await service.emit(
                task_id=task_id, type="erp.sync_failed",
                payload={"error_kind": type(exc).__name__, "duration_ms": duration_ms},
            )
            # the operation is genuinely over (with an exception): now it is
            # correct to close the task
            await service.emit(
                task_id=task_id, type="task.failed",
                payload={"error": type(exc).__name__, "duration_ms": duration_ms},
            )
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        duration_ms = round((time.monotonic() - started) * 1000)
        if report.get("ok"):
            await service.emit(
                task_id=task_id, type="erp.sync_completed",
                payload={
                    "window": report.get("window"),
                    "new_quotes": (report.get("quotes") or {}).get("new", 0),
                    "new_orders": (report.get("orders") or {}).get("new", 0),
                    "new_payments": (report.get("payments") or {}).get("new", 0),
                    "duration_ms": duration_ms,
                },
            )
            await service.emit(
                task_id=task_id, type="task.completed",
                payload={"trigger": "erp_refresh", "duration_ms": duration_ms},
            )
        else:
            # only the exception type name - never the message, which can carry
            # an ERP URL; there is no token in it, but the event is broadcast.
            error_text = str(report.get("error") or "")
            await service.emit(
                task_id=task_id, type="erp.sync_failed",
                payload={"error_kind": error_text.split(":")[0] or "refresh_failed",
                         "duration_ms": duration_ms},
            )
            await service.emit(
                task_id=task_id, type="task.failed",
                payload={"error": error_text.split(":")[0] or "refresh_failed",
                         "duration_ms": duration_ms},
            )
        return report

    # -------------------------------------------------------- job: snapshot

    async def _job_snapshot(self, now: datetime) -> dict:
        try:
            snapshot = await self._snapshot_func(database=self._db(), now=now)
            await self._store_snapshot(snapshot)
            await self._publish("snapshot", snapshot)
            return {"ok": True, "generated_at": snapshot.get("generated_at")}
        except Exception as exc:
            log.exception("snapshot job failed")
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    async def _store_snapshot(self, snapshot: dict) -> None:
        await self._db().snapshots.insert_one(dict(snapshot))
        await self._redis().set(
            SNAPSHOT_KEY, json.dumps({"event": "snapshot", "data": snapshot}, default=str)
        )

    # ---------------------------------------------------- job: lead snapshots

    async def _job_lead_snapshots(self) -> dict:
        """Retain an immutable snapshot of the current lead state for history.

        The `leads` collection is upserted in place, so without this there is no
        way to answer "what changed since the previous pull". `capture_lead_snapshot`
        is idempotent per UTC day (keyed on pull_id) and never raises: an
        incomplete or failed capture is reported, not silently marked success, so
        history is never fabricated.
        """
        try:
            return await self._lead_snapshot_func(database=self._db())
        except Exception as exc:  # a lead-snapshot failure must not stop the cycle
            log.exception("lead snapshot job failed")
            return {"ok": False, "complete": False, "stored": False, "error": f"{type(exc).__name__}: {exc}"}

    # -------------------------------------------------------- job: signals

    async def _job_signals(self, now: datetime) -> dict:
        """Refresh the cached GET /api/signals report the API serves from Redis.

        Doing it here means the expensive read happens once per interval in the
        leader instead of once per dashboard request.
        """
        try:
            payload = await signals.build_signals_payload(self._db(), now)
            await signals.cache_store(payload)
            return {"ok": True, "generated_at": payload.get("generated_at")}
        except Exception as exc:
            log.exception("signals cache job failed")
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # ---------------------------------------------------------- job: agents

    async def _job_agents(self, now: datetime) -> dict:
        try:
            if await self._daily_run_due(now):
                return await self._run_agent("daily_run", now, ignore_cache=True)

            (
                new_quotes, new_invoices, new_leads,
                new_crm_engagement, new_crm_deals,
            ) = await self._new_items(now)
            if (
                not new_quotes and not new_invoices and not new_leads
                and not new_crm_engagement and not new_crm_deals
            ):
                log.info("scheduler: no new or changed items - no agent run")
                return {"ok": True, "ran": False, "new_count": 0}

            trigger = "_and_".join(
                part
                for part, chosen in (
                    ("new_quote", new_quotes),
                    ("new_overdue_invoice", new_invoices),
                    ("new_lead", new_leads),
                    ("new_crm_engagement", new_crm_engagement),
                    ("new_crm_deal", new_crm_deals),
                )
                if chosen
            )
            return await self._run_agent(
                trigger, now, new_quotes=new_quotes, new_invoices=new_invoices,
                new_leads=new_leads, new_crm_engagement=new_crm_engagement,
                new_crm_deals=new_crm_deals,
            )
        except Exception as exc:  # trigger evaluation itself broke
            log.exception("agent trigger check failed")
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    async def _daily_run_due(self, now: datetime) -> bool:
        """True once a day, from AGENT_FULL_RUN_HOUR (server local time) on.

        The `agent_triggers` check means a restart later in the day does not
        bill a second full run.
        """
        local = now.astimezone()  # system local timezone
        day_key = local.date().isoformat()
        if local.hour < int(settings.AGENT_FULL_RUN_HOUR):
            return False
        if self._last_daily_day == day_key:
            return False
        try:
            already = await self._db().agent_triggers.find_one(
                {"trigger": "daily_run", "day": day_key}
            )
        except Exception:
            already = None
        if already is not None:
            self._last_daily_day = day_key
            return False
        return True

    async def _current_items(
        self, now: datetime
    ) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
        """Reuse the graph's own loading node so hashes match the pipeline.

        `_erp=None` on purpose: the lead-outcomes ERP calls add nothing to the
        stable cache keys (those use Mongo-only history counts), and we do not
        want extra ERP traffic every ten minutes.
        """
        from app.agents import graph  # noqa: PLC0415 - heavy import

        state = await graph.load_signals_node(
            {
                "run_id": "scheduler-check",
                "now_epoch": now.timestamp(),
                "_database": self._db(),
                "_erp": None,
                "_use_cache": False,  # we only want the hashes, not cached output
            }
        )
        return (
            state.get("negotiation_input") or [],
            state.get("collections_input") or [],
            state.get("lead_followup_input") or [],
            state.get("crm_engagement_input") or [],
            state.get("crm_deal_watch_input") or [],
        )

    async def _previous_hashes(self) -> set[str]:
        """Every input hash from the most recent completed agent run."""
        docs = (
            await self._db()
            .agent_runs.find({"finished_epoch": {"$exists": True}})
            .sort("finished_epoch", -1)
            .to_list(1)
        )
        if not docs:
            return set()
        run = docs[0]
        hashes: set[str] = set()
        for kind in ("negotiation", "collections", "lead_followup", "crm_engagement", "crm_deal_watch"):
            for item in run.get(kind) or []:
                if item.get("input_hash"):
                    hashes.add(item["input_hash"])
        return hashes

    async def _new_items(
        self, now: datetime
    ) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
        """Items whose input hash the previous run has never seen."""
        negotiation, collections, leads, crm_engagement, crm_deals = await self._current_items(now)
        previous = await self._previous_hashes()
        new_quotes = [item for item in negotiation if item.get("input_hash") not in previous]
        new_invoices = [item for item in collections if item.get("input_hash") not in previous]
        new_leads = [item for item in leads if item.get("input_hash") not in previous]
        new_crm_engagement = [
            item for item in crm_engagement if item.get("input_hash") not in previous
        ]
        new_crm_deals = [item for item in crm_deals if item.get("input_hash") not in previous]
        return new_quotes, new_invoices, new_leads, new_crm_engagement, new_crm_deals

    async def _run_agent(
        self,
        trigger: str,
        now: datetime,
        ignore_cache: bool = False,
        new_quotes: Optional[list[dict]] = None,
        new_invoices: Optional[list[dict]] = None,
        new_leads: Optional[list[dict]] = None,
        new_crm_engagement: Optional[list[dict]] = None,
        new_crm_deals: Optional[list[dict]] = None,
    ) -> dict:
        """One pipeline call + the trigger log + the published briefing."""
        new_quotes = new_quotes or []
        new_invoices = new_invoices or []
        new_leads = new_leads or []
        new_crm_engagement = new_crm_engagement or []
        new_crm_deals = new_crm_deals or []
        local_day = now.astimezone().date().isoformat()
        if trigger == "daily_run":
            self._last_daily_day = local_day

        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)
        log.info(
            "scheduler: agent run %s (%s, new_quotes=%d, new_invoices=%d, new_leads=%d, "
            "new_crm_engagement=%d, new_crm_deals=%d, full=%s)",
            run_id, trigger, len(new_quotes), len(new_invoices), len(new_leads),
            len(new_crm_engagement), len(new_crm_deals), ignore_cache,
        )

        try:
            result = await self._pipeline_func(
                run_id=run_id,
                database=self._db(),
                ignore_cache=ignore_cache,
                trigger=trigger,
            )
        except Exception as exc:
            log.exception("agent run %s failed", run_id)
            await self._log_trigger(
                trigger, run_id, started_at, len(new_quotes), len(new_invoices),
                len(new_leads), ignore_cache, ok=False, error=f"{type(exc).__name__}: {exc}",
            )
            # only agent failures are worth an error event on the live stream
            await self._publish(
                "error",
                {
                    "message": "Agent run failed",
                    "trigger": trigger,
                    "run_id": run_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return {"ok": False, "ran": True, "trigger": trigger, "error": str(exc)}

        await self._log_trigger(
            trigger, run_id, started_at, len(new_quotes), len(new_invoices),
            len(new_leads), ignore_cache, ok=True, error=None,
        )
        await self._publish("briefing", await self._latest_briefing())
        return {
            "ok": True,
            "ran": True,
            "trigger": trigger,
            "new_count": len(new_quotes) + len(new_invoices) + len(new_leads),
            "run_id": run_id,
        }

    async def _log_trigger(
        self,
        trigger: str,
        run_id: str,
        started_at: datetime,
        new_quotes: int,
        new_invoices: int,
        new_leads: int,
        full_run: bool,
        ok: bool,
        error: Optional[str],
    ) -> None:
        doc = {
            "trigger": trigger,
            "day": started_at.astimezone().date().isoformat(),  # server-local day
            "run_id": run_id,
            "new_count": new_quotes + new_invoices + new_leads,
            "new_quotes": new_quotes,
            "new_invoices": new_invoices,
            "new_leads": new_leads,
            "full_run": bool(full_run),
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "ok": ok,
            "error": error,
        }
        try:
            await self._db().agent_triggers.insert_one(doc)
        except Exception:  # logging must never break the cycle
            log.exception("could not write the agent trigger log")

    async def _latest_briefing(self) -> Optional[dict]:
        docs = (
            await self._db()
            .agent_runs.find({"finished_epoch": {"$exists": True}})
            .sort("finished_epoch", -1)
            .to_list(1)
        )
        if not docs:
            return None
        doc = docs[0]
        doc.pop("_id", None)
        return doc

    # ------------------------------------------------------------ publish

    async def _publish(self, event: str, data: Any) -> None:
        """Send one event to the live subscribers. Redis trouble is not fatal."""
        try:
            message = json.dumps({"event": event, "data": data}, default=str)
            await self._redis().publish(UPDATES_CHANNEL, message)
        except Exception:
            log.warning("could not publish %r to Redis", event, exc_info=True)


# ---------------------------------------------------------------------------
# process-wide singleton used by the FastAPI lifespan
# ---------------------------------------------------------------------------

_scheduler: Optional[DashboardScheduler] = None


def get_scheduler() -> DashboardScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = DashboardScheduler()
    return _scheduler


async def start_scheduler() -> None:
    await get_scheduler().start()


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        await _scheduler.stop()
