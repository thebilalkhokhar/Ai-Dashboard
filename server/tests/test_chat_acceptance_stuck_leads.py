"""Acceptance: the stuck-leads conversation of Oct 23, 2026, end to end.

The live first answer quoted a real lead record - a lead whose company is
"Flat pouch 25k" - and the numeric guard read the quotation as the invented
claim 25,000, discarded the answer, and after one identical regeneration
returned "I couldn't verify that figure from the available data." The fourth
form of the bug lived inside the third patch: the verbatim rescue matched
against tool-returned strings with every space removed, so "Flat pouch 25k"
became "flatpouch25k" and the left-boundary check saw the quote as the tail
of a longer word.

These tests pin both halves of the fix:
  * the guard accepts a real token that follows a word ("pouch 25k",
    "HP indigo 25K") while still refusing a different figure and money;
  * a fresh stuck-leads chat serves page 1, then "yes" serves page 2 of the
    SAME stuck list, then the literal phrase "show next page" executes the
    deterministic continuation to page 3 - never a wider unfiltered query -
    and the invented-figure guard still discards a fabricated total.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agents.chat import _confirmation_kind, _evidence_summary, run_chat, validate_numeric_claims
from app.agents.chat_tools import invoke_tool
from test_chat import FakeCollection, FakeModel
from test_chat_data_tools import Collection, business_db, lead, payment, tool_call

REAL_NOW = datetime.now(timezone.utc)
STUCK_TOTAL = 60
UNFILTERED_TOTAL = STUCK_TOTAL + 100
FLAT_POUCH_LEAD_ID = "6374650000073956008"


def stuck_lead(index: int, **over) -> dict:
    """A realistic roster row: JSON description, recent activity, open desk."""
    row = lead(
        zohoLeadId=f"6374650000073956{index:03d}",
        firstName=f"Lead{index}",
        lastName="Bentley",
        status="Attempted to Contact",
        createdAt=(REAL_NOW - timedelta(days=30)).isoformat(),
        lastActivityAt=(REAL_NOW - timedelta(days=3.2)).isoformat(),
        description=(
            '{"product":"mylar bag","product_type":"stand-up pouch",'
            '"quantity":"500","dimensions":{"width":"4","height":"6","unit":"inch"},'
            '"artwork":[]}'
        ),
    )
    row.update(over)
    return row


def stuck_db():
    stuck = [stuck_lead(i) for i in range(1, STUCK_TOTAL + 1)]
    # The live record shape: the token is the tail of a written phrase, not a
    # standalone field value. This is what the old rescue could not match.
    stuck[7] = stuck_lead(
        8,
        firstName="Thomas",
        lastName="Wall Sr.",
        company="Flat pouch 25k",
        description=(
            '{"product":"Pouch","quantity":"25k",'
            '"dimensions":{"width":"4","height":"6","unit":"In"},'
            '"artwork":[]}'
        ),
    )
    others = [
        lead(
            zohoLeadId=f"6374650000099999{i:03d}",
            firstName=f"Other{i}",
            status="Qualified",
            createdAt=(REAL_NOW - timedelta(days=1)).isoformat(),
            lastActivityAt=(REAL_NOW - timedelta(hours=2)).isoformat(),
        )
        for i in range(UNFILTERED_TOTAL - STUCK_TOTAL)
    ]
    return business_db(leads=Collection(stuck + others), chat_logs=FakeCollection([]))


def thread_doc(database, thread_id="thread-1"):
    return next(row for row in database.chat_threads.rows if row["thread_id"] == thread_id)


def seeded_tool_messages(model) -> list[ToolMessage]:
    return [
        message
        for message in model.seen_messages[0]
        if isinstance(message, ToolMessage)
    ]


# --------------------------------------------------------------- the guard

REAL_PAGE_RESULT = {
    "tool": "get_stuck_leads",
    "result": {
        "days": 14,
        "items": [
            {
                "lead_id": "6374650000073956008",
                "contact_name": "Thomas Wall Sr.",
                "company": "Flat pouch 25k",
                "status": "Attempted to Contact",
                "days_since_activity": 3,
                "product": "Pouch",
                # the real row's quantity is stored as the string "25k"
                "quantity": "25k",
                "size": "4 x 6 In",
                "material": None,
                "finish": None,
                "artwork": "Not stated",
                "missing": ["material", "finish"],
            },
            {
                "lead_id": "6374650000073912055",
                "contact_name": "Hedi Azri",
                "company": None,
                "status": "Attempted to Contact",
                "days_since_activity": 3,
                "product": "sacs personnalisés",
                "quantity": "2 000 unités réparties en deux designs",
                "size": "160 × 260 × 80 mm",
                "material": None,
                "finish": None,
            },
        ],
        "offset": 0,
        "limit": 20,
        "total_count": 560,
        "returned": 2,
        "remaining_count": 558,
        "has_more": True,
        "next_offset": 2,
        "all_records_returned": False,
        "page": 1,
        "pages_total": 280,
    },
}

REAL_ANSWER = (
    "Here are the leads that need follow-up (page 1 of 280).\n\n"
    "- Lead 6374650000073956008 — Thomas Wall Sr. — company: Flat pouch 25k — "
    "status: Attempted to Contact — product: Pouch — quantity: 25k — size: 4 x 6 In — "
    "missing: material, finish — days since activity: 3\n"
    "- Lead 6374650000073912055 — Hedi Azri — quantity: 2,000 units in two designs — "
    "size: 160 × 260 × 80 mm — days since activity: 3\n\n"
    "560 stuck leads match in total; 2 are on this page and 558 remain."
)


def test_real_flat_pouch_25k_row_is_a_quotation_not_an_invented_number():
    """The live failure itself: a correct page answer quoting '25k' passes."""
    assert validate_numeric_claims(
        REAL_ANSWER, [REAL_PAGE_RESULT], "", "Show me the leads that need follow-up."
    ) == []


def test_verbatim_rescue_keeps_word_boundaries_and_refuses_new_figures():
    results = [
        {
            "tool": "get_stuck_leads",
            "result": {
                "items": [
                    {"company": "Flat pouch 25k", "size": "360gsm", "note": "HP indigo 25K"},
                    {"quantity": "2 000 unités"},
                    {"size": "1360mm"},
                ]
            },
        }
    ]
    # a token after a word is a quotation, in any case/spacing form
    assert validate_numeric_claims("Company: Flat pouch 25k.", results) == []
    assert validate_numeric_claims("Quantity: 25 k.", results) == []
    assert validate_numeric_claims("Quantity: 2,000 units.", results) == []
    assert validate_numeric_claims("Size: 360.", results) == []
    # a different figure is still invented
    assert validate_numeric_claims("Company: Flat pouch 40k.", results)
    # the tail of a longer number is not a quote of it
    longer = [{"tool": "get_leads", "result": {"items": [{"size": "1360mm"}]}}]
    assert validate_numeric_claims("Size: 360.", longer)
    # money stays strict: a quoted budget string mints no amount
    assert validate_numeric_claims("The budget is $25,000.", results)


# ------------------------------------------------- the whole conversation


@pytest.mark.anyio
async def test_stuck_leads_pages_1_to_3_survive_the_numeric_guard():
    database = stuck_db()
    first = FakeModel(
        [
            tool_call("get_stuck_leads", {"limit": 20}),
            # the wider, unfiltered call the model also makes to sanity-check
            # the total - it must not become the recorded continuation
            tool_call("get_leads", {"limit": 20}, index=2),
            AIMessage(
                content=(
                    "Here are the leads that need follow-up (page 1 of 3).\n\n"
                    f"- Lead {FLAT_POUCH_LEAD_ID} — Thomas Wall Sr. — company: Flat pouch 25k — "
                    "status: Attempted to Contact — product: Pouch — quantity: 25k — "
                    "size: 4 x 6 In — missing: material, finish — days since activity: 3\n"
                    "- Lead 6374650000073956002 — Lead2 Bentley — company: Zaskii Boyz — "
                    "status: Attempted to Contact — quantity: 500 — days since activity: 3\n\n"
                    "60 stuck leads in this view; 20 are listed here and 40 remain. "
                    "Would you like the next page?"
                )
            ),
        ]
    )
    result = await run_chat(
        "thread-1", "Show me the leads that need follow-up.", database=database, model=first
    )

    # No fallback: the real record text is a quotation, not an invented figure.
    assert result["text"].startswith("Here are the leads that need follow-up")
    assert "couldn't verify" not in result["text"]

    pending = thread_doc(database)["pending_action"]
    assert pending["kind"] == "next_page"
    assert pending["tool"] == "get_stuck_leads"
    assert pending["total_count"] == STUCK_TOTAL
    assert pending["next_offset"] == 20
    assert pending["args"].get("stuck_only") is not True
    # the recorded continuation is the 60-lead stuck view, not the 160-lead
    # unfiltered call that happened last
    assert thread_doc(database)["last_query"]["total_count"] == STUCK_TOTAL

    # ---- "yes": page 2 of the same stuck list, no fallback -----------------
    second = FakeModel([AIMessage(content="Here are the next 20 of the 60 stuck leads; 20 remain.")])
    events: asyncio.Queue = asyncio.Queue()
    result = await run_chat("thread-1", "yes", database=database, model=second, events=events)

    frames = []
    while not events.empty():
        frames.append(events.get_nowait())
    evidence_frames = [
        frame["data"] for frame in frames if frame.get("event") == "tool_result"
    ]
    assert any(frame.get("records_total") == STUCK_TOTAL for frame in evidence_frames)
    assert not any(frame.get("records_total") == UNFILTERED_TOTAL for frame in evidence_frames)

    seeded = seeded_tool_messages(second)
    assert any('"total_count": 60' in message.content for message in seeded)
    assert any('"offset": 20' in message.content for message in seeded)
    # the continuation keeps the page size the user is reading: page 2, not a
    # relabelled 50-row batch
    assert any('"limit": 20' in message.content for message in seeded)
    assert any('"page": 2' in message.content for message in seeded)
    assert not any('"total_count": 160' in message.content for message in seeded)
    assert result["text"].startswith("Here are the next 20")
    assert "couldn't verify" not in result["text"]
    # the thread record moved to the page just fetched, and the remainder
    # stays continuable
    assert thread_doc(database)["last_query"]["offset"] == 20
    assert thread_doc(database)["pending_action"]["next_offset"] == 40

    # ---- "show next page": deterministic continuation to page 3 -----------
    third = FakeModel([AIMessage(content="Here are the last 20 of the 60 stuck leads.")])
    result = await run_chat("thread-1", "show next page", database=database, model=third)

    assert _confirmation_kind("show next page") == "next_page"
    seeded = seeded_tool_messages(third)
    assert any('"offset": 40' in message.content for message in seeded)
    assert any('"total_count": 60' in message.content for message in seeded)
    assert any('"has_more": false' in message.content for message in seeded)
    assert any('"page": 3' in message.content for message in seeded)
    assert result["text"] == "Here are the last 20 of the 60 stuck leads."
    assert thread_doc(database)["pending_action"] is None


@pytest.mark.anyio
async def test_both_stuck_lead_views_page_the_same_sequence():
    """get_stuck_leads and get_leads(stuck_only) describe one list.

    They report the same total, so the continuation anchor cannot tell them
    apart by count alone. If they paged in different orders, page 2 of the
    view the answer listed could repeat or skip rows. One stable order (age,
    then lead id) makes whichever view the anchor picks serve page 2 of the
    same sequence.
    """
    database = stuck_db()
    for offset in (0, 20, 40):
        stuck = await invoke_tool("get_stuck_leads", {"limit": 20, "offset": offset}, database=database)
        leads = await invoke_tool(
            "get_leads", {"stuck_only": True, "limit": 20, "offset": offset}, database=database
        )
        assert stuck["total_count"] == leads["total_count"] == STUCK_TOTAL
        assert [row["lead_id"] for row in stuck["items"]] == [
            row["lead_id"] for row in leads["items"]
        ], f"the two stuck views disagree at offset {offset}"


@pytest.mark.asyncio
async def test_a_nameless_lead_ships_not_stated_instead_of_null():
    """Live 2026-10-26: a lead with no name on file was answered as
    'Contact: (null)' - the model renders a null it receives literally,
    which the empty-fields rule forbids. Dropping the keys outright was
    tried next, but the Oct 26 retest showed the model then fills the
    slot by copying a neighbour row's company (Canyon Plastics on 7
    rows). Identity fields therefore always carry the explicit label
    'Not stated', which the model reproduces faithfully (as artwork)."""
    nameless = lead(firstName="", lastName="", company="")
    db = business_db(leads=Collection([nameless]), chat_logs=FakeCollection([]))
    result = await invoke_tool("get_stuck_leads", {}, database=db)
    assert result["total_count"] == 1
    item = result["items"][0]
    assert item["contact_name"] == "Not stated"
    assert item["company"] == "Not stated"
    assert None not in item.values()


def test_evidence_summary_keeps_a_page_total_even_with_no_money():
    """A leads page computes its total_count but no money; the replaced answer
    must still state the verified count instead of the generic apology."""
    summary = _evidence_summary([REAL_PAGE_RESULT])
    assert summary == (
        "Verified from backend records: 560 matching leads; "
        "558 more are on later pages and can be fetched."
    )
    assert validate_numeric_claims(summary, [REAL_PAGE_RESULT], "", "") == []


@pytest.mark.anyio
async def test_a_rejected_leads_answer_is_replaced_by_its_verified_count():
    database = stuck_db()
    invented = "There are 999 stuck leads."
    model = FakeModel([
        tool_call("get_stuck_leads", {"limit": 20}),
        AIMessage(content=invented),
        AIMessage(content=invented),
    ])
    result = await run_chat(
        "thread-1", "Show me the leads that need follow-up.", database=database, model=model
    )

    assert "999" not in result["text"]
    assert result["validation_replaced"] is True
    # the backend-computed total survives the discarded prose
    assert "60 matching leads" in result["text"]
    assert "40 more are on later pages" in result["text"]


@pytest.mark.anyio
async def test_an_invented_total_is_still_discarded_in_a_follow_up():
    """The strictness control: the guard was never loosened for money."""
    database = business_db(
        payments_flat=Collection([payment(index=index) for index in range(1, 61)])
    )
    first = FakeModel([
        tool_call("get_payments", {"limit": 50}),
        AIMessage(content="First page delivered."),
    ])
    await run_chat("thread-1", "List payments.", database=database, model=first)
    assert thread_doc(database)["pending_action"]["kind"] == "next_page"

    invented = "The total is $999,999 across 9,999 payments."
    second = FakeModel([AIMessage(content=invented), AIMessage(content=invented)])
    result = await run_chat("thread-1", "yes", database=database, model=second)

    assert "999,999" not in result["text"]
    assert "9,999" not in result["text"]
    assert result["validation_replaced"] is True
    # and the failed confirmation cannot chain the same page forward
    assert thread_doc(database)["pending_action"] is None
