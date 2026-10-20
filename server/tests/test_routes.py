"""Route registration and customer-context endpoint tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import db as app_db
from app.config import settings
from app.api import live as live_api
from app.api import routes
from app.main import app
from app.services import metrics


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


def test_all_five_expected_api_routes_are_registered():
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
    def __init__(self, quotes=(), orders=()):
        self.quotes_flat = FakeCollection(quotes)
        self.orders_flat = FakeCollection(orders)


def prepare_test_client(monkeypatch):
    monkeypatch.setattr(app_db, "get_client", lambda: object())

    async def close_db():
        return None

    monkeypatch.setattr(app_db, "close", close_db)


def test_signals_can_omit_stalled_items_without_changing_summary(monkeypatch):
    database = FakeSignalDatabase(
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
    database = FakeSignalDatabase(quotes, orders)

    prepare_test_client(monkeypatch)
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
