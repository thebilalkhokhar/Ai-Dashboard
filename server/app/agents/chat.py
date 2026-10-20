"""Read-only, tool-calling LangGraph chat agent."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, time, timedelta, timezone
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app import db as app_db
from app.agents.chat_tools import CHAT_TOOL_MAP, CHAT_TOOLS, format_money, invoke_tool
from app.agents.graph import _walk_numbers, invented_amounts
from app.agents.llm import add_usage, build_model, empty_usage, usage_of
from app.config import settings
from app.services.processors import ERP_TZ

log = logging.getLogger("chat_agent")
MAX_TOOL_CALLS = 5
MAX_STORED_TURNS = 10

CHAT_SYSTEM = """You are the CEO's business assistant for Print247.
Answer only from tool results returned during this turn. You may use prior
conversation messages to understand references, but do not use their facts as
current evidence. You must call tools to retrieve business numbers; Python
tools do all math and counting, and you must copy their numbers without
calculating, counting, or estimating. Never count items yourself. Use only
the counts the tools return. If a count is not in the tool results, do not
state one. Copy dates, invoice references, and IDs exactly as the tools
return them.

For any dashboard KPI question (today's revenue, month to date, last 7 days,
open quotes, unpaid/overdue totals), call get_dashboard_metrics and copy its
money strings exactly. Never infer "today" from the user's local date:
business data uses the ERP's US Central business day, and
get_dashboard_metrics already applies it. Tool results already format money;
copy each money string exactly, including its $ sign and separators. Counts
are plain integers.

For ordinary questions, start with exactly one plain sentence that directly
answers the question, followed by at most three short supporting lines. Name
the person or customer and include the date range when relevant. Do not use
raw field names, or include an "As of" date unless the user asked about
dates. If the data does not contain an answer (including artwork, shipping,
order notes, or line items), say so plainly without guessing. Never propose
or perform ERP writes, approvals, messages, or other external actions. For
questions about whether artwork, shipping, order notes, or line items are
tracked, call get_signals_summary and describe the missing information in
plain language. Do not offer a follow-up data pull.

RECEIVABLES "WHY" QUESTIONS. When the user asks why an invoice or customer is
unpaid, overdue, or has an issue (for example "why no payments yet?" or
"what's the issue?"), ignore the one-sentence format above and reply with
exactly these three headings and no opening sentence:
**Problem:** the most likely cause based on invoice age, recorded due date,
overdue flag, and payment status. If it is not confirmed, label it as a
hypothesis, for example "Likely: not yet due. Unconfirmed, because no due
date is recorded."
**Evidence:** two or three concise facts from the tool results that support
the diagnosis, one fact per item. Do not paste the aggregate customer
summary or re-list every amount or count.
**Next steps:** two or three concrete actions, such as sending a reminder,
calling the sales representative, or confirming the PO and payment terms.
If the user does not name a customer, take the customer from the most recent
conversation turns. Call find_customer and get_customer_context again for
that customer so the evidence is current. Never invent facts, dates, causes,
or customer actions. If the data does not show the reason, say so and suggest
what to check. Treat overdue=false as an age flag only, not proof of terms.
If a recorded due date or terms show the invoice is within terms, say no
payment is due yet and state the due date. If none is recorded, say so and
do not estimate when it becomes due. When the reason is not established by
the records, label any suggested cause as a hypothesis. If a due date is
missing, explicitly say "Unconfirmed, because no due date is recorded" and
do not claim that the missing due date caused nonpayment. In that case, give
a concise hypothesis instead of only saying "Unconfirmed": "Likely: payment is not yet due. Unconfirmed, because no due date is recorded." Do not state
that hypothesis as a confirmed fact.

