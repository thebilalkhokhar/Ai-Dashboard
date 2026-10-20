"""API endpoints.

GET /api/signals                    - all attention-needed items in one JSON
GET /api/customers/{id}/context     - one customer: quotes, orders, payments,
                                      leads + the ambiguous flag
POST /api/agents/run                - starts ONE agent pipeline run (idempotent,
                                      nothing is sent anywhere)
GET /api/briefing/latest            - the latest saved master output + items

Every number is computed in app/services/processors.py with ordinary Python.
"""

from __future__ import annotations

import asyncio
import logging
import json
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Annotated, Dict, Any, Set

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import StreamingResponse

from app.db import get_db
from app.config import settings
from app.schemas.signals import (
    SignalsReport,
    SignalsSummary,
    StaleQuoteSummary,
    StalledOrderSummary,
    UnpaidSummary,
)
from app.schemas.customer import CustomerContext
from app.schemas.chat import ChatRequest
from app.services.erp_client import ERPClient, ERPError
from app.services import processors

router = APIRouter()


def _round2(value: Any) -> float:
    return processors.to_number(value)


def summarize(row_list: list[dict]) -> tuple[int, float]:
    """count + amount for a signal list."""
    return len(row_list), round(sum(_round2(row["amount"]) for row in row_list), 2)


def summarize_unpaid(row_list: list[dict]) -> tuple[int, float, int, int, int]:
    """count, balance total, overdue count, not-paid count, partially-paid count."""
    count = len(row_list)
    balance = round(sum(_round2(row["balance"]) for row in row_list), 2)
    overdue = sum(1 for row in row_list if row["overdue"])
    not_paid = sum(
        1
        for row in row_list
        if row["payment_status"] in (0, None) and row["payment_status_label"] != "Partially Paid"
    )
    partial = sum(1 for row in row_list if row["payment_status"] == 1)
    return count, balance, overdue, not_paid, partial


def summarize_stalled(row_list: list[dict]) -> tuple[int, float]:
    return len(row_list), round(sum(_round2(row["total_amount"]) for row in row_list), 2)


@router.get("/signals", response_model=SignalsReport)
async def get_signals(
    stale_days: Annotated[int | None, Query(ge=0)] = None,
    stalled_days: Annotated[int | None, Query(ge=0)] = None,
    overdue_days: Annotated[int | None, Query(ge=0)] = None,
    include_stalled: bool = True,
) -> SignalsReport:
    """Everything that needs attention, in one deterministic JSON."""
    db = get_db()
    now = datetime.now(timezone.utc)

    quotes = await db.quotes_flat.find().to_list(100_000)
    orders = await db.orders_flat.find().to_list(100_000)

    # quotes that already converted into an order must not count as stale
    order_quote_ids = {row["quote_id"] for row in orders if row.get("quote_id") is not None}
    stale = processors.stale_quotes(quotes, now, days=stale_days, order_quote_ids=order_quote_ids)
    unpaid = processors.unpaid_invoices(orders, now, overdue_days=overdue_days)
    stalled = processors.stalled_orders(orders, now, days=stalled_days)

    stale_count, stale_amount = summarize(stale)
    unpaid_count, unpaid_balance, overdue_count, not_paid, partial = summarize_unpaid(unpaid)
    stalled_count, stalled_amount = summarize_stalled(stalled)

    quote_dates = sorted(d for d in (processors.iso_or_none(q.get("created_at")) for q in quotes) if d)
    order_dates = sorted(d for d in (processors.iso_or_none(o.get("invoice_date")) for o in orders) if d)

    return SignalsReport(
        generated_at=now.isoformat(),
        stale_quotes=stale,
        unpaid_invoices=unpaid,
        stalled_orders={"reliable": False, "items": stalled if include_stalled else []},
        summary=SignalsSummary(
            quotes_scanned=len(quotes),
            orders_scanned=len(orders),
            data_from=min(quote_dates or order_dates or [""]),
            data_to=max(quote_dates or order_dates or [""]),
            stale_quotes=StaleQuoteSummary(count=stale_count, total_amount=stale_amount),
            unpaid_invoices=UnpaidSummary(
                count=unpaid_count,
                total_amount=unpaid_balance,
                overdue_count=overdue_count,
                unpaid_count=not_paid,
                partially_paid_count=partial,
            ),
            stalled_orders=StalledOrderSummary(
                count=stalled_count,
                total_amount=stalled_amount,
                reliable=False,
            ),
        ),
    )


