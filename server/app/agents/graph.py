"""LangGraph pipeline: one agent run.

    load_signals (code)  ->  negotiation (LLM, <=5 concurrent)
                         ->  collections (LLM, <=5 concurrent)
                         ->  lead_followup (LLM, <=5 concurrent)
                         ->  group_by_customer (code)
                         ->  master (LLM)
                         ->  save (code)

Rules encoded here, never left to the LLM:
  * each sub-agent sees only the top MAX_ITEMS_PER_AGENT items of its signal;
  * money validation: every dollar amount in a message_draft must be one of
    that item's own money fields (quote amount, invoice balance/total/paid) -
    not any number that happens to sit in the payload. One regeneration on
    failure; if it still fails the item is stored with flag "needs_review" and
    message_draft=None;
  * text quality: glued words ("todiscuss") cost one regeneration and are then
    repaired in code; the Master brief is rejected when it keeps reasoning
    artifacts, unknown numbers/IDs or sentences copied out of its own prompt,
    and falls back to a brief built by code;
  * every collections item keeps a customer-facing payment reminder draft;
    escalation is an extra flag plus an internal note, never a replacement;
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
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from app import db as app_db
from app.config import settings
from app.services import processors
from app.services.events import EventService, get_event_service
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
from app.agents.crm_agents import (
    crm_deal_payload,
    crm_engagement_payload,
    CRM_DEAL_WATCH_SYSTEM,
    CRM_ENGAGEMENT_SYSTEM,
)
from app.services import customer360 as c360_service
from app.services.customer360_client import Customer360Client, Customer360Error
from app.agents.master import (
    MASTER_SYSTEM,
    build_master_brief,
    group_by_customer,
    run_master,
)
from app.agents.text_rules import (
    glued_words,
    invented_numbers,
    number_tokens,
    prompt_echo,
    repair_glued_words,
    reasoning_artifacts,
)
from app.schemas.agents import (
    MasterOutput,
    NegotiationOutput,
    CollectionsOutput,
    LeadFollowupOutput,
    CrmEngagementOutput,
    CrmDealWatchOutput,
)

CONCURRENCY = int(settings.AGENT_CONCURRENCY)
CACHE_HOURS = int(settings.AGENT_CACHE_HOURS)
MAX_ITEMS = int(settings.MAX_ITEMS_PER_AGENT)

AGENT_KINDS = ("negotiation", "collections", "lead_followup", "crm_engagement", "crm_deal_watch")


@dataclass
class RunContext:
    """Per-run, non-persisted collaborators shared by every node.

    One task id (the run_id), one event service, and a single semaphore and
    usage accumulator shared across ALL parallel specialist branches - so
    AGENT_CONCURRENCY is the system-wide bound on concurrent LLM calls, not a
    per-branch limit that multiplies when the branches fan out.
    """

    task_id: str
    events: EventService
    caller_factory: Callable
    started: float = field(default_factory=time.monotonic)
    sema: Optional[asyncio.Semaphore] = None
    usage_lock: Optional[asyncio.Lock] = None
    usage: dict = field(default_factory=dict)
    regen: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)

    async def emit(self, type: str, agent_id: Optional[str] = None, payload: Optional[dict] = None) -> None:
        """Fire one task event. The service never raises, but this guard means
        even a misbehaving/instrumentation-only service cannot corrupt a run."""
        try:
            await self.events.emit(
                task_id=self.task_id, type=type, agent_id=agent_id, payload=payload or {}
            )
        except Exception:  # observability must never break business execution
            import logging

            logging.getLogger("graph").warning("could not emit %s", type, exc_info=True)


class RunState(TypedDict, total=False):
    run_id: str
    started_at: str
    negotiation_input: list[dict]
    collections_input: list[dict]
    lead_followup_input: list[dict]
    crm_engagement_input: list[dict]
    crm_deal_watch_input: list[dict]
    cached_hashes: dict
    signals_summary: dict
    negotiation_out: list[dict]
    collections_out: list[dict]
    lead_followup_out: list[dict]
    crm_engagement_out: list[dict]
    crm_deal_watch_out: list[dict]
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
    _ctx: Any


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


def all_numbers(payload: Any) -> set[float]:
    """Every number the MASTER can read in its input.

    Numbers count when they are a JSON value OR when they sit inside a text
    field it was given ('111 days overdue' inside an escalation note), because
    that text is the master's only source.
    """
    found: set[float] = set()
    _walk_numbers(payload, found)

    def mine(node: Any) -> None:
        if isinstance(node, str):
            for token in number_tokens(node):
                try:
                    found.add(float(token.replace(",", "")))
                except ValueError:
                    pass
        elif isinstance(node, dict):
            for value in node.values():
                mine(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                mine(value)

    mine(payload)
    return found | money_values(payload)


import re  # noqa: E402  (must come before the regex below)

_DOLLAR_RE = re.compile(r"\$\s?([\d][\d,]*(?:\.\d+)?)|([\d][\d,]*(?:\.\d+)?)\s(?:dollars?|USD|usd)", re.I)


# The only money fields one item's own draft may mention. History totals are
# NOT here: a draft about this quote may not quote the customer's lifetime
# spend as if it were the price ($56,600 from past orders vs. this quote 56800).
ITEM_MONEY_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "negotiation": (("quote", "amount"),),
    "collections": (
        ("invoice", "balance"),
        ("invoice", "total_amount"),
        ("invoice", "amount_paid"),
    ),
    "lead_followup": (),
}


def allowed_amounts(kind: str, payload: dict) -> set[float]:
    """Exact money values this item's draft is allowed to mention."""
    fields = ITEM_MONEY_FIELDS.get(kind)
    if fields is None:
        return money_values(payload)
    allowed: set[float] = set()
    for path in fields:
        node: Any = payload
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if node is not None:
            value = _amount_number(node)
            allowed.add(value)
    return allowed


