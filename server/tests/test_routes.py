"""Route registration and customer-context endpoint tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db as app_db
from app.api import routes
from app.main import app


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
