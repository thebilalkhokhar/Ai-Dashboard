"""Offline tests for chat validation, tool limits, memory, and SSE order."""

from __future__ import annotations

from collections import deque
from datetime import date, datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from pydantic import ValidationError

from app.agents.chat import CHAT_SYSTEM, execute_chat, run_chat, stream_chat, trim_turns
from app.agents.chat_tools import (
    CHAT_TOOL_MAP,
    EmptyArgs,
    RevenueArgs,
    TopItemsArgs,
    ChatTool,
    get_dashboard_metrics,
    get_customer_context,
    invoke_tool,
)
from app.services import metrics


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
        self.seen_messages = []

    def bind_tools(self, tools, **kwargs):
        self.bind_count += 1
        return self

    async def ainvoke(self, messages):
        self.seen_messages.append(messages)
        return self.responses.popleft()


class FakeStreamingModel:
    """Streams two chunks; blocks after the first until the consumer reads it."""

    def __init__(self, chunks, gate):
        self.chunks = chunks
        self.gate = gate

    def bind_tools(self, tools, **kwargs):
        return self

    async def astream(self, messages):
        for index, text in enumerate(self.chunks):
            yield AIMessageChunk(content=text)
            if index == 0:
                await self.gate.wait()


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
async def test_tool_results_format_money_and_keep_counts_as_integers():
    async def read_tool(database=None):
        return {"balance": 357500.0, "total_amount": 2379.5, "count": 3.0}

    tool = ChatTool("format_read", "Return fixed values", EmptyArgs, read_tool)
    result = await invoke_tool("format_read", {}, registry={"format_read": tool})
    assert result == {"balance": "$357,500", "total_amount": "$2,379.50", "count": 3}


@pytest.mark.asyncio
async def test_dashboard_metrics_tool_matches_the_dashboard_snapshot():
    now = datetime(2026, 10, 20, 20, 30, tzinfo=timezone.utc)
    db = FakeDatabase(
        quotes_flat=FakeCollection(
            [
                {
                    "id": 1,
                    "status": "Quote Created",
                    "amount": 5000,
                    "created_at": "2026-10-18T15:00:00Z",
                    "customer": {"id": 7, "company_name": "Acme"},
                }
            ]
        ),
        orders_flat=FakeCollection([]),
        payments_flat=FakeCollection(
            [{"amount": 11687, "received_at": "2026-10-20T15:00:00Z"}]
        ),
    )
    result = await get_dashboard_metrics(database=db, now=now)
    expected = metrics.build_snapshot_from_docs(
        db.quotes_flat.rows, db.orders_flat.rows, db.payments_flat.rows, now
    )
    assert result["revenue_today"] == expected["revenue_today_display"] == "$11,687"
    assert result["payments_count_today"] == 1
    assert result["revenue_month_to_date"] == expected["revenue_month_to_date_display"]


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
    assert result["total_payments"] == "$120.50"


@pytest.mark.asyncio
async def test_stuck_leads_tool_parses_in_python_and_hides_contacts():
    now = datetime.now(timezone.utc)
    db = FakeDatabase(
        leads=FakeCollection(
            [
                {
                    "zohoLeadId": "L-1",
                    "firstName": "Keiwan",
                    "lastName": "Bentley",
                    "company": "Zaskii Boyz",
                    "status": "New",
                    "email": "keiwan@example.com",
                    "phone": "5551234567",
                    "description": '{"product": "mylar bag", "quantity": "500", "artwork": []}',
                    "createdAt": (now - timedelta(days=5)).isoformat(),
                    "lastActivityAt": (now - timedelta(days=3)).isoformat(),
                }
            ]
        )
    )
    result = await invoke_tool("get_stuck_leads", {}, database=db)
    assert result["total_count"] == 1
    item = result["items"][0]
    assert item["product"] == "mylar bag"
    assert item["quantity"] == "500"
    assert item["artwork"] == "Missing"  # explicit empty JSON artwork list
    assert "artwork" in item["missing"]
    # contact details must never reach the model
    assert "email" not in item and "phone" not in item


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
async def test_sse_streams_tokens_before_the_model_finishes():
    import asyncio

    db = FakeDatabase(
        chat_threads=FakeCollection(),
        chat_logs=FakeCollection(),
    )
    gate = asyncio.Event()
    model = FakeStreamingModel(["The dashboard ", "is ready."], gate)
    stream = stream_chat("thread-stream", "status?", db, model=model, tools=[])

    # If the backend buffered the full answer, this would block until the gate
    # is set and the test would time out.
    first = await asyncio.wait_for(anext(stream), timeout=2)
    assert first == {"event": "token", "data": {"text": "The dashboard "}}

    gate.set()
    rest = [event async for event in stream]
    tokens = [first, *rest]
    text = "".join(
        event["data"]["text"] for event in tokens if event["event"] == "token"
    )
    assert text == "The dashboard is ready."
    assert [event["event"] for event in rest][-1] == "done"
    assert [event["event"] for event in tokens if event["event"] == "token"] == [
        "token",
        "token",
    ]


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
    assert validate_numeric_claims("The order data does not include artwork status.", results) == []


