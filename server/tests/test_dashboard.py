"""Dashboard tests: metrics, incremental refresh, scheduler and the live SSE API.

Everything is offline. FakeMongo (plain dict collections), FakeRedis (set/get/
publish + pubsub) and FakeERP (returns fixture rows) stand in for the real
services, so no network, no Redis server and no keys are needed.

Run:  .venv/bin/python -m pytest tests -q
"""

from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pymongo import ReplaceOne

from app import db as app_db
from app.api import live as live_api
from app.main import app
from app.services import metrics
from app.services.refresh import run_incremental_refresh
from app.services.scheduler import LOCK_KEY, SNAPSHOT_KEY, UPDATES_CHANNEL, DashboardScheduler

# 2026-10-20 15:00 UTC == 10:00 US Central (the ERP's business day).
NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
ERP_OFFSET = timezone(timedelta(hours=-5))


def erp_time(days_ago: float) -> str:
    """An ERP-style timestamp `days_ago` days before NOW."""
    return (NOW.astimezone(ERP_OFFSET) - timedelta(days=days_ago)).isoformat()


# ---------------------------------------------------------------------------
# fixture ERP data
# ---------------------------------------------------------------------------


def fixture_quotes() -> list[dict]:
    return [
        # 6 days old -> inside the 3..30 day stale window
        {"id": 1, "ref": "Q-1", "status": "Quote Created", "amount": 1000.0,
         "created_at": erp_time(6), "customer": {"id": 10, "company_name": "ACME"},
         "sale_rep": {"id": 84, "name": "Jessica Joe"}},
        # 1 day old -> too fresh to be open/stale
        {"id": 2, "ref": "Q-2", "status": "Estimate", "amount": 5000.0,
         "created_at": erp_time(1), "customer": {"id": 11, "company_name": "Fresh"},
         "sale_rep": {"id": 84, "name": "Jessica Joe"}},
        # 10 days old -> second open quote for a different customer
        {"id": 3, "ref": "Q-3", "status": "Quote Created", "amount": 7000.0,
         "created_at": erp_time(10), "customer": {"id": 12, "company_name": "Old"},
         "sale_rep": {"id": 48, "name": "Ella Jacob"}},
        # 40 days old -> dead history, outside the max age
        {"id": 4, "ref": "Q-4", "status": "Quote Created", "amount": 999.0,
         "created_at": erp_time(40), "customer": {"id": 13, "company_name": "Ancient"},
         "sale_rep": {"id": 48, "name": "Ella Jacob"}},
        # already converted into order 103 -> not open
        {"id": 5, "ref": "Q-5", "status": "Quote Created", "amount": 300.0,
         "created_at": erp_time(5), "customer": {"id": 14, "company_name": "Converted"},
         "sale_rep": {"id": 84, "name": "Jessica Joe"}},
        # closed status -> not open
        {"id": 6, "ref": "Q-6", "status": "Cancelled", "amount": 4000.0,
         "created_at": erp_time(7), "customer": {"id": 15, "company_name": "Cancelled"},
         "sale_rep": {"id": 48, "name": "Ella Jacob"}},
    ]


def order_row(order_id: int, total: float, paid: float, days: float, **over) -> dict:
    row = {
        "id": order_id,
        "inv_ref": f"INV-{order_id}",
        "status": 2,
        "status_label": "Approved",
        "payment_status": 0,
        "payment_status_label": "Not Paid",
        "invoice_date": erp_time(days),
        "total_amount": total,
        "amount_paid": paid,
        "customer": {"id": 10 + order_id, "company_name": f"Customer {order_id}"},
        "sale_rep": {"id": 84, "name": "Jessica Joe"},
    }
    row.update(over)
    return row


def fixture_orders() -> list[dict]:
    return [
        order_row(100, 750.0, 0.0, 35),                       # unpaid + overdue
        order_row(101, 300.0, 0.0, 10),                       # unpaid, not overdue
        order_row(102, 500.0, 500.0, 5, payment_status=2,
                  payment_status_label="Paid"),               # paid -> excluded
        order_row(103, 200.0, 0.0, 35, quote_id=5),           # unpaid + overdue
        order_row(104, 0.0, 0.0, 8, payment_status=None,
                  payment_status_label=None),                 # empty invoice -> excluded
    ]


