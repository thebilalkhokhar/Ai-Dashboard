"""LangGraph pipeline: one agent run.

    load_signals (code)  ->  negotiation (LLM, <=5 concurrent)
                         ->  collections (LLM, <=5 concurrent)
                         ->  lead_followup (LLM, <=5 concurrent)
                         ->  group_by_customer (code)
                         ->  master (LLM)
                         ->  save (code)

Rules encoded here, never left to the LLM:
  * each sub-agent sees only the top MAX_ITEMS_PER_AGENT items of its signal;
  * money validation: every dollar amount in a message_draft must appear in
    that item's input data. One regeneration on failure; if it still fails the
    item is stored with flag "needs_review" and message_draft=None;
  * 24-hour cache: identical input hash -> reuse the stored output, no new
    LLM call (so a 24/7 loop does not re-bill identical work);
  * the stalled-orders signal is NEVER fed to agents (unreliable);
  * lead follow-up (sub-agent 3) reuses the same guardrails: its drafts may
    not invent contacts or amounts, its `missing` list is Python-computed and
    copied, and its lead_id is always filled in by code, never by the model;
  * nothing is ever sent to customers - drafts are stored text only.
Everything lands in the Mongo collection `agent_runs`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from app import db as app_db
from app.config import settings
from app.services import processors
from app.services.processors import money_text, to_number
from app.services.erp_client import ERPClient
from app.agents.llm import (
    FakeCaller,
    StructuredCaller,
    add_usage,
    build_model,
    empty_usage,
    make_caller,
)
from app.agents.negotiation import negotiation_payload, NEGOTIATION_SYSTEM
from app.agents.collections import collections_payload, COLLECTIONS_SYSTEM
from app.agents.lead_followup import lead_followup_payload, LEAD_FOLLOWUP_SYSTEM
from app.agents.master import group_by_customer, run_master
from app.schemas.agents import (
    MasterOutput,
    NegotiationOutput,
    CollectionsOutput,
    LeadFollowupOutput,
)

CONCURRENCY = int(settings.AGENT_CONCURRENCY)
CACHE_HOURS = int(settings.AGENT_CACHE_HOURS)
MAX_ITEMS = int(settings.MAX_ITEMS_PER_AGENT)

AGENT_KINDS = ("negotiation", "collections", "lead_followup")


class RunState(TypedDict, total=False):
    run_id: str
    started_at: str
    negotiation_input: list[dict]
    collections_input: list[dict]
    lead_followup_input: list[dict]
    cached_hashes: dict
    signals_summary: dict
    negotiation_out: list[dict]
    collections_out: list[dict]
    lead_followup_out: list[dict]
    grouped: dict
    leads_grouped: list[dict]
    master: dict
    usage: dict
    calls: int
    errors: list[str]
    now_epoch: float
    regen_counts: dict
    saved_counts: dict
    # injected dependencies (never persisted)
    _database: Any
    _erp: Any
    _caller_factory: Callable
    _use_cache: bool


# ---------------------------------------------------------------------------
# pure helpers (unit-tested without Mongo or an LLM)
# ---------------------------------------------------------------------------


def input_hash(kind: str, payload: dict) -> str:
    """Stable hash of one item's full input (kind + JSON, sorted keys).

    KEPT for one-off/whole-payload hashing; the pipeline actually uses
    `input_hash` below built from STABLE fields only - see stable_cache_key -
    otherwise a volatile minute-level age value would defeat the 24h cache."""
    blob = json.dumps({"kind": kind, "payload": payload}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


_DOLLAR = r"(?:\$\s?([\d,]+(?:\.\d+)?))|(?:(\d[\d,]*(?:\.\d+)?)\s?(?:dollars|USD|usd))"


def _walk_numbers(node: Any, found: set[float]) -> None:
    """Every number that exists anywhere in the input payload."""
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, (int, float)):
        found.add(float(node))
        return
    if isinstance(node, str):
        # formatted money from the payload ('$56,600', '$2,379.50') is a legal
        # money reference: the validator must accept it verbatim
        text = node.strip().replace(",", "").replace("$", "").strip()
        for suffix in ("dollars", "dollar", "usd"):
            if text.lower().endswith(suffix):
                text = text[: -len(suffix)].strip()
        try:
            found.add(float(text))
        except ValueError:
            pass
        return
    if isinstance(node, dict):
        for value in node.values():
            _walk_numbers(value, found)
        return
    if isinstance(node, (list, tuple)):
        for value in node:
            _walk_numbers(value, found)


import re  # noqa: E402  (must come before the regex below)

_DOLLAR_RE = re.compile(r"\$\s?([\d][\d,]*(?:\.\d+)?)|([\d][\d,]*(?:\.\d+)?)\s(?:dollars?|USD|usd)", re.I)


def invented_amounts(draft: str, payload: dict) -> list[float]:
    """Dollar amounts in the draft whose value is NOT a money value in the payload.
    The pipeline runs this on every message_draft."""
    allowed = money_values(payload)
    bad: list[float] = []
    for match in _DOLLAR_RE.finditer(draft or ""):
        value = float((match.group(1) or match.group(2)).replace(",", ""))
        if not any(abs(value - allowed_value) < 0.005 for allowed_value in allowed):
            bad.append(value)
    return bad


# ---------------------------------------------------------------------------
# text rules on model output (money format, contact details, greetings)
# ---------------------------------------------------------------------------

# money written with a '$' or with decimal cents ("$36414.0", "28869.0");
# bare integers (invoice numbers, ages, counts) never match
_MONEY_TOKEN_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)|(\d[\d,]*\.\d+)")

_FORMATTED_MONEY_RE = re.compile(r"^-?\$\s?\d[\d,]*(?:\.\d+)?$")
_FORMATTED_MONEY_TOKEN_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s)>\"]+")
_PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]?\d{3}[\s.\-]?\d{4}")
_GREETING_RE = re.compile(
    r"^(\s*)(hi|hello|hey|dear)\s+([A-Za-z][A-Za-z'\-]+)(\s+team)?", re.I
)
# a raw float like 36414.0 is unformatted money; '$36,414' is fine
_UNFORMATTED_MONEY_RE = re.compile(r"\d{3,}\.\d(?!\d)")


def money_values(payload: Any) -> set[float]:
    """Every REAL money value in the payload (numbers or '$56,600' text).

    Only money fields and money that code already formatted inside text count.
    Days, counts and IDs are excluded, so '39.3 days late' can never become
    '$39.30' and cannot pass the money validator as if it were an amount.
    """
    found: set[float] = set()

    def add(value: Any) -> None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            found.add(float(value))
        elif isinstance(value, str) and _FORMATTED_MONEY_RE.match(value.strip()):
            found.add(to_number(value))

    def walk(node: Any, is_money: bool) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                walk(item, key in processors.MONEY_FIELDS)
        elif isinstance(node, list):
            for item in node:
                walk(item, is_money)
        elif isinstance(node, str):
            for match in _FORMATTED_MONEY_TOKEN_RE.finditer(node):
                found.add(to_number(match.group(0)))
            if is_money:
                add(node)
        elif is_money:
            add(node)

    walk(payload, False)
    return found


def normalise_money_text(text: str, payload: dict) -> str:
    """Rewrite raw / half-formatted money in model text as '$56,600'.

    Only numbers that carry a '$' or decimals AND match a real money value in
    the payload are touched, so invoice numbers, days and counts stay alone.
    """
    if not text:
        return text
    allowed = money_values(payload)

    def replace(match: "re.Match") -> str:
        raw = (match.group(1) or match.group(2) or "").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            return match.group(0)
        if any(abs(value - known) < 0.005 for known in allowed):
            return money_text(value)
        return match.group(0)

    return _MONEY_TOKEN_RE.sub(replace, text)


def invented_contacts(draft: str, payload: dict) -> list[str]:
    """Phones / emails / URLs in a draft that are NOT verbatim input data.

    Copying contact details is fine; inventing one is not. The string must
    appear character for character in the item's payload.
    """
    if not draft:
        return []
    haystack = json.dumps(payload, sort_keys=True, default=str)
    found: list[str] = []
    for regex in (_EMAIL_RE, _URL_RE, _PHONE_RE):
        for match in regex.finditer(draft):
            token = match.group(0).rstrip(".,;:)")
            if token and token not in haystack and token not in found:
                found.append(token)
    return found


def sanitise_greeting(draft: str, payload: dict) -> str:
    """A greeting first name must come from the input contact data.

    Otherwise the greeting becomes 'Hello <company> team', which cannot be
    wrong about who is reading it.
    """
    if not draft:
        return draft
    match = _GREETING_RE.match(draft)
    if not match:
        return draft
    name = match.group(3)
    lead = payload.get("lead")
    if isinstance(lead, dict):
        # lead payloads carry no contact blob - only the parsed name/company
        contact_blob = str(lead.get("contact_name") or "")
        company = str(lead.get("company") or "").strip()
    else:
        history = payload.get("customer_history") or {}
        contact = history.get("contact") or {}
        contact_blob = " ".join(str(value) for value in contact.values() if value)
        company = str(history.get("company_name") or "").strip()
    first_word = company.split()[0] if company else ""
    # 'Hello Polkadot team' greets the company, not a person - leave it
    if first_word and name.lower() == first_word.lower():
        return draft
    if re.search(rf"\b{re.escape(name)}\b", contact_blob, re.I):
        return draft  # the name really is in the contact data
    neutral = f"Hello {company} team" if company else "Hello"
    # match.end() also swallows a trailing ' team', so nothing is duplicated
    return neutral + draft[match.end():]


def has_unformatted_money(text: Any) -> bool:
    """True for raw model money like '36414.0' (False for '$36,414')."""
    return bool(text) and bool(_UNFORMATTED_MONEY_RE.search(str(text)))


def cache_text_is_clean(item: dict) -> bool:
    """A cached output may only be reused when its money is already formatted."""
    return not any(
        has_unformatted_money(item.get(field))
        for field in ("message_draft", "reason", "evidence")
    )


def normalise_master_output(master: MasterOutput, payload: dict) -> MasterOutput:
    """Apply the same money format to bullets, priorities and conflicts."""
    bullets = [normalise_money_text(text, payload) for text in master.bullets]
    priorities = [
        priority.model_copy(
            update={
                "why": normalise_money_text(priority.why, payload),
                "suggested_action": normalise_money_text(priority.suggested_action, payload),
            }
        )
        for priority in master.priorities
    ]
    conflicts = [normalise_money_text(text, payload) for text in master.conflicts_resolved]
    return master.model_copy(
        update={"bullets": bullets, "priorities": priorities, "conflicts_resolved": conflicts}
    )


def master_invented_amounts(master_dict: dict, payload: dict) -> list[float]:
    """Dollar amounts in the MASTER's bullets/priorities/conflicts that are not
    money values in the data Python sent it. Masters must not do math,"""
    allowed = money_values(payload)
    texts = list(master_dict.get("bullets") or [])
    for priority in master_dict.get("priorities") or []:
        texts.extend([str(priority.get("why") or ""), str(priority.get("suggested_action") or "")])
    texts.extend(str(c) for c in master_dict.get("conflicts_resolved") or [])
    bad: list[float] = []
    for text in texts:
        for match in _DOLLAR_RE.finditer(text or ""):
            value = float((match.group(1) or match.group(2)).replace(",", ""))
            if not any(abs(value - ok) < 0.005 for ok in allowed):
                bad.append(value)
    return list(dict.fromkeys(bad))


def cache_lookup(recent_runs: list[dict], items: list[dict], cutoff_epoch: float) -> dict[str, dict]:
    """hash -> saved output from a run newer than the cutoff.

    Only non-needs_review outputs may be reused (a bad answer is not a cache
    hit). Pure function so tests can drive it with a fake run history."""
    saved: dict[str, dict] = {}
    # oldest runs first so a newer (better) saved output overwrites an older one
    for run in sorted(recent_runs, key=lambda r: float(r.get("finished_epoch") or 0)):
        if float(run.get("finished_epoch") or 0) < cutoff_epoch:
            continue
        for kind in AGENT_KINDS:
            for item in run.get(kind, []):
                item_hash = item.get("input_hash")
                if not item_hash or item.get("source") != "agent":
                    continue
                if "needs_review" in (item.get("flags") or []):
                    continue
                if not item.get("message_draft"):
                    continue  # a draft-less copy (old schema) is not reusable
                if not cache_text_is_clean(item):
                    continue  # raw money ('36414.0') -> regenerate once, then cache
                saved[item_hash] = item
    for item in items:
        if item["input_hash"] in saved:
            item["cached_output"] = saved[item["input_hash"]]
    return {item["input_hash"]: item["cached_output"] for item in items if item.get("cached_output")}




def _hash_ready(value: Any) -> Any:
    """Formatted money hashes exactly like the number it was built from.

    Without this, switching the payload to '$56,600' would invalidate the
    whole 24h cache; with it, only the cached items whose text was never
    formatted regenerate (see cache_text_is_clean).
    """
    if isinstance(value, str):
        text = value.strip()
        return to_number(text) if _FORMATTED_MONEY_RE.match(text) else value
    if isinstance(value, dict):
        return {key: _hash_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_hash_ready(item) for item in value]
    return value


def stable_cache_key(kind: str, payload: dict) -> str:
    """Cache key built ONLY from fields that do not change minute to minute.

    The quotes/invoices rows carry a float age computed from the wall clock;
    hashing it would re-bill the same work every minute. Days are already ints
    in the rows, and history summaries are totals/counts, so they are stable.
    """
    if kind == "negotiation":
        quote = payload.get("quote", {})
        history = payload.get("customer_history", {})
        stable = {
            "quote_id": quote.get("id"),
            "amount": quote.get("amount"),
            "days_stale": quote.get("days_stale"),
            "history": {
                "quotes": history.get("past_quotes"),
                "orders": history.get("past_orders"),
                "payments": history.get("past_payments"),
            },
        }
    elif kind == "collections":
        invoice = payload.get("invoice", {})
        history = payload.get("customer_history", {})
        stable = {
            "invoice_id": invoice.get("id"),
            "balance": invoice.get("balance"),
            "days_invoice_age": invoice.get("days_invoice_age"),
            "overdue": invoice.get("overdue"),
            "history": {
                "quotes": history.get("open_quotes_count"),
                "orders": history.get("open_orders_value"),
                "payments": history.get("payments"),
            },
        }
    elif kind == "lead_followup":
        lead = payload.get("lead", {})
        stable = {
            "contact_name": lead.get("contact_name"),
            "company": lead.get("company"),
            "status": lead.get("status"),
            # days_since_activity is deliberately excluded: it ticks over
            # daily and would re-bill the same judgment every 24 hours
            "parsed": lead.get("parsed"),
            "missing_fields": lead.get("missing_fields"),
            "artwork_present": lead.get("artwork_present"),
            "description": lead.get("description"),
            "note": lead.get("note"),
        }
    else:
        stable = payload
    blob = json.dumps({"kind": kind, "stable": _hash_ready(stable)}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


# ---------------------------------------------------------------------------
# loading (code node)
# ---------------------------------------------------------------------------


async def _customer_context_map(database, erp: Any, customer_ids: list) -> dict:
    """customer_context per customer (uses the ERP lead-outcomes endpoint)."""
    contexts: dict[str, dict] = {}
    for cid in dict.fromkeys(customer_ids):  # dedupe, keep order
        try:
            contexts[cid] = await processors.build_customer_context(
                cid, database=database, erp=erp, max_leads=25
            )
        except Exception as exc:  # a broken context must not kill the run
            contexts[cid] = {
                "customer_id": cid,
                "company_name": "",
                "contact": {},
                "quotes": {"count": 0},
                "orders": {"count": 0},
                "payments": {"count": 0},
                "leads": [],
                "notes": [f"context error: {exc}"],
            }
    return contexts


async def load_signals_node(state: RunState) -> RunState:
    database = state["_database"]
    erp = state.get("_erp")
    now = datetime.fromtimestamp(state["now_epoch"], tz=timezone.utc)

    quotes = await database.quotes_flat.find().to_list(200_000)
    orders = await database.orders_flat.find().to_list(200_000)
    payments = await database.payments_flat.find().to_list(200_000)
    order_quote_ids = {row["quote_id"] for row in orders if row.get("quote_id") is not None}

    stale = processors.stale_quotes(quotes, now, order_quote_ids=order_quote_ids)[:MAX_ITEMS]
    unpaid = processors.unpaid_invoices(orders, now)[:MAX_ITEMS]
    # stalled_orders deliberately NOT loaded here: marked unreliable.

    contexts = await _customer_context_map(
        database, erp, [row.get("customer", {}).get("id") for row in stale + unpaid]
    )

    negotiation_items: list[dict] = []
    for row in stale:
        cid = row.get("customer", {}).get("id")
        payload = negotiation_payload(row, contexts.get(cid, {}))  # the exact LLM input (raw numbers)
        negotiation_items.append(
            {"kind": "negotiation", "item": row, "context": contexts.get(cid, {}), "payload": payload, "input_hash": stable_cache_key("negotiation", payload)}
        )

    collections_items: list[dict] = []
    for row in unpaid:
        cid = row.get("customer", {}).get("id")
        history = processors.summarize_payment_history(
            p for p in payments if p.get("customer_id") == cid or (p.get("customer") or {}).get("id") == cid
        )
        payload = collections_payload(row, contexts.get(cid, {}), history)  # the exact LLM input (raw numbers)
        collections_items.append(
            {"kind": "collections", "item": row, "context": contexts.get(cid, {}), "payment_history": history, "payload": payload, "input_hash": stable_cache_key("collections", payload)}
        )

    # --- leads: stuck before becoming an order (sub-agent 3, pure Python) ---
    lead_rows = await database.leads.find(
        processors.LEAD_STATUS_QUERY, processors.LEAD_FIELDS
    ).to_list(50_000)
    stuck = processors.stuck_leads(lead_rows, now)[:MAX_ITEMS]

    # A lead is matched to a customer only by email/phone. When a stuck lead's
    # contact details belong to a customer already in this run, that is a NOTE
    # on the lead - never a forced merge into the customer groups.
    known_contacts = [
        (
            str(context.get("company_name") or ""),
            processors.norm_email((context.get("contact") or {}).get("email")),
            processors.norm_phone((context.get("contact") or {}).get("phone")),
        )
        for context in contexts.values()
    ]
    contact_by_lead = {
        (processors.contact_to_s(row.get("zohoLeadId")) or str(row.get("_id") or "")): (
            processors.norm_email(row.get("email")),
            processors.norm_phone(row.get("phone")),
        )
        for row in lead_rows
    }
    lead_notes: dict[str, str] = {}
    for row in stuck:
        email, phone = contact_by_lead.get(row["lead_id"], ("", ""))
        for company, known_email, known_phone in known_contacts:
            matched_by = (
                "email" if email and email == known_email
                else "phone" if len(phone) >= 7 and phone == known_phone
                else None
            )
            if matched_by and company:
                lead_notes[row["lead_id"]] = (
                    f"contact details match customer {company} already in this run ({matched_by})"
                )
                break

    lead_items: list[dict] = []
    for row in stuck:
        note = lead_notes.get(row["lead_id"])
        payload = lead_followup_payload(row, note)  # the exact LLM input
        lead_items.append(
            {"kind": "lead_followup", "item": row, "note": note, "payload": payload, "input_hash": stable_cache_key("lead_followup", payload)}
        )

    # --- cache: reuse outputs from the last CACHE_HOURS hours -------------
    cutoff_epoch = state["now_epoch"] - CACHE_HOURS * 3600
    cached: dict = {}
    if state.get("_use_cache", True):
        recent = await database.agent_runs.find(
            {"finished_epoch": {"$gt": cutoff_epoch}}
        ).to_list(50)
        cached = cache_lookup(recent, negotiation_items + collections_items + lead_items, cutoff_epoch)

    signals_summary = {
        "quotes_scanned": len(quotes),
        "orders_scanned": len(orders),
        "leads_scanned": len(lead_rows),
        "stale_quotes_count": len(stale),
        "unpaid_invoices_count": len(unpaid),
        "stuck_leads_count": len(stuck),
        "unpaid_balance_total": round(sum(r.get("balance", 0.0) for r in unpaid), 2),
        "stalled_orders": "signal unavailable - unreliable by design (no status timestamps)",
    }
    return {
        "negotiation_input": negotiation_items,
        "collections_input": collections_items,
        "lead_followup_input": lead_items,
        "cached_hashes": cached,
        "signals_summary": signals_summary,
    }


# ---------------------------------------------------------------------------
# sub-agent nodes
# ---------------------------------------------------------------------------


def _item_skeleton(kind: str, entry: dict) -> dict:
    row = entry.get("item") or {}
    if kind == "lead_followup":
        return {
            "kind": kind,
            "input_hash": entry.get("input_hash"),
            "lead_id": row.get("lead_id"),
            "contact_name": row.get("contact_name"),
            "company": row.get("company"),
            "status": row.get("status"),
            "days_since_activity": row.get("days_since_activity"),
            "note": entry.get("note"),
        }
    customer = row.get("customer") or {}
    return {
        "kind": kind,
        "input_hash": entry.get("input_hash"),
        "customer_id": customer.get("id"),
        "customer_name": customer.get("company_name"),
    }


def _amount_number(value: Any) -> float:
    """Parse raw or formatted money values from the agent payload."""
    if isinstance(value, str):
        value = value.strip().replace("$", "").replace(",", "")
    return to_number(value)


def _must_escalate(payload: dict) -> bool:
    invoice = payload.get("invoice", {})
    return (
        _amount_number(invoice.get("balance")) > 50_000
        or int(invoice.get("days_overdue") or 0) > 90
    )


def _escalation_reason(payload: dict) -> str:
    invoice = payload.get("invoice", {})
    triggers = []
    if _amount_number(invoice.get("balance")) > 50_000:
        triggers.append(f"balance {invoice.get('balance')}")
    if int(invoice.get("days_overdue") or 0) > 90:
        triggers.append(f"{invoice.get('days_overdue')} days overdue")
    return "Code escalation: " + " and ".join(triggers) + "."


def _escalation_note(payload: dict) -> str:
    history = payload.get("customer_history", {})
    invoice = payload.get("invoice", {})
    name = history.get("company_name") or "Unknown customer"
    return (
        f"Internal note to sales manager: Please review {name}'s invoice "
        f"{invoice.get('id')} ({invoice.get('balance')} balance; "
        f"{invoice.get('days_overdue')} days overdue) and decide next steps."
    )


async def _run_one(
    entry: dict,
    kind: str,
    caller_factory: Callable,
    usage_total: dict,
    usage_lock: asyncio.Lock,
    errors: list[str],
    regen_count: dict,
) -> dict:
    """One item through (call -> validate -> one regen -> needs_review)."""
    skeleton = _item_skeleton(kind, entry)
    payload = entry["payload"]
    semaphore = getattr(_run_one, "_sema", None) or asyncio.Semaphore(CONCURRENCY)

    async def one_pass() -> Tuple[Any, dict, list[str]]:
        # send the EXACT payload that the input hash covers (no rebuilds)
        caller = caller_factory(kind)
        system = {
            "negotiation": NEGOTIATION_SYSTEM,
            "collections": COLLECTIONS_SYSTEM,
            "lead_followup": LEAD_FOLLOWUP_SYSTEM,
        }[kind]
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, default=str)},
        ]
        parsed, usage = await caller.ainvoke(messages)

        # deterministic cleanups BEFORE validation:
        # 1) a greeting first name must exist in the contact data
        parsed.message_draft = sanitise_greeting(
            getattr(parsed, "message_draft", "") or "", payload
        )
        # 2) raw / half formatted money ('36414.0') becomes '$36,414'
        parsed.message_draft = normalise_money_text(parsed.message_draft, payload)
        parsed.evidence = normalise_money_text(getattr(parsed, "evidence", "") or "", payload)
        if hasattr(parsed, "reason"):
            parsed.reason = normalise_money_text(parsed.reason, payload)

        problems: list[str] = []
        invented = invented_amounts(parsed.message_draft, payload)
        if invented:
            problems.append(f"amounts {invented} are not in the input data")
        # phone / email / URL must appear verbatim in the item's input
        contacts = invented_contacts(parsed.message_draft, payload)
        if contacts:
            problems.append(f"contact details {contacts} are not in the input data")
        return parsed, usage, problems

    async def guarded() -> dict:
        async with semaphore:
            result = dict(skeleton)
            try:
                parsed, usage, bad = await one_pass()
                # defense in depth: the COPIED fields must equal the input too
                # (payload amounts are formatted strings now - _amount_number parses them)
                if kind == "negotiation" and abs(getattr(parsed, "quote_amount", -1) - _amount_number(payload["quote"]["amount"])) > 0.005:
                    bad.append(
                        f"quote_amount {getattr(parsed, 'quote_amount', 0.0)} does not match the input"
                    )
                if kind == "collections" and abs(getattr(parsed, "balance", -1) - _amount_number(payload["invoice"]["balance"])) > 0.005:
                    bad.append(
                        f"balance {getattr(parsed, 'balance', 0.0)} does not match the input"
                    )
                if kind == "lead_followup" and list(getattr(parsed, "missing", []) or []) != list(
                    (payload.get("lead") or {}).get("missing_fields") or []
                ):
                    bad.append("the missing list does not match the input missing_fields")
                regen = 0
                while bad:
                    if regen >= 1:
                        break
                    regen += 1
                    parsed, usage, bad = await one_pass()  # regenerate once
                if kind == "collections" and _must_escalate(payload):
                    parsed.approach = "escalate_to_human"
                    parsed.reason = _escalation_reason(payload)
                    parsed.message_draft = _escalation_note(payload)
                    # the escalation note is code-written: still check it like a draft
                    bad = []
                    if invented_amounts(parsed.message_draft, payload):
                        bad.append("the escalation note mentions an amount not in the input")
                    if invented_contacts(parsed.message_draft, payload):
                        bad.append("the escalation note mentions contact details not in the input")
                result["source"] = "agent"
                async with usage_lock:
                    regen_count[kind] += regen
                if bad:
                    result["flags"] = ["needs_review"]
                    result["message_draft"] = None
                    result["rejected_reason"] = "; ".join(bad)
                    result.update(
                        {
                            key: getattr(parsed, key)
                            for key in (
                                "confidence", "evidence", "likely_reason", "tactic",
                                "approach", "quote_id", "quote_amount", "invoice_id",
                                "balance", "reason", "blocker", "owner_team",
                                "action_plan", "missing", "product",
                            )
                            if hasattr(parsed, key)
                        }
                    )
                else:
                    model_dump = parsed.model_dump()
                    result.update(model_dump)
                    unclear = (
                        getattr(parsed, "likely_reason", None) == "unclear"
                        or getattr(parsed, "owner_team", None) == "unclear"
                    )
                    result["flags"] = ["unclear"] if unclear else []
                if kind == "lead_followup":
                    # ids and names are facts Python extracted, never model text
                    lead_row = entry.get("item") or {}
                    result["lead_id"] = str(lead_row.get("lead_id") or "")
                    result["contact_name"] = lead_row.get("contact_name")
                    result["company"] = lead_row.get("company")
                    result["status"] = lead_row.get("status")
                    result["days_since_activity"] = lead_row.get("days_since_activity")
                async with usage_lock:
                    usage_total.update(add_usage(usage_total, usage))
                    usage_total["sub_call"] = usage_total.get("sub_call", 0) + 1
            except Exception as exc:
                result["flags"] = ["error"]
                result["message_draft"] = None
                import traceback
                tb = traceback.format_exc()
                result["error"] = f"{type(exc).__name__}: {exc}"
                result["traceback"] = tb[-800:]
                errors.append(f"{kind} item {skeleton}: {exc}")
            result.setdefault("flags", [])
            return result

    return await guarded()


async def negotiation_node(state: RunState) -> RunState:
    """One structured LLM call per fresh stale quote (cached ones skipped)."""
    fresh = [entry for entry in state["negotiation_input"] if entry["input_hash"] not in (state["cached_hashes"] or {})]
    cached_items = []
    for entry in state["negotiation_input"]:
        if entry["input_hash"] in (state["cached_hashes"] or {}):
            cached = dict(_item_skeleton("negotiation", entry))
            cached.update(state["cached_hashes"][entry["input_hash"]])
            cached["source"] = "cache"
            cached_items.append(cached)

    caller_factory = state["_caller_factory"]
    usage_total = state.get("usage") or empty_usage()
    usage_lock = asyncio.Lock()
    errors: list[str] = []
    regen_count = {"negotiation": 0, "collections": 0}
    _run_one._sema = asyncio.Semaphore(CONCURRENCY)

    done = await asyncio.gather(
        *(_run_one(entry, "negotiation", caller_factory, usage_total, usage_lock, errors, regen_count) for entry in fresh)
    )
    return {
        "negotiation_out": cached_items + list(done),
        "usage": usage_total,
        "errors": errors,
        "regen_counts": regen_count,
    }


async def collections_node(state: RunState) -> RunState:
    fresh = [entry for entry in state["collections_input"] if entry["input_hash"] not in (state["cached_hashes"] or {})]
    cached_items = []
    for entry in state["collections_input"]:
        if entry["input_hash"] in (state["cached_hashes"] or {}):
            cached = dict(_item_skeleton("collections", entry))
            cached.update(state["cached_hashes"][entry["input_hash"]])
            cached["source"] = "cache"
            cached_items.append(cached)

    caller_factory = state["_caller_factory"]
    usage_total = state.get("usage") or empty_usage()
    usage_lock = asyncio.Lock()
    errors: list[str] = list(state.get("errors") or [])
    regen_count = dict(state.get("regen_counts") or {"negotiation": 0, "collections": 0})
    _run_one._sema = asyncio.Semaphore(CONCURRENCY)

    done = await asyncio.gather(
        *(_run_one(entry, "collections", caller_factory, usage_total, usage_lock, errors, regen_count) for entry in fresh)
    )
    return {
        "collections_out": cached_items + list(done),
        "usage": usage_total,
        "errors": errors,
        "regen_counts": regen_count,
    }


async def lead_followup_node(state: RunState) -> RunState:
    """One structured LLM call per fresh stuck lead (cached ones skipped)."""
    fresh = [entry for entry in state["lead_followup_input"] if entry["input_hash"] not in (state["cached_hashes"] or {})]
    cached_items = []
    for entry in state["lead_followup_input"]:
        if entry["input_hash"] in (state["cached_hashes"] or {}):
            cached = dict(_item_skeleton("lead_followup", entry))
            cached.update(state["cached_hashes"][entry["input_hash"]])
            cached["source"] = "cache"
            cached_items.append(cached)

    caller_factory = state["_caller_factory"]
    usage_total = state.get("usage") or empty_usage()
    usage_lock = asyncio.Lock()
    errors: list[str] = list(state.get("errors") or [])
    regen_count = dict(state.get("regen_counts") or {})
    regen_count.setdefault("lead_followup", 0)
    _run_one._sema = asyncio.Semaphore(CONCURRENCY)

    done = await asyncio.gather(
        *(_run_one(entry, "lead_followup", caller_factory, usage_total, usage_lock, errors, regen_count) for entry in fresh)
    )
    return {
        "lead_followup_out": cached_items + list(done),
        "usage": usage_total,
        "errors": errors,
        "regen_counts": regen_count,
    }


async def group_by_customer_node(state: RunState) -> RunState:
    outputs = list(state["negotiation_out"]) + list(state["collections_out"])
    outputs = [o for o in outputs if "needs_review" not in (o.get("flags") or []) and "error" not in (o.get("flags") or [])]
    # Leads are NOT customers: they travel to the master as their own section
    # (matched only by email/phone, never force-merged into customer groups).
    lead_outputs = [
        o for o in state["lead_followup_out"]
        if "needs_review" not in (o.get("flags") or []) and "error" not in (o.get("flags") or [])
    ]
    return {"grouped": group_by_customer(outputs), "leads_grouped": lead_outputs}


async def master_node(state: RunState) -> RunState:
    payload_hint = {
        "stale_quote_count": len(state["negotiation_out"]),
        "unpaid_count": len(state["collections_out"]),
        "stuck_lead_count": len(state["lead_followup_out"]),
    }
    grouped = state["grouped"]
    # raw numbers only - the LLM may copy but never compute totals
    master_input = {**state["signals_summary"], **payload_hint}
    parsed = None
    usage = empty_usage()
    bad: list[float] = []
    attempt = 0
    while True:
        attempt += 1
        try:
            caller = state["_caller_factory"]("master")
            parsed, master_payload_used, usage = await run_master(
                grouped, master_input, caller, state.get("leads_grouped") or []
            )
            # same money format in bullets / priorities / conflicts
            parsed = normalise_master_output(parsed, master_payload_used)
            # numbers-only rule extends to the whole brief: no invented totals
            # (validated against the exact formatted payload the master saw)
            bad = master_invented_amounts(parsed.model_dump(), master_payload_used)
            if not bad or attempt >= 2:
                break
        except Exception as exc:
            if attempt >= 2:
                return {
                    "master": {"bullets": [], "priorities": [], "conflicts_resolved": [], "error": str(exc)},
                    "errors": list(state.get("errors") or []) + [f"master failed: {exc}"],
                }
    master_dump = parsed.model_dump() if parsed is not None else {}
    if bad:
        master_dump["flags"] = ["needs_review"]
        master_dump["rejected_reason"] = f"brief mentioned amounts {bad} that Python never computed"
    else:
        master_dump["flags"] = []
    # RETURN the update: node-side mutation does not persist in the graph
    return {
        "master": master_dump,
        "usage": add_usage(state.get("usage") or empty_usage(), usage),
        "calls": (state.get("calls") or 0) + attempt,
        "errors": list(state.get("errors") or []),
    }


async def save_node(state: RunState) -> RunState:
    database = state["_database"]
    negotiation = state["negotiation_out"]
    collections = state["collections_out"]
    lead_followup = state["lead_followup_out"]
    all_outputs = negotiation + collections + lead_followup
    counts = {
        "negotiation_total": len(negotiation),
        "collections_total": len(collections),
        "lead_followup_total": len(lead_followup),
        "unclear": sum(1 for o in all_outputs if "unclear" in (o.get("flags") or [])),
        "needs_review": sum(
            1 for o in all_outputs if "needs_review" in (o.get("flags") or []) or "error" in (o.get("flags") or [])
        ),
        "cached": sum(1 for o in all_outputs if o.get("source") == "cache"),
    }
    doc = {
        "run_id": state["run_id"],
        "started_at": state["started_at"],
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "finished_epoch": state["now_epoch"],
        "counts": counts,
        "negotiation": negotiation,
        "collections": collections,
        "lead_followup": lead_followup,
        "master": state.get("master"),
        "usage": state.get("usage"),
        "errors": state.get("errors", []),
    }
    await database.agent_runs.insert_one(doc)
    return {"saved_counts": counts}


# ---------------------------------------------------------------------------
# graph assembly + entry point
# ---------------------------------------------------------------------------


def build_graph() -> Any:
    graph = StateGraph(RunState)
    graph.add_node("load_signals", load_signals_node)
    graph.add_node("negotiation", negotiation_node)
    graph.add_node("collections", collections_node)
    graph.add_node("lead_followup", lead_followup_node)
    graph.add_node("group_by_customer", group_by_customer_node)
    graph.add_node("master", master_node)
    graph.add_node("save", save_node)
    graph.add_edge(START, "load_signals")
    graph.add_edge("load_signals", "negotiation")
    graph.add_edge("negotiation", "collections")
    graph.add_edge("collections", "lead_followup")
    graph.add_edge("lead_followup", "group_by_customer")
    graph.add_edge("group_by_customer", "master")
    graph.add_edge("master", "save")
    graph.add_edge("save", END)
    return graph.compile()


def default_caller_factory(kind: str):
    """Fresh caller per call; 'master' uses MODEL_MASTER, else MODEL_SUB."""
    schema = {
        "master": MasterOutput,
        "negotiation": NegotiationOutput,
        "collections": CollectionsOutput,
        "lead_followup": LeadFollowupOutput,
    }[kind]
    return make_caller(build_model("master" if kind == "master" else "sub"), schema)


async def run_pipeline(
    run_id: Optional[str] = None,
    database: Any = None,
    erp: Any = None,
    caller_factory: Optional[Callable] = None,
    now_epoch: Optional[float] = None,
    ignore_cache: bool = False,
) -> dict:
    """Run the whole pipeline once. Returns the saved agent_runs doc."""
    if database is None:
        database = app_db.get_db()
    if caller_factory is None:
        caller_factory = default_caller_factory
    erp_owned = erp is None
    client = ERPClient() if erp_owned else None

    now_epoch = now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp()
    initial: RunState = {
        "run_id": run_id or str(uuid.uuid4()),
        "started_at": datetime.fromtimestamp(now_epoch, tz=timezone.utc).isoformat(),
        "usage": empty_usage(),
        "calls": 0,
        "errors": [],
        "now_epoch": now_epoch,
        "_database": database,
        "_erp": client if erp is None else erp,
        "_caller_factory": caller_factory,
        "_use_cache": not ignore_cache,
    }
    try:
        app = build_graph()
        final = await app.ainvoke(initial)
        return {
            "run_id": final["run_id"],
            "started_at": final["started_at"],
            "counts": final.get("saved_counts", {}),
            "master": final.get("master"),
            "negotiation": final.get("negotiation_out", []),
            "collections": final.get("collections_out", []),
            "lead_followup": final.get("lead_followup_out", []),
            "usage": final.get("usage"),
            "errors": final.get("errors", []),
        }
    finally:
        if erp_owned and client is not None:
            await client.close()
