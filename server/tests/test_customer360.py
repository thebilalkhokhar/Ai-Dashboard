"""Customer 360 integration: client auth, the detail summarizer, the
fetch-through cache and the two chat tools.

The fixture payload mirrors the live detail response captured in the
Oct 23 study (same field names and shapes; values are invented).
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.agents import chat_tools
from app.agents.chat_tools import (
    CHAT_TOOLS,
    CHAT_TOOL_MAP,
    get_customer360,
    search_customer360,
)
from app.services import customer360 as c360_service
from app.services.customer360_client import (
    Customer360Client,
    Customer360Error,
    _jwt_expiry,
)


# --------------------------------------------------------------- fixtures

def detail_payload(**over):
    payload = {
        "success": True,
        "baseType": "Lead",
        "baseEntity": {
            "_id": "665f2ab1c360",
            "zohoLeadId": "6374650000059763002",
            "firstName": "Ismat",
            "lastName": "Khalil",
            "email": "ismat@example.com",
            "phone": "+1 832 555 0100",
            "company": "Rinada Lifestyle",
            "status": "Contacted",
            "source": "Chat",
            "createdAt": "2026-06-08T22:29:03.179Z",
            "lastActivityAt": "2026-06-23T22:01:32.507Z",
            "aiSummary": "Stored CRM summary text.",
        },
        "leads": [{"_id": "665f2ab1c360"}],
        "contacts": [{"_id": "c1"}, {"_id": "c2"}],
        "customers": [],
        "timeline": [
            {"type": "Email", "title": "Pricing question", "timestamp": "2026-07-01T10:00:00Z"},
            {"type": "Call", "title": "Outgoing call", "timestamp": "2026-07-03T09:30:00Z"},
            {"type": "Call", "title": "Missed call", "timestamp": "2026-07-02T12:00:00Z"},
        ],
        "projects": [{"projectName": "Deal A", "amount": 500, "stage": "Qualification"}],
        "invoices": [
            {"grandTotal": 700, "status": "Approved"},
            {"grandTotal": 300, "status": "Cancelled"},
        ],
        "estimates": [
            {"grandTotal": 100.50, "stage": "Draft"},
            {"grandTotal": 200.25, "stage": "Confirmed"},
        ],
        "payments": [{"amount": 250.5}, {"amount": 249.5}],
        "whatsapp": [{"_id": "w1"}, {"_id": "w2"}],
    }
    payload.update(over)
    return payload


class CacheCollection:
    """Minimal _id-keyed stand-in for the customer360_cache collection."""

    def __init__(self):
        self.docs: dict[str, dict] = {}

    async def find_one(self, query):
        return self.docs.get(query.get("_id"))

    async def update_one(self, query, update, upsert=False):
        key = query.get("_id")
        doc = dict(self.docs.get(key) or {"_id": key})
        doc.update(update.get("$set") or {})
        self.docs[key] = doc


class RecordsCursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, limit):
        return self._rows[:limit]


class RecordsCollection:
    """Stand-in for the harvested customer360_records index."""

    def __init__(self, docs=None):
        self.docs: dict[str, dict] = dict(docs or {})

    def find(self, query, projection=None):
        return RecordsCursor(list(self.docs.values()))


class CacheDb:
    def __init__(self, records=None):
        self.cache = CacheCollection()
        self.records = RecordsCollection(records)

    def __getitem__(self, name):
        return {
            "customer360_cache": self.cache,
            "customer360_records": self.records,
        }[name]


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


def make_jwt(exp: int) -> str:
    def enc(obj) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{enc({'alg': 'HS256'})}.{enc({'exp': exp})}.sig"


# ------------------------------------------------------------- summarizer

def test_summarize_detail_computes_everything():
    summary = c360_service.summarize_detail(detail_payload())
    entity = summary["entity"]
    assert entity["name"] == "Ismat Khalil"
    assert entity["record_type"] == "Lead"
    assert entity["zoho_id"] == "6374650000059763002"
    assert entity["status"] == "Contacted"
    assert entity["crm_ai_summary"] == "Stored CRM summary text."
    assert summary["leads_count"] == 1
    assert summary["contacts_count"] == 2
    assert summary["customers_count"] == 0
    assert summary["timeline_count"] == 3
    assert summary["whatsapp_count"] == 2
    assert summary["estimates_total_amount"] == pytest.approx(300.75)
    assert summary["estimates_by_stage"] == {"Draft": 1, "Confirmed": 1}
    assert summary["projects_total_amount"] == 500
    assert summary["invoices_total_amount"] == 1000
    assert summary["invoices_total_field"] == "grandTotal"
    assert summary["invoices_by_status"] == {"Approved": 1, "Cancelled": 1}
    assert summary["payments_total_amount"] == pytest.approx(500.0)
    assert summary["timeline_by_type"] == {"Email": 1, "Call": 2}
    assert summary["last_interaction"]["title"] == "Outgoing call"
    assert [row["title"] for row in summary["recent_interactions"]] == [
        "Outgoing call", "Missed call", "Pricing question",
    ]


def test_summarize_detail_no_total_without_a_common_money_field():
    payload = detail_payload(
        invoices=[{"grandTotal": 5}, {"balance": 3}],
        payments=[{"amount": "not-a-number"}, {"amount": 10}],
    )
    summary = c360_service.summarize_detail(payload)
    assert "invoices_total_amount" not in summary
    assert "payments_total_amount" not in summary
    assert summary["invoices_count"] == 2


def test_summarize_detail_money_strings_and_customer_name():
    payload = detail_payload(
        baseType="Customer",
        baseEntity={"_id": "x1", "customerName": "Acme Boxes", "zohoAccountId": 42},
        payments=[{"amountReceived": "1,200.50"}, {"amountReceived": 300}],
    )
    summary = c360_service.summarize_detail(payload)
    assert summary["entity"]["name"] == "Acme Boxes"
    assert summary["entity"]["zoho_id"] == "42"
    assert summary["payments_total_amount"] == pytest.approx(1500.50)
    assert summary["payments_total_field"] == "amountReceived"


# ----------------------------------------------------------------- client

@pytest.fixture
def fake_redis(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr("app.services.customer360_client.get_redis", lambda: redis)
    return redis


def make_client(handler) -> Customer360Client:
    client = Customer360Client(
        base_url="https://crm.example/api", email="a@b.c", password="pw", timeout=5
    )
    client._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5
    )
    return client


@pytest.mark.asyncio
async def test_client_logs_in_once_and_reuses_token(fake_redis):
    calls = {"login": 0, "search": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            calls["login"] += 1
            return httpx.Response(200, json={
                "success": True,
                "token": make_jwt(int(time.time()) + 7 * 24 * 3600),
                "user": {"role": "Owner"},
            })
        assert request.headers.get("authorization", "").startswith("Bearer ")
        calls["search"] += 1
        return httpx.Response(200, json={"success": True, "results": [{"id": "1"}]})

    client = make_client(handler)
    assert await client.search("x") == [{"id": "1"}]
    assert await client.search("y") == [{"id": "1"}]
    assert calls == {"login": 1, "search": 2}
    await client.close()


@pytest.mark.asyncio
async def test_client_relogs_in_once_after_401(fake_redis):
    calls = {"login": 0, "search": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            calls["login"] += 1
            return httpx.Response(200, json={
                "success": True,
                "token": make_jwt(int(time.time()) + 7 * 24 * 3600),
                "user": {},
            })
        calls["search"] += 1
        if calls["search"] == 1:
            return httpx.Response(401, json={"error": "INVALID_TOKEN"})
        return httpx.Response(200, json={"success": True, "results": []})

    client = make_client(handler)
    assert await client.search("x") == []
    assert calls == {"login": 2, "search": 2}
    await client.close()


@pytest.mark.asyncio
async def test_client_without_credentials_fails_plainly(fake_redis):
    client = Customer360Client(base_url="https://crm.example/api", email="", password="")
    with pytest.raises(Customer360Error, match="C360_EMAIL"):
        await client.search("x")
    await client.close()


def test_jwt_expiry_reads_exp():
    token = make_jwt(1893456000)
    assert _jwt_expiry(token) == datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert _jwt_expiry("not-a-jwt") is None


def test_client_has_no_write_methods():
    # trigger-call / save-draft / analysis must not be reachable at all.
    for name in ("trigger_call", "save_draft", "analysis", "trigger", "call_customer"):
        assert not hasattr(Customer360Client, name)


# ------------------------------------------------------------------ tools

class StubClient:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.detail_calls = 0
        self.search_calls = 0

    async def search(self, query):
        self.search_calls += 1
        if self.error:
            raise self.error
        return [{"id": "665f2ab1c360", "type": "Lead", "name": "Ismat Khalil",
                 "email": "ismat@example.com", "phone": "1", "company": "Rinada",
                 "status": "Contacted"}]

    async def detail(self, record_id, record_type):
        self.detail_calls += 1
        if self.error:
            raise self.error
        return self.payload


@pytest.fixture
def stub(monkeypatch):
    client = StubClient(payload=detail_payload())
    monkeypatch.setattr(chat_tools, "_c360_client", lambda: client)
    return client


@pytest.mark.asyncio
async def test_search_tool(stub):
    result = await search_customer360("ismat", database=CacheDb())
    assert result["available"] is True
    assert result["total_count"] == 1
    assert result["results"][0]["record_id"] == "665f2ab1c360"
    assert result["results"][0]["record_type"] == "Lead"


@pytest.mark.asyncio
async def test_search_tool_reports_unavailability(monkeypatch):
    client = StubClient(error=Customer360Error("network error: ReadTimeout"))
    monkeypatch.setattr(chat_tools, "_c360_client", lambda: client)
    result = await search_customer360("ismat", database=CacheDb())
    assert result["available"] is False
    assert result["results"] == []


@pytest.mark.asyncio
async def test_search_tool_merges_local_index_without_duplicates(stub):
    db = CacheDb(records={
        # The same person the live search returns - must appear once.
        "665f2ab1c360": {
            "_id": "665f2ab1c360", "record_type": "Lead", "name": "Ismat Khalil",
            "email": "ismat@example.com", "phone": "1", "company": "Rinada",
            "status": "Contacted",
        },
        # A record only the harvested index knows about.
        "L9": {
            "_id": "L9", "record_type": "Lead", "name": "Ismat Other",
            "email": "other@example.com", "phone": "9", "company": "Elsewhere",
            "status": "New",
        },
    })
    result = await search_customer360("ismat", database=db)
    assert result["available"] is True
    assert result["live_matches"] == 1
    assert result["local_index_matches"] == 2
    assert result["total_count"] == 2
    assert [row["record_id"] for row in result["results"]] == ["665f2ab1c360", "L9"]


@pytest.mark.asyncio
async def test_search_tool_live_down_serves_index_with_a_note(monkeypatch):
    down = StubClient(error=Customer360Error("network error: ReadTimeout"))
    monkeypatch.setattr(chat_tools, "_c360_client", lambda: down)
    db = CacheDb(records={
        "L9": {
            "_id": "L9", "record_type": "Lead", "name": "Ismat Other",
            "email": "other@example.com", "phone": "9", "company": "Elsewhere",
            "status": "New",
        },
    })
    result = await search_customer360("ismat", database=db)
    assert result["available"] is True
    assert result["total_count"] == 1
    assert "local index only" in result["note"]


@pytest.mark.asyncio
async def test_detail_tool_caches_then_serves_from_cache(stub):
    db = CacheDb()
    first = await get_customer360("665f2ab1c360", "Lead", database=db)
    assert first["available"] is True and first["from_cache"] is False
    assert first["invoices_total_amount"] == 1000
    assert stub.detail_calls == 1
    second = await get_customer360("665f2ab1c360", "Lead", database=db)
    assert second["from_cache"] is True
    assert stub.detail_calls == 1  # the slow backend was not touched again


@pytest.mark.asyncio
async def test_detail_tool_serves_stale_copy_on_failure(monkeypatch):
    db = CacheDb()
    good = StubClient(payload=detail_payload())
    monkeypatch.setattr(chat_tools, "_c360_client", lambda: good)
    await get_customer360("665f2ab1c360", "Lead", database=db)
    # Age the cached copy past the freshness window.
    doc = db.cache.docs["Lead:665f2ab1c360"]
    doc["fetched_at"] = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    down = StubClient(error=Customer360Error("network error: ReadTimeout"))
    monkeypatch.setattr(chat_tools, "_c360_client", lambda: down)
    result = await get_customer360("665f2ab1c360", "Lead", database=db)
    assert result["available"] is True
    assert result["from_cache"] is True
    assert "last saved copy" in result["note"]


@pytest.mark.asyncio
async def test_detail_tool_no_copy_and_backend_down(monkeypatch):
    down = StubClient(error=Customer360Error("login -> HTTP 500"))
    monkeypatch.setattr(chat_tools, "_c360_client", lambda: down)
    result = await get_customer360("nope", "Lead", database=CacheDb())
    assert result["available"] is False
    assert "no saved copy" in result["note"]


def test_tools_are_registered():
    assert "search_customer360" in CHAT_TOOL_MAP
    assert "get_customer360" in CHAT_TOOL_MAP
    assert len(CHAT_TOOLS) == len(CHAT_TOOL_MAP)
