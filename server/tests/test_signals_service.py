"""GET /api/signals must stop copying the ERP into memory per request.

Tests for item 10: the report is served from the Redis copy the scheduler
refreshes, and on a cache miss it is built from Mongo with projections,
$sort + $limit - so the page size, not the collection size, decides what Python
holds. The summaries still cover every row.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import db as app_db
from app.api import routes
from app.config import settings
from app.main import app
from app.services import processors, signals
from mongo_fake import FakeAggCollection


NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def quote(index: int, *, days_ago: int, amount: float = 1_000.0, status: str = "Estimate", customer: int = None):
    return {
        "id": 10_000 + index,
        "ref": f"Q-{index}",
        "status": status,
        "status_order": 5,
        "amount": amount,
        "created_at": (NOW - timedelta(days=days_ago)).astimezone(processors.ERP_TZ).isoformat(),
        "customer": {"id": customer if customer is not None else index, "company_name": f"C{index}"},
        "sale_rep": {"id": 1, "name": "Rep"},
    }


def order(index: int, *, days_ago: int, total: float = 500.0, paid: float = 0.0, payment_status=0,
          status_label: str = "Approved", quote_id=None):
    row = {
        "id": 50_000 + index,
        "inv_ref": f"O-{index}",
        "status": 2,
        "status_label": status_label,
        "payment_status": payment_status,
        "payment_status_label": {0: "Not Paid", 1: "Partially Paid", 2: "Paid"}[payment_status],
        "total_amount": total,
        "amount_paid": paid,
        "invoice_date": (NOW - timedelta(days=days_ago)).astimezone(processors.ERP_TZ).isoformat(),
        "due_date": None,
        "customer": {"id": index, "company_name": f"C{index}"},
        "sale_rep": {"id": 1, "name": "Rep"},
    }
    if quote_id is not None:
        row["quote_id"] = quote_id
    return row


class FakeDatabase:
    def __init__(self, quotes, orders):
        self.quotes_flat = FakeAggCollection(quotes, "quotes_flat")
        self.orders_flat = FakeAggCollection(orders, "orders_flat")


class ExplodingDatabase:
    """Any Mongo work at all fails the test (the cached path must do none)."""

    def __getattr__(self, name):
        raise AssertionError(f"/api/signals should not have queried {name!r}")

    def __getitem__(self, name):
        raise AssertionError(f"/api/signals should not have queried {name!r}")


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        return True


@pytest.fixture
def no_redis(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(signals, "get_redis", lambda: redis)
    return redis


# ---------------------------------------------------------------------------
# equivalence with the old in-Python rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payload_matches_the_python_rules_exactly():
    quotes = [
        quote(1, days_ago=6, amount=2_000.0),
        quote(2, days_ago=10, amount=500.0, status="Rejected"),   # closed
        quote(3, days_ago=2, amount=900.0),                        # too fresh
        quote(4, days_ago=200, amount=700.0),                      # too old
        quote(5, days_ago=8, amount=400.0),                        # converted below
    ]
    orders = [
        order(11, days_ago=40, total=300.0, paid=0.0),              # unpaid, overdue
        order(12, days_ago=5, total=200.0, paid=200.0, payment_status=2),  # paid
        order(13, days_ago=9, total=150.0, paid=50.0, payment_status=1),   # partial
        order(14, days_ago=400, total=1_000.0, paid=0.0, status_label="Delivered"),  # final
        order(15, days_ago=9, total=400.0, quote_id=10_005),        # converts quote 5
    ]
    database = FakeDatabase(quotes, orders)

    payload = await signals.build_signals_payload(database, NOW)

    expected_stale = processors.stale_quotes(
        quotes, NOW, order_quote_ids={row["quote_id"] for row in orders if "quote_id" in row}
    )
    assert [row["id"] for row in payload["stale_quotes"]] == [row["id"] for row in expected_stale]
    assert payload["summary"]["stale_quotes"]["count"] == len(expected_stale)

    expected_unpaid = processors.unpaid_invoices(orders, NOW)
    assert payload["summary"]["unpaid_invoices"]["count"] == len(expected_unpaid)
    assert payload["summary"]["unpaid_invoices"]["total_amount"] == round(
        sum(row["balance"] for row in expected_unpaid), 2
    )
    assert payload["summary"]["unpaid_invoices"]["partially_paid_count"] == 1

    expected_stalled = processors.stalled_orders(orders, NOW)
    assert payload["summary"]["stalled_orders"]["count"] == len(expected_stalled)

    assert payload["summary"]["quotes_scanned"] == len(quotes)
    assert payload["summary"]["orders_scanned"] == len(orders)
    assert payload["summary"]["data_from"] == processors.iso_or_none(
        min(row["created_at"] for row in quotes)
    )


@pytest.mark.asyncio
async def test_converted_quotes_are_excluded_by_the_mongo_side():
    quotes = [quote(1, days_ago=6), quote(2, days_ago=7)]
    orders = [order(1, days_ago=3, quote_id=quotes[0]["id"])]
    payload = await signals.build_signals_payload(FakeDatabase(quotes, orders), NOW)
    assert [row["id"] for row in payload["stale_quotes"]] == [quotes[1]["id"]]


@pytest.mark.asyncio
async def test_two_open_quotes_minutes_apart_keep_the_newest_one():
    """age_days is rounded, so ties need a deterministic winner: the newer quote.

    The old endpoint read the collection in Mongo's natural order, which picked
    whichever row happened to come first - now candidates arrive newest-first.
    """
    older = {
        "id": 1, "ref": "Q-old", "status": "Estimate", "amount": 500.0,
        "created_at": "2026-10-10T09:00:00-05:00",
        "customer": {"id": 77, "company_name": "Tied Customer"}, "sale_rep": None,
    }
    newer = {
        "id": 2, "ref": "Q-new", "status": "Estimate", "amount": 400.0,
        "created_at": "2026-10-10T09:02:00-05:00",
        "customer": {"id": 77, "company_name": "Tied Customer"}, "sale_rep": None,
    }
    payload = await signals.build_signals_payload(FakeDatabase([older, newer], []), NOW)
    assert [row["id"] for row in payload["stale_quotes"]] == [2]
    assert payload["summary"]["stale_quotes"]["count"] == 1


# ---------------------------------------------------------------------------
# bounded work
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_page_pipelines_ask_mongo_for_a_limit(monkeypatch):
    """With 20k orders the page never comes back bigger than the cap allows."""
    monkeypatch.setattr(settings, "SIGNALS_ITEM_LIMIT", 20)
    quotes = [quote(index, days_ago=5 + (index % 20)) for index in range(2_000)]
    orders = [
        order(index, days_ago=index % 400 + 1, total=100.0 + index) for index in range(20_000)
    ]
    database = FakeDatabase(quotes, orders)

    payload = await signals.build_signals_payload(database, NOW)

    assert len(payload["unpaid_invoices"]) <= 20
    assert len(payload["stalled_orders"]["items"]) <= 20
    # summaries cover every row, the page does not
    assert payload["summary"]["unpaid_invoices"]["count"] > 20
    for collection in (database.quotes_flat, database.orders_flat):
        for pipeline, returned in collection.results:
            limited = [stage for stage in pipeline if "$limit" in stage]
            if limited:
                # Mongo cut the page down: Python never saw more than the cap
                assert returned <= limited[0]["$limit"] <= 20 * 3
    # the old endpoint called find().to_list(); a projection never ships _id
    for pipeline in database.orders_flat.pipelines:
        for stage in pipeline:
            if "$project" in stage:
                assert stage["$project"].get("_id") == 0


@pytest.mark.asyncio
async def test_summaries_stay_exact_when_the_page_is_capped(monkeypatch):
    monkeypatch.setattr(settings, "SIGNALS_ITEM_LIMIT", 5)
    orders = [order(index, days_ago=index + 1, total=1_000.0) for index in range(60)]
    payload = await signals.build_signals_payload(FakeDatabase([], orders), NOW)

    expected = processors.unpaid_invoices(orders, NOW)
    assert len(payload["unpaid_invoices"]) == 5
    assert payload["summary"]["unpaid_invoices"]["count"] == len(expected)
    assert payload["summary"]["unpaid_invoices"]["total_amount"] == round(
        sum(row["balance"] for row in expected), 2
    )
    assert payload["summary"]["item_limit"] == 5


# ---------------------------------------------------------------------------
# the cache is what makes a request cheap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_report_is_served_from_redis_without_touching_mongo(monkeypatch, no_redis):
    payload = {
        "generated_at": NOW.isoformat(),
        "stale_quotes": [],
        "unpaid_invoices": [],
        "stalled_orders": {"reliable": False, "items": []},
        "summary": {"quotes_scanned": 123_456, "orders_scanned": 65_432, "item_limit": 200},
    }
    no_redis.store[signals.cache_key(None, None, None, True)] = json.dumps(payload)

    async def explode(*_args, **_kwargs):
        raise AssertionError("the cached report must not query Mongo")

    monkeypatch.setattr(routes, "get_db", lambda: ExplodingDatabase())
    monkeypatch.setattr(signals, "build_signals_payload", explode)

    async def build():
        return await routes.get_signals()

    report = await build()
    assert report.summary.quotes_scanned == 123_456


def test_endpoint_returns_the_cached_report_and_caches_a_miss(monkeypatch, no_redis):
    quotes = [quote(1, days_ago=6, amount=1_500.0)]
    orders = [order(1, days_ago=40, total=300.0)]
    database = FakeDatabase(quotes, orders)

    monkeypatch.setattr(app_db, "get_client", lambda: object())
    monkeypatch.setattr(app_db, "get_db", lambda: database)
    monkeypatch.setattr(routes, "get_db", lambda: database)

    async def close_db():
        return None

    monkeypatch.setattr(app_db, "close", close_db)

    key = signals.cache_key(None, None, None, True)
    with TestClient(app) as client:
        first = client.get("/api/signals")
        assert first.status_code == 200
        assert key in no_redis.store  # the miss was stored for the next request

        database.quotes_flat.rows = []          # Mongo changes...
        database.orders_flat.rows = []
        second = client.get("/api/signals")      # ...and the cached report answers

    assert second.status_code == 200
    assert second.json()["summary"]["quotes_scanned"] == 1
    assert first.json()["summary"]["quotes_scanned"] == 1


def test_custom_window_uses_its_own_cache_key():
    default = signals.params_hash(None, None, None, True)
    custom = signals.params_hash(7, None, None, True)
    assert default == "default"
    assert custom != default
    assert signals.cache_key(7, None, None, True) != signals.cache_key(None, None, None, True)


@pytest.mark.asyncio
async def test_cache_miss_when_redis_is_down_still_answers(monkeypatch):
    class NoRedis:
        async def get(self, key):
            raise ConnectionError("redis is down")

        async def set(self, key, value, ex=None):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(signals, "get_redis", lambda: NoRedis())
    database = FakeDatabase([quote(1, days_ago=6)], [order(1, days_ago=40, total=300.0)])
    payload = await signals.build_signals_payload(database, NOW)
    assert payload["summary"]["stale_quotes"]["count"] == 1
