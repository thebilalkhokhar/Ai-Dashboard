"""Real-time task-event service.

This is the single place that turns backend activity into the versioned
``TaskEvent`` records the JARVIS command center reads. It does three things:

  1. allocates a monotonic ``seq`` per task with an atomic Mongo operation, so
     concurrent items inside a run - and concurrent backend workers - never
     collide and never use an in-process counter;
  2. persists every event to the ``task_events`` collection (unique event
     identity via ``_id``, unique ``(task_id, seq)`` via an index);
  3. publishes the persisted event on the existing Redis update channel so the
     live dashboard sees it immediately.

Failure model (documented honestly, per the delivery guarantees below):

  * A Redis publish failure NEVER crashes an agent, chat turn or scheduler run:
    the event is already persisted and a reconnecting client replays it from
    Mongo. ``emit`` swallows publish errors and logs them without secrets.
  * A persistence failure means there is no durable record, so nothing is
    published; ``emit`` logs it and returns ``None``. Callers must treat a
    ``None`` event as "not recorded", not as "crash".

Delivery guarantees:

  * Persistence (Mongo) is the durable source of truth.
  * Live delivery (Redis pub/sub) is best-effort, at-most-once per connected
    client and NOT durable: a client that is disconnected while an event is
    published misses it live, and recovers it through the ``/api/tasks/{id}
    /events`` replay backed by Mongo. We do not claim at-least-once or
    exactly-once delivery over Redis.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app import db as app_db
from app.schemas.events import AGENT_EVENT_TYPES, SCHEMA_VERSION, TaskEvent
from app.services.redis_client import get_redis

log = logging.getLogger("events")

EVENTS_COLLECTION = "task_events"
COUNTERS_COLLECTION = "task_event_counters"

# The live SSE stream relays persisted events on the channel the dashboard
# already listens to, wrapped in a "task_event" envelope so the existing
# snapshot/briefing/error formats are untouched.
TASK_EVENT_ENVELOPE = "task_event"

# agent.* event type -> a coarse status the registry/UI can show.
_STATUS_BY_TYPE = {
    "agent.queued": "queued",
    "agent.started": "running",
    "agent.progress": "running",
    "agent.completed": "completed",
    "agent.failed": "failed",
}
# Event types that close a task, and the status they prove. An ERP sync has no
# `task.*` events of its own, so its domain terminal events count here too -
# without them every refresh looked permanently "running".
TERMINAL_TASK_TYPES = {
    "task.completed": "completed",
    "task.failed": "failed",
    "task.cancelled": "cancelled",
    "erp.sync_completed": "completed",
    "erp.sync_failed": "failed",
}
# A task with no terminal event is only "running" while it is plausibly still
# going. Past this window the honest answer is "unknown": the record ended
# without a terminal event (a crash, or a run predating this fix), and the UI
# must not present it as live work.
STALE_TASK_SECONDS = 30 * 60
# task.* / erp.* event type -> task status for non-terminal progress.
_TASK_STATUS_BY_TYPE = {
    "task.completed": "completed",
    "task.failed": "failed",
    "task.cancelled": "cancelled",
    "task.started": "running",
    "task.created": "created",
    "erp.sync_started": "running",
    "erp.sync_completed": "completed",
    "erp.sync_failed": "failed",
}


_INPUT_VALUE_RE = re.compile(r",?\s*input_value=.*?(?=(?:\[type=|$)|, id=)", re.S)


def _safe_validation_message(exc: Exception) -> str:
    """A validation message with any echoed payload value removed.

    Pydantic renders rejected input as ``input_value={'refresh_token': 'x'}``,
    so logging the exception verbatim would write the very secret the schema
    just refused into the log. Only the rule that fired is kept.
    """
    text = str(exc)
    if "validation error" in text.lower():
        # keep the first offending rule line, drop the echoed values
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        rule = next((line for line in lines if line.startswith("Value error,")), lines[-1] if lines else "")
        return _INPUT_VALUE_RE.sub("", rule).strip()
    return _INPUT_VALUE_RE.sub("", text).strip()


def _moment(doc: Optional[dict]) -> Optional[datetime]:
    """The aware UTC timestamp of one stored event, or None if it has none."""
    value = (doc or {}).get("ts")
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _history_details(events: list[dict], status: str, closing: Optional[dict]) -> dict:
    """Everything a history row can claim from the task's own events.

    Only what is in the stored documents: the first and last moment the task
    reported, how long it ran once it reached an ending, which agents took part,
    and the redacted error kind a failure recorded. A task with no terminal
    event gets no duration rather than an invented one.
    """
    moments = [stamp for stamp in (_moment(doc) for doc in events) if stamp]
    first = min(moments) if moments else None
    last = max(moments) if moments else None
    details: dict[str, Any] = {
        "created_at": first.isoformat() if first else None,
        "updated_at": last.isoformat() if last else None,
        "duration_ms": None,
        "agents": sorted({str(doc["agent_id"]) for doc in events if doc.get("agent_id")}),
        "error_kind": None,
    }
    if closing is not None and first is not None:
        end = _moment(closing)
        if end is not None and end >= first:
            details["duration_ms"] = int((end - first).total_seconds() * 1000)
    if status in ("failed", "cancelled") and closing is not None:
        payload = closing.get("payload") or {}
        kind = payload.get("error_kind") or payload.get("error")
        if isinstance(kind, str) and kind:
            # emitters store the exception type name, never its message
            details["error_kind"] = kind[:60]
    return details


class EventService:
    """Emit, persist, publish and read task events.

    ``database`` and ``redis_factory`` are injectable so tests never touch a
    real MongoDB or Redis - the same convention the rest of the codebase uses.
    """

    def __init__(
        self,
        database: Any = None,
        redis_factory: Optional[Callable[[], Any]] = None,
        channel: Optional[str] = None,
    ) -> None:
        self._database = database
        self._redis_factory = redis_factory
        # Resolved lazily to avoid an import cycle (scheduler imports this
        # module at load time). The dashboard update channel is a stable name.
        self._channel = channel
        self._indexes_ready = False
        self._indexes_attempted = False
        # how many emits were dropped as schema-invalid (a programming-error
        # counter; it must stay 0 in a healthy system)
        self.rejected = 0

    @property
    def channel(self) -> str:
        if self._channel is None:
            from app.services.scheduler import UPDATES_CHANNEL  # local: no cycle

            self._channel = UPDATES_CHANNEL
        return self._channel

    # ---------------------------------------------------------------- plumbing

    def _db(self) -> Any:
        # AsyncDatabase cannot be truth-tested: never use `or` here.
        return self._database if self._database is not None else app_db.get_db()

    def _redis(self) -> Any:
        return (self._redis_factory or get_redis)()

    def _events(self):
        db = self._db()
        try:
            return db[EVENTS_COLLECTION]
        except TypeError:
            return getattr(db, EVENTS_COLLECTION)

    def _counters(self):
        db = self._db()
        try:
            return db[COUNTERS_COLLECTION]
        except TypeError:
            return getattr(db, COUNTERS_COLLECTION)

    async def ensure_indexes(self) -> None:
        """Create the unique (task_id, seq) index and the seq-1 read index.

        Best-effort and idempotent: a duplicate-name re-create is quiet. A
        failure here is logged, never raised, so a broken index does not stop
        the API - reads still work, the app just loses the DB-level guard
        against a (task_id, seq) collision. The atomic ``$inc`` counter already
        guarantees per-task order and unique seq across workers, so event
        ordering and uniqueness are not lost even if this fails.
        """
        if self._indexes_attempted:
            return
        self._indexes_attempted = True
        try:
            await self._events().create_index(
                [("task_id", 1), ("seq", 1)],
                name="task_events_unique_task_seq",
                unique=True,
            )
            await self._events().create_index(
                [("task_id", 1), ("ts", -1)], name="task_events_by_task_ts"
            )
        except Exception as exc:
            log.warning("could not create the task-event indexes: %s", exc)
        else:
            self._indexes_ready = True

    # ------------------------------------------------------- sequence numbers

    async def next_seq(self, task_id: str) -> int:
        """Atomically reserve the next sequence number for a task.

        ``$inc`` with upsert is atomic across concurrent coroutines, threads
        and backend workers, so every caller of one task gets a distinct,
        monotonically increasing integer - no in-process counter, no race.
        """
        doc = await self._counters().find_one_and_update(
            {"_id": task_id},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(doc["seq"])

    # ------------------------------------------------------------------ emit

    async def emit(
        self,
        *,
        task_id: str,
        type: str,
        agent_id: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> Optional[TaskEvent]:
        """Allocate a seq, persist, then publish one event. Never raises.

        Returns the persisted ``TaskEvent``, or ``None`` when the event could
        not be recorded (bad schema, sequence or persistence failure) so the
        caller can log "not recorded" without the run crashing.
        """
        # Make sure the unique (task_id, seq) index exists from the very first
        # event (best-effort, one-shot). Even if it cannot be created, the
        # atomic counter below still guarantees per-task ordering and unique
        # seq across every coroutine and worker - the index is defence in depth.
        await self.ensure_indexes()

        try:
            seq = await self.next_seq(task_id)
        except Exception as exc:
            log.warning("could not allocate an event seq for task %s: %s", task_id, exc)
            return None

        try:
            event = TaskEvent(
                v=SCHEMA_VERSION,
                task_id=task_id,
                seq=seq,
                ts=datetime.now(timezone.utc),
                type=type,  # validated against the closed set
                agent_id=agent_id,
                payload=payload or {},
            )
        except Exception as exc:
            # A rejected event is a programming error, not an infrastructure
            # hiccup: it is the only way a lost lifecycle event can be noticed,
            # so name the payload keys (never the values) and log it loudly.
            self.rejected += 1
            log.error(
                "DROPPED task event %s/%s - payload keys %s are not valid: %s",
                task_id,
                type,
                sorted(str(key) for key in (payload or {})),
                _safe_validation_message(exc),
            )
            return None

        if not await self._persist(event):
            return None

        await self._publish(event)
        return event

    async def _persist(self, event: TaskEvent) -> bool:
        """Insert one event; True when it is durably recorded.

        Uses event_id as ``_id`` so a retried/replayed emit can never create a
        duplicate event identity. The unique (task_id, seq) index is the second
        guard. Logs on failure without ever touching the payload contents."""
        try:
            doc = event.to_document()
            doc["_id"] = doc.pop("event_id")
            await self._events().insert_one(doc)
            return True
        except DuplicateKeyError:
            log.warning(
                "duplicate task event (%s seq %s) ignored", event.task_id, event.seq
            )
            return False
        except Exception as exc:
            log.warning("could not persist task event (%s): %s", event.type, exc)
            return False

    async def _publish(self, event: TaskEvent) -> None:
        """Best-effort live relay. A Redis failure is not fatal (see guarantees)."""
        try:
            message = json.dumps(
                {"event": TASK_EVENT_ENVELOPE, "data": event.model_dump(mode="json")},
                default=str,
                ensure_ascii=False,
            )
            await self._redis().publish(self.channel, message)
        except Exception as exc:
            log.warning("could not publish task event live (%s): %s", event.type, exc)

    # ------------------------------------------------------------------ read

    async def get_events(
        self, task_id: str, after: int = 0, limit: int = 1000
    ) -> list[TaskEvent]:
        """Persisted events for a task with ``seq > after``, in seq order."""
        cursor = self._events().find({"task_id": task_id, "seq": {"$gt": int(after)}})
        try:
            cursor = cursor.sort("seq", 1)
        except Exception:
            pass  # a test cursor may not support sort; rows are still bounded
        docs = await cursor.to_list(limit)
        events = []
        for doc in docs:
            try:
                events.append(TaskEvent.from_document(doc))
            except Exception as exc:
                log.warning("skipping unreadable stored event (%s): %s", doc.get("_id"), exc)
        return events

    async def last_seq(self, task_id: str) -> int:
        """Highest persisted seq for a task (0 when none)."""
        events = await self.get_events(task_id, after=0, limit=10_000)
        return max((event.seq for event in events), default=0)

    async def task_exists(self, task_id: str) -> bool:
        cursor = self._events().find({"task_id": task_id})
        rows = await cursor.to_list(1)
        return bool(rows)

    def _status_from_events(
        self, events: list[dict]
    ) -> tuple[str, Optional[dict], Optional[dict]]:
        """One task's status, decided from every event it recorded.

        Returns ``(status, newest, closing)``. The newest terminal event wins
        and is returned as ``closing``; with no terminal event at all the task
        is "running" only while it is recent enough to plausibly still be going
        and ``closing`` is None. An older record that simply stopped is reported
        as "unknown" rather than pretending to be live work.
        """
        newest = max(events, key=lambda doc: int(doc.get("seq") or 0))
        terminal = [
            doc for doc in events if doc.get("type") in TERMINAL_TASK_TYPES
        ]
        if terminal:
            closing = max(terminal, key=lambda doc: int(doc.get("seq") or 0))
            return TERMINAL_TASK_TYPES[closing["type"]], newest, closing

        status = _TASK_STATUS_BY_TYPE.get(newest.get("type"), "running")
        moment = newest.get("ts")
        if isinstance(moment, datetime):
            aware = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - aware).total_seconds()
            if age > STALE_TASK_SECONDS:
                return "unknown", newest, None
        return status, newest, None

    async def recent_tasks(self, limit: int = 20, scan: int = 2000) -> list[dict]:
        """The most recently active tasks, collapsed from their real events.

        Reads the freshest events (bounded scan) and folds them into one row per
        ``task_id``. Status comes from the task's whole event set, not just its
        last row, so a completed run whose terminal event arrived out of order
        still reads as completed.
        """
        cursor = self._events().find({})
        try:
            cursor = cursor.sort("ts", -1)
        except Exception:
            pass
        docs = await cursor.to_list(scan)

        grouped: dict[str, list[dict]] = {}
        for doc in docs:
            task_id = doc.get("task_id")
            if not task_id:
                continue
            grouped.setdefault(task_id, []).append(doc)

        rows: list[dict] = []
        for task_id, events in grouped.items():
            status, newest, closing = self._status_from_events(events)
            ts = newest.get("ts")
            rows.append(
                {
                    "task_id": task_id,
                    "last_seq": int(newest.get("seq") or 0),
                    "last_type": newest.get("type"),
                    "status": status,
                    "ts": ts.isoformat() if isinstance(ts, datetime) else ts,
                    **_history_details(events, status, closing),
                    # Filled from the task's real task.created payload below.
                    "prompt": None,
                    "trigger": None,
                }
            )
        rows.sort(key=lambda row: row["ts"] or "", reverse=True)
        tasks = {row["task_id"]: row for row in rows[:limit]}

        # The history drawer shows the user's own request when the backend
        # recorded one. This is the persisted prompt/trigger, never a label
        # derived from a task id or an invented description.
        if tasks:
            id_filter = {"task_id": {"$in": list(tasks)}}
            created = await self._events().find(id_filter).to_list(scan)
            for doc in created:
                if doc.get("type") != "task.created":
                    continue
                row = tasks.get(doc.get("task_id"))
                if row is None or row["prompt"] is not None:
                    continue
                payload = doc.get("payload") or {}
                prompt = payload.get("prompt")
                trigger = payload.get("trigger")
                if isinstance(prompt, str) and prompt.strip():
                    row["prompt"] = prompt.strip()
                if isinstance(trigger, str) and trigger.strip():
                    row["trigger"] = trigger.strip()
        return list(tasks.values())

    async def last_status_by_agent(self, scan: int = 2000) -> dict[str, str]:
        """Latest known coarse status per agent, from persisted agent.* events.

        Agents that have never run are absent here - the registry renders those
        as idle/unknown, never as running."""
        cursor = self._events().find({"type": {"$in": list(AGENT_EVENT_TYPES)}})
        try:
            cursor = cursor.sort("ts", -1)
        except Exception:
            pass
        docs = await cursor.to_list(scan)
        status_by_agent: dict[str, str] = {}
        for doc in docs:
            agent_id = doc.get("agent_id")
            if not agent_id or agent_id in status_by_agent:
                continue
            status_by_agent[agent_id] = _STATUS_BY_TYPE.get(doc.get("type"), "unknown")
        return status_by_agent


# ---------------------------------------------------------------------------
# process-wide default service
# ---------------------------------------------------------------------------

_service: Optional[EventService] = None


def get_event_service() -> EventService:
    """The shared service used by agents, chat and the API routes."""
    global _service
    if _service is None:
        _service = EventService()
    return _service


def set_event_service(service: Optional[EventService]) -> None:
    """Swap the shared service (tests); pass None to reset to the default."""
    global _service
    _service = service
