"""Agent-pipeline tests with a FAKE LLM (no network, no keys needed).

Covers the code parts the spec asked for:
  * grouping outputs by customer
  * dollar-amount validation
  * 24h caching
  * full graph run + needs_review flow with fixture data

Run:  .venv/bin/python -m pytest tests -q
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.agents.graph import (
    RunState,
    cache_lookup,
    invented_amounts,
    master_invented_amounts,
    run_pipeline,
)
from app.agents.master import group_by_customer
from app.schemas.agents import CollectionsOutput, MasterOutput, NegotiationOutput
from app.services.processors import to_number  # parses "$2,000" style strings

NOW = datetime.now(timezone.utc)
EPOCH = NOW.timestamp()


class FakeCursor:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    async def to_list(self, _: int) -> list[dict]:
        return list(self._docs)


class FakeCollection:
    def __init__(self, docs: list[dict]):
        self._docs = docs
        self.saved: list[dict] = []

    def find(self, query: dict | None = None):
        docs = self._docs + self.saved
        if query and "finished_epoch" in query:
            gt = query["finished_epoch"].get("$gt")
            docs = [d for d in docs if d.get("finished_epoch", 0) > gt]
        return FakeCursor(docs)

    async def insert_one(self, doc: dict) -> None:
        self.saved.append(copy.deepcopy(doc))


class FakeDatabase:
    def __init__(self, quotes, orders, payments, runs=None):
        self.quotes_flat = FakeCollection(quotes)
        self.orders_flat = FakeCollection(orders)
        self.payments_flat = FakeCollection(payments)
        self.agent_runs = FakeCollection(runs or [])


def there_and_last_month(**over):
    """Fixture helpers reused from test_processors shape (ERP-like rows)."""
    row = {
        "id": 1,
        "ref": "Q-1",
        "status": "Quote Created",
        "created_at": (NOW - timedelta(days=6)).isoformat(),
        "amount": 1000.0,
        "customer": {"id": 10, "company_name": "ACME"},
        "sale_rep": {"id": 5, "name": "Sue"},
    }
    row.update(over)
    return row


def order_row(**over):
    row = {
        "id": 100,
        "inv_ref": "O-100",
        "status": 2,
        "status_label": "Approved",
        "payment_status": 0,
        "payment_status_label": "Not Paid",
        "invoice_date": (NOW - timedelta(days=20)).isoformat(),
        "total_amount": 500.0,
        "amount_paid": 0.0,
        "customer": {"id": 10, "company_name": "ACME"},
        "sale_rep": {"id": 5, "name": "Sue"},
    }
    row.update(over)
    return row


def good_factory():
    """Fake LLM that copies every number it mentions from its own input."""
    def factory(k: str):
        if k == "negotiation":
            def answer(payload: dict) -> dict:
                q = payload["quote"]
                history = payload["customer_history"]
                is_new = history.get("past_orders", {}).get("count", 0) == 0
                return {
                    "customer_id": history["customer_id"],
                    "customer_name": history["company_name"],
                    "quote_id": q["id"],
                    # the model copies the amount it reads ('$2,000.00') and the
                    # schema stores it as a number, exactly like a real answer
                    "quote_amount": to_number(q["amount"]),
                    "likely_reason": "new_customer_no_history" if is_new else "repeat_customer_slow",
                    "tactic": "follow_up",
                    "message_draft": (
                        f"Hi, checking in on quote ${to_number(q['amount']):,.2f} - happy to walk through it."
                        if to_number(q["amount"]) else "Checking in on the quote."
                    ),
                    "confidence": "medium",
                    "evidence": "used quote amount and the order count from history only",
                }
            return FakeCallerForTest(NegotiationOutput, answer)
        elif k == "collections":
            def answer(payload: dict) -> dict:
                inv = payload["invoice"]
                history = payload["customer_history"]
                return {
                    "customer_id": history["customer_id"],
                    "customer_name": history["company_name"],
                    "invoice_id": inv["id"],
                    "balance": to_number(inv["balance"]),
                    "approach": "firm_nudge" if inv.get("overdue") else "soft_reminder",
                    "reason": f"balance ${inv['balance']} on file, overdue={inv.get('overdue')}",
                    "message_draft": f"Friendly reminder: balance ${to_number(inv['balance']):,.2f} is open.",
                    "confidence": "medium",
                    "evidence": "used balance, overdue flag and payment history counts",
                }
            return FakeCallerForTest(CollectionsOutput, answer)
        else:
            def answer(payload: dict) -> dict:
                customers = payload.get("customers") or []
                names = [c.get("customer_name") or "" for c in customers]
                return {
                    "bullets": [
                        f"{len(customers)} customers need a human touch this week.",
                        "One repeat customer quote is still open.",
                        "One unpaid invoice needs a firm nudge.",
                    ],
                    "priorities": [
                        {
                            "customer_name": names[0] if names else "ACME",
                            "why": "stale quote and unpaid invoice at once",
                            "suggested_action": "rep calls, mentions both",
                        }
                    ],
                    "conflicts_resolved": ["1 customer appears in both drafts"],
                }
            return FakeCallerForTest(MasterOutput, answer)
    return factory


def bad_draft_factory():
    """Valid copies everywhere EXCEPT the negotiation draft invents $9999.00."""
    base = good_factory()

    def factory(k: str):
        caller = base(k)
        if k == "negotiation":
            original_answer = caller._answer

            def answer(payload: dict) -> dict:
                data = original_answer(payload)
                data["message_draft"] = "Special price: only $9999.00 today!"
                return data

            return FakeCallerForTest(NegotiationOutput, answer)
        return caller

    return factory


def contact_bad_factory():
    """Valid copies everywhere EXCEPT the negotiation draft invents a phone."""
    base = good_factory()

    def factory(k: str):
        caller = base(k)
        if k == "negotiation":
            original_answer = caller._answer

            def answer(payload: dict) -> dict:
                data = original_answer(payload)
                data["message_draft"] += " Call 818-225-9999 to discuss."
                return data

            return FakeCallerForTest(NegotiationOutput, answer)
        return caller

    return factory


def first_name_factory():
    """Negotiation draft greets a person the ERP does not know."""
    base = good_factory()

    def factory(k: str):
        caller = base(k)
        if k == "negotiation":
            original_answer = caller._answer

            def answer(payload: dict) -> dict:
                data = original_answer(payload)
                data["message_draft"] = "Hi Arthur, " + data["message_draft"].split("quote", 1)[-1].strip()
                return data

            return FakeCallerForTest(NegotiationOutput, answer)
        return caller

    return factory


class FakeCallerForTest:
    """Minimal caller: mirrors app.agents.llm.FakeCaller behavior in tests."""

    def __init__(self, schema, answer):
        self._schema = schema
        self._answer = answer

    async def ainvoke(self, messages):
        import json as _json

        payload = messages[-1]
        content = payload.get("content", "") if isinstance(payload, dict) else str(payload)
        start, end = content.find("{"), content.rfind("}")
        payload_dict = _json.loads(content[start : end + 1]) if start != -1 else {}
        return self._schema.model_validate(self._answer(payload_dict)), {
            "input_tokens": len(content) // 4,
            "output_tokens": 40,
            "total_tokens": len(content) // 4 + 40,
        }


def fresh_db() -> FakeDatabase:
    return FakeDatabase(
        quotes=[
            there_and_last_month(id=1, amount=1000.0, created_at=(NOW - timedelta(days=6)).isoformat()),
            there_and_last_month(id=2, amount=2000.0, created_at=(NOW - timedelta(days=5)).isoformat()),  # newer -> kept
        ],
        orders=[
            order_row(id=100, payment_status=2, payment_status_label="Paid", amount_paid=500.0),  # paid -> excluded
            order_row(id=101, payment_status=0, total_amount=750.0, invoice_date=(NOW - timedelta(days=35)).isoformat()),
        ],
        payments=[
            {
                "id": 7100,
                "amount": 1350,
                "total_payment_used": 1350,
                "received_at": (NOW - timedelta(days=5)).isoformat(),
                "customer_id": 10,
                "payment_details": [
                    {"invoice": {"id": 33366, "date": (NOW - timedelta(days=25)).strftime("%Y-%m-%d %H:%M:%S"), "status": 2, "inv_ref": "33366-2026-09"}}
                ],
            }
        ],
    )





# ------------------------------------------------------------- pure helpers

def test_group_by_customer_puts_conflicts_next_to_each_other():
    grouped = group_by_customer(
        [
            {"customer_id": 10, "customer_name": "ACME", "kind": "negotiation"},
            {"customer_id": 10, "customer_name": "ACME", "kind": "collections"},
            {"customer_id": 99, "customer_name": "OTHER", "kind": "negotiation"},
        ]
    )
    assert set(grouped.keys()) == {10, 99}
    assert len(grouped[10]["items"]) == 2  # conflict visible: two outputs
    assert len(grouped[99]["items"]) == 1


def test_invented_amounts_finds_copy_and_rejects_invention():
    payload = {"invoice": {"balance": 250.0, "total_amount": 300.0}, "history": {"orders": {"balance": 50.5}}}
    assert invented_amounts("Your balance $250.00 is open.", payload) == []
    assert invented_amounts("Your balance $250.00 and only $900.00 today!", payload) == [900.0]


def test_cache_lookup_ignores_needs_review_and_old_runs():
    item = {"input_hash": "h1"}
    recent = [
        {"finished_epoch": EPOCH - 3600, "negotiation": [{"input_hash": "h1", "source": "agent", "flags": [], "message_draft": "ok draft"}]},
        {"finished_epoch": EPOCH - 48 * 3600, "negotiation": [{"input_hash": "h2", "source": "agent", "flags": [], "message_draft": "ok"}]},
        {"finished_epoch": EPOCH - 3600, "negotiation": [{"input_hash": "h3", "source": "agent", "flags": ["needs_review"], "message_draft": None}]},
        {"finished_epoch": EPOCH - 1800, "negotiation": [{"input_hash": "h1", "source": "agent", "flags": [], "message_draft": "older-schema copy with no draft"}]},
    ]
    recent[-1]["negotiation"][0].pop("message_draft")  # legacy copy without a draft
    result = cache_lookup(recent, [dict(item), {"input_hash": "h2"}, {"input_hash": "h3"}], EPOCH - 24 * 3600)
    # h2 too old, h3 needs_review, and the second h1 copy (no draft) must NOT
    # shadow the good one - the cache keeps the newest draft-bearing copy
    assert set(result.keys()) == {"h1"}
    assert result["h1"]["message_draft"] == "ok draft"


def test_master_brief_cannot_invent_totals():
    """The Master must only repeat numbers Python computed."""
    grouped = {
        10: {
            "customer_name": "ACME",
            "items": [
                {
                    "customer_id": 10,
                    "balance": 250.0,
                    "approach": "firm_nudge",
                    "reason": "$250.0 open",
                }
            ],
        }
    }
    good = {"bullets": ["ACME owes $250.0"], "priorities": [], "conflicts_resolved": []}
    assert master_invented_amounts(good, {"grouped": grouped}) == []
    bad = {"bullets": ["ACME owes $542,154.0 in total"], "priorities": [], "conflicts_resolved": []}
    assert master_invented_amounts(bad, {"grouped": grouped}) == [542154.0]


# ------------------------------------------------------------ full pipeline


@pytest.mark.asyncio
async def test_pipeline_happy_path_saves_run():
    database = fresh_db()
    result = await run_pipeline(
        run_id="test-run-1", database=database, caller_factory=good_factory(), now_epoch=EPOCH
    )
    doc = database.agent_runs.saved[0]
    assert doc["run_id"] == "test-run-1"
    assert doc["counts"]["negotiation_total"] == 1
    assert doc["counts"]["collections_total"] == 1
    assert doc["counts"]["needs_review"] == 0
    assert len(doc["master"]["bullets"]) == 3
    # the negotiation draft copied the quote amount ($2,000.00)
    negotiation = doc["negotiation"][0]
    assert negotiation["quote_amount"] == 2000.0
    assert "flags" not in negotiation or negotiation["flags"] != ["needs_review"]
    assert doc["usage"]["total_tokens"] > 0


@pytest.mark.asyncio
async def test_invented_amount_becomes_needs_review():
    database = fresh_db()
    result = await run_pipeline(
        run_id="test-run-2", database=database, caller_factory=bad_draft_factory(), now_epoch=EPOCH
    )
    doc = database.agent_runs.saved[0]
    negotiation = doc["negotiation"][0]
    assert "needs_review" in negotiation["flags"]
    assert negotiation["message_draft"] is None
    assert doc["counts"]["needs_review"] >= 1
    # the invalid item must NOT reach the master brief
    master_customers = doc["master"].get("bullets", [])
    assert len(master_customers) == 3  # master still runs on remaining items


@pytest.mark.asyncio
async def test_invented_phone_becomes_needs_review():
    database = fresh_db()
    await run_pipeline(
        run_id="test-run-phone", database=database, caller_factory=contact_bad_factory(), now_epoch=EPOCH
    )
    item = database.agent_runs.saved[0]["negotiation"][0]
    assert "needs_review" in item["flags"]
    assert item["message_draft"] is None
    assert "818-225-9999" in item["rejected_reason"]


@pytest.mark.asyncio
async def test_unknown_greeting_name_becomes_company_greeting():
    database = fresh_db()
    await run_pipeline(
        run_id="test-run-greeting", database=database, caller_factory=first_name_factory(), now_epoch=EPOCH
    )
    item = database.agent_runs.saved[0]["negotiation"][0]
    assert item["flags"] != ["needs_review"]
    assert item["message_draft"].startswith("Hello ACME team,")


@pytest.mark.asyncio
async def test_second_run_reuses_cache():
    database = fresh_db()
    first = await run_pipeline(
        run_id="run-a", database=database, caller_factory=good_factory(), now_epoch=EPOCH
    )
    second = await run_pipeline(
        run_id="run-b", database=database, caller_factory=good_factory(), now_epoch=EPOCH + 60
    )
    assert second["counts"]["cached"] == first["counts"]["negotiation_total"] + first["counts"]["collections_total"]
    assert second["usage"]["total_tokens"] < first["usage"]["total_tokens"]