def invented_amounts(draft: str, payload: dict, kind: Optional[str] = None) -> list[float]:
    """Dollar amounts in the draft that the item is not allowed to mention.

    With a `kind` the check is against that item's own money fields; without
    one it falls back to every money value in the payload. The pipeline runs
    this on every message_draft.
    """
    allowed = allowed_amounts(kind, payload) if kind else money_values(payload)
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
# the address is letters only: a money amount ('$22,990') must never be read as
# part of a greeting, because its comma would end the match inside the number
_GREETING_RE = re.compile(
    r"^(\s*)(hi|hello|hey|dear)\s+([A-Za-z][A-Za-z'\- ]{0,40}?)(?:\s+team)?\s*,", re.I
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
    """Every name in a greeting must come from the input contact data.

    Otherwise the greeting becomes 'Hello <company> team', which cannot be
    wrong about who is reading it. The whole address is checked ('Saba
    Enterprises LLC' is one company address, not a first name) and only the
    greeting itself is replaced, so no words get duplicated.
    """
    if not draft:
        return draft
    match = _GREETING_RE.match(draft)
    if not match:
        return draft
    address = match.group(3)
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
    blob = f"{contact_blob} {company}".lower()
    words = re.findall(r"[A-Za-z][A-Za-z'\-]+", address)
    if words and all(re.search(rf"\b{re.escape(word.lower())}\b", blob) for word in words):
        return draft  # the ERP really knows this addressee
    neutral = f"Hello {company} team" if company else "Hello"
    rest = draft[match.end():]
    if not rest:
        return neutral
    return f"{neutral}, {rest.lstrip()}"


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


def master_texts(master_dict: dict) -> list[str]:
    """Every free-text line the master wrote."""
    texts = [str(item) for item in (master_dict.get("bullets") or [])]
    for priority in master_dict.get("priorities") or []:
        texts.extend(
            [
                str(priority.get("customer_name") or ""),
                str(priority.get("why") or ""),
                str(priority.get("suggested_action") or ""),
            ]
        )
    texts.extend(str(item) for item in (master_dict.get("conflicts_resolved") or []))
    return texts


def master_problems(master_dict: dict, payload: dict) -> list[str]:
    """What is wrong with a master brief, as plain problems.

    Money must be money Python computed, every other number (invoice ids
    included) must exist in the data the master was sent, and the text must not
    still be thinking out loud or repeat its own instructions.
    """
    problems: list[str] = []
    texts = master_texts(master_dict)
    amounts = master_invented_amounts(master_dict, payload)
    if amounts:
        problems.append(f"amounts {amounts} are not money Python computed")
    numbers = invented_numbers(texts, all_numbers(payload))
    if numbers:
        problems.append(f"numbers {numbers} appear nowhere in the sub-agent outputs")
    for text in texts:
        for artifact in reasoning_artifacts(text):
            problems.append(f"{text[:60]!r} keeps leftover reasoning {artifact!r}")
        for echo in prompt_echo(text, MASTER_SYSTEM):
            problems.append(f"{text[:60]!r} copies its own instructions ({echo!r})")
    return problems


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


def _crm_collection(database: Any, name: str) -> Any:
    """A synced CRM collection, or None - the CRM side is optional
    infrastructure: no sync yet (or a test double without the attribute)
    means no CRM items, never a broken ERP run."""
    try:
        return database[name]
    except Exception:
        return getattr(database, name, None)


async def _crm_deal_items(database: Any, now: datetime) -> list[dict]:
    """Stalled-deal items from the locally synced CRM rows (no API calls)."""
    try:
        projects_col = _crm_collection(database, "customer360_projects")
        estimates_col = _crm_collection(database, "customer360_estimates")
        projects = (
            await projects_col.find().to_list(20_000) if projects_col is not None else []
        )
        estimates = (
            await estimates_col.find().to_list(20_000) if estimates_col is not None else []
        )
    except Exception:
        return []
    items: list[dict] = []
    for candidate in processors.crm_deal_candidates(projects, estimates, now)[:MAX_ITEMS]:
        payload = crm_deal_payload(candidate)
        items.append(
            {
                "kind": "crm_deal_watch",
                "item": candidate,
                "payload": payload,
                "input_hash": stable_cache_key("crm_deal_watch", payload),
            }
        )
    return items


async def _crm_engagement_items(database: Any, now: datetime) -> list[dict]:
    """Awaiting-reply items: synced CRM leads idle 2-90 days whose detail
    timeline ends with an INBOUND interaction (the customer wrote last).

    Details come through the same fetch-through cache chat uses. Live
    fetches are bounded (at most MAX_ITEMS attempts, giving up after two
    failures) so a cold or down CRM can never hold the pipeline hostage;
    any failure simply yields fewer items.
    """
    try:
        leads_col = _crm_collection(database, "customer360_leads")
        if leads_col is None:
            return []
        rows = await leads_col.find().to_list(20_000)
    except Exception:
        return []

    window: list[tuple[float, dict]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "").strip().lower() in (
            "converted", "closed", "won", "lost", "junk",
        ):
            continue
        idle = processors.age_days(processors.parse_date(row.get("lastActivityAt")), now)
        if idle is None or idle < 2 or idle > 90:
            continue
        window.append((idle, row))
    window.sort(key=lambda pair: -pair[0])  # longest idle first

    items: list[dict] = []
    client: Optional[Customer360Client] = None
    fetch_attempts = 0
    fetch_failures = 0
    try:
        for _idle, row in window[: MAX_ITEMS * 10]:
            if len(items) >= MAX_ITEMS or fetch_failures >= 2:
                break
            record_id = str(row.get("_id") or "")
            if not record_id:
                continue
            payload, _fetched_at, fresh = await c360_service.get_cached_detail(
                database, record_id, "Lead"
            )
            if payload is None or not fresh:
                if fetch_attempts >= MAX_ITEMS:
                    continue
                fetch_attempts += 1
                if client is None:
                    client = Customer360Client()
                try:
                    payload = await client.detail(record_id, "Lead")
                    await c360_service.put_cached_detail(
                        database, record_id, "Lead", payload
                    )
                except Customer360Error:
                    fetch_failures += 1
                    continue
            summary = c360_service.summarize_detail(payload)
            last = summary.get("last_interaction") or {}
            if str(last.get("direction") or "").strip().lower() != "inbound":
                continue
            waiting = processors.age_days(processors.parse_date(last.get("at")), now)
            if waiting is None or waiting < 1:
                continue
            entity = summary.get("entity") or {}
            # the channel the customer is waiting on (WhatsApp above
            # all - the user's standing ask) and their actual last
            # words, from the summarizer's bounded conversation
            conversation = summary.get("conversation") or []
            last_message = next(
                (
                    entry.get("content")
                    for entry in conversation
                    if isinstance(entry, dict) and entry.get("content")
                ),
                None,
            )
            item = {
                "record_id": record_id,
                "record_type": "Lead",
                "contact_name": entity.get("name"),
                "company": entity.get("company"),
                "waiting_days": int(waiting),
                "channel": last.get("type"),
                "last_customer_message": (last_message or "")[:200] or None,
            }
            payload_out = crm_engagement_payload(summary, "Lead", int(waiting))
            items.append(
                {
                    "kind": "crm_engagement",
                    "item": item,
                    "payload": payload_out,
                    "input_hash": stable_cache_key("crm_engagement", payload_out),
                }
            )
    except Exception:
        import logging

        logging.getLogger("graph").warning("crm engagement load failed", exc_info=True)
    finally:
        if client is not None:
            await client.close()
    return items


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

    # The same lead may also live in the Zoho CRM (matched by zohoLeadId,
    # the id space both systems share): its CRM status and last CRM
    # activity are a note on the lead, exactly like the customer match
    # above - context for the analyst, never a merge.
    crm_leads_by_zoho: dict[str, dict] = {}
    try:
        crm_leads_col = _crm_collection(database, "customer360_leads")
        if crm_leads_col is not None:
            for crow in await crm_leads_col.find().to_list(50_000):
                if isinstance(crow, dict):
                    zid = processors.contact_to_s(crow.get("zohoLeadId"))
                    if zid:
                        crm_leads_by_zoho[zid] = crow
    except Exception:
        crm_leads_by_zoho = {}
    for row in stuck:
        # a stuck row's lead_id IS its zoho id when it has one
        # (processors.stuck_leads), which is the CRM map's key
        crow = crm_leads_by_zoho.get(row["lead_id"])
        if not crow:
            continue
        crm_note = f"Zoho CRM lead record: status {crow.get('status') or 'unknown'}"
        if crow.get("lastActivityAt"):
            crm_note += f", last CRM activity {str(crow['lastActivityAt'])[:10]}"
        existing = lead_notes.get(row["lead_id"])
        lead_notes[row["lead_id"]] = f"{existing}; {crm_note}" if existing else crm_note

    lead_items: list[dict] = []
    for row in stuck:
        note = lead_notes.get(row["lead_id"])
        payload = lead_followup_payload(row, note)  # the exact LLM input
        lead_items.append(
            {"kind": "lead_followup", "item": row, "note": note, "payload": payload, "input_hash": stable_cache_key("lead_followup", payload)}
        )

    # --- CRM specialists: Deal Watch reads the synced rows locally;
    # Engagement reads detail summaries through the cache. Both degrade
    # to no items when the CRM side is absent or down. ---
    crm_deal_items = await _crm_deal_items(database, now)
    crm_engagement_items = await _crm_engagement_items(database, now)

    # --- cache: reuse outputs from the last CACHE_HOURS hours -------------
    cutoff_epoch = state["now_epoch"] - CACHE_HOURS * 3600
    cached: dict = {}
    if state.get("_use_cache", True):
        recent = await database.agent_runs.find(
            {"finished_epoch": {"$gt": cutoff_epoch}}
        ).to_list(50)
        cached = cache_lookup(
            recent,
            negotiation_items + collections_items + lead_items
            + crm_engagement_items + crm_deal_items,
            cutoff_epoch,
        )

    signals_summary = {
        "quotes_scanned": len(quotes),
        "orders_scanned": len(orders),
        "leads_scanned": len(lead_rows),
        "stale_quotes_count": len(stale),
        "unpaid_invoices_count": len(unpaid),
        "stuck_leads_count": len(stuck),
        "unpaid_balance_total": round(sum(r.get("balance", 0.0) for r in unpaid), 2),
        "stalled_orders": "signal unavailable - unreliable by design (no status timestamps)",
        "crm_awaiting_reply_count": len(crm_engagement_items),
        "crm_stalled_deals_count": len(crm_deal_items),
    }
    return {
        "negotiation_input": negotiation_items,
        "collections_input": collections_items,
        "lead_followup_input": lead_items,
        "crm_engagement_input": crm_engagement_items,
        "crm_deal_watch_input": crm_deal_items,
        "cached_hashes": cached,
        "signals_summary": signals_summary,
    }


# ---------------------------------------------------------------------------
# sub-agent nodes
# ---------------------------------------------------------------------------


def _item_skeleton(kind: str, entry: dict) -> dict:
    row = entry.get("item") or {}
    if kind == "crm_engagement":
        return {
            "kind": kind,
            "input_hash": entry.get("input_hash"),
            "record_id": row.get("record_id"),
            "record_type": row.get("record_type"),
            "contact_name": row.get("contact_name"),
            "company": row.get("company"),
            "waiting_days": row.get("waiting_days"),
        }
    if kind == "crm_deal_watch":
        return {
            "kind": kind,
            "input_hash": entry.get("input_hash"),
            "record_id": row.get("record_id"),
            "deal_kind": row.get("deal_kind"),
            "deal_name": row.get("deal_name"),
            "account_name": row.get("account_name"),
            "amount": row.get("amount"),
            "days_idle": row.get("days_idle"),
        }
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


def _escalation_note(payload: dict) -> str:
    """The internal manager note. Code writes it so its numbers are always real."""
    history = payload.get("customer_history", {})
    invoice = payload.get("invoice", {})
    name = history.get("company_name") or "Unknown customer"
    return (
        f"Internal note to sales manager: Please review {name}'s invoice "
        f"{invoice.get('id')} ({invoice.get('balance')} balance; "
        f"{invoice.get('days_overdue')} days overdue) and decide next steps."
    )


# text that belongs in an internal note, never in a message the customer reads
_INTERNAL_NOTE_RE = re.compile(
    r"(?i)\b(internal note|sales manager|escalat\w*|decide next steps|manager review)\b"
)


def _payload_blob(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def glued_problems(parsed: Any, payload: dict) -> list[str]:
    """Draft text where the model glued words together ('todiscuss')."""
    blob = _payload_blob(payload)
    found: list[str] = []
    for field in ("message_draft", "escalation_note"):
        text = getattr(parsed, field, None)
        if isinstance(text, str) and text:
            glued = glued_words(text, payload_blob=blob)
            if glued:
                found.append(f"{field} has words without spaces: {glued}")
    return found


def repair_glued_text(parsed: Any, payload: dict) -> None:
    """Put the missing spaces back (code fix, no LLM involved)."""
    blob = _payload_blob(payload)
    for field in ("message_draft", "escalation_note"):
        text = getattr(parsed, field, None)
        if isinstance(text, str) and text:
            setattr(parsed, field, repair_glued_words(text, payload_blob=blob))


def copied_field_problems(parsed: Any, payload: dict, kind: str) -> list[str]:
    """Fields the model must COPY from the input (they are Python facts).

    A mismatch costs one regeneration; if the model still mis-copies, code
    writes the real value (see sync_copied_fields) instead of throwing the item
    away - the amount in the draft text is what a human reads.
    """
    problems: list[str] = []
    if kind == "negotiation":
        expected = _amount_number(payload["quote"]["amount"])
        if abs(getattr(parsed, "quote_amount", -1) - expected) > 0.005:
            problems.append(
                f"quote_amount {getattr(parsed, 'quote_amount', 0.0)} does not match the input"
            )
    elif kind == "collections":
        expected = _amount_number(payload["invoice"]["balance"])
        if abs(getattr(parsed, "balance", -1) - expected) > 0.005:
            problems.append(
                f"balance {getattr(parsed, 'balance', 0.0)} does not match the input"
            )
        if getattr(parsed, "tone", None) != payload["invoice"].get("tone"):
            problems.append("tone is not the tone code picked from the days overdue")
    elif kind == "lead_followup":
        if list(getattr(parsed, "missing", []) or []) != list(
            (payload.get("lead") or {}).get("missing_fields") or []
        ):
            problems.append("the missing list does not match the input missing_fields")
    return problems


def sync_copied_fields(parsed: Any, payload: dict, kind: str) -> None:
    """Amounts, IDs and the Python-computed lists are code's, never the model's."""
    if kind == "negotiation":
        parsed.quote_id = int(payload["quote"]["id"])
        parsed.quote_amount = _amount_number(payload["quote"]["amount"])
    elif kind == "collections":
        parsed.invoice_id = int(payload["invoice"]["id"])
        parsed.balance = _amount_number(payload["invoice"]["balance"])
        parsed.tone = payload["invoice"]["tone"]
    elif kind == "lead_followup":
        parsed.missing = list((payload.get("lead") or {}).get("missing_fields") or [])


def draft_rules_problems(parsed: Any, payload: dict, kind: str) -> list[str]:
    """Rules on the customer-facing text itself (blocking, not code-fixable)."""
    if kind != "collections":
        return []
    draft = (getattr(parsed, "message_draft", "") or "").strip()
    if not draft:
        return ["the customer-facing reminder draft is empty"]
    if _INTERNAL_NOTE_RE.search(draft):
        return ["message_draft reads like an internal note; it must be customer-facing"]
    return []


async def _run_one(
    ctx: RunContext,
    entry: dict,
    kind: str,
    index: int,
) -> dict:
    """One item through (call -> validate -> one regen -> needs_review).

    The shared semaphore, usage accumulator and event stream all come from the
    run context, so every parallel branch obeys ONE system-wide concurrency
    bound and reports real per-item activity.
    """
    skeleton = _item_skeleton(kind, entry)
    payload = entry["payload"]
    semaphore = ctx.sema or asyncio.Semaphore(CONCURRENCY)
    caller_factory = ctx.caller_factory
    usage_total = ctx.usage
    usage_lock = ctx.usage_lock
    errors = ctx.errors
    regen_count = ctx.regen

    async def one_pass() -> Tuple[Any, dict, list[str]]:
        # send the EXACT payload that the input hash covers (no rebuilds)
        caller = caller_factory(kind)
        system = {
            "negotiation": NEGOTIATION_SYSTEM,
            "collections": COLLECTIONS_SYSTEM,
            "lead_followup": LEAD_FOLLOWUP_SYSTEM,
            "crm_engagement": CRM_ENGAGEMENT_SYSTEM,
            "crm_deal_watch": CRM_DEAL_WATCH_SYSTEM,
        }[kind]
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, default=str)},
        ]
        parsed, usage = await caller.ainvoke(messages)

        # deterministic cleanups BEFORE validation:
        # 1) a greeting name must exist in the contact data
        parsed.message_draft = sanitise_greeting(
            getattr(parsed, "message_draft", "") or "", payload
        )
        # 2) raw / half formatted money ('36414.0') becomes '$36,414'
        parsed.message_draft = normalise_money_text(parsed.message_draft, payload)
        parsed.evidence = normalise_money_text(getattr(parsed, "evidence", "") or "", payload)
        if hasattr(parsed, "reason"):
            parsed.reason = normalise_money_text(parsed.reason, payload)

        problems: list[str] = []
        # only this item's own money fields may be mentioned in its draft
        invented = invented_amounts(parsed.message_draft, payload, kind)
        if invented:
            problems.append(f"amounts {invented} are not this item's amounts in the input")
        # phone / email / URL must appear verbatim in the item's input
        contacts = invented_contacts(parsed.message_draft, payload)
        if contacts:
            problems.append(f"contact details {contacts} are not in the input data")
        problems += draft_rules_problems(parsed, payload, kind)
        return parsed, usage, problems

    async def guarded() -> dict:
        async with semaphore:
            item_started = time.monotonic()
            result = dict(skeleton)
            try:
                parsed, usage, bad = await one_pass()
                # defense in depth: the COPIED fields must equal the input too
                # (payload amounts are formatted strings now - _amount_number parses them)
                fixable = copied_field_problems(parsed, payload, kind)
                glued = glued_problems(parsed, payload)
                regen = 0
                while (bad or fixable or glued) and regen < 1:
                    regen += 1
                    parsed, usage, bad = await one_pass()  # regenerate once
                    fixable = copied_field_problems(parsed, payload, kind)
                    glued = glued_problems(parsed, payload)
                # amounts, ids and the missing list are facts Python already has:
                # whatever the model finally said, code writes the real values
                sync_copied_fields(parsed, payload, kind)
                if glued and not bad:
                    # still glued after the regen: the split is known, so code
                    # writes the spaces instead of throwing a good draft away
                    repair_glued_text(parsed, payload)
                if kind == "collections":
                    # escalation is an extra note on top of the customer reminder
                    # draft, never a replacement for it
                    note = _escalation_note(payload) if _must_escalate(payload) else (
                        getattr(parsed, "escalation_note", None) or ""
                    )
                    parsed.escalation_note = note or None
                    if note:
                        if invented_amounts(note, payload, kind):
                            bad.append("the escalation note mentions an amount not in the input")
                        if invented_contacts(note, payload):
                            bad.append(
                                "the escalation note mentions contact details not in the input"
                            )
                result["source"] = "agent"
                async with usage_lock:
                    regen_count[kind] = regen_count.get(kind, 0) + regen
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
                                "action_plan", "missing", "product", "tone",
                                "escalation_note", "situation", "stall_reason",
                            )
                            if hasattr(parsed, key)
                        }
                    )
                else:
                    model_dump = parsed.model_dump()
                    result.update(model_dump)
                    flags: list[str] = []
                    if getattr(parsed, "likely_reason", None) == "unclear":
                        flags.append("unclear")
                    if getattr(parsed, "owner_team", None) == "unclear":
                        flags.append("unclear")
                    if kind == "collections" and getattr(parsed, "escalation_note", None):
                        flags.append("escalate")
                    result["flags"] = flags
                if kind == "lead_followup":
                    # ids and names are facts Python extracted, never model text
                    lead_row = entry.get("item") or {}
                    result["lead_id"] = str(lead_row.get("lead_id") or "")
                    result["contact_name"] = lead_row.get("contact_name")
                    result["company"] = lead_row.get("company")
                    result["status"] = lead_row.get("status")
                    result["days_since_activity"] = lead_row.get("days_since_activity")
                if kind == "crm_engagement":
                    # identity and the wait are Python's facts, never model text
                    crm_row = entry.get("item") or {}
                    result["record_id"] = str(crm_row.get("record_id") or "")
                    result["contact_name"] = crm_row.get("contact_name")
                    result["company"] = crm_row.get("company")
                    result["waiting_days"] = crm_row.get("waiting_days")
                    result["channel"] = crm_row.get("channel")
                    result["last_customer_message"] = crm_row.get("last_customer_message")
                if kind == "crm_deal_watch":
                    deal_row = entry.get("item") or {}
                    result["record_id"] = str(deal_row.get("record_id") or "")
                    result["account_name"] = deal_row.get("account_name")
                    result["deal_name"] = deal_row.get("deal_name")
                    result["amount"] = deal_row.get("amount")
                    result["days_idle"] = deal_row.get("days_idle")
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
            flags = result["flags"]
            status = "error" if "error" in flags else (
                "needs_review" if "needs_review" in flags else "ok"
            )
            # real per-item progress: only safe metadata (no customer names)
            await ctx.emit(
                "agent.progress",
                agent_id=kind,
                payload={
                    "index": index,
                    "status": status,
                    "cached": False,
                    "duration_ms": round((time.monotonic() - item_started) * 1000),
                },
            )
            return result

    return await guarded()


