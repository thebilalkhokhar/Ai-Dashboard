"""RC6: a follow-up continues the query it refers to.

"yes", "show all", "get the rest" and "show all 34 payments" are only answerable
against the query the previous turn ran. The page record - tool, filters, cursor
- is stored on the thread and restated to the model, so a follow-up keeps the
original date range and continues from where the last page stopped instead of
quietly re-running something narrower.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from app.agents import chat
from app.agents.chat import execute_chat, run_chat
from app.agents.chat_tools import invoke_tool
from test_chat import FakeCollection, FakeDatabase, FakeModel
from test_chat_data_tools import NOW, business_db, payment

TODAY = date(2026, 10, 21)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    for module in (chat,):
        monkeypatch.setattr(module, "datetime", FrozenDateTime)


def call(name: str, args: dict, index: int = 1):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": f"call-{index}", "type": "tool_call"}],
        usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
    )


def answer(text: str):
    return AIMessage(
        content=text,
        usage_metadata={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
    )


def payments_db(count: int = 34):
    rows = [payment(index=index) for index in range(1, count + 1)]
    return business_db(payments_flat=FakeCollection(rows))


def system_texts(model: FakeModel, turn: int = 0) -> list[str]:
    return [
        str(message.content)
        for message in model.seen_messages[turn]
        if isinstance(message, SystemMessage)
    ]


# ------------------------------------------------------------- capturing it


@pytest.mark.asyncio
async def test_a_paged_answer_leaves_a_bounded_query_record():
    database = payments_db()
    model = FakeModel([call("get_payments", {"limit": 10}), answer("10 of the 34 payments.")])

    result = await execute_chat("who paid this week?", [], model=model, database=database)

    record = result["query_context"]
    assert record["tool"] == "get_payments"
    # the window is the server's, not the model's: "this week" on the frozen
    # business date (Wed 2026-10-21) is Monday 10-19 through Sunday 10-25, and
    # it is recorded so a follow-up continues that exact range.
    assert record["args"]["limit"] == 10
    assert record["args"]["date_from"] == "2026-10-19"
    assert record["args"]["date_to"] == "2026-10-25"
    assert (record["offset"], record["returned"], record["total_count"]) == (0, 10, 34)
    assert record["has_more"] is True
    assert record["next_offset"] == 10
    assert record["remaining_count"] == 24
    assert record["question"] == "who paid this week?"
    # a continuation record, not a snapshot of the ERP
    assert "items" not in record
    assert len(str(record)) < 1_000


@pytest.mark.asyncio
async def test_an_unpaged_answer_keeps_the_previous_record():
    database = business_db()
    last_query = {"tool": "get_payments", "args": {}, "offset": 0, "limit": 10,
                  "total_count": 34, "returned": 10, "remaining_count": 24,
                  "has_more": True, "next_offset": 10, "question": "who paid?"}
    model = FakeModel([answer("Picazo Cleaning Services.")])

    result = await execute_chat("I need that name", [], model=model, database=database, last_query=last_query)

    assert result["query_context"] == last_query


@pytest.mark.asyncio
async def test_query_record_arguments_are_clipped_to_scalars():
    record = chat.query_context(
        "get_leads",
        {"search": "x" * 400, "stuck_only": True, "nested": {"deep": [1, 2]}, "missing": None},
        {"offset": 0, "limit": 10, "total_count": 3, "returned": 3, "has_more": False, "next_offset": None},
    )

    assert len(record["args"]["search"]) == chat._MAX_CONTEXT_VALUE_CHARS
    assert record["args"]["nested"] == "{'deep': [1, 2]}"
    assert record["args"]["missing"] is None
    assert record["next_offset"] is None


@pytest.mark.asyncio
async def test_more_than_the_allowed_number_of_filters_is_truncated():
    args = {f"filter_{index}": index for index in range(30)}
    record = chat.query_context(
        "get_payments", args,
        {"offset": 0, "limit": 10, "total_count": 1, "returned": 1, "has_more": False, "next_offset": None},
    )

    assert len(record["args"]) == chat._MAX_CONTEXT_ARGS


# ------------------------------------------------------------- using it


@pytest.mark.asyncio
async def test_the_followup_is_given_the_exact_query_it_continues():
    database = payments_db()
    last_query = {
        "tool": "get_payments",
        "args": {"date_from": "2026-10-19", "date_to": "2026-10-21", "limit": 10},
        "offset": 0, "limit": 10, "total_count": 34, "returned": 10, "remaining_count": 24,
        "has_more": True, "next_offset": 10, "question": "who paid this week?",
    }
    model = FakeModel([call("get_payments", {"date_from": "2026-10-19", "date_to": "2026-10-21",
                                             "limit": 10, "offset": 10}),
                       answer("10 more of the 34 payments this week.")])

    await execute_chat("show all", [], model=model, database=database, last_query=last_query)

    block = [text for text in system_texts(model) if "LAST QUERY ON THIS THREAD" in text]
    assert len(block) == 1
    assert "tool=get_payments" in block[0]
    assert '"date_from": "2026-10-19"' in block[0]
    assert "next page starts at offset 10" in block[0]
    assert "Never widen, drop or change the recorded date range" in block[0]
    # placed immediately before the new question, so the model reads it as context
    messages = model.seen_messages[0]
    injected = [index for index, message in enumerate(messages)
                if "LAST QUERY ON THIS THREAD" in str(message.content)]
    assert len(injected) == 1
    assert str(messages[injected[0] + 1].content) == "show all"


@pytest.mark.asyncio
async def test_the_recorded_counts_are_citable_without_a_new_tool_call():
    """'5 remain' from the thread record is a verified fact, not model arithmetic."""
    database = business_db()
    last_query = {"tool": "get_payments", "args": {}, "offset": 0, "limit": 29,
                  "total_count": 34, "returned": 29, "remaining_count": 5,
                  "has_more": True, "next_offset": 29, "question": "who paid?"}
    model = FakeModel([answer("34 payments match, 29 are listed and 5 remain.")])

    result = await execute_chat("how many are left?", [], model=model, database=database, last_query=last_query)

    assert result["text"] == "34 payments match, 29 are listed and 5 remain."
    assert "couldn't verify" not in result["text"]


@pytest.mark.asyncio
async def test_a_context_for_a_tool_that_no_longer_exists_is_not_injected():
    database = business_db()
    ghost = {"tool": "get_timecards", "args": {}, "offset": 0, "limit": 10, "total_count": 4,
             "returned": 4, "remaining_count": 0, "has_more": False, "next_offset": None, "question": "hours"}
    model = FakeModel([answer("That data is no longer available here.")])

    await execute_chat("and the rest?", [], model=model, database=database, last_query=ghost)

    assert not [text for text in system_texts(model) if "LAST QUERY ON THIS THREAD" in text]


# ------------------------------------------------------------- persistence


@pytest.mark.asyncio
async def test_run_chat_stores_the_page_record_on_the_thread():
    database = payments_db()
    database.chat_threads = FakeCollection([])
    database.chat_logs = FakeCollection([])
    model = FakeModel([call("get_payments", {"limit": 10}), answer("10 of the 34 payments.")])

    await run_chat("thread-rc6", "who paid this week?", database=database, model=model)

    stored = database.chat_threads.updated
    assert stored["last_query"]["tool"] == "get_payments"
    assert stored["last_query"]["next_offset"] == 10
    assert "items" not in stored["last_query"]


@pytest.mark.asyncio
async def test_the_second_turn_reads_the_stored_record_and_moves_the_cursor():
    database = payments_db()
    database.chat_threads = FakeCollection([])
    database.chat_logs = FakeCollection([])

    first = FakeModel([call("get_payments", {"limit": 10}), answer("10 of the 34 payments.")])
    await run_chat("thread-rc6", "who paid this week?", database=database, model=first)

    second = FakeModel([call("get_payments", {"limit": 10, "offset": 10}, index=2),
                        answer("10 more, 14 of the 34 still to come.")])
    await run_chat("thread-rc6", "show all", database=database, model=second)

    record = database.chat_threads.updated["last_query"]
    assert record["offset"] == 10
    assert record["next_offset"] == 20
    assert record["remaining_count"] == 14
    blocks = [text for text in system_texts(second) if "LAST QUERY ON THIS THREAD" in text]
    assert blocks and '"limit": 10' in blocks[0]


@pytest.mark.asyncio
async def test_a_followup_page_really_returns_the_rows_the_first_page_missed():
    """End to end: the two turns together hold every matching payment, once each."""
    database = payments_db()
    page_one = await invoke_tool("get_payments", {"limit": 10, "offset": 0}, database=database)
    page_two = await invoke_tool("get_payments", {"limit": 10, "offset": page_one["next_offset"]}, database=database)

    seen = {row["payment_id"] for row in page_one["items"]} | {row["payment_id"] for row in page_two["items"]}
    assert len(seen) == 20
    assert page_two["remaining_count"] == 14


# ------------------------------------------------------------- isolation


@pytest.mark.asyncio
async def test_one_threads_query_record_never_reaches_another_thread():
    """§5.3: the cursor and filters a follow-up continues are per-thread."""
    database = payments_db()
    database.chat_threads = FakeCollection([])
    database.chat_logs = FakeCollection([])

    first = FakeModel([call("get_payments", {"limit": 10}), answer("10 of the 34 payments.")])
    await run_chat("thread-a", "who paid this week?", database=database, model=first)
    record = database.chat_threads.updated["last_query"]
    assert record["tool"] == "get_payments"

    # a different thread asks the same-shaped follow-up. It has no stored
    # record, so nothing from thread-a may be restated to its model.
    other = FakeModel([answer("There is no earlier query on this thread.")])
    await run_chat("thread-b", "show the rest", database=database, model=other)

    prompts = " ".join(system_texts(other))
    assert "LAST QUERY ON THIS THREAD" not in prompts
    assert "\"offset\"" not in prompts

    # and thread-a's record is still exactly what it was
    stored_a = next(row for row in database.chat_threads.rows if row["thread_id"] == "thread-a")
    assert stored_a["last_query"] == record


@pytest.mark.asyncio
async def test_run_chat_reads_only_the_named_threads_stored_record():
    database = payments_db()
    database.chat_threads = FakeCollection(
        [
            {
                "thread_id": "thread-a",
                "turns": [],
                "last_query": {
                    "tool": "get_payments",
                    "args": {"date_from": "2020-01-01"},
                    "offset": 40,
                    "limit": 10,
                    "total_count": 50,
                    "returned": 10,
                    "remaining_count": 0,
                    "has_more": False,
                    "next_offset": None,
                },
            },
            {"thread_id": "thread-b", "turns": []},
        ]
    )
    database.chat_logs = FakeCollection([])

    model = FakeModel([answer("Nothing carried over from the other thread.")])
    await run_chat("thread-b", "how many are left?", database=database, model=model)

    prompts = " ".join(system_texts(model))
    assert "2020-01-01" not in prompts
    assert "LAST QUERY ON THIS THREAD" not in prompts


# ------------------------------------------------------------------ staleness
#
# §5.2 Example E: an unrelated or later question must not inherit an old range.
# Continuing a page has to keep its filters, so the record cannot simply be
# rewritten - but it can, and now must, say how old it is.


def test_a_fresh_record_is_continued_without_any_age_warning():
    from app.agents import chat as chat_module
    from app.agents.chat import _continuation_prompt

    record = {
        "tool": "get_payments", "args": {}, "offset": 0, "limit": 10,
        "total_count": 40, "returned": 10, "remaining_count": 30,
        "has_more": True, "next_offset": 10, "question": "who paid?",
        # the frozen clock this suite installs, so "now" means the same thing
        # to the record and to the code reading it
        "recorded_at": chat_module.datetime.now(timezone.utc).isoformat(),
    }
    prompt = _continuation_prompt(record)
    assert "hour(s) old" not in prompt
    assert "LAST QUERY ON THIS THREAD" in prompt


def test_an_old_record_warns_that_re_asking_means_a_new_query():
    from app.agents import chat as chat_module
    from app.agents.chat import _continuation_prompt

    record = {
        "tool": "get_payments", "args": {}, "offset": 0, "limit": 10,
        "total_count": 40, "returned": 10, "remaining_count": 30,
        "has_more": True, "next_offset": 10, "question": "who paid this week?",
        "recorded_at": (chat_module.datetime.now(timezone.utc) - timedelta(hours=30)).isoformat(),
    }
    prompt = _continuation_prompt(record)
    assert "30 hour(s) old" in prompt
    assert "run a NEW query" in prompt


def test_a_record_written_before_the_stamp_existed_reports_no_age():
    """No invented age for a thread stored by an older build."""
    from app.agents.chat import _context_age_hours, _continuation_prompt

    record = {"tool": "get_payments", "args": {}, "offset": 0, "limit": 10,
              "total_count": 4, "returned": 4, "has_more": False}
    assert _context_age_hours(record) is None
    assert "hour(s) old" not in _continuation_prompt(record)


@pytest.mark.asyncio
async def test_run_chat_records_when_the_query_was_made():
    database = payments_db()
    database.chat_threads = FakeCollection([])
    database.chat_logs = FakeCollection([])
    model = FakeModel([call("get_payments", {"limit": 10}), answer("10 of the 34 payments.")])

    await run_chat("thread-age", "who paid this week?", database=database, model=model)

    stamp = database.chat_threads.updated["last_query"]["recorded_at"]
    assert datetime.fromisoformat(stamp) <= datetime.now(timezone.utc)
