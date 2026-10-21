"""Route registration, customer-context, API-key and run-lock tests."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import db as app_db
from app.api import live as live_api
from app.api import routes
from app.api.security import key_matches
from app.config import settings
from app.main import app, cors_origins
from app.services import metrics, run_guard, signals
from mongo_fake import FakeAggCollection


@pytest.fixture
def fixture_customer_context():
    return {"customer_id": 1610, "company_name": "Fixture Customer"}


def test_customer_context_route_returns_200(monkeypatch, fixture_customer_context):
    class FakeERP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    async def build_context(customer_id, *, database, erp, max_leads):
        assert customer_id == fixture_customer_context["customer_id"]
        return fixture_customer_context

    async def close_db():
        return None

    monkeypatch.setattr(routes, "ERPClient", FakeERP)
    monkeypatch.setattr(routes, "get_db", lambda: object())
    monkeypatch.setattr(routes.processors, "build_customer_context", build_context)
    monkeypatch.setattr(app_db, "get_client", lambda: object())
    monkeypatch.setattr(app_db, "close", close_db)

    with TestClient(app) as client:
        response = client.get("/api/customers/1610/context")

    assert response.status_code == 200
    assert response.json()["customer_id"] == 1610
    assert response.json()["company_name"] == "Fixture Customer"


def test_all_expected_api_routes_are_registered():
    registered = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }
    assert {
        ("GET", "/api/signals"),
        ("POST", "/api/agents/run"),
        ("GET", "/api/briefing/latest"),
        ("GET", "/api/customers/{customer_id}/context"),
        ("POST", "/api/chat"),
        ("GET", "/api/leads/stuck"),
    } <= registered


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, _limit):
        return list(self.rows)


class FakeCollection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, *_args, **_kwargs):
        return FakeCursor(self.rows)


class FakeSignalDatabase:
    def __init__(self, quotes=(), orders=(), leads=()):
        self.quotes_flat = FakeCollection(quotes)
        self.orders_flat = FakeCollection(orders)
        self.leads = FakeCollection(leads)


class AggregationDatabase:
    """quotes_flat / orders_flat that answer aggregations, like the real ones."""

    def __init__(self, quotes=(), orders=(), leads=()):
        self.quotes_flat = FakeAggCollection(quotes, "quotes_flat")
        self.orders_flat = FakeAggCollection(orders, "orders_flat")
        self.leads = FakeSignalDatabase(leads=leads).leads


class RunCursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *_args):
        return self

    async def to_list(self, _limit):
        return list(self.rows)


class RunCollection:
    """Enough of a Mongo collection to drive the run guard."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.released = []

    def find(self, query=None, *_args, **_kwargs):
        rows = self.rows
        if query:
            status = query.get("status")
            rows = [row for row in rows if status is None or row.get("status") == status]
            cutoff = (query.get("started_epoch") or {}).get("$gt")
            if cutoff is not None:
                rows = [row for row in rows if float(row.get("started_epoch") or 0) > cutoff]
        return RunCursor(rows)

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def delete_many(self, query):
        keep = [row for row in self.rows if row.get("run_id") != query.get("run_id")]
        self.released.append(len(self.rows) - len(keep))
        self.rows = keep

    async def update_many(self, query, update):
        changed = 0
        for row in self.rows:
            if row.get("status") == query.get("status"):
                row.update(update["$set"])
                changed += 1
        return type("Result", (), {"modified_count": changed})()


class RunDatabase:
    """agent_runs plus the empty collections the other endpoints read."""

    def __init__(self, rows=None):
        self.agent_runs = RunCollection(rows)
        self.leads = RunCollection([])
        self.quotes_flat = RunCollection([])
        self.orders_flat = RunCollection([])
        self.payments_flat = RunCollection([])


class FakeRedis:
    """Only what the run guard uses: SET NX EX, GET, DELETE."""

    def __init__(self):
        self.store = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


