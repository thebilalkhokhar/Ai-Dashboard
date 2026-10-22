"""The "what did customers pay this week?" fallback bug.

Reproduced against live ERP data on 2026-10-22: the assistant called
get_dashboard_metrics and get_payments, both succeeded (53 payments for the week,
with a Python-computed total), and it then answered with the total plus the payment ids it had
been given. The numeric guard rejected all 27 of those ids as
`unsupported number 7262`, the one regeneration failed the same way, and the
user was shown "I couldn't verify that figure from the available data."

The ids were never the problem - the guard had no idea the word "Payment"
introduces an identifier, while "Customer" did.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage

from app.agents import chat, chat_tools
from app.agents.chat import execute_chat, validate_numeric_claims
from app.agents.chat_tools import invoke_tool
from app.config import settings
from test_chat import FakeModel
from test_chat_data_tools import NOW, Collection, FrozenDateTime, business_db, payment

TODAY = date(2026, 10, 21)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    for module in (chat_tools, chat):
        monkeypatch.setattr(module, "datetime", FrozenDateTime)


def payments_result(rows, *, total=None, offset=0, limit=10, sum_amount=12500.0):
    """One page exactly as get_payments describes it to the model."""
    total = len(rows) if total is None else total
    return {
        "tool": "get_payments",
        "result": {
            "sum_amount": sum_amount,
            "items": [
                {
                    "payment_id": row["id"],
                    "amount": row["amount"],
                    "payment_date": "2026-10-21",
                    "received_at": row["received_at"],
                    "reference": row["payment_ref"],
                    "customer_id": row["customer"]["id"],
                    "customer_name": row["customer"]["company_name"],
                    "invoices": [],
                }
                for row in rows
            ],
            "offset": offset,
            "limit": limit,
            "total_count": total,
            "returned": len(rows),
            "remaining_count": max(0, total - offset - len(rows)),
            "has_more": offset + len(rows) < total,
            "all_records_returned": offset + len(rows) >= total,
        },
    }


ROWS = [
    payment(index=12, amount=1500.0, payment_ref="7262-1969-12",
            customer={"id": 101, "company_name": "Acme Print Co"}),
    payment(index=11, amount=250.0, payment_ref="7261-1969-12",
            customer={"id": 102, "company_name": "Beta Packaging"}),
    payment(index=10, amount=4000.0, payment_ref="7260-1969-12",
            customer={"id": 103, "company_name": "Cedar Graphics"}),
]


def listing_text(rows, total, sum_display="$12,500"):
    lines = [f"Payments this week: {len(rows)} listed of {total}, total {sum_display}.\n"]
    for row in rows:
        lines.append(
            f"- {row['payment_date']} | {row['customer_name']} | "
            f"{chat_tools.format_money(row['amount'])} | Payment {row['payment_id']} | "
            f"Customer {row['customer_id']}"
        )
    return "\n".join(lines)


# ------------------------------------------------ the guard, in isolation


def test_payment_ids_the_tool_returned_are_accepted_when_named_as_such():
    """The regression: 'Payment 7262' is an identifier the backend really gave."""
    result = payments_result(ROWS, total=53)
    text = listing_text(result["result"]["items"], total=53)
    assert validate_numeric_claims(text, [result]) == []


def test_a_payment_id_the_tools_never_returned_is_still_rejected():
    """Fixing the label must not turn the guard into a rubber stamp."""
    result = payments_result(ROWS, total=53)
    assert validate_numeric_claims("Payment 9999 paid $1,500.", [result])
    assert validate_numeric_claims("Payment 7262 paid $1,500.", [result]) == []


def test_an_amount_cannot_hide_in_a_payment_id_slot():
    """7262 is an id, not a sum: in a money sentence it must still be refused."""
    result = payments_result(ROWS, total=53)
    assert validate_numeric_claims("The week came to $7,262.", [result])


def test_amount_formatting_differences_do_not_falsify_a_verified_total():
    """$12,500 / 12500 / $12,500.00 are the same verified figure."""
    result = payments_result(ROWS, total=53)
    assert validate_numeric_claims("Total $12,500.", [result]) == []
    assert validate_numeric_claims("Total 12500.", [result]) == []
    assert validate_numeric_claims("Total $12,500.00.", [result]) == []
    # a materially different amount is not a formatting difference
    assert validate_numeric_claims("Total $12,501.", [result])


def test_a_figure_in_another_currency_is_not_a_verified_amount():
    """The ERP holds no currency field, so every amount here is USD."""
    result = payments_result(ROWS, total=53)
    assert validate_numeric_claims("Total $12,500.", [result]) == []
    assert validate_numeric_claims("Total 12,500 USD.", [result]) == []
    assert validate_numeric_claims("Total EUR 12,500.", [result])
    assert validate_numeric_claims("Total €12,500.", [result])


def test_decimal_amounts_match_to_the_cent_and_nothing_looser():
    rows = [payment(index=9, amount=1234.5, customer={"id": 104, "company_name": "Delta Labels"})]
    result = payments_result(rows, sum_amount=1234.5)
    assert validate_numeric_claims("Delta Labels paid $1,234.50.", [result]) == []
    assert validate_numeric_claims("Delta Labels paid $1,234.55.", [result])


# ------------------------------------------- the calculation behind the page


@pytest.mark.asyncio
async def test_the_weekly_total_covers_every_match_not_just_the_page_returned():
    """sum_amount is a server-side aggregation, so a short page cannot shrink it."""
    rows = [payment(index=index, amount=500.0 + index) for index in range(1, 35)]
    database = business_db(payments_flat=Collection(rows))
    expected = round(sum(500.0 + index for index in range(1, 35)), 2)

    first = await chat_tools.get_payments(limit=10, database=database)
    second = await chat_tools.get_payments(limit=10, offset=10, database=database)

    assert first["sum_amount"] == expected
    assert second["sum_amount"] == expected
    assert first["total_count"] == second["total_count"] == 34
    assert first["has_more"] is True and first["all_records_returned"] is False


@pytest.mark.asyncio
async def test_week_boundaries_are_inclusive_and_outside_rows_are_excluded():
    """Payment days are the USER's calendar days (Asia/Karachi): an instant
    belongs to the PKT day it lands in, so the ERP's late evening is
    already tomorrow for the reader in Lahore."""
    rows = [
        payment(index=1, received_at="2026-10-15T14:00:01-05:00", amount=100.0),   # Oct 16 00:00:01 PKT - first instant of the asked week
        payment(index=2, received_at="2026-10-21T13:59:59-05:00", amount=200.0),   # Oct 21 23:59:59 PKT - last instant asked for
        payment(index=3, received_at="2026-10-21T14:00:00-05:00", amount=900.0),   # Oct 22 00:00:00 PKT - the day after, excluded
        payment(index=4, received_at="2026-10-15T13:59:59-05:00", amount=700.0),   # Oct 15 23:59:59 PKT - the day before, excluded
    ]
    database = business_db(payments_flat=Collection(rows))

    week = await chat_tools.get_payments(
        date_from=date(2026, 10, 16), date_to=date(2026, 10, 21), database=database
    )

    assert week["total_count"] == 2
    assert week["sum_amount"] == 300.0
    assert {item["payment_id"] for item in week["items"]} == {7251, 7252}


@pytest.mark.asyncio
async def test_pages_aggregate_without_repeating_or_skipping_a_payment():
    rows = [payment(index=index, amount=100.0) for index in range(1, 26)]
    database = business_db(payments_flat=Collection(rows))

    seen: list[int] = []
    offset = 0
    for _ in range(20):
        page = await chat_tools.get_payments(limit=10, offset=offset, database=database)
        seen += [item["payment_id"] for item in page["items"]]
        if not page["has_more"]:
            break
        offset = page["next_offset"]

    assert len(seen) == 25 == len(set(seen))


@pytest.mark.asyncio
async def test_the_erp_has_no_payment_status_or_currency_to_filter_on():
    """No pending/void/refund concept may be invented; the tool must say so."""
    described = await chat_tools.describe_data(database=business_db())

    unavailable = " | ".join(described["not_available"]["payments"]).casefold()
    assert "status" in unavailable
    assert "currency" in unavailable
    assert "refund" in unavailable
    assert described["payments"]["count"] == 1


@pytest.mark.asyncio
async def test_a_partial_page_is_never_reported_as_the_whole_result_set():
    """If paging cannot finish, the answer must not read as a complete total."""
    rows = [payment(index=index, amount=100.0) for index in range(1, 12)]
    database = business_db(payments_flat=Collection(rows))

    page = await chat_tools.get_payments(limit=5, database=database)

    assert page["total_count"] == 11
    assert page["returned"] == 5
    assert page["has_more"] is True
    assert page["all_records_returned"] is False
    assert page["remaining_count"] == 6


@pytest.mark.asyncio
async def test_no_matching_payments_explains_itself_instead_of_inventing_a_total():
    database = business_db()

    empty = await chat_tools.get_payments(customer="Nobody Exists", database=database)

    assert empty["total_count"] == 0
    assert empty["sum_amount"] == 0.0
    assert "No customer named" in empty["note"]


# --------------------------------------------------- the turn, end to end


def call(name, args, index=1):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": f"call-{index}", "type": "tool_call"}],
        usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
    )


def answer(text):
    return AIMessage(content=text, usage_metadata={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8})


@pytest.mark.asyncio
async def test_the_original_question_answers_instead_of_falling_back():
    """"What did customers pay this week?" must produce the verified figure.

    The model lists the payments it was given, ids included - which is what used
    to trip the guard and swallow a correct answer.
    """
    rows = [payment(index=index, amount=500.0 + index) for index in range(1, 13)]
    database = business_db(payments_flat=Collection(rows))
    page = await chat_tools.get_payments(
        date_from=TODAY, date_to=TODAY, limit=50, database=database
    )
    claim = listing_text(page["items"], page["total_count"], page["sum_amount"])

    model = FakeModel([
        call("get_payments", {"date_from": TODAY.isoformat(), "date_to": TODAY.isoformat(), "limit": 50}),
        answer(claim),
    ])

    result = await execute_chat("What did customers pay this week?", [], model=model, database=database)

    assert "couldn't verify" not in result["text"]
    assert result["text"] == claim
    assert str(page["sum_amount"]) in result["text"]


@pytest.mark.asyncio
async def test_an_unsupported_amount_is_still_refused_end_to_end():
    """The same turn, but the model makes the total up: the guard must hold.

    The invented amount never reaches the user. Because the tool did compute a
    real figure, the answer is now the backend's own verified total rather than a
    generic apology - the fabrication is dropped, the evidence is kept.
    """
    rows = [payment(index=index, amount=500.0 + index) for index in range(1, 13)]
    database = business_db(payments_flat=Collection(rows))

    model = FakeModel([
        call("get_payments", {"date_from": TODAY.isoformat(), "date_to": TODAY.isoformat(), "limit": 50}),
        answer("Customers paid $9,999,999 this week."),
        answer("Customers paid $9,999,999 this week."),
    ])

    result = await execute_chat("What did customers pay this week?", [], model=model, database=database)

    assert "9,999,999" not in result["text"]
    assert "9999999" not in result["text"]
    # the verified figure the backend computed is shown instead
    assert result["text"] == "Verified from backend records for this week: $6,078 across all 12 matching payments (2026-10-19 to 2026-10-25)."
    assert "couldn't verify" not in result["text"]


@pytest.mark.asyncio
async def test_last_week_prose_fabrication_keeps_the_verified_total():
    """Reproduces the live 'last week' failure: a supported total exists, the
    model adds numbers that are not in evidence (a page index range and an
    invented payment id), the guard rejects them, and the verified total - not
    the fabricated claims - is what the user sees."""
    rows = [
        payment(index=index, amount=500.0 + index, received_at="2026-10-15T12:00:00-05:00")
        for index in range(1, 45)
    ]
    database = business_db(payments_flat=Collection(rows))

    model = FakeModel([
        call("get_payments", {"period": "last_week", "limit": 10}),
        answer(
            "Here’s what customers paid last week (period last_week).\n"
            "- Total sum of payments: $157,144\n"
            "- This page shows 10 payments (0–9 of 44); next page offset is 10.\n"
            "- Payment 9999 was the largest, worth $9,999,999.\n"
        ),
        answer("All 44 payments were exactly $9,999 each, listed above."),
    ])

    result = await execute_chat("What did customers pay last week?", [], model=model, database=database)

    # no fabricated claim reaches the user
    for fabricated in ("9999", "157,144", "0–9", "9,999"):
        assert fabricated not in result["text"]
    # the backend-verified figure for THIS fixture is shown instead
    expected_total = round(sum(500.0 + index for index in range(1, 45)), 2)
    assert chat_tools.format_money(expected_total) in result["text"]
    assert "2026-10-12 to 2026-10-18" in result["text"]
    assert "couldn't verify" not in result["text"]


@pytest.mark.asyncio
async def test_a_non_financial_failure_still_gives_the_plain_fallback():
    """With no money/record evidence to stand on, the generic fallback is kept -
    the summary is only built from real, supported aggregates."""
    database = business_db()
    model = FakeModel([
        answer("There were 999999 widgets shipped."),
        answer("There were 999999 widgets shipped."),
    ])

    result = await execute_chat("How many widgets did we ship?", [], model=model, database=database, tools=[])

    assert result["text"] == "I couldn't verify that figure from the available data."



# ---------------------------------------------------------------------------
# A number the reader supplied is a restatement, not a claim
# ---------------------------------------------------------------------------

REVENUE_RESULT = {
    "date_from": "2026-10-16",
    "date_to": "2026-10-22",
    "rep_id": None,
    "payment_count": 54,
    "total_payments": "$323,116",
}


def test_a_day_range_the_user_asked_for_may_be_echoed():
    """Reproduced live 2026-10-22: get_revenue succeeded and the model wrote
    "over the last 7 days", and the guard refused the whole answer because 7
    appeared nowhere in the tool result - the user had supplied it."""
    results = [{"tool": "get_revenue", "result": REVENUE_RESULT}]
    answer = "Over the last 7 days, customers made 54 payments totalling $323,116."

    assert validate_numeric_claims(answer, results, "", "What did customers pay in the last 7 days?") == []


def test_a_number_the_reader_never_asked_for_is_still_refused():
    results = [{"tool": "get_revenue", "result": REVENUE_RESULT}]
    answer = "Over the last 9 days, customers made 54 payments totalling $323,116."

    errors = validate_numeric_claims(answer, results, "", "What did customers pay in the last 7 days?")
    assert errors == ["unsupported number 9"]


def test_an_amount_the_user_asked_about_does_not_become_verified():
    """Asking about a number is not evidence for it. The money path must still
    demand a figure Python computed, whatever the question contained."""
    results = [{"tool": "get_revenue", "result": REVENUE_RESULT}]
    answer = "You asked about $1,000,000 and the answer is $1,000,000."

    errors = validate_numeric_claims(answer, results, "", "Did we get $1,000,000?")
    assert any("1,000,000" in error for error in errors), errors


@pytest.mark.asyncio
async def test_an_unpaged_aggregate_still_reports_its_verified_total():
    """get_revenue has no pages, so it used to carry no evidence at all: the
    browser saw a money total the UI could not attribute to anything."""
    from app.agents.chat import tool_evidence

    evidence = tool_evidence("get_revenue", REVENUE_RESULT)
    assert evidence is not None
    assert evidence["money_label"] == "total_payments"
    assert evidence["money_total"] == 323116.0
    assert evidence["record_count"] == 54
    assert evidence["window"] == {"date_from": "2026-10-16", "date_to": "2026-10-22"}
    # counts broadcast, money does not go in the durable event
    from app.agents.chat import evidence_counts

    assert "money_total" not in evidence_counts(evidence)
    assert evidence_counts(evidence)["record_count"] == 54


@pytest.mark.asyncio
async def test_a_tool_that_computed_nothing_gives_no_evidence():
    from app.agents.chat import tool_evidence

    assert tool_evidence("get_latest_briefing", {"run_id": None, "note": "No agent run yet."}) is None


def test_a_numbered_list_is_not_read_as_a_claims():
    """Live 2026-10-22: "1. Payment 7262 - $501" was refused for the ordinal 1,
    so a correct page listing fell back to the could-not-verify sentence."""
    results = [{"tool": "get_payments", "result": {
        "offset": 0, "limit": 2, "total_count": 64, "returned": 2,
        "remaining_count": 62, "has_more": True, "next_offset": 2,
        "all_records_returned": False, "page": 1, "pages_total": 32,
        "sum_amount": 385836.0,
        "items": [{"payment_id": 7262, "amount": "$501"}, {"payment_id": 7263, "amount": "$502"}],
    }}]
    answer = (
        "Here are the payments from the last 7 days (page 1 of 32, 10 per page):\n"
        "1. Payment 7262 - $501\n"
        "2. Payment 7263 - $502"
    )

    assert validate_numeric_claims(
        answer, results, "", "List the payments received in the last 7 days, 10 at a time."
    ) == []


def test_a_page_position_the_backend_did_not_compute_is_still_refused():
    results = [{"tool": "get_payments", "result": {
        "offset": 0, "limit": 10, "total_count": 64, "returned": 10,
        "remaining_count": 54, "has_more": True, "next_offset": 10,
        "all_records_returned": False, "page": 1, "pages_total": 7,
        "items": [],
    }}]

    errors = validate_numeric_claims("Page 3 of 9 has more.", results, "", "")
    assert "unsupported number 3" in errors
    assert "unsupported number 9" in errors


def test_prose_under_a_money_named_key_survives_formatting():
    """`sum_amount_covers` holds a sentence, not an amount. Money-formatting it
    turned the explanation of what the total spans into the figure "$0"."""
    from app.agents.chat_tools import format_chat_result

    note = "every matched payment record, not only the rows on this page"
    assert format_chat_result(note, "sum_amount_covers") == note
    assert format_chat_result(12500.0, "sum_amount") == "$12,500"
    assert format_chat_result("12500.0", "sum_amount") == "$12,500"


@pytest.mark.asyncio
async def test_the_page_position_is_computed_not_left_to_the_model():
    rows = [payment(index=index) for index in range(1, 13)]
    database = business_db(payments_flat=Collection(rows))

    page = await invoke_tool("get_payments", {"limit": 5, "offset": 5}, database=database)
    assert page["page"] == 2
    assert page["pages_total"] == 3
    assert page["total_count"] == 12


@pytest.mark.asyncio
async def test_a_listed_page_answers_instead_of_falling_back():
    """The exact live turn that used to fail end to end."""
    rows = [payment(index=index, amount=500.0 + index) for index in range(1, 8)]
    database = business_db(payments_flat=Collection(rows))

    listing = (
        "Here are the payments from the last 7 days (page 1 of 1, 10 per page):\n"
        "1. Payment 7251 - $501\n"
        "2. Payment 7252 - $502"
    )
    model = FakeModel([
        call("get_payments", {"date_from": TODAY.isoformat(), "date_to": TODAY.isoformat(), "limit": 10}),
        answer(listing),
    ])

    result = await execute_chat(
        "List the payments received in the last 7 days, 10 at a time.",
        [], model=model, database=database,
    )
    assert result["text"] == listing


def test_an_erp_field_name_written_literally_is_still_id_context():
    """Second occurrence of the original bug, new spelling.

    Live 2026-10-22: the model echoed the column name it was given and wrote
    "payment_id 7263". `\bpayment\b` cannot match inside "payment_id" - "_" is a
    word character - so the guard read a real id as a bare number, refused all
    27 of them, and the verified total was thrown away with them.
    """
    results = [{"tool": "get_payments", "result": {
        "offset": 0, "limit": 50, "total_count": 40, "returned": 27,
        "remaining_count": 13, "has_more": True, "next_offset": 27,
        "all_records_returned": False, "page": 1, "pages_total": 1,
        "sum_amount": 285651.0,
        "items": [{"payment_id": 7263, "customer_id": 6079, "amount": 250.0}],
    }}]

    assert validate_numeric_claims(
        "1) payment_id 7263 | Arch crown | amount $250", results, "", ""
    ) == []
    assert validate_numeric_claims(
        "customer_id 6079 paid", results, "", ""
    ) == []
    # an id the tools never returned is still refused, not waved through
    assert validate_numeric_claims("payment_id 99999", results, "", "") == ["unsupported id 99999"]


def test_a_quantity_never_borrows_its_legitimacy_from_a_field_name():
    """The fix widens which evidence set a number is checked against, not the
    evidence itself: a count phrased like an id is still a count."""
    results = [{"tool": "get_payments", "result": {
        "offset": 0, "limit": 10, "total_count": 40, "returned": 10,
        "remaining_count": 30, "has_more": True, "next_offset": 10,
        "all_records_returned": False, "items": [{"payment_id": 7263, "amount": 250.0}],
    }}]

    assert validate_numeric_claims("There were 12345 payments.", results, "", "") != []


# ---------------------------------------------------------------------------
# §long-list fix: the deterministic evidence summary that replaces the generic
# fallback when the model's prose is rejected. It states only what a tool
# computed, keeps pagination honest, and returns None when nothing financial was
# proved (so the plain fallback is preserved for genuinely unanswerable turns).
# ---------------------------------------------------------------------------

from app.agents.chat import _answer_consistency_errors, _evidence_summary  # noqa: E402


def test_evidence_summary_reports_a_page_honestly_not_the_whole_listing():
    results = [payments_result(ROWS, total=53, sum_amount=12500.0)]
    summary = _evidence_summary(results)

    assert summary == (
        "Verified from backend records: $12,500 across 53 matching payments; "
        "50 more are on later pages and can be fetched."
    )
    # never implies the rows were displayed or that the listing was complete
    assert "all 53" not in summary and "listed" not in summary and "shown" not in summary
    # the summary is itself supported by the same evidence
    assert validate_numeric_claims(summary, results, "", "") == []


def test_evidence_summary_says_all_only_when_the_walk_completed():
    results = [{"tool": "get_payments", "result": {
        "sum_amount": 12500.0, "offset": 0, "limit": 50, "total_count": 3, "returned": 3,
        "remaining_count": 0, "has_more": False, "all_records_returned": True,
        "items": [{"payment_id": row["id"], "amount": row["amount"], "received_at": row["received_at"],
                   "customer_id": row["customer"]["id"], "customer_name": row["customer"]["company_name"],
                   "invoices": []} for row in ROWS],
    }}]
    summary = _evidence_summary(results)

    assert "all 3 matching payments" in summary and "still to fetch" not in summary
    assert validate_numeric_claims(summary, results, "", "") == []


def test_evidence_summary_is_none_without_financial_or_record_evidence():
    """A briefing, or any tool that totalled nothing, must not be dressed up as a
    verified figure - the generic fallback still applies."""
    assert _evidence_summary([{"tool": "get_latest_briefing",
                               "result": {"run_id": None, "message": "No agent briefing is available."}}]) is None
    assert _evidence_summary([]) is None


@pytest.mark.asyncio
async def test_empty_prose_after_a_tool_returns_the_verified_summary():
    """The model ran the query but wrote nothing: answer from the evidence."""
    rows = [payment(index=index, amount=500.0 + index) for index in range(1, 13)]
    database = business_db(payments_flat=Collection(rows))
    model = FakeModel([call("get_payments", {"limit": 50}), answer("")])

    result = await execute_chat("How much did customers pay?", [], model=model, database=database)

    assert result["text"] == (
        f"Verified from backend records: {chat_tools.format_money(result['tool_results'][0]['result']['sum_amount'])} "
        "across all 12 matching payments."
    )
    assert "couldn't verify" not in result["text"]


# ---------------------------------------------------------------------------
# §5: a named period in the user's message is resolved on the server, so the
# model cannot re-decide it with its own (or a stale) date range.
#
# Frozen business date is Wed 2026-10-21, so "this week" is Monday 2026-10-19
# through Sunday 2026-10-25 - every turn, whatever dates the model passes.
# ---------------------------------------------------------------------------

THIS_WEEK = ("2026-10-19", "2026-10-25")


def week_db():
    rows = [
        payment(index=1, amount=1000.0, received_at="2026-10-19T10:00:00-05:00"),  # Mon, in week
        payment(index=2, amount=2000.0, received_at="2026-10-21T10:00:00-05:00"),  # Wed, in week
        payment(index=3, amount=300.0, received_at="2026-10-16T10:00:00-05:00"),   # prior Fri, NOT in week
    ]
    return business_db(payments_flat=Collection(rows))


@pytest.mark.asyncio
async def test_the_model_cannot_override_a_named_period_with_its_own_dates():
    """Question names "this week"; the model sends a different window anyway."""
    database = week_db()
    model = FakeModel([
        call("get_payments", {"date_from": "2026-10-16", "date_to": "2026-10-21", "limit": 50}),
        answer("Customers paid this week."),
    ])

    result = await execute_chat("What did customers pay this week?", [], model=model, database=database)

    filters = result["tool_results"][0]["result"]["filters_applied"]
    assert (filters["date_from"], filters["date_to"]) == THIS_WEEK
    # only the two in-week payments counted: the 2026-10-16 row is excluded
    assert result["tool_results"][0]["result"]["total_count"] == 2
    assert result["tool_results"][0]["result"]["sum_amount"] == "$3,000"


@pytest.mark.asyncio
async def test_the_same_phrase_resolves_to_the_same_window_whatever_the_model_sends():
    """No dates, wrong dates, an inconsistent period name: all land on one window."""
    for bogus in (
        {},
        {"date_from": "2026-01-01", "date_to": "2026-12-31"},
        {"period": "last_week"},
    ):
        database = week_db()
        model = FakeModel([call("get_payments", {**bogus, "limit": 50}), answer("This week.")])
        result = await execute_chat("What did customers pay this week?", [], model=model, database=database)
        filters = result["tool_results"][0]["result"]["filters_applied"]
        assert (filters["date_from"], filters["date_to"]) == THIS_WEEK, bogus


@pytest.mark.asyncio
async def test_a_date_the_user_typed_themselves_still_wins():
    """Explicit dates in the question are the user's, not a period to override."""
    database = week_db()
    model = FakeModel([
        call("get_payments", {"date_from": "2026-10-16", "date_to": "2026-10-21", "limit": 50}),
        answer("Chosen range."),
    ])

    result = await execute_chat(
        "What did customers pay from 2026-10-16 to 2026-10-21?",
        [], model=model, database=database,
    )

    filters = result["tool_results"][0]["result"]["filters_applied"]
    assert (filters["date_from"], filters["date_to"]) == ("2026-10-16", "2026-10-21")
    assert filters["date_to"] != THIS_WEEK[1]