def _branch_summary(kind: str, results: list[dict]) -> dict:
    """Safe counts describing one specialist branch's outcome (no PII)."""
    return {
        "processed": len(results),
        "cached": sum(1 for r in results if r.get("source") == "cache"),
        "needs_review": sum(1 for r in results if "needs_review" in (r.get("flags") or [])),
        "failed": sum(1 for r in results if "error" in (r.get("flags") or [])),
    }


def _make_specialist_node(kind: str, input_key: str, out_key: str):
    """Build one parallel specialist branch node with real event reporting.

    Cached items are reused without any LLM call; fresh items go through the
    shared semaphore. A branch that fails records the failure and still returns
    its results, so one specialist never cancels its siblings at the join.
    """

    async def node(state: RunState) -> RunState:
        ctx: RunContext = state["_ctx"]
        inputs = state.get(input_key) or []
        cached_hashes = state.get("cached_hashes") or {}

        cached_items: list[dict] = []
        fresh: list[dict] = []
        for entry in inputs:
            if entry["input_hash"] in cached_hashes:
                cached = dict(_item_skeleton(kind, entry))
                cached.update(cached_hashes[entry["input_hash"]])
                cached["source"] = "cache"
                cached_items.append(cached)
            else:
                fresh.append(entry)

        await ctx.emit(
            "agent.queued",
            agent_id=kind,
            payload={"total": len(inputs), "cached": len(cached_items), "fresh": len(fresh)},
        )
        branch_started = time.monotonic()
        await ctx.emit(
            "agent.started", agent_id=kind, payload={"total": len(inputs), "fresh": len(fresh)}
        )

        # cached reuse is real activity too, but never a new LLM call
        for position, _item in enumerate(cached_items):
            await ctx.emit(
                "agent.progress",
                agent_id=kind,
                payload={"index": position, "status": "cached", "cached": True},
            )

        try:
            done = await asyncio.gather(
                *(
                    _run_one(ctx, entry, kind, len(cached_items) + position)
                    for position, entry in enumerate(fresh)
                )
            )
        except Exception as exc:  # branch isolation: never propagate to siblings
            async with ctx.usage_lock:
                ctx.errors.append(f"{kind} branch failed: {type(exc).__name__}: {exc}")
            await ctx.emit("agent.failed", agent_id=kind, payload={"error": type(exc).__name__})
            return {out_key: cached_items}

        results = cached_items + list(done)
        summary = _branch_summary(kind, results)
        await ctx.emit(
            "agent.completed",
            agent_id=kind,
            payload={**summary, "duration_ms": round((time.monotonic() - branch_started) * 1000)},
        )
        return {out_key: results}

    return node