class BrokenRedis:
    async def set(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")

    async def get(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")

    async def delete(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")


# ------------------------------------------------------------------ API keys


class IndexCollection:
    def __init__(self, name):
        self.name = name
        self.created: list[tuple[str, tuple]] = []

    async def create_index(self, keys, name=None):
        self.created.append((name, tuple(tuple(key) for key in keys)))
        return name


class IndexDatabase:
    def __init__(self, names):
        self._collections = {name: IndexCollection(name) for name in names}

    def __getitem__(self, name):
        return self._collections[name]


def test_every_declared_index_is_created():
    from app.services.indexes import INDEXES, ensure_indexes

    database = IndexDatabase(list(INDEXES))
    created = asyncio.run(ensure_indexes(database))

    assert set(created) == set(INDEXES)
    for name, indexes in INDEXES.items():
        collection = database[name]
        assert created[name] == len(indexes)
        for suffix, keys in indexes:
            assert (f"{name}_{suffix}", tuple(keys)) in collection.created, name
    # the run guard, the briefing query and the 24h hash cache are all indexed
    agent_runs = {entry[0] for entry in database["agent_runs"].created}
    assert {"agent_runs_by_finished", "agent_runs_by_started", "agent_runs_open_run"} <= agent_runs
    assert "agent_runs_collections_hash" in agent_runs
    assert "orders_flat_by_quote" in {entry[0] for entry in database["orders_flat"].created}


def test_indexes_are_idempotent_and_survive_a_refusal():
    """A collection that rejects an index must not stop the rest of them."""
    from app.services.indexes import INDEXES, ensure_indexes

    database = IndexDatabase(list(INDEXES))

    async def refuse(keys, name=None):
        raise RuntimeError("index in progress")

    database["agent_runs"].create_index = refuse
    created = asyncio.run(ensure_indexes(database))
    assert created["agent_runs"] == 0
    assert created["quotes_flat"] == len(INDEXES["quotes_flat"])

    asyncio.run(ensure_indexes(database))  # second startup changes nothing


def test_startup_creates_the_indexes(monkeypatch):
    import app.main as main_module

    prepare_test_client(monkeypatch)
    seen: dict[str, int] = {}

    async def record(database):
        seen["called"] = 1
        return {}

    monkeypatch.setattr(main_module, "ensure_indexes", record)
    with TestClient(app):
        assert seen.get("called") == 1


def test_health_needs_no_key_and_api_routes_do(monkeypatch):
    prepare_test_client(monkeypatch)
    monkeypatch.setattr(routes, "get_db", lambda: RunDatabase())
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "API_KEYS", "secret-one, secret-two")

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/leads/stuck").status_code == 401
        assert client.get("/api/leads/stuck", headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.get(
            "/api/leads/stuck", headers={"X-API-Key": "secret-two"}
        ).status_code == 200


def test_auth_disabled_is_the_local_development_default(monkeypatch):
    prepare_test_client(monkeypatch)
    monkeypatch.setattr(routes, "get_db", lambda: RunDatabase())
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)

    with TestClient(app) as client:
        assert client.get("/api/leads/stuck").status_code == 200


def test_key_comparison_is_constant_time_and_accepts_any_configured_key():
    allowed = [b"alpha", b"beta"]
    assert key_matches("beta", allowed)
    assert not key_matches("bet", allowed)
    assert not key_matches("gamma", allowed)
    assert not key_matches("alpha", [])


def test_cors_origins_come_from_the_env(monkeypatch):
    monkeypatch.setattr(settings, "CORS_ORIGINS", "http://localhost:3000, https://bi.example.com")
    assert cors_origins() == ["http://localhost:3000", "https://bi.example.com"]


# ------------------------------------------------------------------ run lock


@pytest.mark.asyncio
async def test_two_calls_start_only_one_agent_run(monkeypatch):
    database = RunDatabase()
    redis = FakeRedis()
    started: list[str] = []

    monkeypatch.setattr(routes, "get_db", lambda: database)
    monkeypatch.setattr(run_guard, "get_redis", lambda: redis)

    async def fake_run(run_id: str) -> None:
        started.append(run_id)

    monkeypatch.setattr(routes, "_background_run", fake_run)

    first = await routes.run_agents()
    await asyncio.sleep(0)
    second = await routes.run_agents()
    await asyncio.sleep(0)

    assert first["status"] == "started"
    assert second.status_code == 409
    assert json.loads(second.body)["run_id"] == first["run_id"]
    assert started == [first["run_id"]]
    # one 'running' document for the one live run
    assert [row["status"] for row in database.agent_runs.rows] == ["running"]

    await run_guard.release_run(first["run_id"], database)
    monkeypatch.setattr(run_guard, "get_redis", lambda: redis)
    third = await routes.run_agents()
    assert third["status"] == "started"
    assert third["run_id"] != first["run_id"]


@pytest.mark.asyncio
async def test_run_guard_falls_back_to_the_running_document(monkeypatch):
    database = RunDatabase()
    monkeypatch.setattr(run_guard, "get_redis", lambda: BrokenRedis())

    assert await run_guard.acquire_run("run-1", database) is None
    assert await run_guard.acquire_run("run-2", database) == "run-1"

    await run_guard.release_run("run-1", database)
    assert database.agent_runs.rows == []
    # a dead lock cannot block a new run
    assert await run_guard.acquire_run("run-3", database) is None


@pytest.mark.asyncio
async def test_stale_running_documents_are_cleaned_at_startup():
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    database = RunDatabase(
        [{"run_id": "dead-run", "status": "running", "started_epoch": old.timestamp()}]
    )
    monkeypatch_result = await run_guard.mark_stale_runs_abandoned(database)
    assert monkeypatch_result == 1
    row = database.agent_runs.rows[0]
    assert row["status"] == "abandoned"
    assert row["errors"]


@pytest.mark.asyncio
async def test_run_guard_hands_the_lock_to_the_holder_when_redis_is_busy(monkeypatch):
    """A held Redis lock answers with the holder's run_id, not with a new one."""
    redis = FakeRedis()
    await redis.set(run_guard.LOCK_KEY, "holder-run", nx=True)
    monkeypatch.setattr(run_guard, "get_redis", lambda: redis)

    database = RunDatabase()
    assert await run_guard.acquire_run("mine", database) == "holder-run"
    # nothing was inserted: this caller never started a run
    assert database.agent_runs.rows == []


def prepare_test_client(monkeypatch):
    monkeypatch.setattr(app_db, "get_client", lambda: object())
    # the lifespan cleans up stale runs through the real database accessor
    monkeypatch.setattr(app_db, "get_db", lambda: RunDatabase())

    async def close_db():
        return None

    monkeypatch.setattr(app_db, "close", close_db)


def no_signals_cache(monkeypatch):
    """Force /api/signals to build from Mongo instead of serving the Redis copy."""

    async def miss(_key):
        return None

    async def store(_payload, _key=""):
        return None

    monkeypatch.setattr(signals, "cache_load", miss)
    monkeypatch.setattr(signals, "cache_store", store)


def test_signals_can_omit_stalled_items_without_changing_summary(monkeypatch):
    database = AggregationDatabase(
        orders=[
            {
                "id": 9,
                "invoice_date": "2026-10-01T00:00:00+00:00",
                "status_label": "Approved",
                "total_amount": 375.25,
                "payment_status": 0,
            }
        ]
    )
    prepare_test_client(monkeypatch)
    no_signals_cache(monkeypatch)
    monkeypatch.setattr(routes, "get_db", lambda: database)

    with TestClient(app) as client:
        full = client.get("/api/signals?stalled_days=2").json()
        compact = client.get("/api/signals?stalled_days=2&include_stalled=false").json()

    assert len(full["stalled_orders"]["items"]) == 1
    assert compact["stalled_orders"]["items"] == []
    assert compact["stalled_orders"]["reliable"] is False
    assert compact["summary"]["stalled_orders"] == full["summary"]["stalled_orders"]
    assert compact["summary"]["stalled_orders"] == {
        "count": 1,
        "total_amount": 375.25,
        "reliable": False,
    }


def test_stuck_leads_endpoint_parses_in_python_and_hides_contacts(monkeypatch):
    now = datetime.now(timezone.utc)
    database = FakeSignalDatabase(
        leads=[
            {
                "zohoLeadId": "L-1",
                "firstName": "Keiwan",
                "lastName": "Bentley",
                "company": "Zaskii Boyz",
                "status": "New",
                "source": "Ecommerce",
                "email": "keiwan@example.com",
                "phone": "5551234567",
                "description": '{"product": "mylar bag", "quantity": "500", "artwork": []}',
                "createdAt": (now - timedelta(days=5)).isoformat(),
                "lastActivityAt": (now - timedelta(days=2)).isoformat(),
                "leadDesk": {"stage": "open", "flagReason": ""},
            },
            {
                "zohoLeadId": "L-2",
                "status": "Junk Lead",
                "description": "quantity: 10",
                "lastActivityAt": (now - timedelta(days=2)).isoformat(),
            },
        ]
    )
    prepare_test_client(monkeypatch)
    monkeypatch.setattr(routes, "get_db", lambda: database)

    with TestClient(app) as client:
        response = client.get("/api/leads/stuck")

    assert response.status_code == 200
    payload = response.json()
    assert payload["days"] == settings.LEAD_ACTIVE_DAYS
    assert payload["total"] == 1  # the Junk Lead is filtered by Python
    lead = payload["items"][0]
    assert lead["lead_id"] == "L-1"
    assert lead["contact_name"] == "Keiwan Bentley"
    assert lead["company"] == "Zaskii Boyz"
    assert lead["product"] == "mylar bag"
    assert lead["quantity"] == "500"
    assert lead["artwork_present"] is False
    assert "artwork" in lead["missing"]
    assert lead["days_since_activity"] == 2
    # contact details must never leave this endpoint
    assert "email" not in lead and "phone" not in lead


def test_metrics_and_signals_open_quote_totals_match_for_same_fixture(monkeypatch):
    now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is not None else now.replace(tzinfo=None)

    quotes = [
        {
            "id": 1,
            "customer": {"id": 101},
            "status": "Estimate",
            "amount": 1245.67,
            "created_at": (now - timedelta(days=6)).isoformat(),
        },
        {
            "id": 2,
            "customer": {"id": 202},
            "status": "Quote Created",
            "amount": 312.34,
            "created_at": (now - timedelta(days=12)).isoformat(),
        },
    ]
    orders = []
    database = AggregationDatabase(quotes, orders)

    prepare_test_client(monkeypatch)
    no_signals_cache(monkeypatch)
    monkeypatch.setattr(routes, "datetime", FrozenDateTime)
    monkeypatch.setattr(routes, "get_db", lambda: database)
    monkeypatch.setattr(settings, "STALE_QUOTE_DAYS", 3)
    monkeypatch.setattr(settings, "STALE_QUOTE_MAX_AGE_DAYS", 30)
    snapshot = metrics.build_snapshot_from_docs(quotes, orders, [], now)

    async def current_snapshot():
        return snapshot

    monkeypatch.setattr(live_api, "_current_snapshot", current_snapshot)

    with TestClient(app) as client:
        metrics_response = client.get("/api/metrics")
        signals_response = client.get("/api/signals?include_stalled=false")

    assert metrics_response.status_code == 200
    assert signals_response.status_code == 200
    metric = metrics_response.json()
    signals = signals_response.json()
    assert metric["open_quotes_count"] == signals["summary"]["stale_quotes"]["count"]
    assert metric["open_quotes_value"] == signals["summary"]["stale_quotes"]["total_amount"]
