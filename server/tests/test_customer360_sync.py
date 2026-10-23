"""Full-module CRM sync: paginated list endpoints upserted by CRM id.

The fixture mirrors the live list shapes verified Oct 24, 2026 (same
field names; values invented). The properties that matter: a re-run
never duplicates, the people index gets the search shape, and the run
summary proves coverage against the API's own reported total.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import customer360_sync as sync


class FakeCollection:
    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.inserted: list[dict] = []

    async def update_one(self, query, update, upsert=False):
        key = query["_id"]
        if key in self.docs:
            self.docs[key].update(update.get("$set", {}))
            return SimpleNamespace(upserted_id=None, modified_count=1)
        if not upsert:
            return SimpleNamespace(upserted_id=None, modified_count=0)
        doc = {"_id": key}
        doc.update(update.get("$setOnInsert", {}))
        doc.update(update.get("$set", {}))
        self.docs[key] = doc
        return SimpleNamespace(upserted_id=key, modified_count=0)

    async def insert_one(self, doc):
        self.inserted.append(doc)


class FakeDb:
    def __init__(self):
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


class StubListClient:
    PAGES = {
        ("leads", 1): [
            {"_id": "L1", "firstName": "Ismat", "lastName": "Khalil",
             "email": "ismat@example.com", "phone": "1", "company": "Rinada",
             "status": "Contacted", "zohoLeadId": "6374650000001"},
            {"_id": "L2", "firstName": "Frank", "lastName": "Jr",
             "email": "frank@example.com", "phone": "2", "company": "Caps",
             "status": "New", "zohoLeadId": "6374650000002"},
        ],
        ("leads", 2): [
            {"_id": "L3", "firstName": "Ana", "lastName": "Lee",
             "email": "ana@example.com", "phone": "3", "company": "Lee Co",
             "status": "New", "zohoLeadId": "6374650000003"},
        ],
        ("payments", 1): [
            {"_id": "P1", "paymentName": "30677", "erpId": "9958",
             "customerName": "MNJ Enterprise LLC", "amountReceived": 4627,
             "amountReceiveDate": "2026-10-23T00:00:00.000Z"},
        ],
        ("customers", 1): [
            {"_id": "C1", "customerName": "Candles Studio", "phone": "5026448927",
             "zohoAccountId": "6374650000031"},
        ],
    }
    TOTALS = {"leads": 3, "payments": 1, "customers": 1}
    PAGE_COUNTS = {"leads": 2, "payments": 1, "customers": 1}

    async def list_page(self, module, page=1, limit=100, search=""):
        rows = self.PAGES.get((module, page), [])
        return {
            "success": True,
            module: rows,
            "pagination": {
                "total": self.TOTALS[module],
                "page": page,
                "limit": limit,
                "pages": self.PAGE_COUNTS[module],
            },
        }


@pytest.mark.asyncio
async def test_sync_module_upserts_every_row_and_reports_coverage():
    db = FakeDb()
    summary = await sync.sync_module(db, StubListClient(), "leads")
    assert summary["rows_seen"] == 3
    assert summary["new_records"] == 3
    assert summary["total_reported"] == 3
    assert summary["complete"] is True
    leads = db["customer360_leads"].docs
    assert set(leads) == {"L1", "L2", "L3"}
    assert leads["L1"]["zohoLeadId"] == "6374650000001"
    assert "synced_at" in leads["L1"] and "first_synced_at" in leads["L1"]


@pytest.mark.asyncio
async def test_sync_feeds_the_people_index_in_search_shape():
    db = FakeDb()
    await sync.sync_module(db, StubListClient(), "leads")
    await sync.sync_module(db, StubListClient(), "customers")
    index = db["customer360_records"].docs
    assert index["L1"]["record_type"] == "Lead"
    assert index["L1"]["name"] == "Ismat Khalil"
    assert index["L1"]["company"] == "Rinada"
    assert index["L1"]["status"] == "Contacted"
    assert index["C1"]["record_type"] == "Customer"
    assert index["C1"]["name"] == "Candles Studio"


@pytest.mark.asyncio
async def test_resync_never_duplicates_and_payments_skip_the_index():
    db = FakeDb()
    client = StubListClient()
    await sync.sync_module(db, client, "leads")
    second = await sync.sync_module(db, client, "leads")
    assert second["new_records"] == 0
    assert len(db["customer360_leads"].docs) == 3
    assert len(db["customer360_records"].docs) == 3

    await sync.sync_module(db, client, "payments")
    assert set(db["customer360_payments"].docs) == {"P1"}
    assert "P1" not in db["customer360_records"].docs


@pytest.mark.asyncio
async def test_sync_all_logs_the_run():
    db = FakeDb()
    summary = await sync.sync_all(db, StubListClient(), ("leads", "payments"))
    assert summary["complete"] is True
    assert summary["modules"]["leads"]["rows_seen"] == 3
    assert db["customer360_sync_runs"].inserted


@pytest.mark.asyncio
async def test_sync_all_records_the_run_mode():
    db = FakeDb()
    summary = await sync.sync_all(db, StubListClient(), ("leads",), mode="auto")
    assert summary["mode"] == "auto"
    assert db["customer360_sync_runs"].inserted[0]["mode"] == "auto"
    # and the default stays a manual run
    db2 = FakeDb()
    summary2 = await sync.sync_all(db2, StubListClient(), ("leads",))
    assert summary2["mode"] == "manual"
