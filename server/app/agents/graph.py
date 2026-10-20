"""LangGraph pipeline: one agent run.

    load_signals (code)  ->  negotiation (LLM, <=5 concurrent)
                         ->  collections (LLM, <=5 concurrent)
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
from app.services.processors import to_number
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
from app.agents.master import group_by_customer, run_master
from app.schemas.agents import MasterOutput, NegotiationOutput, CollectionsOutput

CONCURRENCY = int(settings.AGENT_CONCURRENCY)
CACHE_HOURS = int(settings.AGENT_CACHE_HOURS)
MAX_ITEMS = int(settings.MAX_ITEMS_PER_AGENT)


class RunState(TypedDict, total=False):
    run_id: str
    started_at: str
    negotiation_input: list[dict]
    collections_input: list[dict]
    cached_hashes: dict
    signals_summary: dict
    negotiation_out: list[dict]
    collections_out: list[dict]
    grouped: dict
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
        text = node.strip().replace(",", "").replace("$", "").strip()
        try:  # plain numeric strings are legal money references too
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
    """Dollar amounts in the draft whose value is NOT anywhere in the payload.
    The pipeline runs this on every message_draft."""
    allowed: set[float] = set()
    _walk_numbers(payload, allowed)
    bad: list[float] = []
    for match in _DOLLAR_RE.finditer(draft or ""):
        value = float((match.group(1) or match.group(2)).replace(",", ""))
        if not any(abs(value - allowed_value) < 0.005 for allowed_value in allowed):
            bad.append(value)
    return bad


def master_invented_amounts(master_dict: dict, payload: dict) -> list[float]:
    """Dollar amounts in the MASTER's bullets/priorities/conflicts that do not
    appear anywhere in the data Python sent it. Masters must not do math,"""
    allowed: set[float] = set()
    _walk_numbers(payload, allowed)
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
        for kind in ("negotiation", "collections"):
            for item in run.get(kind, []):
                item_hash = item.get("input_hash")
                if not item_hash or item.get("source") != "agent":
                    continue
                if "needs_review" in (item.get("flags") or []):
                    continue
                if not item.get("message_draft"):
                    continue  # a draft-less copy (old schema) is not reusable
                saved[item_hash] = item
    for item in items:
        if item["input_hash"] in saved:
            item["cached_output"] = saved[item["input_hash"]]
    return {item["input_hash"]: item["cached_output"] for item in items if item.get("cached_output")}




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
    else:
        stable = payload
    blob = json.dumps({"kind": kind, "stable": stable}, sort_keys=True, default=str)
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

    # --- cache: reuse outputs from the last CACHE_HOURS hours -------------
    cutoff_epoch = state["now_epoch"] - CACHE_HOURS * 3600
    cached: dict = {}
    if state.get("_use_cache", True):
        recent = await database.agent_runs.find(
            {"finished_epoch": {"$gt": cutoff_epoch}}
        ).to_list(50)
        cached = cache_lookup(recent, negotiation_items + collections_items, cutoff_epoch)

    signals_summary = {
        "quotes_scanned": len(quotes),
        "orders_scanned": len(orders),
        "stale_quotes_count": len(stale),
        "unpaid_invoices_count": len(unpaid),
        "unpaid_balance_total": round(sum(r.get("balance", 0.0) for r in unpaid), 2),
        "stalled_orders": "signal unavailable - unreliable by design (no status timestamps)",
    }
    return {
        "negotiation_input": negotiation_items,
        "collections_input": collections_items,
        "cached_hashes": cached,
        "signals_summary": signals_summary,
    }


# ---------------------------------------------------------------------------
# sub-agent nodes
# ---------------------------------------------------------------------------


def _item_skeleton(kind: str, entry: dict) -> dict:
    row = entry.get("item") or {}
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

    async def one_pass() -> Tuple[Any, dict]:
        # send the EXACT payload that the input hash covers (no rebuilds)
        caller = caller_factory(kind)
        system = NEGOTIATION_SYSTEM if kind == "negotiation" else COLLECTIONS_SYSTEM
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, default=str)},
        ]
        parsed, usage = await caller.ainvoke(messages)
        # dollar validation on the draft
        bad = invented_amounts(getattr(parsed, "message_draft", "") or "", payload)
        return parsed, usage, bad

    async def guarded() -> dict:
        async with semaphore:
            result = dict(skeleton)
            try:
                parsed, usage, bad = await one_pass()
                # defense in depth: the COPIED fields must equal the input too
                if kind == "negotiation" and abs(getattr(parsed, "quote_amount", -1) - _amount_number(payload["quote"]["amount"])) > 0.005:
                    bad = bad or [getattr(parsed, "quote_amount", 0.0)]
                if kind == "collections" and abs(getattr(parsed, "balance", -1) - _amount_number(payload["invoice"]["balance"])) > 0.005:
                    bad = bad or [getattr(parsed, "balance", 0.0)]
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
                    bad = invented_amounts(parsed.message_draft, payload)
                result["source"] = "agent"
                async with usage_lock:
                    regen_count[kind] += regen
                if bad:
                    result["flags"] = ["needs_review"]
                    result["message_draft"] = None
                    result["rejected_reason"] = (
                        f"message mentioned amounts {bad} that are not in the input data"
                    )
                    result.update(
                        {
                            key: getattr(parsed, key)
                            for key in (
                                "confidence", "evidence", "likely_reason", "tactic",
                                "approach", "quote_id", "quote_amount", "invoice_id",
                                "balance", "reason",
                            )
                            if hasattr(parsed, key)
                        }
                    )
                else:
                    model_dump = parsed.model_dump()
                    result.update(model_dump)
                    result["flags"] = ["unclear"] if getattr(parsed, "likely_reason", None) == "unclear" else []
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


async def group_by_customer_node(state: RunState) -> RunState:
    outputs = list(state["negotiation_out"]) + list(state["collections_out"])
    outputs = [o for o in outputs if "needs_review" not in (o.get("flags") or []) and "error" not in (o.get("flags") or [])]
    return {"grouped": group_by_customer(outputs)}


async def master_node(state: RunState) -> RunState:
    payload_hint = {
        "stale_quote_count": len(state["negotiation_out"]),
        "unpaid_count": len(state["collections_out"]),
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
            parsed, _, usage = await run_master(grouped, master_input, caller)
            # numbers-only rule extends to the whole brief: no invented totals
            bad = master_invented_amounts(
                parsed.model_dump(), {"signals": master_input, "grouped": grouped}
            )
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
    counts = {
        "negotiation_total": len(negotiation),
        "collections_total": len(collections),
        "unclear": sum(1 for o in negotiation + collections if "unclear" in (o.get("flags") or [])),
        "needs_review": sum(
            1 for o in negotiation + collections if "needs_review" in (o.get("flags") or []) or "error" in (o.get("flags") or [])
        ),
        "cached": sum(1 for o in negotiation + collections if o.get("source") == "cache"),
    }
    doc = {
        "run_id": state["run_id"],
        "started_at": state["started_at"],
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "finished_epoch": state["now_epoch"],
        "counts": counts,
        "negotiation": negotiation,
        "collections": collections,
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
    graph.add_node("group_by_customer", group_by_customer_node)
    graph.add_node("master", master_node)
    graph.add_node("save", save_node)
    graph.add_edge(START, "load_signals")
    graph.add_edge("load_signals", "negotiation")
    graph.add_edge("negotiation", "collections")
    graph.add_edge("collections", "group_by_customer")
    graph.add_edge("group_by_customer", "master")
    graph.add_edge("master", "save")
    graph.add_edge("save", END)
    return graph.compile()


def default_caller_factory(kind: str):
    """Fresh caller per call; 'master' uses MODEL_MASTER, else MODEL_SUB."""
    schema = MasterOutput if kind == "master" else (NegotiationOutput if kind == "negotiation" else CollectionsOutput)
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
            "usage": final.get("usage"),
            "errors": final.get("errors", []),
        }
    finally:
        if erp_owned and client is not None:
            await client.close()