@pytest.mark.asyncio
async def test_a_new_named_period_does_not_reuse_a_stale_recorded_range():
    """"what about this week?" after an older query must re-resolve, not continue."""
    database = week_db()
    stale = {
        "tool": "get_payments",
        "args": {"date_from": "2026-09-01", "date_to": "2026-09-30"},
        "offset": 0, "limit": 10, "total_count": 3, "returned": 3,
        "remaining_count": 0, "has_more": False, "next_offset": None,
        "question": "what did customers pay last month?",
    }
    model = FakeModel([
        call("get_payments", {"date_from": "2026-09-01", "date_to": "2026-09-30", "limit": 50}),
        answer("This week."),
    ])

    result = await execute_chat(
        "and this week?", [dict(question="q", answer="a")], model=model,
        database=database, last_query=stale,
    )

    filters = result["tool_results"][0]["result"]["filters_applied"]
    assert (filters["date_from"], filters["date_to"]) == THIS_WEEK
    assert "2026-09" not in str(filters)


# ---------------------------------------------------------------------------
# period wording + displayed-count consistency. Both only ADD rejections (the
# numeric guard is untouched): the model self-corrects on regeneration, or the
# turn falls through to the deterministic, backend-labelled evidence summary.
# ---------------------------------------------------------------------------