negotiation_node = _make_specialist_node("negotiation", "negotiation_input", "negotiation_out")
collections_node = _make_specialist_node("collections", "collections_input", "collections_out")
lead_followup_node = _make_specialist_node(
    "lead_followup", "lead_followup_input", "lead_followup_out"
)
crm_engagement_node = _make_specialist_node(
    "crm_engagement", "crm_engagement_input", "crm_engagement_out"
)
crm_deal_watch_node = _make_specialist_node(
    "crm_deal_watch", "crm_deal_watch_input", "crm_deal_watch_out"
)


async def group_by_customer_node(state: RunState) -> RunState:
    """Join of the three parallel branches, then group customers (code only).

    Runs only after every branch returned, so it snapshots the shared usage /
    errors / regen accumulated concurrently into the state the master and the
    save stage read.
    """
    ctx: RunContext = state["_ctx"]
    outputs = list(state["negotiation_out"]) + list(state["collections_out"])
    outputs = [o for o in outputs if "needs_review" not in (o.get("flags") or []) and "error" not in (o.get("flags") or [])]
    # Leads are NOT customers: they travel to the master as their own section
    # (matched only by email/phone, never force-merged into customer groups).
    lead_outputs = [
        o for o in state["lead_followup_out"]
        if "needs_review" not in (o.get("flags") or []) and "error" not in (o.get("flags") or [])
    ]
    return {
        "grouped": group_by_customer(outputs),
        "leads_grouped": lead_outputs,
        "usage": dict(ctx.usage),
        "errors": list(ctx.errors),
        "regen_counts": dict(ctx.regen),
    }


