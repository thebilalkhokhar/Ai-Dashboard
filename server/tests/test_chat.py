"""Offline tests for chat validation, tool limits, memory, and SSE order."""

from __future__ import annotations

from collections import deque
from datetime import date

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from app.agents.chat import execute_chat, run_chat, stream_chat, trim_turns
from app.agents.chat_tools import (
    CHAT_TOOL_MAP,
    EmptyArgs,
    RevenueArgs,
    TopItemsArgs,
    ChatTool,
    invoke_tool,
)


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, *args, **kwargs):
        return self

    async def to_list(self, limit):
        return self.rows[:limit]


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.updated = None
        self.inserted = []

    def find(self, *args, **kwargs):
        return FakeCursor(self.rows)

    async def find_one(self, query):
        return next((row for row in self.rows if row.get("thread_id") == query.get("thread_id")), None)

    async def update_one(self, query, update, upsert=False):
        self.updated = update["$set"]
        row = {"thread_id": query["thread_id"], **self.updated}
        self.rows = [item for item in self.rows if item.get("thread_id") != query.get("thread_id")]
        self.rows.append(row)

    async def insert_one(self, row):
        self.inserted.append(row)


class FakeDatabase:
    def __init__(self, **collections):
        self.__dict__.update(collections)


class FakeModel:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.bind_count = 0

    def bind_tools(self, tools, **kwargs):
        self.bind_count += 1
        return self

    async def ainvoke(self, messages):
        return self.responses.popleft()


def tool_call(index=1):
    return AIMessage(
        content="",
        tool_calls=[{"name": "fake_read", "args": {}, "id": f"call-{index}", "type": "tool_call"}],
        usage_metadata={"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
    )


def plain_answer(text):
    return AIMessage(
        content=text,
        usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
    )


@pytest.mark.asyncio
async def test_tool_arguments_are_pydantic_validated_and_bounded():
    with pytest.raises(ValidationError):
        TopItemsArgs.model_validate({"limit": 11})
    with pytest.raises(ValidationError):
        RevenueArgs.model_validate({"date_from": "2026-10-21", "date_to": "2026-10-20"})
    with pytest.raises(ValidationError):
        await invoke_tool("get_top_unpaid", {"limit": 11}, registry=CHAT_TOOL_MAP)


@pytest.mark.asyncio
async def test_revenue_tool_filters_dates_and_sums_in_python():
    db = FakeDatabase(
        payments_flat=FakeCollection(
            [
                {"amount": "120.50", "received_at": "2026-10-03", "sale_rep": 8},
                {"amount": 30, "received_at": "2026-10-04T12:00:00Z", "sale_rep": 9},
                {"amount": 12, "received_at": "2026-10-01", "sale_rep": 8},
            ]
        )
    )
    result = await invoke_tool(
        "get_revenue",
        {"date_from": "2026-10-03", "date_to": "2026-10-04", "rep_id": 8},
        database=db,
    )
    assert result["payment_count"] == 1
    assert result["total_payments"] == 120.5


@pytest.mark.asyncio
async def test_graph_executes_no_more_than_five_tool_calls():
    async def read_tool(database=None):
        return {"count": 4}

    tool = ChatTool("fake_read", "Return a fixed count", EmptyArgs, read_tool)
    model = FakeModel([tool_call(i) for i in range(1, 6)] + [plain_answer("I found 4 items.")])
    result = await execute_chat("Look this up", model=model, tools=[tool])
    assert len(result["tools_used"]) == 5
    assert result["text"] == "I found 4 items."
    assert len(model.responses) == 0


@pytest.mark.asyncio
async def test_number_guard_regenerates_once_then_rejects_invented_amount():
    model = FakeModel([plain_answer("Revenue was $987.00."), plain_answer("Revenue was $987.00.")])
    result = await execute_chat("What was revenue?", model=model, tools=[])
    assert result["text"] == "I couldn't verify that figure from the available data."
    assert len(model.responses) == 0


def test_thread_memory_keeps_the_last_ten_complete_turns():
    turns = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(15)]
    turns.append({"question": "incomplete"})
    saved = trim_turns(turns)
    assert len(saved) == 10
    assert saved[0]["question"] == "q5"
    assert saved[-1]["answer"] == "a14"


@pytest.mark.asyncio
async def test_run_chat_persists_only_the_last_ten_turns():
    prior = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(15)]
    db = FakeDatabase(
        chat_threads=FakeCollection([{"thread_id": "memory-thread", "turns": prior}]),
        chat_logs=FakeCollection(),
    )
    result = await run_chat(
        "memory-thread",
        "new question",
        database=db,
        model=FakeModel([plain_answer("Done.")]),
        tools=[],
    )
    turns = db.chat_threads.updated["turns"]
    assert result["text"] == "Done."
    assert len(turns) == 10
    assert turns[0]["question"] == "q6"
    assert turns[-1]["question"] == "new question"
    assert len(db.chat_logs.inserted) == 1


@pytest.mark.asyncio
async def test_sse_events_order_tool_then_tokens_then_done():
    async def read_tool(database=None):
        return {"count": 4}

    db = FakeDatabase(
        chat_threads=FakeCollection(),
        chat_logs=FakeCollection(),
    )
    tool = ChatTool("fake_read", "Return a fixed count", EmptyArgs, read_tool)
    model = FakeModel([tool_call(), plain_answer("There are 4 open quotes.")])
    events = [
        event
        async for event in stream_chat(
            "thread-a",
            "How many open quotes?",
            db,
            model=model,
            tools=[tool],
        )
    ]
    names = [event["event"] for event in events]
    assert names[0] == "tool"
    assert "token" in names
    assert names[-1] == "done"
    assert names.index("tool") < names.index("token") < names.index("done")
    assert events[-1]["data"] == {"text": "There are 4 open quotes.", "thread_id": "thread-a"}
    assert len(db.chat_logs.inserted) == 1


def test_count_guard_only_accepts_counts_present_in_tool_results():
    from app.agents.chat import validate_numeric_claims

    results = [{"tool": "get_signals_summary", "result": {"stale_quotes": {"count": 3}}}]
    assert validate_numeric_claims("There are 3 stale quotes.", results) == []
    assert validate_numeric_claims("There are 8 stale quotes.", results)
    assert validate_numeric_claims("Revenue is 900.00.", results)
