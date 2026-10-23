"""RC3/RC4: record pages are truthful, so every matching row stays reachable.

The scenario these tests pin down is the one that broke: 34 payments matched and
only 19 rows came back, while the answer still read as if the list were complete.
A page must say exactly how far it got, hand back a cursor that continues from
the first row it did not deliver, and let the caller walk to the end without
serving a row twice or looping on the same offset.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.agents import chat_tools
from app.agents.chat_tools import DEFAULT_TOOL_ROWS, MAX_TOOL_ROWS, invoke_tool
from test_chat_data_tools import (
    NOW,
    Collection,
    business_db,
    lead,
    order,
    payment,
    quote,
)

TODAY = date(2026, 10, 21)
PAGE_KEYS = (
    "offset",
    "limit",
    "total_count",
    "returned",
    "remaining_count",
    "has_more",
    "next_offset",
    "all_records_returned",
)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    for module in (chat_tools,):
        monkeypatch.setattr(module, "datetime", FrozenDateTime)


async def walk(tool_name: str, *, database, page_size: int = 10, **filters) -> tuple[list[dict], list[dict]]:
    """Follow ``next_offset`` to the end the way the model is told to.

    Returns every row the pages delivered, plus the metadata of each page. The
    loop is bounded and refuses a cursor that does not move forward, so a
    repeated-offset bug fails here instead of hanging the suite.
    """
    rows: list[dict] = []
    pages: list[dict] = []
    offset = 0
    for _ in range(40):
        page = await invoke_tool(tool_name, {**filters, "limit": page_size, "offset": offset}, database=database)
        pages.append(page)
        rows.extend(page["items"])
        assert page["returned"] == len(page["items"])
        assert page["offset"] == offset
        assert page["total_count"] >= len(page["items"])
        if not page["has_more"]:
            assert page["next_offset"] is None
            assert page["remaining_count"] == 0
            return rows, pages
        assert page["next_offset"] > offset, "the cursor did not advance - paging would loop"
        assert page["remaining_count"] == page["total_count"] - page["next_offset"]
        offset = page["next_offset"]
    raise AssertionError("paging never reached the end of the result set")


# ------------------------------------------------------------- the 34 rows


@pytest.mark.asyncio
async def test_every_matching_payment_is_reachable_across_pages():
    rows = [payment(index=index) for index in range(1, 35)]
    database = business_db(payments_flat=Collection(rows))

    listed, pages = await walk("get_payments", database=database, page_size=10)

    assert [page["total_count"] for page in pages] == [34, 34, 34, 34]
    assert [len(page["items"]) for page in pages] == [10, 10, 10, 4]
    assert [page["remaining_count"] for page in pages] == [24, 14, 4, 0]
    assert [page["has_more"] for page in pages] == [True, True, True, False]
    # the last page is the only one allowed to claim completeness
    assert [page["all_records_returned"] for page in pages] == [False, False, False, True]
    # 34 rows in, 34 distinct rows out, in one stable order
    assert len(listed) == 34
    assert len({row["payment_id"] for row in listed}) == 34


@pytest.mark.asyncio
async def test_a_requested_limit_is_authoritative_not_just_a_suggestion():
    rows = [payment(index=index) for index in range(1, 35)]
    database = business_db(payments_flat=Collection(rows))

    page = await invoke_tool("get_payments", {"limit": 5}, database=database)

    assert len(page["items"]) == 5
    assert page["returned"] == 5
    assert page["total_count"] == 34
    assert page["remaining_count"] == 29
    assert page["has_more"] is True
    assert page["next_offset"] == 5
    assert page["all_records_returned"] is False

    greedy = await invoke_tool("get_payments", {"limit": 500}, database=database)
    assert greedy["limit"] == MAX_TOOL_ROWS
    # 50 rows were asked for and 34 exist, but the context budget still decides
    # how many fit in one answer - and says so instead of pretending
    assert greedy["returned"] == len(greedy["items"]) <= MAX_TOOL_ROWS
    assert greedy["total_count"] == 34
    if greedy["returned"] < 34:
        assert greedy["has_more"] is True
        assert greedy["next_offset"] == greedy["returned"]
        assert greedy["page_shorter_than_requested"] is True
    else:
        assert greedy["has_more"] is False
        assert greedy["all_records_returned"] is True


@pytest.mark.asyncio
async def test_a_truncated_page_says_so_and_still_continues_without_gaps():
    """The real defect: the context budget drops rows after the page was sliced.

    34 rows asked for at once cannot fit the budget, so the page must shrink to
    what it can carry and hand back a cursor for the rows it dropped - never a
    ``next_offset`` that walks past rows the model never saw.
    """
    rows = [
        payment(
            index=index,
            payment_ref="REF-" + f"{index:04d}" + "-padding" * 12,
            payment_details=[
                {
                    "invoice_id": 33563 + slot,
                    "amount": "500.00",
                    "invoice": {
                        "id": 33563 + slot,
                        "inv_ref": f"3356{slot}-2026-10-padding-padding-padding",
                        "date": "2026-10-21 09:56:19",
                    },
                }
                for slot in range(6)
            ],
        )
        for index in range(1, 35)
    ]
    database = business_db(payments_flat=Collection(rows))

    first = await invoke_tool("get_payments", {"limit": 34}, database=database)

    assert first["total_count"] == 34
    assert first["returned"] == len(first["items"]) < 34
    assert first["has_more"] is True
    assert first["all_records_returned"] is False
    assert first["page_shorter_than_requested"] is True
    assert first["suggested_limit"] == first["returned"]
    assert first["truncated"] is True

    listed, _ = await walk("get_payments", database=database, page_size=34)
    assert len(listed) == 34
    assert len({row["payment_id"] for row in listed}) == 34


@pytest.mark.asyncio
async def test_equal_sort_keys_cannot_shuffle_rows_between_pages():
    """Rows identical on every sort field are still one stable sequence."""
    rows = [
        payment(index=index, received_at="2026-10-21T00:00:00-05:00", amount=500.0)
        for index in range(1, 13)
    ]
    database = business_db(payments_flat=Collection(rows))

    first_page = await invoke_tool("get_payments", {"limit": 6, "offset": 0}, database=database)
    second_page = await invoke_tool("get_payments", {"limit": 6, "offset": 6}, database=database)
    again = await invoke_tool("get_payments", {"limit": 6, "offset": 0}, database=database)

    assert [row["payment_id"] for row in first_page["items"]] == [row["payment_id"] for row in again["items"]]
    overlap = {row["payment_id"] for row in first_page["items"]} & {row["payment_id"] for row in second_page["items"]}
    assert overlap == set()
    assert len({row["payment_id"] for row in first_page["items"] + second_page["items"]}) == 12


@pytest.mark.asyncio
async def test_an_offset_past_the_end_reports_nothing_left_instead_of_repeating():
    rows = [payment(index=index) for index in range(1, 8)]
    database = business_db(payments_flat=Collection(rows))

    page = await invoke_tool("get_payments", {"limit": 10, "offset": 40}, database=database)

    assert page["items"] == []
    assert page["returned"] == 0
    assert page["total_count"] == 7
    assert page["has_more"] is False
    assert page["next_offset"] is None
    assert "past the end" in page["note"]


@pytest.mark.asyncio
async def test_an_empty_result_set_is_reported_as_complete():
    page = await invoke_tool("get_payments", {"customer": "Nobody Exists"}, database=business_db())

    assert page["items"] == []
    assert page["total_count"] == 0
    assert page["returned"] == 0
    assert page["has_more"] is False
    assert page["all_records_returned"] is True
    assert "No customer named" in page["note"]


@pytest.mark.asyncio
async def test_totals_cover_the_whole_set_while_items_hold_one_page():
    rows = [payment(index=index) for index in range(1, 35)]
    database = business_db(payments_flat=Collection(rows))

    page = await invoke_tool("get_payments", {"limit": 10}, database=database)

    # the money figure is computed over all 34 matches, so it can never change
    # between pages of the same query
    assert page["sum_amount"] == chat_tools.format_money(sum(500.0 + index for index in range(1, 35)))
    assert len(page["items"]) == 10


# ------------------------------------------------- filters survive paging


@pytest.mark.asyncio
async def test_paging_keeps_the_date_range_and_sort_of_the_original_query():
    rows = [
        payment(
            index=index,
            # received_at carries the offset, so the window has to be read from
            # the parsed timestamp rather than the raw string
            received_at=(NOW - timedelta(days=index)).astimezone(timezone(timedelta(hours=-5))).isoformat(),
            amount=500.0 + index,
        )
        for index in range(1, 26)
    ]
    database = business_db(payments_flat=Collection(rows))
    filters = {"date_from": (TODAY - timedelta(days=10)).isoformat(), "date_to": TODAY.isoformat()}

    listed, pages = await walk("get_payments", database=database, page_size=4, **filters)

    inside = [row for row in rows if TODAY - timedelta(days=10) <= _received_date(row) <= TODAY]
    assert {page["total_count"] for page in pages} == {len(inside)}
    assert len(listed) == len(inside)
    assert pages[0]["filters_applied"]["date_from"] == filters["date_from"]
    assert pages[-1]["filters_applied"]["date_to"] == filters["date_to"]
    received = [_received_date(row) for row in listed]
    assert received == sorted(received, reverse=True)


def _received_date(row: dict) -> date:
    return datetime.fromisoformat(row["received_at"]).date()


# ----------------------------------------------- the other record tools


@pytest.mark.asyncio
async def test_invoices_orders_quotes_and_leads_all_page():
    invoices = [
        order(id=33563 + index, inv_ref=f"{33563 + index}-2026-10", total_amount=1000.0 + index)
        for index in range(1, 23)
    ]
    database = business_db(orders_flat=Collection(invoices))

    listed, pages = await walk("get_invoices", database=database, page_size=8)
    assert len(listed) == 22
    assert len({row["invoice_id"] for row in listed}) == 22
    assert pages[0]["returned"] == 8

    listed, pages = await walk("get_orders", database=database, page_size=8)
    assert len(listed) == 22
    assert len({row["invoice_id"] for row in listed}) == 22

    quotes = [
        quote(id=20791 + index, ref=f"{20791 + index}-2026-10", amount=100.0 + index)
        for index in range(1, 15)
    ]
    listed, _ = await walk("get_quotes", database=business_db(quotes_flat=Collection(quotes)), page_size=5)
    assert len(listed) == 14
    assert len({row["quote_id"] for row in listed}) == 14

    leads = [
        lead(zohoLeadId=f"L-{index:02d}", lastActivityAt=(NOW - timedelta(days=index)).isoformat())
        for index in range(1, 15)
    ]
    listed, _ = await walk("get_leads", database=business_db(leads=Collection(leads)), page_size=5)
    assert len(listed) == 14
    assert len({row["lead_id"] for row in listed}) == 14


@pytest.mark.asyncio
async def test_search_pages_over_one_flat_sequence_of_matches():
    rows = [
        payment(index=index, payment_ref=f"725{index}-1969-12", customer={"id": 6079, "company_name": "Picazo Cleaning Services"})
        for index in range(1, 13)
    ]
    database = business_db(payments_flat=Collection(rows))

    listed, pages = await walk(
        "search_records", database=database, page_size=5, query="Picazo", collections=["payments"]
    )

    assert len(listed) == 12
    assert len({row["id"] for row in listed}) == 12
    assert all(row["collection"] == "payments" for row in listed)
    assert pages[0]["matches_by_collection"] == {"payments": 12}


@pytest.mark.asyncio
async def test_briefing_drafts_page_as_one_sequence_without_repeats():
    """get_agent_briefing groups drafts by kind, but its cursor walks them flat."""
    drafts = {
        kind: [
            {"customer_name": f"Customer {index}", "kind": kind, "body": f"draft {index}"}
            for index in range(1, 5)
        ]
        for kind in ("negotiation", "collections", "lead_followup")
    }
    run = {
        "run_id": "run-1",
        "finished_epoch": NOW.timestamp(),
        "started_at": NOW.isoformat(),
        "counts": {"negotiation": 4, "collections": 4, "lead_followup": 4},
        "master": {"bullets": ["Revenue is up"], "priorities": [], "conflicts_resolved": []},
        **drafts,
    }
    database = business_db(agent_runs=Collection([run]))

    seen: list[tuple[str, str]] = []
    offset = 0
    pages = []
    for _ in range(10):
        page = await invoke_tool(
            "get_agent_briefing", {"limit": 3, "offset": offset}, database=database
        )
        pages.append(page)
        assert page["total_count"] == 12
        assert page["returned"] == len(
            [row for rows in page["drafts"].values() for row in rows]
        )
        seen.extend(
            (kind, row["body"]) for kind, rows in page["drafts"].items() for row in rows
        )
        if not page["has_more"]:
            assert page["next_offset"] is None
            assert page["all_records_returned"] is True
            break
        assert page["next_offset"] > offset, "the cursor did not advance - paging would loop"
        offset = page["next_offset"]

    assert len(seen) == 12
    assert len(set(seen)) == 12, "a draft was served twice across pages"
    # the per-kind totals describe the whole set, not the page
    assert pages[0]["draft_totals"] == {"negotiation": 4, "collections": 4, "lead_followup": 4}


@pytest.mark.asyncio
async def test_stuck_leads_and_top_n_answers_admit_what_they_left_out():
    leads = [
        lead(zohoLeadId=f"L-{index:02d}", lastActivityAt=(NOW - timedelta(days=3)).isoformat())
        for index in range(1, 9)
    ]
    database = business_db(leads=Collection(leads))

    page = await invoke_tool("get_stuck_leads", {"limit": 3}, database=database)
    assert page["total_count"] == 8
    assert page["returned"] == 3
    assert page["has_more"] is True
    assert page["all_records_returned"] is False
    # paged since Oct 23: this list is the one "show next page" must continue,
    # so it carries the same cursor as every other record tool - when it was a
    # head-only answer it recorded no continuation and a wider call in the
    # same turn hijacked the follow-up instead.
    assert page["next_offset"] == 3
    page_two = await invoke_tool("get_stuck_leads", {"limit": 3, "offset": 3}, database=database)
    assert page_two["total_count"] == 8
    assert [row["lead_id"] for row in page_two["items"]] == ["L-04", "L-05", "L-06"]

    unpaid = await invoke_tool("get_top_unpaid", {"limit": 1}, database=business_db())
    assert unpaid["total_count"] == 1
    assert unpaid["returned"] == 1
    assert unpaid["all_records_returned"] is True


# ------------------------------------------------------- the contract itself


@pytest.mark.asyncio
async def test_every_paged_tool_carries_the_same_pagination_block():
    for name, filters in (
        ("get_payments", {}),
        ("get_invoices", {}),
        ("get_quotes", {}),
        ("get_orders", {}),
        ("get_leads", {}),
        ("search_records", {"query": "Picazo"}),
    ):
        page = await invoke_tool(name, filters, database=business_db())
        assert page["limit"] == DEFAULT_TOOL_ROWS, name
        assert page["offset"] == 0, name
        for key in PAGE_KEYS:
            assert key in page, f"{name} is missing {key}"

    # the briefing is paged too, over its drafts
    briefing = await invoke_tool("get_agent_briefing", {}, database=business_db())
    assert "note" in briefing and "No agent run" in briefing["note"]
    run = {
        "run_id": "run-1",
        "finished_epoch": NOW.timestamp(),
        "master": {"bullets": [], "priorities": [], "conflicts_resolved": []},
        "collections": [{"customer_name": "Picazo Cleaning Services", "body": "call"}],
    }
    briefing = await invoke_tool(
        "get_agent_briefing", {}, database=business_db(agent_runs=Collection([run]))
    )
    for key in PAGE_KEYS:
        assert key in briefing, f"get_agent_briefing is missing {key}"


@pytest.mark.asyncio
async def test_pagination_arguments_reach_the_model_schema():
    """A cursor the model cannot see is a cursor the model cannot use."""
    for name in ("get_payments", "get_invoices", "get_quotes", "get_orders", "get_leads", "search_records", "get_agent_briefing", "get_stuck_leads"):
        tool = chat_tools.CHAT_TOOL_MAP[name]
        properties = tool.model_schema()["parameters"]["properties"]
        assert "offset" in properties, name
        assert "next_offset" in properties["offset"]["description"], name
        assert "Paged" in tool.description, name


@pytest.mark.asyncio
async def test_a_page_past_the_end_cannot_claim_the_whole_set_was_returned():
    """An offset beyond the last row has nothing after it, but it delivered and
    covered nothing - so it is not evidence that the set was fully listed."""
    rows = [payment(index=index) for index in range(1, 41)]
    database = business_db(payments_flat=Collection(rows))

    past_end = await invoke_tool("get_payments", {"limit": 10, "offset": 999}, database=database)
    assert past_end["returned"] == 0
    assert past_end["has_more"] is False
    assert past_end["all_records_returned"] is False
    from app.agents.chat import tool_evidence

    assert tool_evidence("get_payments", past_end)["complete"] is False

    # a real final page does close the walk, and an empty set was always covered
    last = await invoke_tool("get_payments", {"limit": 10, "offset": 30}, database=database)
    assert last["returned"] == 10
    assert last["all_records_returned"] is True
    assert tool_evidence("get_payments", last)["complete"] is True

    empty = await invoke_tool("get_payments", {"customer": "Nobody Exists"}, database=database)
    assert empty["total_count"] == 0 and empty["all_records_returned"] is True


# ------------------------------------------------- completeness claims (§4.4)


@pytest.mark.asyncio
async def test_a_partial_page_may_not_claim_the_listing_was_complete():
    rows = [payment(index=index) for index in range(1, 41)]
    database = business_db(payments_flat=Collection(rows))
    page = await invoke_tool("get_payments", {"limit": 10}, database=database)
    results = [{"tool": "get_payments", "result": page}]

    assert page["has_more"] is True
    from app.agents.chat import validate_numeric_claims

    errors = validate_numeric_claims(
        "All 40 payments are listed above.", results, "", ""
    )
    assert any("complete listing" in error for error in errors), errors
    assert validate_numeric_claims("No more payments remain.", results, "", "") != []


@pytest.mark.asyncio
async def test_a_honest_page_answer_over_the_same_result_still_passes():
    """The control that keeps this from becoming the old false-rejection bug."""
    rows = [payment(index=index) for index in range(1, 41)]
    database = business_db(payments_flat=Collection(rows))
    page = await invoke_tool("get_payments", {"limit": 10}, database=database)
    results = [{"tool": "get_payments", "result": page}]
    from app.agents.chat import validate_numeric_claims

    for honest in (
        "Ten payments of the 40 matched (page 1 of 4); 30 still to fetch.",
        "The total $20,820 covers every matched payment record, "
        "not only the rows on this page.",
    ):
        assert validate_numeric_claims(honest, results, "", "") == [], honest


@pytest.mark.asyncio
async def test_a_walk_that_reached_the_end_may_state_the_set_is_complete():
    """Reading the pages together is what makes the claim honest at the end."""
    rows = [payment(index=index) for index in range(1, 15)]
    database = business_db(payments_flat=Collection(rows))
    one = await invoke_tool("get_payments", {"limit": 10, "offset": 0}, database=database)
    two = await invoke_tool("get_payments", {"limit": 10, "offset": one["next_offset"]}, database=database)
    assert two["all_records_returned"] is True
    results = [{"tool": "get_payments", "result": one}, {"tool": "get_payments", "result": two}]

    from app.agents.chat import validate_numeric_claims

    assert validate_numeric_claims(
        "All 14 payments are listed above.", results, "", ""
    ) == []
    # and the same claim is refused if only the first page was fetched
    assert validate_numeric_claims(
        "All 14 payments are listed above.", results[:1], "", ""
    ) != []