def fixture_payments() -> list[dict]:
    return [
        {"id": 701, "amount": 1000, "received_at": erp_time(0), "sale_rep": 84,
         "users": {"id": 84, "name": "Jessica Joe"}, "customer_id": 10},
        {"id": 702, "amount": 500, "received_at": erp_time(1), "sale_rep": 84,
         "users": {"id": 84, "name": "Jessica Joe"}, "customer_id": 10},
        {"id": 703, "amount": 250, "received_at": erp_time(15), "sale_rep": {"id": 48, "name": "Ella Jacob"},
         "customer_id": 12},
        {"id": 704, "amount": 999, "received_at": erp_time(20), "sale_rep": 48,
         "users": {"id": 48, "name": "Ella Jacob"}, "customer_id": 12},
        {"id": 705, "amount": 100, "received_at": erp_time(49), "sale_rep": 84,
         "users": {"id": 84, "name": "Jessica Joe"}, "customer_id": 10},
    ]


# ---------------------------------------------------------------------------
# fake Mongo
# ---------------------------------------------------------------------------


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in expected):
                return False
            continue
        actual: Any = doc
        for part in key.split("."):
            actual = actual.get(part) if isinstance(actual, dict) else None
            if actual is None:
                break
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$exists" in expected and (actual is not None) != bool(expected["$exists"]):
                return False
        elif actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, docs: list[dict]):
        self.docs = list(docs)

    def sort(self, field: str, direction: int = -1):
        self.docs.sort(key=lambda row: row.get(field) or 0, reverse=direction < 0)
        return self

    async def to_list(self, limit: int):
        return list(self.docs[:limit])


class FakeCollection:
    def __init__(self, docs: list[dict] | None = None):
        self.docs = list(docs or [])

    def find(self, query: dict | None = None):
        query = query or {}
        return FakeCursor([row for row in self.docs if _matches(row, query)])

    async def insert_one(self, doc: dict) -> None:
        self.docs.append(dict(doc))

    async def find_one(self, query: dict | None = None):
        for row in self.docs:
            if _matches(row, query or {}):
                return dict(row)
        return None

    async def bulk_write(self, operations, ordered: bool = False):
        for op in operations:
            assert isinstance(op, ReplaceOne)
            key = op._filter["_id"]
            replacement = dict(op._doc)
            self.docs = [row for row in self.docs if row.get("_id") != key]
            self.docs.append(replacement)

        class _Result:
            modified_count = len(operations)

        return _Result()


class FakeDatabase:
    def __bool__(self):
        # pymongo's AsyncDatabase cannot be truth-tested either: this keeps the
        # `is not None` checks in metrics/refresh/scheduler honest.
        raise NotImplementedError(
            "AsyncDatabase objects do not implement truth value testing or bool()"
        )

    def __init__(self, **collections):
        for name in (
            "quotes_flat", "orders_flat", "payments_flat", "users_flat",
            "agent_runs", "agent_triggers", "refresh_logs", "snapshots", "leads",
        ):
            setattr(self, name, collections.get(name) or FakeCollection())


def scheduler_db() -> FakeDatabase:
    return FakeDatabase(
        quotes_flat=FakeCollection(fixture_quotes()),
        orders_flat=FakeCollection(fixture_orders()),
        payments_flat=FakeCollection(fixture_payments()),
    )


# ---------------------------------------------------------------------------
# fake Redis
# ---------------------------------------------------------------------------


class FakePubSub:
    def __init__(self, redis: "FakeRedis"):
        self.redis = redis
        self.channels: set[str] = set()
        self.pending: deque[dict] = deque()
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.channels.add(channel)
        self.redis.subscribers.add(self)
        for message in self.redis.staged.pop(channel, []):
            self.pending.append(message)

    async def unsubscribe(self, channel: str | None = None) -> None:
        if channel is None:
            self.channels.clear()
        else:
            self.channels.discard(channel)
        self.redis.subscribers.discard(self)

    async def aclose(self) -> None:
        self.closed = True
        self.channels.clear()
        self.redis.subscribers.discard(self)

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 1.0):
        if self.pending:
            return self.pending.popleft()
        # a real client would block for `timeout`; a tiny sleep keeps the
        # stream loop responsive without slowing the test down
        import asyncio

        await asyncio.sleep(0.01)
        return None


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []
        self.subscribers: set[FakePubSub] = set()
        self.pubsubs: list[FakePubSub] = []
        self.staged: dict[str, list[dict]] = {}

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key: str):
        return self.store.get(key)

    async def delete(self, key: str):
        self.store.pop(key, None)
        return 1

    async def publish(self, channel: str, message: str):
        self.published.append((channel, message))
        for sub in list(self.subscribers):
            if channel in sub.channels:
                sub.pending.append({"type": "message", "channel": channel, "data": message})
        return len(self.subscribers)

    def pubsub(self) -> FakePubSub:
        sub = FakePubSub(self)
        self.pubsubs.append(sub)
        return sub

    def stage(self, channel: str, payload: dict) -> None:
        """Queue a message that the next subscriber will see immediately."""
        self.staged.setdefault(channel, []).append(
            {"type": "message", "channel": channel, "data": json.dumps(payload)}
        )


