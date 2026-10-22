"""Read-only, tool-calling LangGraph chat agent."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time as monotonic_time
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app import db as app_db
from app.agents.chat_tools import CHAT_TOOL_MAP, CHAT_TOOLS, PeriodMixin, format_money, invoke_tool
from app.agents.graph import invented_amounts
from app.agents.llm import add_usage, build_model, empty_usage, usage_of
from app.config import settings
from app.services import periods
from app.services.events import get_event_service
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
   describe_data when you are not sure a field exists at all. For any comparison
   of lead pulls over time, call compare_lead_pulls before answering.

LEAD PULLS. The current leads collection is one upserted snapshot, not a pull
history. compare_lead_pulls reads separately retained pulls. If it returns
available=false, tell the user plainly that historical lead pulls are not
retained and name the required capability it returns; you may then summarise
the current lead state with get_leads. Never present the current snapshot as an
earlier pull, never invent a previous count, and never reduce this to "the
figure could not be verified" when the real cause is missing history.
3. Call several tools in one turn when the question needs them; you have eight
   tool calls per question. A truncated answer (total_count larger than the rows
   you got) must be described as a page, not as everything.

PAGES. A list tool answers one page and says exactly which: offset, limit,
total_count, returned, remaining_count, has_more, next_offset.
- Report those numbers as the tool wrote them. "34 payments match, 29 are listed
  here and 5 remain" is a supported answer whenever those three numbers come
  from the page block - use it instead of apologising.
- When the user asks for all of them, "show all", "the rest", "yes" to fetching
  more, or the next batch: call the SAME tool again with the SAME filters and
  offset set to the previous next_offset, and keep going until has_more is false.
  Never change the filters or the date range while continuing a page.
- Never say you listed every record while has_more is true, and never say
  nothing matched when total_count is above zero.
- sum_amount is Python's total over EVERY matched record, whatever page you are
  on - sum_amount_covers says so. Describe it as the total for the whole
  filtered period, and the listed rows as a page of it. Never add up the rows in
  front of you and call that the total.
- Record numbers you copy from a page (Payment 7262, Customer 691, invoice refs)
  are identifiers the backend gave you; name them with their label so they read
  as identifiers, not as figures.
4. Python does all math, joins and counting. Copy amounts, dates, IDs and counts
   from tool results exactly; never add, average or count items yourself - the
   one exception is the page arithmetic in PAGES above, which the tools already
   computed for you. Never pass a local date for "today" - the ERP business day
   is US Central and the metrics tool already applies it.
5. If a value really is not stored (for example a payment method), call
   describe_data once, say which field is missing in one short sentence, and do
   not repeat that sentence in the same answer.
6. Follow-ups refer to the conversation: "I need that name", "and the balance?"
   mean the customer or record of the previous answer - reuse it, and re-call a
   tool if you need fresh numbers. When the thread carries a LAST QUERY record, a
   short reply like "yes", "show all", "the rest" or "get the rest" continues that
   query: same tool, same filters, offset = its next_offset. Never answer a new
   question by repeating the previous answer, and never invent an intent the
   message does not state - ask one short question instead.
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
    last_query: dict
    query_context: dict
    business_date: Any
    database: Any
    events: Any
    task_id: str


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
# Words that introduce an identifier rather than a quantity. A payment id the
# tool really returned is written as "Payment 7262", and without "payment" in
# this list the guard reads it as a plain number, demands it be an amount, and
# throws away a correct answer. An id that is not in the evidence is still
# refused - the label only decides which evidence set it is checked against.
_ID_CONTEXT_RE = re.compile(
    r"(?i)\b(invoice|inv|quote|quotation|order|lead|customer|rep|run|thread|ref|reference|"
    r"payment|payments|txn|transaction|transactions|receipt|slip|voucher|"
    r"id|ids|no|number|#)\b[^0-9$]{0,18}$"
    # ... or the ERP's own field name written out: "payment_id 7263". `\bpayment\b`
    # cannot match inside "payment_id", because "_" is a word character, so a
    # model that echoes the column name it was given was read as claiming a bare
    # number, refused, and the whole answer was discarded.
    r"|\b\w+_(?:id|ids|no|num|number|ref|reference|code)\b[^0-9$]{0,18}$"
)
_MONEYISH_RE = re.compile(r"^\s*-?\$[\d,]+(?:\.\d+)?\s*$")
_NUMBER_RE = re.compile(
    r"(?<![\w.])[\d۰-۹٠-٩][\d۰-۹٠-٩,]*(?:\.[\d۰-۹٠-٩]+)?(?:\s*[kKmM])?(?![\w])"
)
_MONEY_RE = re.compile(r"\$\s*(-?[\d,]+(?:\.\d+)?)")
# Every amount in this ERP is USD (no payments_flat row carries a currency
# field), so a figure written in another currency is not a formatting variant -
# it is a claim the records cannot support.
_OTHER_CURRENCY_RE = re.compile(
    r"(?i)(?:\b(eur|gbp|jpy|cny|inr|pkr|aed|sar)\b\s*[-\d]|[€£¥₨]\s*-?[\d,])"
)
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

# ------------------------------------------------- exhaustive-listing claims
#
# A partial page already reports its own counts honestly, and `total_count` is a
# number the model may legitimately cite - so the numeric guard cannot stop the
# sentence "all 40 payments are listed above" while showing 10 rows. The claim
# itself, not its arithmetic, is what is false. This catches only the wording
# shapes that assert nothing remains of the *listing*; a page position, a
# fraction, or the backend's own coverage note never match.
_CLAIM_NOUNS = r"(?:payments?|invoices?|quotes?|orders?|records?|rows|results?|leads?|list|items?)"
_CLAIM_VERBS = r"listed|shown|displayed|returned|retrieved|provided|fetched|given|above|here|included"
_CLAIM_TAIL = r"[^.?!;\n]{0,45}?\b(?:" + _CLAIM_VERBS + r")\b"
_EXHAUSTIVE_RE = re.compile(
    "|".join(
        (
            r"\b(?:all|every|each|both)\b[^.?!;\n]{0,60}?\b(?:" + _CLAIM_NOUNS + r")" + _CLAIM_TAIL,
            r"\b(?:the|this)\b[^.?!;\n]{0,20}?\b(?:complete|full|entire|whole)\b[^.?!;\n]{0,60}?\b(?:"
            + _CLAIM_NOUNS + r")" + _CLAIM_TAIL,
            r"\bno\b[^.?!;\n]{0,30}?\b(?:more|remaining|other|further)\b[^.?!;\n]{0,40}?\b(?:"
            + _CLAIM_NOUNS + r")[^.?!;\n]{0,40}?(?:remain|left|exist|to\s+(?:fetch|show|list))",
            r"\b(?:thats|that\s+is|that\s+s)\s+all\b[^.?!;\n]{0,40}?\b(?:" + _CLAIM_NOUNS + r")\b",
            r"\b(?:all|every)\s+(?:" + _CLAIM_NOUNS + r")\s+(?:have|has|was|were)\s+(?:been\s+)?"
            r"(?:listed|retrieved|fetched|shown|returned)\b",
        )
    ),
    re.I,
)


def walk_completed(tool_results: list[dict]) -> bool:
    """True when some page this turn actually closed the walk.

    Reading the pages together is what makes "all 67 are listed above" honest
    after a walk that reached the end, and still a false claim on page 1 of 7.
    """
    for entry in tool_results:
        result = entry.get("result") if isinstance(entry, dict) else None
        if isinstance(result, dict) and result.get("all_records_returned") is True:
            return True
    return False


def paged_but_incomplete(tool_results: list[dict]) -> bool:
    """True when a paged result is still open and nothing closed the walk."""
    saw_page = False
    for entry in tool_results:
        result = entry.get("result") if isinstance(entry, dict) else None
        if not isinstance(result, dict) or "has_more" not in result:
            continue
        saw_page = True
        if result.get("all_records_returned") is True:
            return False
    return saw_page

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
    """(amounts/counts/days, ids, date strings) a chat answer may write.

    A paged tool result carries its own arithmetic - ``total_count``,
    ``returned``, ``remaining_count``, ``next_offset`` - so "5 of the 34 remain"
    is a copied fact rather than a model calculation. That is why the guard needs
    no derived-number channel: the backend does the subtracting, and a remainder
    it did not compute is still rejected.
    """
    facts: set[float] = set()
    identifiers: set[float] = set()
    dates: set[str] = set()
    _harvest({"tool_results": tool_results}, facts, identifiers, dates)
    return facts, identifiers, dates


_ASKED_NUMBER_RE = re.compile(r"(?<![\$\d.,])\d[\d,]*(?:\.\d+)?(?![\d.,])")


def asked_numbers(question: str) -> set[float]:
    """Plain numbers the user wrote in their own question.

    "last 7 days", "the top 5 customers", "older than 30 days" - the reader gave
    the model that number, so repeating it is a restatement, not an invention.
    A figure the user wrote next to a ``$`` is deliberately excluded: the amount
    someone asks about is not evidence that the amount is true, so a user-typed
    total still has to come back from a tool.
    """
    text = normalize_digits(question or "")
    asked: set[float] = set()
    for match in _ASKED_NUMBER_RE.finditer(text):
        if "$" in text[max(0, match.start() - 3): match.start()]:
            continue
        try:
            asked.add(float(match.group(0).replace(",", "")))
        except ValueError:
            pass
    return asked


def _mask(text: str, spans: list[tuple[int, int]]) -> str:
    """Blank out ranges so masked-out tokens are not checked twice."""
    characters = list(text)
    for start, end in spans:
        for index in range(start, min(end, len(characters))):
            characters[index] = " "
    return "".join(characters)


def validate_numeric_claims(
    text: str, tool_results: list[dict], prior_text: str = "", question: str = ""
) -> list[str]:
    """Reject amounts, counts, IDs and dates the tools never returned."""
    text = normalize_digits(text or "")

    # Checked before the digit shortcut below, because the falsest sentence a
    # paginated answer can make needs no numbers at all: "no more payments
    # remain" while the walk is still open on page 1 of 7.
    errors: list[str] = []
    if paged_but_incomplete(tool_results):
        claim = _EXHAUSTIVE_RE.search(text)
        if claim:
            errors.append(f"claims a complete listing while a page is still open: {claim.group(0)[:60]}")

    if not re.search(r"\d", text):
        return errors

    facts, identifiers, dates = allowed_numbers(tool_results)
    restated = asked_numbers(question)
    prior_facts: set[float] = set()
    prior_ids: set[float] = set()
    prior_dates: set[str] = set()
    _harvest({"prior": prior_text or ""}, prior_facts, prior_ids, prior_dates)
    facts |= prior_facts
    identifiers |= prior_ids
    dates |= prior_dates

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

    # A numbered list is formatting, not a claim: "1. Payment 7262" asserts
    # nothing about 1. Only a line-leading small ordinal qualifies, so a figure
    # written inside prose is still checked.
    ordinal_spans = [
        (match.start(), match.end())
        for match in re.finditer(r"(?m)^[ \t]*(?:\d{1,3}[.)]|[-*]\s*\d{1,3}[.)])[ \t]+", scanned)
    ]
    scanned = _mask(scanned, ordinal_spans)

    # 4) every remaining number: a value the tools computed, a count their own
    #    page contract makes inevitable, or an id/date the sentence presents as
    #    an identifier
    for match in _NUMBER_RE.finditer(scanned):
        token = match.group(0)
        value, tolerance = _number_value(token)
        before = scanned[: match.start()]
        window = scanned[max(0, match.start() - 2): match.end() + 2]
        in_id_context = bool(_ID_CONTEXT_RE.search(before[-40:])) or bool(_REFERENCE_RE.search(window))
        in_money_context = before.rstrip().endswith("$")
        # A number the reader wrote themselves ("the last 7 days", "10 at a
        # time") is a restatement wherever it appears - including next to the
        # word "payments", which makes the sentence look like an id context.
        restates = _matches(value, tolerance, restated) and not in_money_context
        if in_id_context:
            if not restates and not _matches(value, tolerance, facts | identifiers):
                errors.append(f"unsupported id {token.strip()}")
            continue
        if not _matches(value, tolerance, facts):
            if restates:
                continue
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

    # 5) a figure stated in a currency the ERP does not hold
    if _OTHER_CURRENCY_RE.search(text):
        errors.append("amount stated in a currency other than USD")

    # Currency claims must match both a tool value and the Python format used
    # elsewhere (for example, $2,379.50 rather than $2379.5).
    for match in _MONEY_RE.finditer(scanned):
        value = float(match.group(1).replace(",", ""))
        expected = f"${int(value):,}" if value == int(value) else f"${value:,.2f}"
        # The same verified money in the same currency may be written with its
        # cents spelled out; that is a wording choice, not a different figure.
        accepted = {expected}
        if value == int(value):
            accepted.add(f"${int(value):,.2f}")
        if match.group(0).replace(" ", "") not in accepted:
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


_EVIDENCE_NOUNS = {
    "get_payments": "payments", "get_revenue": "payments", "get_customer_profile": "payments",
    "get_invoices": "invoices", "get_top_unpaid": "invoices", "get_customer_context": "invoices",
    "get_quotes": "quotes", "get_top_open_quotes": "quotes",
    "get_orders": "orders", "get_leads": "leads", "get_stuck_leads": "leads",
}

# The human words for a period the backend resolved. The answer formatter uses
# these so the user-facing wording comes from the resolved range, never from the
# model's guess of what "this week" meant.
_PERIOD_PHRASES = {
    "today": "today", "yesterday": "yesterday",
    "this_week": "this week", "last_week": "last week", "this_week_to_date": "this week so far",
    "this_month": "this month", "last_month": "last month", "this_month_to_date": "this month so far",
    "this_quarter": "this quarter", "last_quarter": "last quarter",
    "this_year": "this year", "last_year": "last year",
    "last_7_days": "the last 7 days", "last_30_days": "the last 30 days", "last_90_days": "the last 90 days",
}

# A period code the model may echo verbatim from the tool ("(last_week)").
_PERIOD_ECHO_RE = re.compile(r"\b(?:this|last|current)_[a-z0-9]+\b", re.I)
# One rendered payment row: "Payment 7263" / "payment #7,263".
_PAYMENT_ROW_RE = re.compile(r"\bpayments?\s+#?\s*\d[\d,]*\b", re.I)
# A claim that a number of payments were *displayed*: "27 payments listed".
# Only the noun+listing-verb shape counts; "N matching payments" is a total, not
# a display claim, and is deliberately not matched here.
_DISPLAY_CLAIM_RE = re.compile(
    r"\b(\d[\d,]*)\s+payments?\b[^.!?;\n]{0,24}?\b(listed|shown|displayed|shown\s+here|shown\s+above|"
    r"displayed\s+here|listed\s+here|listed\s+above|below|above|included|here)\b",
    re.I,
)


def _period_phrase(named_period: str | None) -> str | None:
    return _PERIOD_PHRASES.get(named_period) if named_period else None


def _answer_consistency_errors(text: str, tool_results: list[dict], named_period: str | None) -> list[str]:
    """Wording/claim consistency the numeric guard cannot see.

    These never relax numeric verification - they only ADD rejections, so the
    answer either self-corrects on regeneration or falls through to the
    deterministic evidence summary:

      * the prose names a period that contradicts the range the backend resolved,
        or echoes the machine code ("(last_week)") instead of a phrase;
      * the prose says more payments are shown than are actually present in it.
    """
    if not text:
        return []

    errors: list[str] = []
    expected = _PERIOD_PHRASES.get(named_period) if named_period else None
    if expected:
        lowered = text.casefold()
        for phrase in _PERIOD_PHRASES.values():
            if phrase != expected and phrase in lowered:
                errors.append(f"period wording '{phrase}' contradicts the resolved range ({expected})")
    if _PERIOD_ECHO_RE.search(text):
        errors.append("the answer echoes a backend period code instead of a plain phrase")

    # Payment rows actually rendered vs a claim that more were displayed.
    rows = len(_PAYMENT_ROW_RE.findall(text))
    if rows >= 1:
        for match in _DISPLAY_CLAIM_RE.finditer(text):
            claimed, _ = _number_value(match.group(1))
            if claimed > rows:
                errors.append(f"claims {int(claimed)} payments shown but only {rows} appear in the answer")
                break
    return list(dict.fromkeys(errors))


def _evidence_summary(tool_results: list[dict], period_phrase: str | None = None) -> str | None:
    """A deterministic answer built only from this turn's verified tool evidence.

    When the model's prose is rejected (by the numeric guard or by the wording /
    displayed-count consistency checks), the verified total must not be thrown
    away with the fabricated numbers around it. This states exactly what a backend
    tool computed - the money total, the record counts, the date range and the
    period the range resolves to - and nothing the model claimed. Every figure
    comes from the same tool_results the guard reads, so the summary is always
    supported, and it is re-validated before it is returned (returning None keeps
    the generic fallback for anything that is not a clean, evidence-backed line).

    Pagination stays honest, and the summary never claims rows it did not render:
    an unfinished walk is reported as a total with records still on later pages,
    never as "N listed here".
    """
    money_evidence = None
    count_evidence = None
    for entry in tool_results:
        if not isinstance(entry, dict):
            continue
        evidence = tool_evidence(str(entry.get("tool")), entry.get("result"))
        if not evidence:
            continue
        if "money_total" in evidence and money_evidence is None:
            money_evidence = evidence
        if "record_count" in evidence and count_evidence is None:
            count_evidence = evidence

    evidence = money_evidence or count_evidence
    if evidence is None:
        return None

    tool = str(evidence.get("tool"))
    noun = _EVIDENCE_NOUNS.get(tool, "records")
    window = evidence.get("window") or {}
    span = ""
    if window.get("date_from") and window.get("date_to"):
        span = f" ({window['date_from']} to {window['date_to']})"
    scope = f" for {period_phrase}" if period_phrase else ""

    if "money_total" in evidence:
        amount = format_money(evidence["money_total"])
        if "records_total" in evidence:
            total = evidence["records_total"]
            if evidence.get("complete"):
                sentence = f"Verified from backend records{scope}: {amount} across all {total} matching {noun}{span}."
            else:
                remaining = evidence.get("records_remaining")
                tail = f"; {remaining} more are on later pages and can be fetched." if remaining else "; more records are on later pages."
                sentence = f"Verified from backend records{scope}: {amount} across {total} matching {noun}{span}" + tail
        elif "record_count" in evidence:
            count = evidence["record_count"]
            sentence = f"Verified from backend records{scope}: {amount} across {count} matching {noun}{span}."
        else:
            sentence = f"Verified from backend records{scope}: total {amount}{span}."
    else:
        if "records_total" in evidence:
            total = evidence["records_total"]
            if evidence.get("complete"):
                sentence = f"Verified from backend records{scope}: all {total} matching {noun}{span}."
            else:
                remaining = evidence.get("records_remaining")
                tail = f"; {remaining} more are on later pages and can be fetched." if remaining else "; more records are on later pages."
                sentence = f"Verified from backend records{scope}: {total} matching {noun}{span}" + tail
        else:
            count = evidence["record_count"]
            sentence = f"Verified from backend records{scope}: {count} matching {noun}{span}."

    summary = sentence.strip()
    if not summary or validate_numeric_claims(summary, tool_results, "", ""):
        return None
    return summary


def _named_period_args(name: str, business_date: Any, args_model: Any, raw_args: Any) -> Any:
    """Force the server-resolved window on a tool that accepts a date range.

    The user's message named a period, so that name - never a range the model
    invented (or left out) - decides the window, and "this week" means the same
    thing every time. A tool that takes no date range is passed through unchanged.
    Explicit dates the *user* typed never reach here: `detect_period_name` returns
    None for a message that carries a literal date, so those still win.
    """
    if not (isinstance(args_model, type) and issubclass(args_model, PeriodMixin)):
        return raw_args
    start, end = periods.resolve_period(name, today=business_date)
    forced = dict(raw_args or {})
    forced.pop("period", None)
    forced["date_from"] = start.isoformat()
    forced["date_to"] = end.isoformat()
    return forced


# ---------------------------------------------------------------------------
# follow-up query context
#
# "show all", "get the rest", "yes" and "fetch payments" only mean something
# against the query the previous turn actually ran. The page block a paged tool
# reported is therefore kept on the thread - the tool, its filters, and where its
# page stopped - so a follow-up continues that exact result set instead of
# re-asking with a widened date range or a different collection. Rows themselves
# are never stored: continuing a query needs the query, not a copy of the data.
# ---------------------------------------------------------------------------

PAGE_KEYS = ("offset", "limit", "total_count", "returned", "has_more")
_MAX_CONTEXT_ARGS = 12
_MAX_CONTEXT_VALUE_CHARS = 120


def _page_block(result: Any) -> dict | None:
    """The result of a paged record tool, or None when it is not one."""
    if not isinstance(result, dict) or any(key not in result for key in PAGE_KEYS):
        return None
    if not all(
        isinstance(result.get(key), int) and not isinstance(result.get(key), bool)
        for key in ("offset", "limit", "total_count", "returned")
    ):
        return None
    return result


def _bounded_args(args: Any) -> dict:
    """A scalar-only, size-capped copy of the arguments one tool call used."""
    if not isinstance(args, dict):
        return {}
    clean: dict[str, Any] = {}
    for key, value in list(args.items())[:_MAX_CONTEXT_ARGS]:
        if value is None or isinstance(value, (bool, int, float)):
            clean[str(key)] = value
        else:
            clean[str(key)] = str(value)[:_MAX_CONTEXT_VALUE_CHARS]
    return clean


# ---------------------------------------------------------------------------
# result evidence
#
# A paged lookup already knows how much it established: how many records
# matched, how many it delivered, how many are still out there, and the money
# Python totalled over all of them. That is the difference between an answer a
# reader can trust and a sentence they have to take on faith, so it travels to
# the browser next to the answer. Only aggregates and the query window cross
# this boundary - never a row, a customer name or a contact.
# ---------------------------------------------------------------------------

# The aggregate keys a tool totals in Python. `total_payments` is get_revenue's
# own spelling and `total_amount` the customer-context one; both are sums the
# backend computed, so an answer may cite them and the reader may inspect them.
_MONEY_KEYS = (
    "sum_amount",
    "sum_balance",
    "total_balance",
    "revenue_total",
    "total_payments",
    "total_amount",
)
_COUNT_KEYS = ("payment_count", "invoice_count", "quote_count", "order_count", "lead_count")


def _money_value(result: dict, key: str) -> Any:
    """A money aggregate as a number, whatever format the tool chose."""
    value = result.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        stripped = value.replace("$", "").replace(",", "").strip()
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def tool_evidence(name: str, result: Any) -> dict | None:
    """What one tool call actually proved, or None if it proved nothing.

    A paged lookup reports how much of its result set it delivered. An
    aggregate tool (a revenue total, one customer's context) has no pages but
    still computes money in Python, and that figure is worth showing next to the
    answer too - otherwise a correct total looks exactly like an invented one.
    """
    if not isinstance(result, dict):
        return None
    page = _page_block(result)
    evidence: dict[str, Any] = {"tool": name}
    if page is not None:
        evidence["records_total"] = page["total_count"]
        evidence["records_returned"] = page["returned"]
        evidence["records_remaining"] = page.get("remaining_count")
        # the tool's own verdict on whether the walk is complete - which is not
        # simply "nothing left after this offset" (a page past the end delivers
        # nothing yet proves nothing)
        evidence["complete"] = page.get("all_records_returned") is True
    for key in _MONEY_KEYS:
        money = _money_value(result, key)
        if money is not None:
            evidence["money_label"] = key
            evidence["money_total"] = money
            break
    for key in _COUNT_KEYS:
        if isinstance(result.get(key), (int, float)) and not isinstance(result.get(key), bool):
            evidence["record_count"] = result[key]
            break
    covers = result.get("sum_amount_covers")
    if isinstance(covers, str):
        evidence["money_covers"] = covers
    filters = result.get("filters_applied")
    if isinstance(filters, dict):
        window = {
            key: filters[key]
            for key in ("date_from", "date_to")
            if isinstance(filters.get(key), str)
        }
        if window:
            evidence["window"] = window
    else:
        # aggregate tools carry the range at the top level instead
        window = {
            key: result[key]
            for key in ("date_from", "date_to")
            if isinstance(result.get(key), str)
        }
        if window:
            evidence["window"] = window
    if page is None and "money_total" not in evidence and "record_count" not in evidence:
        return None
    return evidence


def evidence_counts(evidence: dict | None) -> dict:
    """The safe subset of the evidence that may be broadcast in an event."""
    if not evidence:
        return {}
    payload = {
        key: evidence[key]
        for key in ("records_total", "records_returned", "records_remaining", "record_count")
        if evidence.get(key) is not None
    }
    if "complete" in evidence:
        payload["complete"] = evidence["complete"]
    return payload


def query_context(tool: str, args: Any, page: dict, question: str = "") -> dict:
    """The bounded record a follow-up continues from."""
    return {
        "tool": tool,
        "args": _bounded_args(args),
        "offset": page["offset"],
        "limit": page["limit"],
        "total_count": page["total_count"],
        "returned": page["returned"],
        "remaining_count": page.get("remaining_count"),
        "has_more": page["has_more"],
        "next_offset": page.get("next_offset"),
        "question": (question or "")[:_MAX_CONTEXT_VALUE_CHARS],
        # when the walk was recorded, so a follow-up days later can tell that the
        # range it would continue is no longer the range the user would mean
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }


def _context_age_hours(last_query: dict) -> float | None:
    """Hours since the recorded query, or None when the record has no stamp.

    Records written before this field existed simply report no age; nothing is
    invented for them.
    """
    stamp = last_query.get("recorded_at")
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - moment).total_seconds() / 3600)


def _continuation_prompt(last_query: dict) -> str:
    """The previous query, restated as facts the follow-up must continue from."""
    args = json.dumps(last_query.get("args") or {}, ensure_ascii=False, sort_keys=True, default=str)
    outstanding = (
        f"{last_query.get('remaining_count')} are still outstanding and the next page starts at "
        f"offset {last_query.get('next_offset')}"
        if last_query.get("has_more")
        else "nothing is outstanding, the whole result set has been delivered"
    )
    age = _context_age_hours(last_query)
    staleness = (
        f"This record is about {round(age)} hour(s) old. Continuing it keeps the range it was "
        "asked with, which is right for 'the rest' or 'show all' but wrong for a question that "
        "means now: if the user re-asks with 'this week', 'today' or 'latest', run a NEW query "
        "and say the earlier one covered an older range. "
        if age is not None and age >= 6
        else ""
    )
    return (
        "LAST QUERY ON THIS THREAD. "
        f"tool={last_query.get('tool')} args={args}; it matched {last_query.get('total_count')} "
        f"records and delivered {last_query.get('returned')} of them from offset "
        f"{last_query.get('offset')}; {outstanding}. It was asked as: "
        f"{str(last_query.get('question') or '')!r}.\n"
        f"{staleness}"
        "Read the new message against that record. 'the rest', 'show all', 'yes', 'get them all', "
        "'next' or a count of what is left mean call the SAME tool with the SAME args and offset set "
        "to the last next_offset, and keep going until has_more is false. 'again' or 'what did you say' "
        "mean repeat that query unchanged. A question about other data or other filters is a NEW query "
        "- call a tool afresh and let this record go. Never widen, drop or change the recorded date "
        "range or filters while continuing a page, and never answer a follow-up from a different "
        "collection. If the wording genuinely fits none of these, ask one short clarifying question."
    )


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
        validation_started = False
        usage = empty_usage()
        final_text = ""
        emitted_tokens = 0
        last_query = state.get("last_query") or None
        continuation = (
            _continuation_prompt(last_query)
            if last_query and last_query.get("tool") in registry
            else ""
        )
        prior_text = "\n".join(
            _message_text(message)
            for message in messages
            if isinstance(message, AIMessage)
        )
        # the recorded page counts are backend-computed facts, so an answer that
        # continues the previous query may cite them without calling a tool again
        prior_text = f"{prior_text}\n{continuation}" if continuation else prior_text
        contexts: list[dict] = []
        if continuation:
            for index in range(len(messages) - 1, -1, -1):
                if isinstance(messages[index], HumanMessage):
                    messages.insert(index, SystemMessage(content=continuation))
                    break

        # The user's own words decide the period, on the ERP business calendar -
        # so a named range cannot be re-decided by the model per call.
        named_period = periods.detect_period_name(_last_user_text(state["messages"]))
        business_date = state.get("business_date")

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
                        service = get_event_service()
                        task_id = state.get("task_id")
                        # real tool activity: the tool identifier only (never the
                        # arguments, which can carry a customer name or id)
                        if task_id:
                            await service.emit(
                                task_id=task_id, type="tool.started", agent_id="chat",
                                payload={"tool": name},
                            )
                        tool_started = monotonic_time.monotonic()
                        ok = True
                        effective_args = call.get("args") or {}
                        if named_period:
                            period_tool = registry.get(name)
                            if period_tool is not None:
                                effective_args = _named_period_args(
                                    named_period, business_date, period_tool.args_model, effective_args
                                )
                        try:
                            result = await invoke_tool(
                                name,
                                effective_args,
                                database=state.get("database"),
                                registry=registry,
                            )
                        except (ValidationError, KeyError, TypeError):
                            ok = False
                            result = {"error": "Tool arguments were invalid or the requested tool is unavailable."}
                        except Exception:
                            ok = False
                            log.exception("chat tool %s failed", name)
                            result = {"error": "The requested business data could not be loaded."}
                        evidence = tool_evidence(name, result) if ok else None
                        if task_id:
                            # aggregate counts only: no money, no rows, no filter
                            # values - this payload reaches every dashboard client
                            completed_payload = {
                                "tool": name,
                                "ok": ok,
                                "duration_ms": round((monotonic_time.monotonic() - tool_started) * 1000),
                            }
                            if ok:
                                completed_payload.update(evidence_counts(evidence))
                            await service.emit(
                                task_id=task_id, type="tool.completed", agent_id="chat",
                                payload=completed_payload,
                            )
                            # The data stages the tool genuinely performed, as
                            # their own events so the command center can draw
                            # them: a paged tool matched a set and delivered a
                            # page of it (filtering), and an aggregate tool
                            # totalled values in Python (aggregation). Counts
                            # and key names only - never money values or rows.
                            if ok and evidence:
                                page_block = _page_block(result)
                                if page_block is not None:
                                    filter_payload = {
                                        "tool": name,
                                        "matched": page_block["total_count"],
                                        "returned": page_block["returned"],
                                        "complete": page_block.get("all_records_returned") is True,
                                    }
                                    if page_block.get("remaining_count") is not None:
                                        filter_payload["remaining"] = page_block["remaining_count"]
                                    await service.emit(
                                        task_id=task_id, type="data.filter_completed",
                                        agent_id="chat", payload=filter_payload,
                                    )
                                aggregate_key = evidence.get("money_label") or (
                                    "record_count" if "record_count" in evidence else None
                                )
                                if aggregate_key:
                                    await service.emit(
                                        task_id=task_id, type="data.aggregation_completed",
                                        agent_id="chat",
                                        payload={"tool": name, "aggregate": aggregate_key},
                                    )
                        results.append({"tool": name, "result": result})
                        page = _page_block(result)
                        if ok and page is not None:
                            contexts.append(
                                query_context(name, effective_args, page, _last_user_text(messages))
                            )
                        if events is not None and evidence:
                            # additive: a client that ignores unknown frames is
                            # only poorer by the evidence, never broken by it
                            events.put_nowait({"event": "tool_result", "data": evidence})
                    messages.append(
                        ToolMessage(
                            content=json.dumps(result, ensure_ascii=False, default=str),
                            tool_call_id=call_id,
                            name=name or "unknown_tool",
                        )
                    )
                continue

            final_text = _format_answer_money(_message_text(response))
            period_phrase = _period_phrase(named_period)
            # A no-tool-call response IS the answer generation, and the guard
            # below is the validation stage: both are real boundaries in this
            # loop, so they are recorded as events for the command center.
            # Emission is best-effort (the event service never raises) and
            # changes nothing about the answer itself.
            task_id = state.get("task_id")
            if task_id:
                service = get_event_service()
                await service.emit(
                    task_id=task_id, type="chat.response_started", agent_id="chat",
                    payload={"attempt": regen_count + 1},
                )
                if not validation_started:
                    validation_started = True
                    await service.emit(
                        task_id=task_id, type="chat.validation_started", agent_id="chat",
                        payload={},
                    )
            # Wording/displayed-count consistency the numeric guard cannot see.
            # Computed on the model's prose before any substitution; it can only
            # ADD rejections, never remove one.
            consistency_errors = _answer_consistency_errors(final_text, results, named_period) if final_text else []
            if not final_text:
                # the model produced no prose: answer from what the tools actually
                # proved, or the honest fallback when they proved no figure.
                final_text = _evidence_summary(results, period_phrase) or _fallback_text(_last_user_text(messages))
            errors = validate_numeric_claims(
                final_text, results, prior_text, _last_user_text(messages)
            ) + consistency_errors
            if errors:
                log.warning("numeric validation failed: %s | text=%s", errors, final_text[:600])
            if not errors:
                if task_id:
                    await service.emit(
                        task_id=task_id, type="chat.validation_completed", agent_id="chat",
                        payload={"outcome": "passed" if regen_count == 0 else "corrected"},
                    )
                break
            if regen_count == 0:
                if events is not None and attempt_emitted:
                    events.put_nowait({"event": "reset", "data": {}})
                messages.append(
                    SystemMessage(
                        content=(
                            "Regenerate your answer once. Remove any amount or count that is not copied from this turn's tool results. "
                            "Page counts are supported evidence: total_count, returned, remaining_count, offset and next_offset were "
                            "computed by the tool, so keep them. While has_more is true you may not claim the listing was complete: "
                            "say which page this is and that the rest can be fetched. Describe the period with the plain words for the "
                            "range the backend resolved (for example 'last week'), never a different period or a code like (last_week). "
                            "Do not say a number of records are listed or shown unless that many records actually appear in your answer. "
                            "If the result is still unavailable, say the figure could not be verified. Reply in the user's language."
                        )
                    )
                )
                regen_count = 1
                continue
            if events is not None and attempt_emitted:
                events.put_nowait({"event": "reset", "data": {}})
            # The prose is still unsupported after one regeneration, so it is
            # discarded entirely - never shown - and, when the tools computed a
            # real figure, replaced with a backend-only summary of it. Fabricated
            # claims never reach the user; a verified total is never lost.
            if task_id:
                await service.emit(
                    task_id=task_id, type="chat.validation_completed", agent_id="chat",
                    payload={"outcome": "replaced"},
                )
            final_text = _evidence_summary(results, period_phrase) or _fallback_text(_last_user_text(messages))
            break

        return {
            "messages": messages,
            "tool_results": results,
            "tools_used": used,
            "usage": usage,
            "final_text": final_text,
            "emitted_tokens": emitted_tokens,
            # a turn that paged replaces the record; one that did not leaves the
            # previous query in place, so "yes" can still resume it
            "query_context": contexts[-1] if contexts else last_query,
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
    task_id: str | None = None,
    last_query: dict | None = None,
) -> dict:
    today = datetime.now(ZoneInfo("Asia/Karachi")).date().isoformat()
    business_date = datetime.now(ERP_TZ).date()
    business_today = business_date.isoformat()
    system = (
        f"{CHAT_SYSTEM}\nCurrent date in the user's timezone (Asia/Karachi): {today}. "
        f"Current ERP business date ({ERP_TZ}): {business_today}. Business records use the "
        "ERP business calendar. When the user names a period ('this week', 'today', "
        "'last month'), pass it as the tool's `period` argument and let the server resolve "
        "the dates - never turn a named period into your own date_from/date_to, because "
        "'this week' and 'the last 7 days' are different ranges and only the first has one "
        "correct answer. Send date_from/date_to only when the user wrote exact dates. Use "
        "get_dashboard_metrics for KPIs rather than sending the local date to get_revenue. "
        "Weeks run Monday to Sunday, a 'last ...' period is the previous complete one, and all "
        "date ranges are inclusive."
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
        "last_query": last_query or {},
        "business_date": business_date,
        "database": database,
        "events": events,
        "task_id": task_id,
    }
    result = await graph.ainvoke(initial)
    return {
        "text": result.get("final_text", ""),
        "tools_used": result.get("tools_used", []),
        "tool_results": result.get("tool_results", []),
        "tokens": result.get("usage") or empty_usage(),
        "emitted_tokens": int(result.get("emitted_tokens") or 0),
        "query_context": result.get("query_context") or None,
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
    task_id: str | None = None,
) -> dict:
    """Run one chat turn, then persist its text history and token log.

    Each turn gets a unique task id (thread_id stays the conversation key), so
    the command center can correlate a chat answer with the real tool calls that
    produced it. Task, agent and (in the graph) tool events are emitted around
    the actual work; a broken event stream never changes the turn's outcome.
    """
    db = database if database is not None else app_db.get_db()
    task_id = task_id or uuid.uuid4().hex
    service = get_event_service()
    previous = await db.chat_threads.find_one({"thread_id": thread_id}) or {}
    turns = trim_turns(previous.get("turns") or [])
    last_query = previous.get("last_query") or None
    started = datetime.now(timezone.utc).isoformat()
    turn_started = monotonic_time.monotonic()
    # The prompt is part of the durable task record so the history drawer can
    # show what was actually asked. It is clipped to the event schema's safe
    # string limit; no customer records or tool results are included.
    await service.emit(
        task_id=task_id,
        type="task.created",
        agent_id="chat",
        payload={"thread_id": thread_id, "prompt": message.strip()[:300]},
    )
    await service.emit(
        task_id=task_id, type="task.started", agent_id="chat", payload={"thread_id": thread_id}
    )
    await service.emit(task_id=task_id, type="agent.started", agent_id="chat", payload={})
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
            task_id=task_id,
            last_query=last_query,
        )
        updated_turns = trim_turns(
            [
                *turns,
                {"question": message, "answer": outcome["text"], "created_at": started, "task_id": task_id},
            ]
        )
        await db.chat_threads.update_one(
            {"thread_id": thread_id},
            {
                "$set": {
                    "turns": updated_turns,
                    # only the bounded page record (tool, filters, cursor) - never rows
                    "last_query": outcome.get("query_context") or last_query,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            },
            upsert=True,
        )
        status = "complete"
        duration_ms = round((monotonic_time.monotonic() - turn_started) * 1000)
        await service.emit(
            task_id=task_id, type="agent.completed", agent_id="chat",
            payload={"tools_used": len(outcome.get("tools_used", [])), "duration_ms": duration_ms},
        )
        await service.emit(
            task_id=task_id, type="task.completed", agent_id="chat",
            payload={
                "tools_used": len(outcome.get("tools_used", [])),
                "total_tokens": (outcome.get("tokens") or {}).get("total_tokens", 0),
                "duration_ms": duration_ms,
            },
        )
        outcome["task_id"] = task_id
        return outcome
    except Exception as exc:
        await service.emit(
            task_id=task_id, type="agent.failed", agent_id="chat",
            payload={"error": type(exc).__name__},
        )
        await service.emit(
            task_id=task_id, type="task.failed", agent_id="chat",
            payload={"error": type(exc).__name__},
        )
        raise
    finally:
        tokens = (outcome or {}).get("tokens") or empty_usage()
        await db.chat_logs.insert_one(
            {
                "thread_id": thread_id,
                "task_id": task_id,
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
    task_id: str | None = None,
):
    """Yield tool progress, answer text chunks, and the final SSE payload.

    The response format is unchanged; the turn's task id is persisted
    server-side (in the stored turn and chat log) so the command center can
    later link an answer to the real tool events behind it.
    """
    queue: asyncio.Queue = asyncio.Queue()
    task_id = task_id or uuid.uuid4().hex

    async def worker() -> None:
        try:
            # Announce this turn's task id before anything else so the client can
            # correlate the answer with the real task-event stream behind it. The
            # id is the same one every tool/agent event for this turn is stored
            # under; unknown event names are ignored by older clients.
            await queue.put(
                {"event": "task", "data": {"task_id": task_id, "thread_id": thread_id}}
            )
            outcome = await run_chat(
                thread_id,
                message,
                database=database,
                model=model,
                tools=tools,
                events=queue,
                task_id=task_id,
            )
            # Real streaming already sent deltas. Only models without astream
            # (tests, unusual providers) need the post-hoc chunk fallback.
            if not outcome.get("emitted_tokens"):
                for chunk in _text_chunks(outcome["text"]):
                    await queue.put({"event": "token", "data": {"text": chunk}})
            await queue.put(
                {
                    "event": "done",
                    "data": {
                        "text": outcome["text"],
                        "thread_id": thread_id,
                        "task_id": task_id,
                    },
                }
            )
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

