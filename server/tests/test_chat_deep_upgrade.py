"""Deep-upgrade contracts: pending read-only actions, the Garcoa "yes pull"
reproduction, customer-360 completeness, duplicate-call prevention, and the
deterministic comparison / anomaly / priorities tools.

Everything runs offline against the shared fakes: no live ERP, no live LLM.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agents import chat_tools
from app.agents.chat import _answer_consistency_errors, _confirmation_kind, run_chat
from app.agents.chat_tools import (
    compare_periods,
    detect_anomalies,
    get_customer_profile,
    get_priorities,
    invoke_tool,
)
from app.services.processors import ERP_TZ
from test_chat import FakeCollection, FakeModel
from test_chat_data_tools import NOW, business_db, lead, order, payment, quote, tool_call


# --------------------------------------------------------------------- data

def garcoa_order(index: int) -> dict:
    return order(
        id=4000 + index,
        inv_ref=f"{4000 + index}-2026-09",
        invoice_date="2026-09-20T00:00:00-05:00",
        due_date="2026-10-05",
        total_amount=100.0,
        amount_paid=0.0,
        customer={"id": 777, "company_name": "Garcoa Inc"},
    )


def garcoa_db():
    return business_db(
        orders_flat=FakeCollection([garcoa_order(i) for i in range(60)]),
        payments_flat=FakeCollection([
            payment(i, amount=200.0, customer_id=777,
                    customer={"id": 777, "company_name": "Garcoa Inc"})
            for i in range(1, 4)
        ]),
        quotes_flat=FakeCollection([
            quote(id=3100 + i, customer={"id": 777, "company_name": "Garcoa Inc"})
            for i in range(2)
        ]),
    )


def thread_doc(database, thread_id="thread-1"):
    return next(row for row in database.chat_threads.rows if row["thread_id"] == thread_id)


# ------------------------------------------------------- confirmation words

def test_confirmation_kind_classifies_short_replies_only():
    assert _confirmation_kind("yes pull") == "generic"
    assert _confirmation_kind("Yes!") == "generic"
    assert _confirmation_kind("show me all of them") == "all"
    assert _confirmation_kind("fetch the next page") == "next_page"
    assert _confirmation_kind("what about last week?") is None
    assert _confirmation_kind("yes but why did payments drop in the second week of October?") is None
    assert _confirmation_kind("") is None


# ------------------------------------------------------------- profile 360

@pytest.mark.anyio
async def test_profile_reports_partial_sections_honestly():
    profile = await get_customer_profile("Garcoa Inc", database=garcoa_db())
    assert profile["customer_id"] == 777
    assert profile["complete"] is False
    assert set(profile["missing_sections"]) == {"orders", "invoices"}
    assert profile["unpaid_invoices"]["total_count"] == 60
    assert profile["unpaid_invoices"]["has_more"] is True
    assert profile["recent_payments"]["complete"] is True
    assert profile["balances"]["unpaid_invoice_count"] == 60


@pytest.mark.anyio
async def test_profile_complete_when_every_section_fits():
    database = business_db(
        orders_flat=FakeCollection([garcoa_order(1), garcoa_order(2)]),
        payments_flat=FakeCollection([
            payment(1, amount=200.0, customer_id=777,
                    customer={"id": 777, "company_name": "Garcoa Inc"})
        ]),
        quotes_flat=FakeCollection([quote(customer={"id": 777, "company_name": "Garcoa Inc"})]),
    )
    profile = await get_customer_profile("Garcoa Inc", database=database)
    assert profile["complete"] is True
    assert profile["missing_sections"] == []
    assert profile["recent_orders"]["total_count"] == 2


@pytest.mark.anyio
async def test_profile_section_failure_is_isolated(monkeypatch):
    async def broken_orders(**kwargs):
        raise RuntimeError("orders source unavailable")

    monkeypatch.setattr(chat_tools, "get_orders", broken_orders)
    profile = await get_customer_profile("Garcoa Inc", database=garcoa_db())
    assert profile["complete"] is False
    assert "orders" in profile["missing_sections"]
    assert profile["recent_orders"]["available"] is False
    # the rest of the profile still arrived
    assert profile["unpaid_invoices"]["total_count"] == 60
    assert profile["company_name"] == "Garcoa Inc"


# ------------------------------------------------- the Garcoa reproduction

@pytest.mark.anyio
async def test_garcoa_yes_pull_executes_the_pending_retrieval():
    database = garcoa_db()
    first = FakeModel([
        tool_call("get_customer_profile", {"name_or_id": "Garcoa Inc"}),
        AIMessage(content="Garcoa Inc has 60 unpaid invoices on record. The rest of the profile can be pulled."),
    ])
    await run_chat("thread-1", "Give me all details for Garcoa Inc.", database=database, model=first)
    pending = thread_doc(database)["pending_action"]
    assert pending["kind"] == "customer_sections"
    assert set(pending["missing_sections"]) == {"orders", "invoices"}

    second = FakeModel([
        AIMessage(content="Pulled. Garcoa Inc has 60 unpaid invoices in total."),
    ])
    result = await run_chat("thread-1", "yes pull", database=database, model=second)

    # the backend fetched before the model answered: the model was handed
    # real walked results, not another chance to repeat the offer
    tool_messages = [
        message for message in second.seen_messages[0] if isinstance(message, ToolMessage)
    ]
    assert any('"walk_complete": true' in message.content for message in tool_messages)
    assert result["text"] == "Pulled. Garcoa Inc has 60 unpaid invoices in total."
    assert "get_invoices" in result["tools_used"]
    # nothing is left outstanding, so nothing is left pending
    assert thread_doc(database)["pending_action"] is None


# ------------------------------------------------------------ page pending

def sixty_payments_db():
    return business_db(payments_flat=FakeCollection([payment(i) for i in range(1, 61)]))


@pytest.mark.anyio
async def test_fetch_the_next_page_executes_the_recorded_page():
    database = sixty_payments_db()
    first = FakeModel([
        tool_call("get_payments", {"limit": 50}),
        AIMessage(content="First page delivered."),
    ])
    await run_chat("thread-1", "List payments.", database=database, model=first)
    pending = thread_doc(database)["pending_action"]
    assert pending["kind"] == "next_page"

    second = FakeModel([AIMessage(content="The next page is delivered.")])
    await run_chat("thread-1", "fetch the next page", database=database, model=second)
    tool_messages = [
        message for message in second.seen_messages[0] if isinstance(message, ToolMessage)
    ]
    # the forced page starts exactly where the recorded walk stopped (the
    # context budget trimmed page one below the requested 50 rows, so the
    # recorded next_offset - not the requested limit - is the truth)
    assert any(f'"offset": {pending["next_offset"]}' in message.content for message in tool_messages)
    assert thread_doc(database)["pending_action"] is None


@pytest.mark.anyio
async def test_show_me_all_of_them_walks_to_completion():
    database = sixty_payments_db()
    first = FakeModel([
        tool_call("get_payments", {"limit": 50}),
        AIMessage(content="First page delivered."),
    ])
    await run_chat("thread-1", "List payments.", database=database, model=first)
    second = FakeModel([AIMessage(content="All of them were fetched.")])
    await run_chat("thread-1", "show me all of them", database=database, model=second)
    tool_messages = [
        message for message in second.seen_messages[0] if isinstance(message, ToolMessage)
    ]
    assert any('"walk_complete": true' in message.content for message in tool_messages)
    assert thread_doc(database)["pending_action"] is None


@pytest.mark.anyio
async def test_confirmation_without_pending_is_a_normal_turn():
    database = garcoa_db()
    model = FakeModel([AIMessage(content="Hello.")])
    result = await run_chat("thread-1", "yes", database=database, model=model)
    assert result["text"] == "Hello."
    assert thread_doc(database)["pending_action"] is None


@pytest.mark.anyio
async def test_a_new_question_supersedes_a_stale_pending_action():
    database = garcoa_db()
    first = FakeModel([
        tool_call("get_customer_profile", {"name_or_id": "Garcoa Inc"}),
        AIMessage(content="Garcoa Inc has 60 unpaid invoices on record. The rest of the profile can be pulled."),
    ])
    await run_chat("thread-1", "Give me all details for Garcoa Inc.", database=database, model=first)
    assert thread_doc(database)["pending_action"] is not None
    second = FakeModel([AIMessage(content="Something else entirely.")])
    await run_chat("thread-1", "What is the weather in the office?", database=database, model=second)
    assert thread_doc(database)["pending_action"] is None


# ------------------------------------------------------ duplicate prevention

@pytest.mark.anyio
async def test_identical_tool_call_is_not_reinvoked():
    database = business_db(payments_flat=FakeCollection([
        payment(1, amount=100.0, received_at="2026-10-03T00:00:00-05:00"),
        payment(2, amount=50.0, received_at="2026-10-03T00:00:00-05:00"),
    ]))
    args = {"date_from": "2026-10-01", "date_to": "2026-10-07"}
    both = AIMessage(
        content="",
        tool_calls=[
            {"name": "get_revenue", "args": args, "id": "c1", "type": "tool_call"},
            {"name": "get_revenue", "args": args, "id": "c2", "type": "tool_call"},
        ],
        usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
    )
    model = FakeModel([both, AIMessage(content="Total payments were $150.")])
    result = await run_chat("thread-1", "Revenue for the first week of October?", database=database, model=model)
    assert result["text"] == "Total payments were $150."
    tool_messages = [
        message for message in model.seen_messages[1] if isinstance(message, ToolMessage)
    ]
    assert len(tool_messages) == 2
    assert sum("duplicate_call_prevented" in message.content for message in tool_messages) == 1


# -------------------------------------------------- completeness in answers

def test_partial_profile_cannot_be_called_complete():
    results = [{"tool": "get_customer_profile", "result": {"complete": False, "missing_sections": ["invoices"]}}]
    errors = _answer_consistency_errors("Here is the complete profile for Garcoa Inc.", results, None)
    assert any("partial customer profile" in error for error in errors)
    disclosed = _answer_consistency_errors(
        "Here is a partial profile for Garcoa Inc; more records remain.", results, None
    )
    assert disclosed == []
    complete = [{"tool": "get_customer_profile", "result": {"complete": True}}]
    assert _answer_consistency_errors("Here is the complete profile for Garcoa Inc.", complete, None) == []


# ------------------------------------------------------------ compare (P2)

@pytest.mark.anyio
async def test_compare_periods_math_is_backend_computed():
    database = business_db(payments_flat=FakeCollection([
        payment(1, amount=100.0, received_at="2026-10-03T00:00:00-05:00"),
        payment(2, amount=100.0, received_at="2026-10-05T00:00:00-05:00"),
        payment(3, amount=300.0, received_at="2026-10-10T00:00:00-05:00"),
    ]))
    result = await compare_periods("payments", date(2026, 10, 8), date(2026, 10, 14), database=database)
    assert result["current"]["total"] == 300.0
    assert result["current"]["count"] == 1
    assert result["previous"]["total"] == 200.0
    assert result["previous"]["count"] == 2
    assert result["previous"]["date_from"] == "2026-10-01"
    assert result["change"] == {"absolute": 100.0, "percent": 50.0, "direction": "up"}


@pytest.mark.anyio
async def test_compare_periods_zero_previous_is_new_not_infinite():
    database = business_db(payments_flat=FakeCollection([
        payment(1, amount=75.0, received_at="2026-12-10T00:00:00-05:00"),
    ]))
    result = await compare_periods("payments", date(2026, 12, 8), date(2026, 12, 14), database=database)
    assert result["previous"]["total"] == 0.0
    assert result["change"]["percent"] is None
    assert result["change"]["direction"] == "new"
    empty = await compare_periods("payments", date(2027, 1, 5), date(2027, 1, 11), database=database)
    assert empty["change"] == {"absolute": 0.0, "percent": 0.0, "direction": "flat"}


@pytest.mark.anyio
async def test_compare_periods_leads_compare_counts():
    database = business_db(leads=FakeCollection([
        lead(zohoLeadId="L-1", createdAt="2026-10-09T00:00:00+00:00"),
        lead(zohoLeadId="L-2", createdAt="2026-10-10T00:00:00+00:00"),
        lead(zohoLeadId="L-3", createdAt="2026-10-11T00:00:00+00:00"),
        lead(zohoLeadId="L-4", createdAt="2026-10-02T00:00:00+00:00"),
    ]))
    result = await compare_periods("leads", date(2026, 10, 8), date(2026, 10, 14), database=database)
    assert result["current"]["count"] == 3
    assert result["previous"]["count"] == 1
    assert result["change"]["percent"] == 200.0
    unknown = await compare_periods("emails", date(2026, 10, 8), date(2026, 10, 14), database=database)
    assert unknown["available"] is False


# ------------------------------------------------------------ anomalies (P2)

def daily_payments(amount_by_offset: dict[int, float]):
    today = datetime.now(ERP_TZ).date()
    rows = []
    for offset, amount in amount_by_offset.items():
        day = today - timedelta(days=offset)
        rows.append(payment(1, amount=amount, received_at=f"{day.isoformat()}T10:00:00-05:00"))
    return business_db(payments_flat=FakeCollection(rows))


@pytest.mark.anyio
async def test_detect_anomalies_flags_a_real_spike():
    amounts = {offset: 100.0 for offset in range(7, 63)}       # steady baseline
    amounts.update({offset: 300.0 for offset in range(0, 7)})  # tripled week
    result = await detect_anomalies("payments", database=daily_payments(amounts))
    assert result["available"] is True
    assert result["anomaly"] is True
    assert result["direction"] == "up"
    assert result["recent"]["total"] == 2100.0


@pytest.mark.anyio
async def test_detect_anomalies_stays_quiet_on_a_normal_week():
    amounts = {offset: 100.0 for offset in range(0, 63)}
    result = await detect_anomalies("payments", database=daily_payments(amounts))
    assert result["anomaly"] is False


@pytest.mark.anyio
async def test_detect_anomalies_refuses_without_data():
    result = await detect_anomalies("payments", database=business_db(payments_flat=FakeCollection([])))
    assert result["available"] is False


# ----------------------------------------------------------- priorities (P2)

@pytest.mark.anyio
async def test_priorities_are_grounded_and_ordered():
    stale_quote = quote(created_at=(NOW - timedelta(days=20)).isoformat())
    database = business_db(
        orders_flat=FakeCollection([order(due_date="2026-09-01")]),
        quotes_flat=FakeCollection([stale_quote]),
        leads=FakeCollection([lead()]),
    )
    result = await get_priorities(database=database)
    actions = [item["action"] for item in result["recommendations"]]
    assert len(actions) == 3
    assert actions[0].startswith("Collect overdue balances")
    first = result["recommendations"][0]
    assert first["metrics"]["overdue_invoice_count"] == 1
    assert 33563 in first["record_ids"]
    assert first["confidence"].startswith("high")
    assert result["recommendations"][2]["metrics"]["stuck_lead_count"] == 1


@pytest.mark.anyio
async def test_priorities_empty_when_nothing_is_outstanding():
    database = business_db(
        orders_flat=FakeCollection([]),
        quotes_flat=FakeCollection([]),
        leads=FakeCollection([]),
        payments_flat=FakeCollection([]),
    )
    result = await get_priorities(database=database)
    assert result["recommendations"] == []


@pytest.mark.anyio
async def test_new_tools_are_registered_for_the_model():
    result = await invoke_tool(
        "compare_periods",
        {"metric": "payments", "date_from": "2026-10-08", "date_to": "2026-10-14"},
        database=business_db(),
    )
    assert result["metric"] == "payments"
    assert result["complete"] is True