def test_numeric_guard_accepts_prior_numbers_and_rejects_new_numbers():
    from app.agents.chat import validate_numeric_claims

    prior = "Desert spore has invoice 4812, dated 2026-10-20, for $113,627."
    assert validate_numeric_claims(
        "Invoice 4812 is still unpaid for $113,627.", [], prior
    ) == []
    assert validate_numeric_claims(
        "Invoice 4812 is still unpaid for $113,628.", [], prior
    )


@pytest.mark.asyncio
async def test_customer_context_includes_true_unpaid_invoice_count():
    now = datetime.now(timezone.utc)
    orders = [
        {
            "id": index,
            "inv_ref": f"INV-{index}",
            "payment_status": 0,
            "payment_status_label": "Not Paid",
            "total_amount": 100 + index,
            "amount_paid": 0,
            "invoice_date": (now - timedelta(days=40)).isoformat(),
            "customer": {"id": 12, "company_name": "Desert spore"},
        }
        for index in range(30)
    ]
    db = FakeDatabase(
        quotes_flat=FakeCollection(),
        orders_flat=FakeCollection(orders),
        payments_flat=FakeCollection(),
    )

    context = await get_customer_context(12, database=db)

    assert context["unpaid_invoice_count"] == 30
    assert len(context["unpaid_invoices"]) == 25
    assert context["unpaid_invoices_truncated"] is True
    assert context["unpaid_invoices"][0]["due_date"] is None
    assert context["unpaid_invoices"][0]["days_invoice_age"] >= 40
    description = CHAT_TOOL_MAP["get_customer_context"].description
    assert "unpaid_invoice_count" in description
    assert "invoice references, ages, invoice and due dates" in description


def test_receivables_prompt_has_follow_up_and_number_rules():
    assert "ignore the one-sentence format" in CHAT_SYSTEM
    assert "Copy dates, invoice references, and IDs exactly" in CHAT_SYSTEM
    assert "Preserve names, counts, dates, and $ amounts as written." in CHAT_SYSTEM
    assert "Likely: payment is not yet due." in CHAT_SYSTEM
    assert "Do not repeat monetary amounts" not in CHAT_SYSTEM
    assert "Do not invent a cause" not in CHAT_SYSTEM


@pytest.mark.asyncio
async def test_prompt_requires_wholly_roman_urdu_replies():
    model = FakeModel([plain_answer("The answer is available.")])
    await execute_chat("Pure 7 ka kya status?", model=model, tools=[])
    prompt = " ".join(model.seen_messages[0][0].content.split())
    assert "Roman Urdu gets wholly Roman Urdu" in prompt
    assert "including every sentence and supporting line" in prompt


@pytest.mark.asyncio
async def test_prompt_requires_urdu_script_replies():
    model = FakeModel([plain_answer("جواب موجود ہے۔")])
    await execute_chat("پیور 7 کی کیا صورتحال ہے؟", model=model, tools=[])
    prompt = " ".join(model.seen_messages[0][0].content.split())
    assert "Urdu script gets Urdu script" in prompt
    assert "including every sentence and supporting line" in prompt
