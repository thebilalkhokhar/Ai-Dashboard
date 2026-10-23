"""The Customer 360 harvest: breadth-first enumeration through the capped
search API, duplicate-free by upsert on the CRM's own record id."""

from __future__ import annotations

import pytest

from app.services import customer360_index as index


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, limit):
        return self._rows[:limit]


class FakeIndexCollection:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    def find(self, query, projection=None):
        return FakeCursor(list(self.docs.values()))

    async def update_one(self, query, update, upsert=False):
        key = query["_id"]
        inserting = key not in self.docs
        doc = dict(self.docs.get(key) or {"_id": key})
        doc.update(update.get("$set") or {})
        if inserting:
            doc.update(update.get("$setOnInsert") or {})
        self.docs[key] = doc


class FakeRunsCollection:
    def __init__(self):
        self.inserted: list[dict] = []

    async def insert_one(self, doc):
        self.inserted.append(doc)


class FakeDb:
    def __init__(self):
        self.records = FakeIndexCollection()
        self.runs = FakeRunsCollection()

    def __getitem__(self, name):
        return {
            "customer360_records": self.records,
            "customer360_harvest_runs": self.runs,
        }[name]


def lead(i, **over):
    row = {
        "id": f"L{i}", "type": "Lead", "name": f"Lead {i}",
        "email": f"lead{i}@x.io", "phone": "1", "company": "Co", "status": "New",
    }
    row.update(over)
    return row


class ScriptedClient:
    """'a' is capped for Leads; its children surface two new leads and one
    repeat; everything else is empty."""

    def __init__(self):
        self.calls: list[str] = []

    async def search(self, query):
        self.calls.append(query)
        if query == "a":
            return [lead(i) for i in range(10)] + [
                {"id": "C0", "type": "Contact", "name": "Con Zero"},
                {"id": "C1", "type": "Contact", "name": "Con One"},
            ]
        if len(query) == 2 and query.startswith("a"):
            return [lead(0), lead(10), lead(11)]
        return []


@pytest.mark.asyncio
async def test_harvest_expands_capped_queries_and_dedupes():
    db = FakeDb()
    client = ScriptedClient()
    summary = await index.run_harvest(db, client)
    # 36 one-char prefixes + the 36 children of capped "a".
    assert summary["queries_run"] == 72
    assert summary["capped_queries"] == ["a"]
    # 10 leads + 2 contacts + 2 genuinely new leads from the children;
    # lead L0 arrived 37 times and is stored once.
    assert summary["unique_records"] == 14
    assert summary["new_records"] == 14
    assert len(db.records.docs) == 14
    assert summary["complete_coverage"] is False
    assert len(db.runs.inserted) == 1


@pytest.mark.asyncio
async def test_second_harvest_adds_nothing():
    db = FakeDb()
    client = ScriptedClient()
    await index.run_harvest(db, client)
    summary = await index.run_harvest(db, client)
    assert summary["new_records"] == 0
    assert summary["unique_records"] == 14
    assert len(db.records.docs) == 14


@pytest.mark.asyncio
async def test_harvest_respects_max_queries():
    db = FakeDb()
    client = ScriptedClient()
    summary = await index.run_harvest(db, client, max_queries=5)
    assert summary["queries_run"] == 5
    assert len(client.calls) == 5


@pytest.mark.asyncio
async def test_search_index_matches_all_words_across_fields():
    db = FakeDb()
    db.records.docs["L1"] = {
        "_id": "L1", "record_type": "Lead", "name": "Ismat Khalil",
        "email": "ismat@example.com", "phone": "832", "company": "Rinada Lifestyle",
        "status": "Contacted",
    }
    db.records.docs["L2"] = {
        "_id": "L2", "record_type": "Lead", "name": "Ismat Other",
        "email": "other@example.com", "phone": "9", "company": "Elsewhere",
        "status": "New",
    }
    hits = await index.search_index(db, "ismat rinada")
    assert [hit["record_id"] for hit in hits] == ["L1"]
    hits = await index.search_index(db, "ismat")
    assert {hit["record_id"] for hit in hits} == {"L1", "L2"}


@pytest.mark.asyncio
async def test_index_stats():
    db = FakeDb()
    db.records.docs["L1"] = {"_id": "L1", "record_type": "Lead"}
    db.records.docs["C1"] = {"_id": "C1", "record_type": "Contact"}
    stats = await index.index_stats(db)
    assert stats == {"total": 2, "by_type": {"Lead": 1, "Contact": 1}}
