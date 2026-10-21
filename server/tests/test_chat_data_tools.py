"""Part A tests: the chat can read the records it used to claim it could not see."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agents import chat_tools
from app.agents.chat import MAX_TOOL_CALLS, execute_chat
from app.agents.chat_tools import get_dashboard_metrics, get_payments
from app.services import metrics
from test_chat import FakeCollection, FakeDatabase, FakeModel

NOW = datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 21)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """The tools ask for 'now'; freeze it so date filters are deterministic."""
    for module in (chat_tools, metrics):
        monkeypatch.setattr(module, "datetime", FrozenDateTime)


def payment(index=1, **over):
    row = {
        "id": 7250 + index,
        "amount": 500.0 + index,
        "payment_date": "2026-10-21",
        "received_at": "2026-10-21T00:00:00-05:00",
        "payment_ref": f"725{index}-1969-12",
        "total_payment_used": 500.0 + index,
        "customer_id": 6079,
        "customer": {"id": 6079, "company_name": "Picazo Cleaning Services"},
        "users": {"id": 177, "name": "Bella Landeros"},
        "payment_details": [
            {
                "invoice_id": 33563,
                "amount": "500.00",
                "invoice": {"id": 33563, "inv_ref": "33563-2026-10", "date": "2026-10-21 09:56:19"},
            }
        ],
    }
    row.update(over)
    return row


def order(**over):
    row = {
        "id": 33563,
        "inv_ref": "33563-2026-10",
        "status": 2,
        "status_label": "Approved",
        "payment_status": 0,
        "payment_status_label": "Not Paid",
        "total_amount": 4000.0,
        "amount_paid": 0.0,
        "invoice_date": (NOW - timedelta(days=45)).astimezone(timezone(timedelta(hours=-5))).isoformat(),
        "due_date": None,
        "customer": {"id": 6079, "company_name": "Picazo Cleaning Services"},
        "sale_rep": {"id": 177, "name": "Bella Landeros"},
    }
    row.update(over)
    return row


def quote(**over):
    row = {
        "id": 20791,
        "ref": "20021-2026-10",
        "status": "Estimate",
        "status_order": 5,
        "amount": 56600.0,
        "created_at": (NOW - timedelta(days=5)).astimezone(timezone(timedelta(hours=-5))).isoformat(),
        "customer": {"id": 3312, "company_name": "Polkadot"},
        "sale_rep": {"id": 120, "name": "Erick Dean"},
    }
    row.update(over)
    return row


def lead(**over):
    row = {
        "zohoLeadId": "L-1",
        "firstName": "Keiwan",
        "lastName": "Bentley",
        "company": "Zaskii Boyz",
        "status": "New",
        "source": "Ecommerce",
        "description": '{"product": "mylar bag", "quantity": "500", "artwork": []}',
        "createdAt": (NOW - timedelta(days=5)).isoformat(),
        "lastActivityAt": (NOW - timedelta(days=2)).isoformat(),
        "leadDesk": {"stage": "open", "flagReason": ""},
    }
    row.update(over)
    return row


class Collection(FakeCollection):
    """The chat tools also count documents; the shared fake only reads them."""

    async def count_documents(self, query=None):
        return len(self.rows)


def business_db(**over):
    kwargs = {
        "payments_flat": Collection([payment()]),
        "orders_flat": Collection([order()]),
        "quotes_flat": Collection([quote(), quote(id=20799, ref="19999-2026-10", amount=1200.0,
                                                   customer={"id": 6079, "company_name": "Picazo Cleaning Services"})]),
        "leads": Collection([lead()]),
        "agent_runs": Collection([]),
        "users_flat": Collection([]),
        "chat_threads": Collection([]),
        "chat_logs": Collection([]),
    }
    kwargs.update(over)
    return FakeDatabase(**kwargs)


def tool_call(name: str, args: dict | None = None, index: int = 1):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args or {}, "id": f"call-{index}", "type": "tool_call"}],
        usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
    )


def answer(text: str):
    return AIMessage(
        content=text,
        usage_metadata={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
    )


def tools_used(model: FakeModel) -> list[str]:
    """Distinct tool results across the whole run (history repeats each turn)."""
    seen: dict[str, str] = {}
    for messages in model.seen_messages:
        for message in messages:
            if isinstance(message, ToolMessage):
                seen[message.tool_call_id] = message.name
    return list(seen.values())


# ------------------------------------------------------------------ A1/A6


@pytest.mark.asyncio
async def test_who_paid_today_calls_get_payments_and_names_the_payer():
    model = FakeModel(
        [
            tool_call("get_payments", {"date_from": TODAY.isoformat(), "date_to": TODAY.isoformat()}),
            answer("Picazo Cleaning Services paid $501 on 2026-10-21 against 33563-2026-10."),
        ]
    )

    result = await execute_chat("who paid today?", [], model=model, database=business_db())

    assert tools_used(model) == ["get_payments"]
    assert "Picazo Cleaning Services" in result["text"]
    assert "check the ERP" not in result["text"].lower()


@pytest.mark.asyncio
async def test_get_payments_pages_and_names_the_unstored_filter():
    rows = [payment(index=index) for index in range(1, 61)]

    result = await get_payments(limit=999, method="card", database=business_db(payments_flat=Collection(rows)))

    assert result["total_count"] == len(rows)
    # a greedy limit is clamped to the safe maximum, not rejected
    assert chat_tools.RowsArgs(limit=999).limit == chat_tools.MAX_TOOL_ROWS
    assert result["returned"] <= chat_tools.MAX_TOOL_ROWS
    assert len(result["items"]) == result["returned"] < result["total_count"]
    assert result["truncated"] is True
    assert "payment method" in result["method_filter_not_applied"]


@pytest.mark.asyncio
async def test_overdue_invoices_for_a_customer_are_computed_in_python():
    invoices = await chat_tools.get_invoices(
        customer="Picazo", overdue_only=True, database=business_db()
    )

    assert invoices["total_count"] == 1
    row = invoices["items"][0]
    assert row["invoice_number"] == "33563-2026-10"
    assert row["balance"] == 4000.0
    assert row["days_overdue"] == 15
    assert invoices["filters_applied"]["customer"] == "Picazo Cleaning Services"


@pytest.mark.asyncio
async def test_customer_360_profile_follows_payment_to_invoice_to_customer():
    profile = await chat_tools.get_customer_profile("Picazo", database=business_db())

    assert profile["company_name"] == "Picazo Cleaning Services"
    assert profile["recent_payments"]["items"][0]["reference"] == "7251-1969-12"
    assert profile["recent_payments"]["items"][0]["invoices"][0]["invoice_number"] == "33563-2026-10"
    assert profile["unpaid_invoices"]["items"][0]["customer_name"] == "Picazo Cleaning Services"
    assert profile["quotes"]["total_amount"] == 1200.0  # the Picazo quote, not the Polkadot one


@pytest.mark.asyncio
async def test_missing_data_is_named_once_instead_of_a_refusal():
    described = await chat_tools.describe_data(database=business_db())

    assert described["payments"]["count"] == 1
    assert "payment method" in described["not_available"]["payments"]
    assert described["business_timezone"]

    missing = await chat_tools.get_payments(customer="Nobody Exists", database=business_db())
    assert missing["total_count"] == 0
    assert "No customer named" in missing["note"]


@pytest.mark.asyncio
async def test_tool_budget_stops_at_eight_calls_per_question():
    model = FakeModel([tool_call("get_payments", index=index) for index in range(1, 13)] + [answer("done.")])

    result = await execute_chat("keep reading", [], model=model, database=business_db())

    assert len(tools_used(model)) == MAX_TOOL_CALLS
    assert result["text"]  # the loop ended instead of running out of calls


@pytest.mark.asyncio
async def test_followup_question_sees_the_previous_answer():
    model = FakeModel([answer("Zaskii Boyz.")])
    history = [{"question": "which lead is stuck?", "answer": "Zaskii Boyz sent 500 bags."}]

    await execute_chat("I need that name", history, model=model, database=business_db())

    texts = [str(message.content) for message in model.seen_messages[0]]
    assert any("Zaskii Boyz sent 500 bags." in text for text in texts)


# ----------------------------------------------------------------------- A2


@pytest.mark.asyncio
async def test_chat_headline_equals_the_dashboard_card(monkeypatch):
    """Both read the same snapshot, so $989,922.11 vs $990,307.11 cannot happen."""
    database = business_db()
    expected = await metrics.build_snapshot(database=database, now=NOW)

    class NoSnapshotRedis:
        async def get(self, key):
            raise ConnectionError("redis is down")

    monkeypatch.setattr("app.services.redis_client.get_redis", lambda: NoSnapshotRedis())
    direct = await get_dashboard_metrics(database=database, now=NOW)
    assert direct["open_quotes_value"] == expected["open_quotes_value_display"]
    assert direct["unpaid_total"] == expected["unpaid_total_display"]

    class HeldRedis:
        async def get(self, key):
            return json.dumps(
                {"event": "snapshot", "data": {**expected, "open_quotes_value_display": "$1,234"}}
            )

    monkeypatch.setattr("app.services.redis_client.get_redis", lambda: HeldRedis())
    from_cache = await get_dashboard_metrics(database=database)
    assert from_cache["open_quotes_value"] == "$1,234"  # whatever the tile shows