async def master_node(state: RunState) -> RunState:
    ctx: RunContext = state["_ctx"]
    # explicit partial-failure counts, so the brief knows a branch fell short
    failed_by_branch = {
        kind: sum(
            1 for item in (state.get(out_key) or [])
            if "needs_review" in (item.get("flags") or []) or "error" in (item.get("flags") or [])
        )
        for kind, out_key in (
            ("negotiation", "negotiation_out"),
            ("collections", "collections_out"),
            ("lead_followup", "lead_followup_out"),
        )
    }
    payload_hint = {
        "stale_quote_count": len(state["negotiation_out"]),
        "unpaid_count": len(state["collections_out"]),
        "stuck_lead_count": len(state["lead_followup_out"]),
        "items_needing_review_or_failed": failed_by_branch,
    }
    grouped = state["grouped"]
    lead_items = state.get("leads_grouped") or []
    # raw numbers only - the LLM may copy but never compute totals
    master_input = {**state["signals_summary"], **payload_hint}

    await ctx.emit("agent.queued", agent_id="master", payload={"customers": len(grouped)})
    master_started = time.monotonic()
    await ctx.emit("agent.started", agent_id="master", payload={"customers": len(grouped)})

    parsed = None
    usage = empty_usage()
    problems: list[str] = []
    attempt = 0
    while attempt < 2:
        attempt += 1
        try:
            caller = state["_caller_factory"]("master")
            parsed, master_payload_used, usage = await run_master(
                grouped, master_input, caller, lead_items
            )
            # same money format in bullets / priorities / conflicts
            parsed = normalise_master_output(parsed, master_payload_used)
            # the brief must only contain numbers Python sent the master, and no
            # leftover thinking - validated against the exact formatted payload
            problems = master_problems(parsed.model_dump(), master_payload_used)
            if not problems:
                break
        except Exception as exc:
            parsed = None
            problems = [f"master call failed: {type(exc).__name__}: {exc}"]

    errors = list(state.get("errors") or [])
    if parsed is not None and not problems:
        master_dump = parsed.model_dump()
        master_dump["flags"] = []
        generated_by = "model"
    else:
        # regenerate did not help: code writes a brief that cannot be wrong
        try:
            brief = build_master_brief(grouped, master_input, lead_items)
        except Exception as exc:  # the fallback itself broke: report and re-raise
            await ctx.emit("agent.failed", agent_id="master", payload={"error": type(exc).__name__})
            raise
        master_dump = brief.model_dump()
        master_dump["flags"] = []
        master_dump["rejected_reason"] = "; ".join(problems)[:600] or "master produced no brief"
        master_dump["generated_by"] = "code_template"
        generated_by = "code_template"
        errors.append("master brief rebuilt from a code template: " + "; ".join(problems)[:300])
    await ctx.emit(
        "agent.completed",
        agent_id="master",
        payload={
            "generated_by": generated_by,
            "attempts": attempt,
            "priority_count": len(master_dump.get("priorities") or []),
            "duration_ms": round((time.monotonic() - master_started) * 1000),
        },
    )
    # RETURN the update: node-side mutation does not persist in the graph
    return {
        "master": master_dump,
        "usage": add_usage(state.get("usage") or empty_usage(), usage),
        "calls": (state.get("calls") or 0) + attempt,
        "errors": errors,
    }