# ---------------------------------------------------------------------------
# fake ERP (quotes/orders/payments windows)
# ---------------------------------------------------------------------------


class FakeERP:
    def __init__(self, quotes=None, orders=None, payments=None):
        self.quotes = list(quotes or [])
        self.orders = list(orders or [])
        self.payments = list(payments or [])

    async def get_quotes_with_total(self, date_from: str, date_to: str):
        return list(self.quotes), len(self.quotes)

    async def get_orders_with_total(self, date_from: str, date_to: str):
        return list(self.orders), len(self.orders)

    async def get_payments(self, date_from: str | None = None, date_to: str | None = None):
        return {
            "data": list(self.payments),
            "count": len(self.payments),
            "total": len(self.payments),
            "sum": sum(row.get("amount", 0) for row in self.payments),
        }


# ---------------------------------------------------------------------------
# scheduler helpers
# ---------------------------------------------------------------------------


class RecordingPipeline:
    """Stands in for run_pipeline and records every call."""

    def __init__(self, error: Exception | None = None):
        self.calls: list[dict] = []
        self.error = error

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"run_id": kwargs.get("run_id"), "counts": {}}


async def ok_refresh(**kwargs):
    return {"ok": True, "window": {"from": "2026-10-19", "to": "2026-10-20"}}


def make_scheduler(redis, database, pipeline=None, refresh_func=ok_refresh) -> DashboardScheduler:
    scheduler = DashboardScheduler(
        database=database,
        redis=redis,
        refresh_func=refresh_func,
        pipeline_func=pipeline or RecordingPipeline(),
        interval_seconds=60,
    )
    # the daily safety net is tested via its own logic; keep these cycles event-only
    scheduler._last_daily_day = datetime.now().astimezone().date().isoformat()
    return scheduler


def published_events(redis: FakeRedis) -> list[tuple[str, Any]]:
    return [(json.loads(message)["event"], json.loads(message)["data"]) for _channel, message in redis.published]


async def seed_previous_run(database, scheduler: DashboardScheduler, now: datetime) -> None:
    """Store the current item hashes as if the agent had just finished a run."""
    negotiation, collections = await scheduler._current_items(now)
    assert negotiation and collections  # fixtures really do produce signals
    database.agent_runs.docs.append(
        {
            "run_id": "previous-run",
            "finished_epoch": now.timestamp() - 60,
            "negotiation": negotiation,
            "collections": collections,
        }
    )


# ---------------------------------------------------------------------------
# 1. metrics
# ---------------------------------------------------------------------------


def test_metrics_tiles_on_fixture_data():
    snapshot = metrics.build_snapshot_from_docs(
        fixture_quotes(), fixture_orders(), fixture_payments(), NOW
    )

    assert snapshot["revenue_today"] == 1000.0
    assert snapshot["revenue_month_to_date"] == 1750.0   # Oct 1..20
    assert snapshot["revenue_last_7_days"] == 1500.0     # Oct 14..20
    assert snapshot["payments_count_today"] == 1

    # stale-quote rules: 6 and 10 days old, converted/cancelled/too-old dropped
    assert snapshot["open_quotes_count"] == 2
    assert snapshot["open_quotes_value"] == 8000.0
    assert snapshot["open_quotes_value_display"] == "$8,000"

    assert snapshot["unpaid_count"] == 3
    assert snapshot["unpaid_total"] == 1250.0           # 750 + 300 + 200
    assert snapshot["overdue_count"] == 2
    assert snapshot["overdue_total"] == 950.0
    assert snapshot["unpaid_total_display"] == "$1,250"

    # payments this month: rep 84 (1000 + 500) beats rep 48 (250)
    assert snapshot["top_reps_this_month"][0]["rep_id"] == 84
    assert snapshot["top_reps_this_month"][0]["name"] == "Jessica Joe"
    assert snapshot["top_reps_this_month"][0]["total"] == 1500.0
    assert snapshot["top_reps_this_month"][0]["count"] == 2
    assert snapshot["top_reps_this_month"][1]["rep_id"] == 48
    assert snapshot["top_reps_this_month"][1]["total"] == 250.0

    assert len(snapshot["quotes_per_day"]) == 14
    assert len(snapshot["revenue_per_day"]) == 14
    assert snapshot["quotes_per_day"][-1] == {"date": "2026-10-20", "count": 0}
    assert snapshot["quotes_per_day"][-7] == {"date": "2026-10-14", "count": 1}
    assert snapshot["revenue_per_day"][-1]["total"] == 1000.0
    assert snapshot["generated_at"] == NOW.isoformat()

    # money text convention used by agents/chat
    assert metrics.money_text(357500) == "$357,500"
    assert metrics.money_text(1500.5) == "$1,500.50"