_running_tasks: Set[asyncio.Task] = set()
_run_log = logging.getLogger("agents_api")


async def _background_run(run_id: str) -> None:
    """Runs the pipeline outside the request so it can return run_id fast."""
    from app import db as app_db
    from app.agents.graph import run_pipeline

    try:
        await run_pipeline(run_id=run_id)
    except Exception as exc:  # keep a trace even if the graph itself blew up
        _run_log.exception("agent run %s failed", run_id)
        try:
            await app_db.get_db().agent_runs.insert_one(
                {
                    "run_id": run_id,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "status": "failed",
                    "errors": [f"{type(exc).__name__}: {exc}"],
                }
            )
        except Exception:
            pass


@router.post("/agents/run")
async def run_agents() -> dict:
    """Start one pipeline run in the background; returns its id immediately."""
    run_id = str(uuid.uuid4())
    task = asyncio.create_task(_background_run(run_id))
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)
    return {"run_id": run_id, "status": "started"}


@router.get("/briefing/latest")
async def latest_briefing() -> dict:
    """Latest agent run: master brief + the sub-agent items it is built from."""
    cursor = get_db().agent_runs.find().sort("started_at", -1)
    docs = await cursor.to_list(1)
    if not docs:
        return {"run_id": None, "message": "no agent run yet - POST /api/agents/run first"}
    doc = docs[0]
    doc.pop("_id", None)
    return doc


@router.get("/customers/{customer_id}/context", response_model=CustomerContext)
async def get_customer_context(
    customer_id: Annotated[int, Path(ge=1)],
    lead_limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> CustomerContext:
    """Everything the system knows about one customer, joined in Python."""
    async with ERPClient() as erp:
        try:
            data = await processors.build_customer_context(
                customer_id, database=get_db(), erp=erp, max_leads=lead_limit or 100
            )
        except ERPError as exc:  # ERP down or token broken: still return the DB part
            data = await processors.build_customer_context(
                customer_id, database=get_db(), erp=None
            )
            data["notes"].append(f"ERP lookup failed: {exc}")
    return CustomerContext(**data)


_chat_request_times: deque[float] = deque()
_chat_rate_lock = asyncio.Lock()


async def _chat_rate_allowed() -> bool:
    """One process-wide limit shared by all callers: 30 requests per minute."""
    now = time.monotonic()
    async with _chat_rate_lock:
        while _chat_request_times and now - _chat_request_times[0] >= 60:
            _chat_request_times.popleft()
        if len(_chat_request_times) >= 30:
            return False
        _chat_request_times.append(now)
        return True


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# Proxies must not buffer a streamed answer into one flush.
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """Stream read-only chat progress and the verified final answer."""
    from app.agents.chat import daily_token_total, log_rejected_turn, stream_chat

    database = get_db()
    if not await _chat_rate_allowed():
        await log_rejected_turn(request.thread_id, request.message, "rate_limited", database)

        async def rate_error():
            yield _sse("error", {"message": "Chat limit reached. Try again in a minute."})

        return StreamingResponse(
            rate_error(), media_type="text/event-stream", headers=_SSE_HEADERS
        )

    if await daily_token_total(database) >= settings.CHAT_DAILY_TOKEN_CAP:
        await log_rejected_turn(request.thread_id, request.message, "daily_token_cap", database)

        async def cap_error():
            yield _sse("error", {"message": "The daily chat token limit has been reached."})

        return StreamingResponse(
            cap_error(), media_type="text/event-stream", headers=_SSE_HEADERS
        )

    async def events():
        async for event in stream_chat(request.thread_id, request.message, database):
            yield _sse(event["event"], event["data"])

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