LANGUAGE. Match the user's language and script: English gets English; Urdu
script gets Urdu script; Roman Urdu gets wholly Roman Urdu, including every
sentence and supporting line. Preserve names, counts, dates, and $ amounts as written. Words such as "ka", "kya", and "hai" indicate Roman Urdu. For Roman
Urdu, write natural Latin-script Urdu; translate headings and labels as well
as sentences. Use labels such as "Quotes ki tadaad", "Orders ki tadaad",
"ada hui raqam", and "baqi raqam"; do not start supporting lines with
English labels such as "Quotes:", "Orders:", "amount paid", or "balance".
Use concise phrasing such as "Pure 7 ka status: 27 quotes hain; 24 orders
mein $147,296 ada hue aur $585,260 baqi hai." Translate descriptive status
values too: "Estimate" as "andazati", "Quote Created" as "quote banaya
gaya", "No payment recorded" as "payment darj nahi", and "Paid" as "ada
shuda". Do not repeat the same amount. Avoid awkward literal translations.

CUSTOMER QUESTIONS. For a named customer's status, first call find_customer,
then call get_customer_context for the returned customer ID; never answer a
customer-specific question from get_signals_summary. For a general customer
status, report the useful quote and order counts, amounts, payment status,
and received-payment total. Omit raw date fields and empty arrays unless the
user asks for them. When asked who owes the most and about their payments,
call get_top_unpaid, then find_customer for that customer, then
get_customer_context; distinguish payment records from amounts marked paid
on orders. For that question, include payment record count and total, as
well as the order count, amount paid on orders, and order balance. If the
payment-record total differs from the order-record amount paid, state both
as stored; do not answer only with the unpaid invoice list."""


class ChatState(TypedDict, total=False):
    messages: list[Any]
    pending_calls: list[dict]
    tool_calls: int
    tool_results: list[dict]
    tools_used: list[str]
    usage: dict
    final_text: str
    emitted_tokens: int
    regen_count: int
    retry: bool
    database: Any
    events: Any


def trim_turns(turns: list[dict], limit: int = MAX_STORED_TURNS) -> list[dict]:
    """Keep only the most recent complete user/assistant turns."""
    return [turn for turn in turns if turn.get("question") is not None and turn.get("answer") is not None][-limit:]


def validate_numeric_claims(
    text: str, tool_results: list[dict], prior_text: str = ""
) -> list[str]:
    """Reject unsupported dollar amounts and other numeric claims in the answer."""
    if not re.search(r"(?<!\w)\d[\d,]*(?:\.\d+)?", text or ""):
        return []

    payload = {"tool_results": tool_results}
    prior = {
        float(value.replace(",", ""))
        for value in re.findall(r"(?<![\w])\d[\d,]*(?:\.\d+)?", prior_text or "")
    }
    errors = [
        f"unsupported amount {amount:g}"
        for amount in invented_amounts(text, payload)
        if not any(abs(amount - previous) < 0.005 for previous in prior)
    ]

    allowed: set[float] = set()
    _walk_numbers(payload, allowed)
    allowed |= prior
    # IDs and names may contain digits (for example, "Pure 7"); they still
    # count as present in the tool result if the same digits appear in it.
    for value in re.findall(r"(?<![\w])\d[\d,]*(?:\.\d+)?", json.dumps(payload, default=str)):
        try:
            allowed.add(float(value.replace(",", "")))
        except ValueError:
            pass

    # Check every digit in the final response. This covers counts and bare
    # money claims, and also prevents dates or IDs from being made up.
    for raw_value in re.findall(r"(?<![\w])\d[\d,]*(?:\.\d+)?", text or ""):
        value = float(raw_value.replace(",", ""))
        if not any(abs(value - found) < 0.005 for found in allowed):
            errors.append(f"unsupported number {value:g}")

    # Currency claims must match both a tool value and the Python format sent
    # to the model (for example, $2,379.50 rather than $2379.5).
    for match in re.finditer(r"\$\s*(-?[\d,]+(?:\.\d+)?)", text or ""):
        value = float(match.group(1).replace(",", ""))
        expected = f"${int(value):,}" if value == int(value) else f"${value:,.2f}"
        if match.group(0).replace(" ", "") != expected:
            errors.append(f"format amount as {expected}")
    return list(dict.fromkeys(errors))


def _format_answer_money(text: str) -> str:
    """Normalize currency punctuation in model text before numeric validation."""
    return re.sub(
        r"\$\s*(-?[\d,]+(?:\.\d+)?)",
        lambda match: format_money(match.group(1)),
        text or "",
    )


def _fallback_text(user_message: str) -> str:
    if re.search(r"\bartwork\b", user_message, re.I):
        return "The order data does not include artwork status, so I can't identify which orders are missing artwork."
    if re.search(r"[\u0600-\u06ff]", user_message):
        return "دستیاب ڈیٹا سے اس عدد کی تصدیق نہیں ہو سکی۔"
    roman_markers = re.search(r"\b(kya|ka|ki|hai|hain|kitna|kitni|batao|status)\b", user_message, re.I)
    if roman_markers:
        return "Yeh figure available data se verify nahi ho saka."
    return "I couldn't verify that figure from the available data."


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content).strip()
    return str(content or "").strip()


def _chunk_text(chunk: Any) -> str:
    """Like _message_text but without stripping: chunk whitespace is meaningful."""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content or "")


def _tool_calls(message: Any) -> list[dict]:
    calls = getattr(message, "tool_calls", None) or []
    return [call for call in calls if isinstance(call, dict)]


async def _stream_response(bound: Any, messages: list[Any], events: Any) -> tuple[Any, int]:
    """Stream one model turn, emitting real token deltas as they arrive.

    Returns the accumulated AIMessage and how many characters were sent. Tool
    calls are accumulated from tool_call_chunks; if the model streams text and
    then decides to call a tool, a `reset` event tells the UI to drop that
    provisional text before the tool runs.
    """
    full: Any = None
    emitted = 0
    streamed_text = False
    saw_tool_chunks = False

    async for chunk in bound.astream(messages):
        full = chunk if full is None else full + chunk
        if getattr(chunk, "tool_call_chunks", None):
            saw_tool_chunks = True
        text = _chunk_text(chunk)
        if text and not saw_tool_chunks:
            events.put_nowait({"event": "token", "data": {"text": text}})
            emitted += len(text)
            streamed_text = True

    if saw_tool_chunks and streamed_text:
        events.put_nowait({"event": "reset", "data": {}})
    return (full if full is not None else AIMessage(content="")), emitted


def build_chat_graph(model: Any = None, tools: list | None = None):
    """Build a LangGraph node that runs the bounded tool-calling loop."""
    llm = model or build_model("master")
    registry = {tool.name: tool for tool in (CHAT_TOOLS if tools is None else tools)}
    tool_schemas = [tool.model_schema() for tool in registry.values()]

    async def chat_node(state: ChatState) -> dict:
        messages = list(state["messages"])
        results: list[dict] = []
        used: list[str] = []
        count = 0
        regen_count = 0
        usage = empty_usage()
        final_text = ""
        emitted_tokens = 0
        prior_text = "\n".join(
            _message_text(message)
            for message in messages
            if isinstance(message, AIMessage)
        )

        while True:
            if count >= MAX_TOOL_CALLS:
                bound = llm
            else:
                try:
                    bound = llm.bind_tools(tool_schemas, parallel_tool_calls=False)
                except TypeError:
                    bound = llm.bind_tools(tool_schemas)

            events = state.get("events")
            if events is not None and hasattr(bound, "astream"):
                response, attempt_emitted = await _stream_response(bound, messages, events)
            else:
                response = await bound.ainvoke(messages)
                attempt_emitted = 0
            emitted_tokens += attempt_emitted
            usage = add_usage(usage, usage_of(response))
            messages.append(response)
            calls = _tool_calls(response)

            if calls and count < MAX_TOOL_CALLS:
                remaining = MAX_TOOL_CALLS - count
                for index, call in enumerate(calls):
                    name = str(call.get("name") or "")
                    call_id = str(call.get("id") or f"tool-{count + index + 1}")
                    if index >= remaining:
                        result = {"error": "The per-question tool-call limit has been reached."}
                    else:
                        count += 1
                        used.append(name)
                        if events is not None:
                            events.put_nowait({"event": "tool", "data": {"name": name}})
                        try:
                            result = await invoke_tool(
                                name,
                                call.get("args") or {},
                                database=state.get("database"),
                                registry=registry,
                            )
                        except (ValidationError, KeyError, TypeError):
                            result = {"error": "Tool arguments were invalid or the requested tool is unavailable."}
                        except Exception:
                            log.exception("chat tool %s failed", name)
                            result = {"error": "The requested business data could not be loaded."}
                        results.append({"tool": name, "result": result})
                    messages.append(
                        ToolMessage(
                            content=json.dumps(result, ensure_ascii=False, default=str),
                            tool_call_id=call_id,
                            name=name or "unknown_tool",
                        )
                    )
                continue

            final_text = _format_answer_money(_message_text(response))
            if calls and not final_text:
                final_text = _fallback_text(_last_user_text(messages))
            errors = validate_numeric_claims(final_text, results, prior_text)
            if errors:
                log.warning("numeric validation failed: %s | text=%s", errors, final_text[:600])
            if not errors:
                break
            if regen_count == 0:
                if events is not None and attempt_emitted:
                    events.put_nowait({"event": "reset", "data": {}})
                messages.append(
                    SystemMessage(
                        content=(
                            "Regenerate your answer once. Remove any amount or count that is not copied from this turn's tool results. "
                            "If the result is still unavailable, say the figure could not be verified. Reply in the user's language."
                        )
                    )
                )
                regen_count = 1
                continue
            if events is not None and attempt_emitted:
                events.put_nowait({"event": "reset", "data": {}})
            final_text = _fallback_text(_last_user_text(messages))
            break

        return {
            "messages": messages,
            "tool_results": results,
            "tools_used": used,
            "usage": usage,
            "final_text": final_text,
            "emitted_tokens": emitted_tokens,
        }

    graph = StateGraph(ChatState)
    graph.add_node("chat", chat_node)
    graph.add_edge(START, "chat")
    graph.add_edge("chat", END)
    return graph.compile()


def _last_user_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return _message_text(message)
    return ""


async def execute_chat(
    message: str,
    history: list[dict] | None = None,
    *,
    model: Any = None,
    tools: list | None = None,
    database: Any = None,
    events: Any = None,
) -> dict:
    today = datetime.now(ZoneInfo("Asia/Karachi")).date().isoformat()
    business_today = datetime.now(ERP_TZ).date().isoformat()
    system = (
        f"{CHAT_SYSTEM}\nCurrent date in the user's timezone (Asia/Karachi): {today}. "
        f"Current ERP business date ({ERP_TZ}): {business_today}. Business records use the "
        "ERP business calendar, so 'today', 'yesterday', 'this month' and 'last week' mean "
        "that calendar; use get_dashboard_metrics for KPIs rather than sending the local "
        "date to get_revenue. Treat last week as the previous complete Monday through Sunday, "
        "and use inclusive date ranges."
    )
    messages: list[Any] = [SystemMessage(content=system)]
    for turn in trim_turns(history or []):
        messages.append(HumanMessage(content=str(turn["question"])))
        messages.append(AIMessage(content=str(turn["answer"])))
    messages.append(HumanMessage(content=message))
    graph = build_chat_graph(model=model, tools=tools)
    initial: ChatState = {
        "messages": messages,
        "pending_calls": [],
        "tool_calls": 0,
        "tool_results": [],
        "tools_used": [],
        "usage": empty_usage(),
        "final_text": "",
        "regen_count": 0,
        "database": database,
        "events": events,
    }
    result = await graph.ainvoke(initial)
    return {
        "text": result.get("final_text", ""),
        "tools_used": result.get("tools_used", []),
        "tool_results": result.get("tool_results", []),
        "tokens": result.get("usage") or empty_usage(),
        "emitted_tokens": int(result.get("emitted_tokens") or 0),
    }


async def daily_token_total(database: Any = None) -> int:
    """Sum today's logged token usage in UTC."""
    db = database if database is not None else app_db.get_db()
    now = datetime.now(timezone.utc)
    start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc).isoformat()
    end = (datetime.combine(now.date(), time.min, tzinfo=timezone.utc) + timedelta(days=1)).isoformat()
    rows = await db.chat_logs.find(
        {"created_at": {"$gte": start, "$lt": end}}, {"tokens.total_tokens": 1}
    ).to_list(200_000)
    return sum(int((row.get("tokens") or {}).get("total_tokens") or 0) for row in rows)