# ---------------------------------------------------------------------------
# 2. incremental refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_upserts_and_counts_changes():
    database = FakeDatabase()
    erp = FakeERP(
        quotes=[fixture_quotes()[0]],
        orders=[fixture_orders()[0]],
        payments=[fixture_payments()[0]],
    )

    first = await run_incremental_refresh(lookback_days=2, database=database, erp=erp)
    assert first["ok"] is True
    assert first["quotes"] == {"fetched": 1, "new": 1, "updated": 0, "unchanged": 0}
    assert first["orders"]["new"] == 1
    assert first["payments"]["new"] == 1
    assert database.quotes_flat.docs[0]["_id"] == 1
    assert len(database.refresh_logs.docs) == 1

    second = await run_incremental_refresh(lookback_days=2, database=database, erp=erp)
    assert second["quotes"]["unchanged"] == 1
    assert second["quotes"]["new"] == 0
    assert second["quotes"]["updated"] == 0

    erp.quotes[0]["amount"] = 1200.0  # ERP changed the quote
    third = await run_incremental_refresh(lookback_days=2, database=database, erp=erp)
    assert third["quotes"] == {"fetched": 1, "new": 0, "updated": 1, "unchanged": 0}
    assert len(database.refresh_logs.docs) == 3


# ---------------------------------------------------------------------------
# 3 + 4. event-triggered agents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unchanged_signals_make_zero_agent_calls():
    database = scheduler_db()
    redis = FakeRedis()
    pipeline = RecordingPipeline()
    scheduler = make_scheduler(redis, database, pipeline)
    await seed_previous_run(database, scheduler, NOW)

    summary = await scheduler.run_cycle(NOW)

    assert pipeline.calls == []
    assert summary["agents"] == {"ok": True, "ran": False, "new_count": 0}
    assert summary["snapshot"]["ok"] is True
    assert [name for name, _ in published_events(redis)] == ["snapshot"]
    assert database.agent_triggers.docs == []  # no run, no trigger log


@pytest.mark.asyncio
async def test_changed_item_triggers_exactly_one_agent_call():
    database = scheduler_db()
    redis = FakeRedis()
    pipeline = RecordingPipeline()
    scheduler = make_scheduler(redis, database, pipeline)
    await seed_previous_run(database, scheduler, NOW)

    # the ERP changed one quote amount -> its stable cache key changes
    database.quotes_flat.docs[0]["amount"] = 2500.0

    summary = await scheduler.run_cycle(NOW)

    assert len(pipeline.calls) == 1
    assert pipeline.calls[0]["ignore_cache"] is False
    assert summary["agents"]["trigger"] == "new_quote"
    assert summary["agents"]["new_count"] == 1
    trigger_log = database.agent_triggers.docs[-1]
    assert trigger_log["trigger"] == "new_quote"
    assert trigger_log["new_quotes"] == 1
    assert trigger_log["ok"] is True
    assert "briefing" in [name for name, _ in published_events(redis)]


# ---------------------------------------------------------------------------
# 5. Redis lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lock_prevents_overlapping_runs():
    database = scheduler_db()
    redis = FakeRedis()
    await redis.set(LOCK_KEY, "another-process")
    refresh_calls: list[dict] = []

    async def refresh_func(**kwargs):
        refresh_calls.append(kwargs)
        return {"ok": True}

    scheduler = make_scheduler(redis, database, refresh_func=refresh_func)
    summary = await scheduler.run_cycle(NOW)

    assert summary == {"at": NOW.isoformat(), "skipped": "locked"}
    assert refresh_calls == []
    assert redis.published == []
    assert await redis.get(LOCK_KEY) == "another-process"  # never released by us


