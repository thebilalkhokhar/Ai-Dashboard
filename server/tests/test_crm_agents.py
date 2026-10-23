"""The two CRM specialists (crm_engagement, crm_deal_watch): candidate
selection, payload shape, the graph loaders and a full pipeline run.

Fixtures mirror the live shapes verified Oct 24, 2026: synced module
rows (customer360_sync) and detail payloads (customer360 detail).
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from app.agents import graph
from app.agents.crm_agents import crm_deal_payload, crm_engagement_payload
from app.agents.graph import run_pipeline
from app.schemas.agents import CrmDealWatchOutput, CrmEngagementOutput, MasterOutput
from app.services import customer360 as c360_service
from app.services import processors

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
EPOCH = NOW.timestamp()


# ------------------------------------------------------------------ fakes

class FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, _n):
        return list(self._docs)


class FakeCollection:
    def __init__(self, docs=()):
        self._docs = list(docs)
        self.saved: list[dict] = []
        self._by_id = {d["_id"]: d for d in self._docs if isinstance(d, dict) and "_id" in d}

    def find(self, query=None, projection=None):
        docs = self._docs + self.saved
        if query and "finished_epoch" in query:
            gt = query["finished_epoch"].get("$gt")
            docs = [d for d in docs if d.get("finished_epoch", 0) > gt]
        return FakeCursor(docs)

    async def find_one(self, query):
        return self._by_id.get(query.get("_id"))

    async def insert_one(self, doc):
        self.saved.append(copy.deepcopy(doc))


class FakeDatabase:
    def __init__(self, **collections):
        self.quotes_flat = FakeCollection()
        self.orders_flat = FakeCollection()
        self.payments_flat = FakeCollection()
        self.leads = FakeCollection()
        self.agent_runs = FakeCollection()
        for name, docs in collections.items():
            setattr(self, name, FakeCollection(docs))


class FakeCallerForTest:
    def __init__(self, schema, answer):
        self._schema = schema
        self._answer = answer

    async def ainvoke(self, messages):
        import json as _json

        content = messages[-1].get("content", "")
        start, end = content.find("{"), content.rfind("}")
        payload = _json.loads(content[start : end + 1]) if start != -1 else {}
        return self._schema.model_validate(self._answer(payload)), {
            "input_tokens": 10, "output_tokens": 10, "total_tokens": 20,
        }


def project_row(**over):
    row = {
        "_id": "P1",
        "projectName": "Blister Cards Run",
        "accountName": "MNJ Enterprise LLC",
        "contactName": "Eric Dean",
        "stage": "In Production",
        "amount": 8775,
        "createdAt": (NOW - timedelta(days=40)).isoformat(),
        "updatedAt": (NOW - timedelta(days=30)).isoformat(),
    }
    row.update(over)
    return row


# ------------------------------------------------------- processors level

def test_deal_candidates_selects_open_and_idle_only():
    projects = [
        project_row(),  # open stage, idle 30d -> in
        project_row(_id="P2", stage="Closed Won", amount=99999),  # closed -> out
        project_row(_id="P3", updatedAt=(NOW - timedelta(days=3)).isoformat()),  # fresh -> out
    ]
    estimates = [
        {
            "_id": "E1", "estimateNumber": "EST-9", "accountName": "Polkadot",
            "stage": "Sent", "grandTotal": 14150,
            "createdAt": (NOW - timedelta(days=50)).isoformat(),
            "updatedAt": (NOW - timedelta(days=20)).isoformat(),
        }
    ]
    out = processors.crm_deal_candidates(projects, estimates, NOW)
    assert [c["record_id"] for c in out] == ["E1", "P1"]  # amount desc
    assert out[1]["deal_kind"] == "project"
    assert out[1]["days_idle"] == 30
    assert out[0]["amount"] == 14150


def test_deal_payload_formats_money_and_hides_contacts():
    candidate = processors.crm_deal_candidates([project_row()], [], NOW)[0]
    payload = crm_deal_payload(candidate)
    assert payload["deal"]["amount"] == "$8,775"
    assert payload["deal"]["days_idle"] == 30
    import json

    blob = json.dumps(payload).lower()
    assert "email" not in blob and "phone" not in blob


# ------------------------------------------------------------ graph loaders

@pytest.mark.asyncio
async def test_deal_items_loader_builds_items():
    db = FakeDatabase(customer360_projects=[project_row()], customer360_estimates=[])
    items = await graph._crm_deal_items(db, NOW)
    assert len(items) == 1
    assert items[0]["kind"] == "crm_deal_watch"
    assert items[0]["item"]["record_id"] == "P1"
    assert items[0]["input_hash"]


def detail_payload(last_direction: str) -> dict:
    return {
        "success": True,
        "baseType": "Lead",
        "baseEntity": {
            "_id": "L1", "name": "Ismat Khalil", "firstName": "Ismat",
            "lastName": "Khalil", "company": "Rinada", "status": "Contacted",
            "email": "ismat@example.com", "phone": "555",
        },
        "timeline": [
            {
                "type": "WhatsApp", "title": "WhatsApp message",
                "timestamp": (NOW - timedelta(days=5)).isoformat(),
                "direction": last_direction,
            },
            {
                "type": "Email", "title": "Quote sent",
                "timestamp": (NOW - timedelta(days=7)).isoformat(),
                "direction": "Outbound" if last_direction == "Inbound" else "Inbound",
            },
        ],
        "leads": [], "contacts": [], "customers": [], "projects": [],
        "invoices": [], "estimates": [], "payments": [], "whatsapp": [],
    }


@pytest.mark.asyncio
async def test_engagement_items_only_when_the_customer_wrote_last():
    lead_row = {
        "_id": "L1", "status": "Contacted",
        "lastActivityAt": (NOW - timedelta(days=5)).isoformat(),
    }
    cache_doc = {
        "_id": "Lead:L1",
        "payload": detail_payload("Inbound"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    db = FakeDatabase(
        customer360_leads=[lead_row], customer360_cache=[cache_doc]
    )
    # the loader reads the cache through c360_service's own _collection,
    # which resolves the attribute name customer360_cache
    items = await graph._crm_engagement_items(db, NOW)
    assert len(items) == 1
    assert items[0]["item"]["waiting_days"] == 5
    assert items[0]["item"]["contact_name"] == "Ismat Khalil"

    cache_doc["payload"] = detail_payload("Outbound")
    db2 = FakeDatabase(customer360_leads=[lead_row], customer360_cache=[cache_doc])
    assert await graph._crm_engagement_items(db2, NOW) == []


def test_engagement_payload_shape():
    summary = c360_service.summarize_detail(detail_payload("Inbound"))
    payload = crm_engagement_payload(summary, "Lead", 5)
    assert payload["waiting"]["waiting_days"] == 5
    assert payload["record"]["name"] == "Ismat Khalil"
    import json

    blob = json.dumps(payload).lower()
    assert "ismat@example.com" not in blob and "555" not in blob


# ------------------------------------------------------------- pipeline run

def crm_factory():
    def factory(kind: str):
        if kind == "crm_deal_watch":
            def answer(payload: dict) -> dict:
                deal = payload["deal"]
                return {
                    "stall_reason": "The deal has not moved for 30 days.",
                    "action_plan": "A rep should check the production status.",
                    "message_draft": "Following up on your project - happy to share a status update whenever useful.",
                    "confidence": "medium",
                    "evidence": "used the stage and idle days from the input",
                }

            return FakeCallerForTest(CrmDealWatchOutput, answer)
        if kind == "master":
            def answer(payload: dict) -> dict:
                return {
                    "bullets": ["No ERP items need attention.", "CRM deals are tracked separately.", "Nothing else is pending."],
                    "priorities": [],
                    "conflicts_resolved": [],
                }

            return FakeCallerForTest(MasterOutput, answer)
        raise AssertionError(f"unexpected caller kind {kind}")

    return factory


@pytest.mark.asyncio
async def test_pipeline_runs_deal_watch_and_saves_drafts():
    db = FakeDatabase(customer360_projects=[project_row()], customer360_estimates=[])
    result = await run_pipeline(
        run_id="run-crm-1", database=db, caller_factory=crm_factory(), now_epoch=EPOCH
    )
    deals = result["crm_deal_watch"]
    assert len(deals) == 1
    item = deals[0]
    # identity is Python's, never the model's
    assert item["record_id"] == "P1"
    assert item["account_name"] == "MNJ Enterprise LLC"
    assert item["amount"] == 8775
    assert item["days_idle"] == 30
    assert item["message_draft"].startswith("Following up on your project")
    assert result["crm_engagement"] == []
    saved = db.agent_runs.saved[0]
    assert saved["counts"]["crm_deal_watch_total"] == 1
    assert saved["crm_deal_watch"][0]["record_id"] == "P1"