def _paged_results(total=53, sum_amount=12500.0):
    return [payments_result(ROWS, total=total, sum_amount=sum_amount)]


def test_consistency_rejects_a_period_word_that_contradicts_the_resolved_range():
    errs = _answer_consistency_errors("Here are this week’s payments, $12,500.", _paged_results(), "last_week")
    assert any("contradicts the resolved range" in e for e in errs)


def test_consistency_accepts_the_correct_period_word():
    assert _answer_consistency_errors("Here are last week’s payments, $12,500.", _paged_results(), "last_week") == []


def test_consistency_rejects_the_machine_period_echo():
    errs = _answer_consistency_errors("Payments (last_week): $12,500 across 53 records.", _paged_results(), "last_week")
    assert any("period code" in e for e in errs)


def test_consistency_rejects_a_displayed_count_larger_than_the_rows_present():
    text = "27 payments listed:\n- Payment 7251 $501\n- Payment 7252 $502\n- Payment 7253 $503"
    errs = _answer_consistency_errors(text, _paged_results(), None)
    assert any("only 3 appear" in e for e in errs)


def test_consistency_allows_a_displayed_count_that_matches_the_rows():
    text = "3 payments listed:\n- Payment 7251 $501\n- Payment 7252 $502\n- Payment 7253 $503"
    assert _answer_consistency_errors(text, _paged_results(), None) == []