# ---------------------------------------------------------------------------
# 6. a failing job does not stop the loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_survives_job_errors():
    database = scheduler_db()
    redis = FakeRedis()

    async def broken_refresh(**kwargs):
        raise RuntimeError("ERP is down")

    scheduler = make_scheduler(
        redis, database, RecordingPipeline(error=RuntimeError("LLM is down")), broken_refresh
    )

    first = await scheduler.run_cycle(NOW)
    second = await scheduler.run_cycle(NOW)

    assert first["refresh"]["ok"] is False
    assert "ERP is down" in first["refresh"]["error"]
    assert first["snapshot"]["ok"] is True         # snapshot still built from Mongo
    assert first["agents"]["ok"] is False
    assert second["agents"]["ok"] is False         # the second cycle still ran
    assert await redis.get(LOCK_KEY) is None       # lock released even on failure

    events = published_events(redis)
    # snapshot events are normal; only agent failures publish "error"
    assert [name for name, _ in events].count("error") == 2
    error_payload = next(data for name, data in events if name == "error")
    assert error_payload["trigger"] == "new_quote_and_new_overdue_invoice"
    assert database.agent_triggers.docs[-1]["ok"] is False


# ---------------------------------------------------------------------------
# 7 + 8. live SSE endpoint
# ---------------------------------------------------------------------------


def snapshot_for_stream() -> dict:
    return metrics.build_snapshot_from_docs(
        fixture_quotes(), fixture_orders(), fixture_payments(), NOW
    )


def prepare_live(monkeypatch) -> FakeRedis:
    """Point the live route at a fake Redis + fake Mongo; no real services."""
    snapshot = snapshot_for_stream()
    redis = FakeRedis()
    redis.store[SNAPSHOT_KEY] = json.dumps({"event": "snapshot", "data": snapshot})
    redis.stage(
        UPDATES_CHANNEL,
        {"event": "snapshot", "data": {**snapshot, "revenue_today": 7777.0}},
    )

    database = FakeDatabase(
        agent_runs=FakeCollection(
            [{"run_id": "run-1", "finished_epoch": 1.0, "master": {"bullets": ["hello"]}}]
        )
    )

    monkeypatch.setattr(live_api, "get_redis", lambda: redis)
    monkeypatch.setattr(app_db, "get_client", lambda: object())

    async def close_db():
        return None

    monkeypatch.setattr(app_db, "close", close_db)
    monkeypatch.setattr(app_db, "get_db", lambda: database)
    live_api._active_clients.clear()
    return redis


def read_events(response, limit: int) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    current = None
    for line in response.iter_lines():
        if isinstance(line, bytes):
            line = line.decode()
        if line.startswith("event: "):
            current = line[len("event: "):].strip()
        elif line.startswith("data: ") and current:
            events.append((current, json.loads(line[len("data: "):])))
            current = None
            if len(events) >= limit:
                break
    return events


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_live_stream_sends_snapshot_first_then_updates(monkeypatch):
    prepare_live(monkeypatch)

    with TestClient(app) as client:
        with client.stream("GET", "/api/live") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            events = read_events(response, 3)

    assert [name for name, _ in events] == ["snapshot", "briefing", "snapshot"]
    assert events[0][1]["revenue_today"] == 1000.0        # current snapshot first
    assert events[1][1]["run_id"] == "run-1"              # then the latest briefing
    assert events[2][1]["revenue_today"] == 7777.0        # then the live update


def test_live_disconnect_cleans_up_the_subscription(monkeypatch):
    redis = prepare_live(monkeypatch)

    with TestClient(app) as client:
        with client.stream("GET", "/api/live") as response:
            # pull line by line without closing: the connection stays open
            lines = response.iter_lines()
            events: list[str] = []
            current = None
            while len(events) < 3:
                line = next(lines)
                if isinstance(line, bytes):
                    line = line.decode()
                if line.startswith("event: "):
                    current = line[len("event: "):].strip()
                elif line.startswith("data: ") and current:
                    events.append(current)
                    current = None

            assert events[0] == "snapshot"
            # while the client is connected it owns one slot and one subscription
            assert len(live_api._active_clients) == 1
            pubsub = redis.pubsubs[-1]
            assert pubsub.channels == {UPDATES_CHANNEL}

            lines.close()  # the client disconnects here

        # disconnecting must drop the subscription and free the slot
        assert wait_for(lambda: pubsub.closed and not pubsub.channels)
        assert wait_for(lambda: len(live_api._active_clients) == 0)