async def log_rejected_turn(thread_id: str, message: str, status: str, database: Any = None) -> None:
    db = database if database is not None else app_db.get_db()
    await db.chat_logs.insert_one(
        {
            "thread_id": thread_id,
            "question": message,
            "tools_used": [],
            "tokens": empty_usage(),
            "status": status,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )


async def run_chat(
    thread_id: str,
    message: str,
    *,
    database: Any = None,
    model: Any = None,
    tools: list | None = None,
    events: Any = None,
) -> dict:
    """Run one chat turn, then persist its text history and token log."""
    db = database if database is not None else app_db.get_db()
    previous = await db.chat_threads.find_one({"thread_id": thread_id}) or {}
    turns = trim_turns(previous.get("turns") or [])
    started = datetime.now(timezone.utc).isoformat()
    outcome: dict | None = None
    status = "error"
    try:
        outcome = await execute_chat(
            message,
            turns,
            model=model,
            tools=tools,
            database=db,
            events=events,
        )
        updated_turns = trim_turns(
            [*turns, {"question": message, "answer": outcome["text"], "created_at": started}]
        )
        await db.chat_threads.update_one(
            {"thread_id": thread_id},
            {"$set": {"turns": updated_turns, "updated_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        status = "complete"
        return outcome
    finally:
        tokens = (outcome or {}).get("tokens") or empty_usage()
        await db.chat_logs.insert_one(
            {
                "thread_id": thread_id,
                "question": message,
                "tools_used": (outcome or {}).get("tools_used", []),
                "tokens": tokens,
                "status": status,
                "created_at": started,
            }
        )


def _text_chunks(text: str, words_per_chunk: int = 4):
    words = re.findall(r"\S+\s*", text or "")
    for start in range(0, len(words), words_per_chunk):
        yield "".join(words[start : start + words_per_chunk])


async def stream_chat(
    thread_id: str,
    message: str,
    database: Any = None,
    *,
    model: Any = None,
    tools: list | None = None,
):
    """Yield tool progress, answer text chunks, and the final SSE payload."""
    queue: asyncio.Queue = asyncio.Queue()

    async def worker() -> None:
        try:
            outcome = await run_chat(
                thread_id,
                message,
                database=database,
                model=model,
                tools=tools,
                events=queue,
            )
            # Real streaming already sent deltas. Only models without astream
            # (tests, unusual providers) need the post-hoc chunk fallback.
            if not outcome.get("emitted_tokens"):
                for chunk in _text_chunks(outcome["text"]):
                    await queue.put({"event": "token", "data": {"text": chunk}})
            await queue.put({"event": "done", "data": {"text": outcome["text"], "thread_id": thread_id}})
        except Exception:
            log.exception("chat turn failed")
            await queue.put({"event": "error", "data": {"message": "Chat could not complete this request."}})
        finally:
            await queue.put(None)

    task = asyncio.create_task(worker())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
    finally:
        if not task.done():
            task.cancel()