def test_consistency_leaves_a_total_count_statement_alone():
    """"44 matching payments" is a total, not a claim of what was displayed."""
    assert _answer_consistency_errors("$X across 44 matching payments.", _paged_results(), "last_week") == []


def test_evidence_summary_labels_the_resolved_period_for_last_week():
    """The summary's wording comes from the backend period, not the model."""
    summary = _evidence_summary(_paged_results(), "last week")
    assert summary.startswith("Verified from backend records for last week:")
    assert "listed" not in summary and "shown" not in summary


def test_evidence_summary_rewords_coverage_without_claiming_display():
    summary = _evidence_summary(_paged_results(total=53), "this week")
    # reports the total and what remains, never how many rows it itself displayed
    assert "53 matching payments" in summary and "on later pages and can be fetched" in summary
    assert "This page lists" not in summary


@pytest.mark.asyncio
async def test_a_valid_correctly_worded_answer_is_not_replaced_by_the_summary():
    """When the prose already agrees with the evidence, it is kept verbatim."""
    rows = [
        payment(index=index, amount=500.0 + index, received_at="2026-10-15T12:00:00-05:00")
        for index in range(1, 8)
    ]
    database = business_db(payments_flat=Collection(rows))
    page = await chat_tools.get_payments(
        date_from=date(2026, 10, 12), date_to=date(2026, 10, 18), limit=50, database=database
    )
    claim = (
        f"Last week (2026-10-12 to 2026-10-18): {chat_tools.format_money(page['sum_amount'])} "
        f"across {page['total_count']} matching payments. Payment {page['items'][0]['payment_id']} led."
    )
    model = FakeModel([call("get_payments", {"period": "last_week", "limit": 50}), answer(claim)])

    result = await execute_chat("What did customers pay last week?", [], model=model, database=database)

    assert result["text"] == claim
    assert not result["text"].startswith("Verified from backend records")


