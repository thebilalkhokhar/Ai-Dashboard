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
from app.agents.graph import invented_amounts
from app.agents.llm import add_usage, build_model, empty_usage, usage_of
from app.config import settings
from app.services.processors import ERP_TZ

log = logging.getLogger("chat_agent")
MAX_TOOL_CALLS = 8      # per question, shared by parallel calls
MAX_STORED_TURNS = 20     # enough history for "I need that name" follow-ups

CHAT_SYSTEM = """You are the business assistant of the Biztek AI Command Center
for a printing company. Every number is in MongoDB and a tool can read it, so
any question about payments, invoices, quotes, orders, leads, customers or reps
is answerable. "I cannot see that" is only allowed after a tool has been called
and came back empty.

HOW TO WORK
1. For any specific record - who paid, which invoices are overdue, a customer's
   status, a stuck lead, an amount - call a tool first. Never answer from memory
   or invent a value, and never tell the user to check the ERP.
2. Tool choice: get_dashboard_metrics for headline KPIs (today's revenue, month
   to date, last 7 days, open quotes, unpaid and overdue totals) - those strings
   are the same ones the dashboard tiles show, copy them exactly. get_payments /
   get_invoices / get_quotes / get_orders / get_leads for lists of records,
   get_customer_profile for one customer, get_agent_briefing for what the AI
   agents drafted, get_record / search_records for one specific thing, and
   describe_data when you are not sure a field exists at all.
3. Call several tools in one turn when the question needs them; you have eight
   tool calls per question. A truncated answer (total_count larger than the rows
   you got) must be described as a page, not as everything.
4. Python does all math, joins and counting. Copy amounts, dates, IDs and counts
   from tool results exactly; never add, average or count items yourself. Never
   pass a local date for "today" - the ERP business day is US Central and the
   metrics tool already applies it.
5. If a value really is not stored (for example a payment method), call
   describe_data once, say which field is missing in one short sentence, and do
   not repeat that sentence in the same answer.
6. Follow-ups refer to the conversation: "I need that name", "and the balance?"
   mean the customer or record of the previous answer - reuse it, and re-call a
   tool if you need fresh numbers. Never answer a new question by repeating the
   previous answer.
7. Be concise: one direct sentence first, then up to three short supporting
   lines. Only use the three-heading form below when the user asks why something
   is unpaid. No raw field names, no unrelated metrics.
8. Never propose or perform ERP writes, approvals, sending messages or any
   external action: drafts are stored text for a human only.

RECEIVABLES "WHY" QUESTIONS (only when asked why an invoice or customer has not
paid). Reply with exactly these three headings and no opening sentence:
**Problem:** the most likely cause from invoice age, recorded due date, overdue
flag and payment status; label it "Likely:" and "Unconfirmed" when the records
do not confirm it.
**Evidence:** two or three facts from the tool results, one per line. Do not
re-list every amount.
**Next steps:** two or three concrete actions a human rep can take.
If no due date is recorded, say "Unconfirmed, because no due date is recorded"
once, and do not claim that caused the nonpayment. overdue=false is an age flag,
not proof of terms.

LANGUAGE. Match the user's language and script: English stays English, Urdu
script stays Urdu script, Roman Urdu stays wholly Roman Urdu - translate
headings and labels too ("Quotes ki tadaad", "ada hui raqam", "baqi raqam",
"andazati" for Estimate, "ada shuda" for Paid). Keep names, counts, dates and $
amounts exactly as the tools wrote them."""


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


# ---------------------------------------------------------------------------
# numeric claim validation
#
# A number in an answer has to be a value a tool actually returned: an amount,
# a count or a day figure from a real field. IDs and dates are only accepted
# when the sentence puts them in an ID/date context. Digit runs found inside
# unrelated strings (a date '2026-10-27' hiding a wrong count of 27) no longer
# count as evidence.
# ---------------------------------------------------------------------------