async def save_node(state: RunState) -> RunState:
    database = state["_database"]
    negotiation = state["negotiation_out"]
    collections = state["collections_out"]
    lead_followup = state["lead_followup_out"]
    crm_engagement = state.get("crm_engagement_out") or []
    crm_deal_watch = state.get("crm_deal_watch_out") or []
    all_outputs = negotiation + collections + lead_followup + crm_engagement + crm_deal_watch
    counts = {
        "negotiation_total": len(negotiation),
        "collections_total": len(collections),
        "lead_followup_total": len(lead_followup),
        "crm_engagement_total": len(crm_engagement),
        "crm_deal_watch_total": len(crm_deal_watch),
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
        "crm_engagement": crm_engagement,
        "crm_deal_watch": crm_deal_watch,
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
    graph.add_node("crm_engagement", crm_engagement_node)
    graph.add_node("crm_deal_watch", crm_deal_watch_node)
    graph.add_node("group_by_customer", group_by_customer_node)
    graph.add_node("master", master_node)
    graph.add_node("save", save_node)
    graph.add_edge(START, "load_signals")
    # negotiation, collections and lead_followup fan out from load_signals and
    # run as independent parallel branches; each keeps its own *_out key so the
    # fan-in at group_by_customer has no state to overwrite.
    graph.add_edge("load_signals", "negotiation")
    graph.add_edge("load_signals", "collections")
    graph.add_edge("load_signals", "lead_followup")
    graph.add_edge("negotiation", "group_by_customer")
    graph.add_edge("collections", "group_by_customer")
    graph.add_edge("lead_followup", "group_by_customer")
    # the CRM branches are not ERP customers: the group node ignores
    # their outputs (they are neither grouped nor briefed by the master),
    # but they join at group_by_customer like every other branch - a
    # direct edge into save made save fire on the CRM branches' earlier
    # superstep, before the master existed (Oct 24 build break).
    graph.add_edge("load_signals", "crm_engagement")
    graph.add_edge("load_signals", "crm_deal_watch")
    graph.add_edge("crm_engagement", "group_by_customer")
    graph.add_edge("crm_deal_watch", "group_by_customer")
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
        "crm_engagement": CrmEngagementOutput,
        "crm_deal_watch": CrmDealWatchOutput,
    }[kind]
    return make_caller(build_model("master" if kind == "master" else "sub"), schema)