@pytest.mark.asyncio
async def test_show_the_rest_continuation_reports_remaining_honestly():
    """A continuation page keeps the date range and states records still outstanding
    rather than claiming the whole set was listed."""
    rows = [
        payment(index=index, amount=100.0, received_at="2026-10-15T12:00:00-05:00")
        for index in range(1, 26)
    ]
    database = business_db(payments_flat=Collection(rows))
    stale = {
        "tool": "get_payments",
        "args": {"date_from": "2026-10-12", "date_to": "2026-10-18", "limit": 10},
        "offset": 0, "limit": 10, "total_count": 25, "returned": 10,
        "remaining_count": 15, "has_more": True, "next_offset": 10,
        "question": "who paid last week?",
    }
    model = FakeModel([
        call("get_payments", {"date_from": "2026-10-12", "date_to": "2026-10-18", "limit": 10, "offset": 10}),
        answer("More last-week payments: Payment 7260."),
    ])

    result = await execute_chat("show the rest", [], model=model, database=database, last_query=stale)
    page = result["tool_results"][0]["result"]

    # the recorded range is continued, not widened
    assert (page["filters_applied"]["date_from"], page["filters_applied"]["date_to"]) == ("2026-10-12", "2026-10-18")
    assert page["total_count"] == 25
    assert page["returned"] + page["remaining_count"] <= page["total_count"]
    assert page["all_records_returned"] is False or page["remaining_count"] == 0

