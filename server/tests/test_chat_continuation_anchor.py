"""Regression: the leads follow-up failure of Oct 23, 2026.

Live transcript: "Show me the leads that need follow-up" -> a correct
filtered page (576 of 18,091) with a "fetch the next page?" offer -> the
user said "yes" -> the backend fetched the next page of the WRONG query
(26 of 18,091 matched, unfiltered) -> the model's answer mixed the two
views, failed validation twice, and was replaced by "I couldn't verify
that figure from the available data." -> the pending chained the same
wrong page forward, so the second "yes" failed identically. The user
could never escape the loop.

Root causes pinned here:
  1. The recorded continuation was simply the LAST paged call of the turn,
     even when the answer's offer was about an earlier, filtered call.
     The record is now anchored to the delivered answer.
  2. A confirmed retrieval whose answer the validator discards must not
     chain: the pending action and the recorded query are cleared, so the
     next message re-derives the follow-up from the conversation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agents.chat import run_chat
from test_chat import FakeCollection, FakeModel
from test_chat_data_tools import business_db, lead, payment, tool_call

# Real clock, not the shared fixtures' fixed NOW: the tools compute lead
# ages against the real now, and the scripted answers below state those
# ages exactly - a fixed fixture clock would drift days away from them.
REAL_NOW = datetime.now(timezone.utc)


def stuck_lead(index: int) -> dict:
    return lead(
        zohoLeadId=f"6374650000073956{index:03d}",
        firstName=f"Lead{index}",
        status="Attempted to Contact",
        createdAt=(REAL_NOW - timedelta(days=30)).isoformat(),
        lastActivityAt=(REAL_NOW - timedelta(days=3.0 + index * 0.1)).isoformat(),
    )


def leads_db():
    stuck = [stuck_lead(i) for i in range(30)]
    others = [
        lead(
            zohoLeadId=f"6374650000099999{i:03d}",
            firstName=f"Other{i}",
            status="Qualified",
            createdAt=(REAL_NOW - timedelta(days=1)).isoformat(),
            lastActivityAt=(REAL_NOW - timedelta(hours=2)).isoformat(),
        )
        for i in range(70)
    ]
    return business_db(leads=FakeCollection(stuck + others))


def lead_line(index: int) -> str:
    return (
        f"Lead 6374650000073956{index:03d} — Lead{index} Bentley — product: mylar bag — "
        f"dayssinceactivity: {3.0 + index * 0.1:.1f} — status: Attempted to Contact — "
        "missing: size, material, finish"
    )


def thread_doc(database, thread_id="thread-1"):
    return next(row for row in database.chat_threads.rows if row["thread_id"] == thread_id)


@pytest.mark.anyio
async def test_yes_continues_the_filtered_list_the_answer_offered():
    database = leads_db()
    first = FakeModel([
        # the asked-for list first, then a wider call to check the total -
        # the wider call is the LAST paged call of the turn
        tool_call("get_leads", {"stuck_only": True, "limit": 10}),
        tool_call("get_leads", {"limit": 10}),
        AIMessage(content=(
            "Here are the leads that need follow-up (first page of results).\n\n"
            + "\n".join(lead_line(i) for i in range(10))
            + "\n\nSummary:\nTotal leads in this view: 30\nLeads returned here: 10\n"
              "More leads available: yes (has_more = true)\n"
              "Would you like me to fetch the next page of results?"
        )),
        AIMessage(content="Here are the leads that need follow-up."),
    ])
    await run_chat("thread-1", "Show me the leads that need follow-up.", database=database, model=first)

    # the continuation record is the filtered list the answer offered -
    # not the wider, unfiltered call that happened to run last
    pending = thread_doc(database)["pending_action"]
    assert pending["kind"] == "next_page"
    assert pending["tool"] == "get_leads"
    assert pending["args"].get("stuck_only") is True
    assert pending["total_count"] == 30
    assert pending["next_offset"] == 10

    page_two = "\n".join(lead_line(i) for i in range(10, 30))
    second = FakeModel([
        AIMessage(content=(
            "Here are the next leads that need follow-up.\n\n"
            + page_two
            + "\n\nTotal leads in this view: 30"
        )),
    ])
    result = await run_chat("thread-1", "yes", database=database, model=second)

    # the backend fetched the filtered query's next page, and the model's
    # answer over it survived validation - no fallback text
    seeded = [
        message.content for message in second.seen_messages[0]
        if isinstance(message, ToolMessage)
    ]
    assert any('"total_count": 30' in content for content in seeded)
    assert any('"offset": 10' in content for content in seeded)
    assert result["text"].startswith("Here are the next leads that need follow-up.")
    assert "couldn't verify" not in result["text"]
    # the filtered set is fully delivered now, so nothing stays pending
    assert thread_doc(database)["pending_action"] is None


@pytest.mark.anyio
async def test_next_page_continues_the_stuck_leads_list_not_a_wider_call():
    """Second live form of the Oct 23 failure: the answer came from
    get_stuck_leads, which recorded NO continuation at all (a head-only
    top-N result), while the same turn's wider unfiltered get_leads call
    became the recorded query - so "show next page" fetched page 2 of all
    18,091 leads. get_stuck_leads is a paged tool now: its own context is
    recorded, the anchor selects it, and the follow-up serves the stuck
    list itself."""
    database = leads_db()
    first = FakeModel([
        tool_call("get_stuck_leads", {"limit": 10}),
        tool_call("get_leads", {"limit": 10}),
        AIMessage(content=(
            "Here are the stuck leads needing follow-up (this page, 10 of 30). "
            "Want me to fetch the next page?"
        )),
        AIMessage(content="Here are the stuck leads needing follow-up."),
    ])
    await run_chat("thread-1", "Show me the leads that need follow-up.", database=database, model=first)

    pending = thread_doc(database)["pending_action"]
    assert pending["kind"] == "next_page"
    assert pending["tool"] == "get_stuck_leads"
    assert pending["total_count"] == 30
    assert pending["next_offset"] == 10

    second = FakeModel([
        AIMessage(content="Here is the next page of stuck leads (30 total)."),
    ])
    result = await run_chat("thread-1", "show next page", database=database, model=second)

    seeded = [
        message.content for message in second.seen_messages[0]
        if isinstance(message, ToolMessage)
    ]
    assert any('"total_count": 30' in content for content in seeded)
    assert any('"offset": 10' in content for content in seeded)
    assert result["text"] == "Here is the next page of stuck leads (30 total)."
    # the whole stuck list was delivered by the confirmed page
    assert thread_doc(database)["pending_action"] is None


@pytest.mark.anyio
async def test_a_discarded_confirmation_answer_breaks_the_loop():
    database = business_db(
        payments_flat=FakeCollection([payment(i) for i in range(1, 61)])
    )
    first = FakeModel([
        tool_call("get_payments", {"limit": 50}),
        AIMessage(content="First page delivered."),
    ])
    await run_chat("thread-1", "List payments.", database=database, model=first)
    assert thread_doc(database)["pending_action"]["kind"] == "next_page"

    # the model answers the confirmed page with figures nothing supports -
    # twice, so the validator discards the prose and replaces the answer
    second = FakeModel([
        AIMessage(content="The total is $999,999 across 9,999 payments."),
        AIMessage(content="The total is $999,999 across 9,999 payments."),
    ])
    await run_chat("thread-1", "fetch the next page", database=database, model=second)

    doc = thread_doc(database)
    assert doc["pending_action"] is None
    assert doc["last_query"] is None

    # and the next "yes" is a clean turn, not the same broken retrieval again
    third = FakeModel([AIMessage(content="Starting fresh.")])
    result = await run_chat("thread-1", "yes", database=database, model=third)
    assert result["text"] == "Starting fresh."
    seeded = [
        message for message in third.seen_messages[0]
        if isinstance(message, ToolMessage)
    ]
    assert seeded == []
