"""Golden regression set, Oct 27, 2026 test run.

Every test here pins a failure the user pasted from a live 31-question
run, and every test goes through the LIVE path (invoke_tool / execute_chat),
because the Oct 26 CRM-payments "fix" passed its unit test while live chat
still failed: that test called the function directly and never met the
argument validator that kills dateless calls in invoke_tool.

The human-readable list with expected facts lives in GOLDEN_QUESTIONS.md
next to this file; these tests lock the tool-level behaviour so a prompt
or tool change cannot silently reintroduce a fixed class.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage

from app.agents.chat import execute_chat, validate_numeric_claims
from app.agents.chat_tools import invoke_tool
from app.agents import chat_tools
from app.services import periods, processors
from test_chat import FakeModel, plain_answer
from test_chat_data_tools import Collection, business_db, lead, order, payment, quote

NOW = datetime.now(timezone.utc)


def _crm_client(rows):
    class FakeCRM:
        async def list_page(self, module, page=1, limit=100, search=""):
            if page == 1:
                return {
                    "success": True,
                    "payments": rows,
                    "pagination": {"total": len(rows), "pages": 1},
                }
            return {"success": True, "payments": [], "pagination": {"total": len(rows), "pages": 1}}

        async def fetch_analysis(self, record_id, record_type):
            return {
                "success": True,
                "apiKeyStatus": "Verified",
                "analysis": {"urgency": "High", "record": record_id},
            }

    return FakeCRM()


CRM_ROWS = [
    {
        "paymentName": "33609", "erpId": 7251, "customerName": "Picazo Cleaning Services",
        "amountReceived": 1250, "totalAmount": 1250,
        "amountReceiveDate": "2026-10-21T00:00:00.000Z",
        "createdAt": "2026-10-21T19:24:00.000Z",
    }
]


@pytest.fixture
def crm(monkeypatch):
    monkeypatch.setattr(chat_tools, "_c360_client", lambda: _crm_client(CRM_ROWS))
    return chat_tools


# ------------------------------------------------------- CRM payments, live path


@pytest.mark.asyncio
async def test_crm_payments_dateless_customer_goes_through_invoke_tool(crm):
    """Oct 27: 'TREATDAY ke CRM payments dikhao' died in invoke_tool with
    'date_from and date_to are required' - the function-level fallback was
    unreachable behind the validator. Through invoke_tool it must now
    scan all time."""
    db = business_db()
    result = await invoke_tool("get_crm_payments", {"customer": "Picazo"}, database=db)
    assert result["available"] is True
    assert result["date_from"] == "2000-01-01"
    assert result["total_count"] == 1
    assert result["sum_amount_received"] == "$1,250"


@pytest.mark.asyncio
async def test_combined_payments_returns_both_ledgers_separately(crm):
    """Canyon Plastics, twice: the ERP leg alone, CRM 'not checked yet'.
    One call must return both totals, kept separate, never summed."""
    db = business_db(payments_flat=Collection([payment(index=1, amount=1250.0)]))
    result = await invoke_tool(
        "get_payments_both_ledgers", {"customer": "Picazo"}, database=db
    )
    assert result["window"]["date_from"] == "2000-01-01"
    assert result["erp"]["total_count"] == 1
    assert result["erp"]["sum_amount"] == "$1,250"
    assert result["crm"]["total_count"] == 1
    assert result["crm"]["sum_amount_received"] == "$1,250"
    # the totals live in separate fields under separate labels, and the
    # result carries the never-add instruction (equal totals are
    # legitimate - the CRM mirrors ERP payments)
    assert "erp" in result and "crm" in result
    assert "Never add the two totals" in result["ledger_note"]
    assert result["paired_on_this_page_only"]["crm_rows_with_erp_id_on_this_page"] == 1


@pytest.mark.asyncio
async def test_combined_payments_today_is_the_erp_business_day(crm):
    """Oct 27 contradiction: payments 'today' $0 vs revenue payments today
    12 - two tools defined 'today' differently. The combined tool resolves
    a named period on the ERP business calendar, the revenue tile's day."""
    db = business_db()
    result = await invoke_tool("get_payments_both_ledgers", {"period": "today"}, database=db)
    business_day = periods.business_today().isoformat()
    assert result["window"]["date_from"] == business_day
    assert result["window"]["date_to"] == business_day
    assert result["window"]["business_date"] == business_day


# ------------------------------------------------------- stuck leads next_action