_ID_KEY_RE = re.compile(r"(^|_)(id|ids|no|number|ref|code|run)(_|$)", re.I)
_ID_CONTEXT_RE = re.compile(
    r"(?i)\b(invoice|inv|quote|quotation|order|lead|customer|rep|run|thread|ref|reference|"
    r"id|number|#)\b[^0-9$]{0,18}$"
)
_MONEYISH_RE = re.compile(r"^\s*-?\$[\d,]+(?:\.\d+)?\s*$")
_NUMBER_RE = re.compile(
    r"(?<![\w.])[\d۰-۹٠-٩][\d۰-۹٠-٩,]*(?:\.[\d۰-۹٠-٩]+)?(?:\s*[kKmM])?(?![\w])"
)
_MONEY_RE = re.compile(r"\$\s*(-?[\d,]+(?:\.\d+)?)")
# "2026-10-27", "2026-10-27T14:24:32", "10/27/2026"
_DATE_STRING_RE = re.compile(
    r"(?<![\d/\-])\d{4}-\d{2}(?:-\d{2})?(?:[T ]\d{2}:\d{2}(?::\d{2})?)?(?!\d)"
    r"|(?<![\d/\-])\d{1,2}/\d{1,2}/\d{2,4}(?![\d/])"
)
# rounded money: "$147k", "$2.4M"
_SUFFIXED_MONEY_RE = re.compile(r"\$\s*([\d][\d,]*(?:\.\d+)?)\s*([kKmM])(?![\w])")
# part of a written reference such as '27608-2025-10' or '10/27/2026'
_REFERENCE_RE = re.compile(r"\d[-/]\d")
# a whole ERP reference / date-ish token: one identifier, never three numbers
_REFERENCE_TOKEN_RE = re.compile(r"\d+(?:[-/]\d+)+")
# the end of a sentence like 'Unpaid invoices: ' or an id list '33245, '
_ID_LIST_CUE_RE = re.compile(r"(?s)[,:]\s*$")

_SUFFIX_MULTIPLIER = {"k": 1_000.0, "m": 1_000_000.0}

# digits the models may write in Urdu-Indic or Arabic-Indic form
_DIGIT_TABLE = {
    **{ord("۰") + i: str(i) for i in range(10)},   # Urdu / Persian
    **{ord("٠") + i: str(i) for i in range(10)},   # Arabic
}


def normalize_digits(text: str) -> str:
    """Urdu/Arabic digit forms -> ASCII, so the checks work in every language."""
    return (text or "").translate(_DIGIT_TABLE)


def _number_value(token: str) -> tuple[float, float]:
    """(value, tolerance) for one written number: '1,500', '$45k', '$2.4M'."""
    cleaned = normalize_digits(token).replace(",", "").replace("$", "").strip()
    suffix = cleaned[-1:].lower()
    if suffix in _SUFFIX_MULTIPLIER:
        multiplier = _SUFFIX_MULTIPLIER[suffix]
        try:
            value = float(cleaned[:-1].strip()) * multiplier
        except ValueError:
            return 0.0, 0.0
        return value, multiplier / 2  # '45k' may really be 44,501 - 45,500
    try:
        return float(cleaned), 0.0
    except ValueError:
        return 0.0, 0.0


def _matches(value: float, tolerance: float, allowed: set[float]) -> bool:
    return any(abs(value - known) <= max(tolerance, 0.005) for known in allowed)


def _harvest(node: Any, facts: set[float], identifiers: set[float], dates: set[str], key_hint: str = "") -> None:
    """Split tool-result numbers into 'values a claim may copy' and IDs/dates."""
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, (int, float)):
        (identifiers if _ID_KEY_RE.search(key_hint) else facts).add(float(node))
        return
    if isinstance(node, dict):
        for key, value in node.items():
            _harvest(value, facts, identifiers, dates, str(key))
        return
    if isinstance(node, (list, tuple)):
        for value in node:
            _harvest(value, facts, identifiers, dates, key_hint)
        return

    text = normalize_digits(str(node))
    if _MONEYISH_RE.match(text):
        facts.add(float(text.strip().replace("$", "").replace(",", "")))
        return
    # a date string is an identifier, never a source of counts - but the year
    # alone is safe to write in prose ("revenue in 2026")
    found_dates = _DATE_STRING_RE.findall(text)
    for chunk in found_dates:
        dates.add(chunk)
        if re.match(r"\d{4}-", chunk):
            facts.add(float(chunk[:4]))
    if _ID_KEY_RE.search(key_hint):
        for run in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", text):
            identifiers.add(float(run.replace(",", "")))
        return
    if re.fullmatch(r"-?\d[\d,]*(?:\.\d+)?", text.strip()):
        facts.add(float(text.strip().replace(",", "")))
        return
    stripped = _DATE_STRING_RE.sub(" ", text)
    # '27608-2025-10' is one ERP reference: it belongs to the id bucket, and its
    # month part must not become a count a claim can borrow
    for chunk in _REFERENCE_TOKEN_RE.findall(stripped):
        for run in re.findall(r"\d+", chunk):
            identifiers.add(float(run))
    stripped = _REFERENCE_TOKEN_RE.sub(" ", stripped)
    for match in re.finditer(r"(?<!\d)\d+(?:,\d{3})*(?:\.\d+)?(?!\d)", stripped):
        facts.add(float(match.group(0).replace(",", "")))