async def run_pipeline(
    run_id: Optional[str] = None,
    database: Any = None,
    erp: Any = None,
    caller_factory: Optional[Callable] = None,
    now_epoch: Optional[float] = None,
    ignore_cache: bool = False,
    trigger: Optional[str] = None,
    events: Optional[EventService] = None,
) -> dict:
    """Run the whole pipeline once. Returns the saved agent_runs doc.

    The run's existing ``run_id`` is reused as the task id, so task identity
    stays consistent with the ``agent_runs`` document and with the scheduler's
    run guard. Real task/agent/tool events are emitted throughout; a broken
    event stream never changes what the pipeline actually does (the service's
    ``emit`` never raises).
    """
    if database is None:
        database = app_db.get_db()
    if caller_factory is None:
        caller_factory = default_caller_factory
    erp_owned = erp is None
    client = ERPClient() if erp_owned else None

    now_epoch = now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp()
    task_id = run_id or str(uuid.uuid4())
    context = RunContext(
        task_id=task_id,
        events=events or get_event_service(),
        caller_factory=caller_factory,
        sema=asyncio.Semaphore(CONCURRENCY),
        usage_lock=asyncio.Lock(),
        usage=empty_usage(),
        regen={kind: 0 for kind in AGENT_KINDS},
        errors=[],
    )
    initial: RunState = {
        "run_id": task_id,
        "started_at": datetime.fromtimestamp(now_epoch, tz=timezone.utc).isoformat(),
        "usage": empty_usage(),
        "calls": 0,
        "errors": [],
        "now_epoch": now_epoch,
        "_database": database,
        "_erp": client if erp is None else erp,
        "_caller_factory": caller_factory,
        "_use_cache": not ignore_cache,
        "_ctx": context,
    }
    await context.emit(
        "task.created",
        payload={"trigger": trigger or "manual", "ignore_cache": bool(ignore_cache)},
    )
    await context.emit("task.started", payload={"now_epoch": now_epoch})
    try:
        app = build_graph()
        final = await app.ainvoke(initial)
    except Exception as exc:
        await context.emit("task.failed", payload={"error": type(exc).__name__})
        raise
    finally:
        if erp_owned and client is not None:
            await client.close()
    result = {
        "run_id": final["run_id"],
        "started_at": final["started_at"],
        "counts": final.get("saved_counts", {}),
        "master": final.get("master"),
        "negotiation": final.get("negotiation_out", []),
        "collections": final.get("collections_out", []),
        "lead_followup": final.get("lead_followup_out", []),
        "crm_engagement": final.get("crm_engagement_out", []),
        "crm_deal_watch": final.get("crm_deal_watch_out", []),
        "usage": final.get("usage"),
        "errors": final.get("errors", []),
    }
    await context.emit(
        "task.completed",
        payload={
            "counts": result["counts"],
            "total_tokens": (result.get("usage") or {}).get("total_tokens", 0),
            "calls": final.get("calls", 0),
            "duration_ms": round((time.monotonic() - context.started) * 1000),
            "master_generated_by": (result.get("master") or {}).get("generated_by", "model"),
            "had_errors": bool(result["errors"]),
        },
    )
    return result