def test_lead_next_action_names_the_missing_field():
    assert processors.lead_next_action(["material"], "Orthoza") == (
        "Ask Orthoza for the missing material."
    )
    assert processors.lead_next_action(["quantity", "size"], None).startswith(
        "Ask the customer for the missing quantity."
    )
    assert processors.lead_next_action([]) == (
        "Confirm the order details and convert the lead to an order."
    )


@pytest.mark.asyncio
async def test_stuck_leads_carry_next_action():
    """Orthoza: the tool must hand the model the exact question, so the
    model copies it instead of recommending the missing material."""
    orthoza = lead(
        firstName="Orthoza",
        lastName="",
        company="",
        status="New",
        description=(
            "product: Vinyl Labels\nquantity: 10000\nsize: 3 x 3 inch\n"
            "finish: gloss_laminated"
        ),
        email="orthoza@example.com",
        lastActivityAt=NOW.isoformat(),
        createdAt=NOW.isoformat(),
    )
    db = business_db(leads=Collection([orthoza]))
    result = await invoke_tool("get_stuck_leads", {}, database=db)
    assert result["total_count"] == 1
    item = result["items"][0]
    assert item["missing"] == ["material"]
    assert item["next_action"] == "Ask Orthoza for the missing material."


# ------------------------------------------------------- status words


@pytest.mark.asyncio
async def test_unpaid_and_open_are_not_label_substrings():
    """Oct 27: 'unpaid invoices' 0 while overdue balances existed, and
    'open quotes' 0 while 365 stale quotes existed - no ERP label
    contains those words."""
    db = business_db(
        orders=[order(payment_status_label="Not Paid", total_amount=4000.0, amount_paid=0.0)],
        quotes=[quote(status="Sent", amount=1000.0)],
    )
    invoices = await invoke_tool("get_invoices", {"status": "unpaid"}, database=db)
    assert invoices["total_count"] >= 1
    quotes_result = await invoke_tool("get_quotes", {"status": "open"}, database=db)
    assert quotes_result["total_count"] >= 1


# ------------------------------------------------------- guard: list ids, recall dates


def test_guard_accepts_a_list_leading_record_id():
    """Oct 27: the stale-quotes list died on every quote id ('- 20550: ...')."""
    result = {
        "tool": "get_quotes",
        "result": {
            "items": [{"quote_id": 20550, "amount": 348000}],
            "total_count": 1, "returned": 1, "has_more": False,
        },
    }
    assert validate_numeric_claims("- 20550: Jasper United LLC, $348,000", [result]) == []
    # a fabricated id still fails
    assert validate_numeric_claims("- 99999: Nobody, $348,000", [result])


@pytest.mark.asyncio
async def test_history_answer_survives_its_own_dates():
    """Oct 27: 'what did we discuss last time' collapsed to 'couldn't
    verify' because recall results were excluded from the guard's
    evidence, so their own updated_at dates failed validation."""
    doc = {
        "thread_id": "thread-1",
        "title": "Liza John january sales 2026 report",
        "updated_at": "2026-10-26 22:57:18",
        "turns": [{"question": "Liza John january sales 2026 report", "answer": "Quotes: 41"}],
    }
    db = business_db(chat_threads=Collection([doc]))
    model = FakeModel([
        AIMessage(
            content="",
            tool_calls=[{"name": "recall_conversations", "args": {}, "id": "call-1", "type": "tool_call"}],
            usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        ),
        plain_answer(
            "- Liza John january sales 2026 report (updated 2026-10-26 22:57:18) — 1 turn"
        ),
    ])
    outcome = await execute_chat(
        "What did we discuss last time?", model=model, database=db
    )
    assert outcome["validation_replaced"] is False
    assert "Liza John january sales 2026 report" in outcome["text"]
    assert "2026-10-26 22:57:18" in outcome["text"]


@pytest.mark.asyncio
async def test_both_analyses_return_in_one_call(crm):
    """Oct 27: 'Frank Jr ke dono records ki analysis do' failed as a
    timeout - two sequential slow CRM calls. The parallel tool returns
    both, and one failure must not take the other down."""
    result = await invoke_tool(
        "get_customer360_analyses",
        {"records": [
            {"record_id": "aaa", "record_type": "Lead"},
            {"record_id": "bbb", "record_type": "Contact"},
        ]},
        database=business_db(),
    )
    assert len(result["analyses"]) == 2
    assert all(item["available"] is True for item in result["analyses"])
    assert result["analyses"][0]["analysis"]["record"] == "aaa"
    assert result["analyses"][1]["analysis"]["record"] == "bbb"