def allowed_numbers(tool_results: list[dict]) -> tuple[set[float], set[float], set[str]]:
    """(amounts/counts/days, ids, date strings) a chat answer may write."""
    facts: set[float] = set()
    identifiers: set[float] = set()
    dates: set[str] = set()
    _harvest({"tool_results": tool_results}, facts, identifiers, dates)
    return facts, identifiers, dates


def _mask(text: str, spans: list[tuple[int, int]]) -> str:
    """Blank out ranges so masked-out tokens are not checked twice."""
    characters = list(text)
    for start, end in spans:
        for index in range(start, min(end, len(characters))):
            characters[index] = " "
    return "".join(characters)


def validate_numeric_claims(
    text: str, tool_results: list[dict], prior_text: str = ""
) -> list[str]:
    """Reject amounts, counts, IDs and dates the tools never returned."""
    text = normalize_digits(text or "")
    if not re.search(r"\d", text):
        return []

    facts, identifiers, dates = allowed_numbers(tool_results)
    prior_facts: set[float] = set()
    prior_ids: set[float] = set()
    prior_dates: set[str] = set()
    _harvest({"prior": prior_text or ""}, prior_facts, prior_ids, prior_dates)
    facts |= prior_facts
    identifiers |= prior_ids
    dates |= prior_dates

    errors: list[str] = []
    scanned = text

    # 1) dates: only when the very same date exists in this turn's data
    date_spans = [(m.start(), m.end()) for m in _DATE_STRING_RE.finditer(scanned)]
    for match in _DATE_STRING_RE.finditer(scanned):
        written = match.group(0)
        if not any(written == known or known.startswith(written) for known in dates):
            errors.append(f"unsupported date {written}")
    scanned = _mask(scanned, date_spans)

    # 0) then a whole ERP reference such as "33563-2026-10": one identifier, so
    # only its leading number has to be real
    reference_spans = []
    for match in _REFERENCE_TOKEN_RE.finditer(scanned):
        token = match.group(0)
        reference_spans.append((match.start(), match.end()))
        leading = re.match(r"\d+", normalize_digits(token))
        if leading is None or not _matches(float(leading.group(0)), 0.0, facts | identifiers):
            errors.append(f"unsupported reference {token}")
    scanned = _mask(scanned, reference_spans)

    # 2) rounded money ("$147k", "$2.4M") may only differ by the rounding
    suffix_spans = []
    for match in _SUFFIXED_MONEY_RE.finditer(scanned):
        value, tolerance = _number_value(match.group(0))
        suffix_spans.append((match.start(), match.end()))
        if not _matches(value, tolerance, facts):
            errors.append(f"unsupported amount {match.group(0).strip()}")
    scanned = _mask(scanned, suffix_spans)

    # 3) dollar amounts must be money Python computed
    errors.extend(
        f"unsupported amount {amount:g}"
        for amount in invented_amounts(scanned, {"tool_results": tool_results, "prior": prior_text or ""})
        if not _matches(amount, 0.0, facts)
    )

    # 4) every remaining number: a value the tools computed, or an id/date that
    #    the sentence presents as an identifier
    for match in _NUMBER_RE.finditer(scanned):
        token = match.group(0)
        value, tolerance = _number_value(token)
        before = scanned[: match.start()]
        window = scanned[max(0, match.start() - 2): match.end() + 2]
        in_id_context = bool(_ID_CONTEXT_RE.search(before[-40:])) or bool(_REFERENCE_RE.search(window))
        if in_id_context:
            if not _matches(value, tolerance, facts | identifiers):
                errors.append(f"unsupported id {token.strip()}")
            continue
        if not _matches(value, tolerance, facts):
            # A large figure that the tools returned as an id may still be
            # listed as one ('unpaid: 33245, 33407') even without the word
            # 'invoice' in front of it; a small count may not borrow it.
            listed = bool(_ID_LIST_CUE_RE.search(before[-40:])) or bool(
                re.match(r"\s*\(", scanned[match.end(): match.end() + 3])
            )
            if listed and _matches(value, tolerance, identifiers) and value >= 1_000:
                continue
            label = "amount" if before.rstrip().endswith("$") else "number"
            errors.append(f"unsupported {label} {token.strip()}")

    # Currency claims must match both a tool value and the Python format used
    # elsewhere (for example, $2,379.50 rather than $2379.5).
    for match in _MONEY_RE.finditer(scanned):
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
    llm = model or build_model("chat")
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
                # parallel calls are allowed: one question can need payments and
                # invoices at the same time, and every call counts against the cap
                try:
                    bound = llm.bind_tools(tool_schemas, parallel_tool_calls=True)
                except TypeError:  # providers without the kwarg still work
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

