"""API endpoints.

Every route here requires the X-API-Key header (see app/api/security.py) and
CORS_ORIGINS decides which browser origins may call it.

GET /api/signals                    - all attention-needed items in one JSON
GET /api/customers/{id}/context     - one customer: quotes, orders, payments,
                                      leads + the ambiguous flag
POST /api/agents/run                - starts ONE agent pipeline run: a Redis
                                      lock plus a "running" document in
                                      agent_runs make a second concurrent call
                                      answer 409 with the run_id in progress
                                      (nothing is ever sent anywhere)
GET /api/briefing/latest            - the latest saved master output + items

Every number is computed in app/services/processors.py with ordinary Python.
"""

from __future__ import annotations

import asyncio
import logging
import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Dict, Any, Optional, Set

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import JSONResponse, StreamingResponse

from app.db import get_db
from app.config import settings
from app.schemas.signals import (
    SignalsReport,
    StuckLead,
    StuckLeadsResponse,
)
from app.schemas.customer import CustomerContext
from app.schemas.chat import ChatRequest
from app.services.erp_client import ERPClient, ERPError
from app.services import processors, signals
from app.services.limits import RateLimiter
from app.services.run_guard import acquire_run, release_run

router = APIRouter()


@router.get("/signals", response_model=SignalsReport)
async def get_signals(
    stale_days: Annotated[int | None, Query(ge=0)] = None,
    stalled_days: Annotated[int | None, Query(ge=0)] = None,
    overdue_days: Annotated[int | None, Query(ge=0)] = None,
    include_stalled: bool = True,
) -> SignalsReport:
    """Everything that needs attention, in one deterministic JSON.

    Served from the Redis copy the scheduler leader refreshes every
    REFRESH_MINUTES, so reading it costs one cache lookup instead of copying
    the ERP collections into memory. A custom window or a cold cache builds the
    report straight from Mongo (projections, streaming cursors, $sort + $limit).
    """
    db = get_db()
    key = signals.cache_key(stale_days, stalled_days, overdue_days, include_stalled)
    cached = await signals.cache_load(key)
    if cached is not None:
        return SignalsReport(**cached)

    payload = await signals.build_signals_payload(
        db,
        datetime.now(timezone.utc),
        stale_days=stale_days,
        stalled_days=stalled_days,
        overdue_days=overdue_days,
        include_stalled=include_stalled,
    )
    await signals.cache_store(payload, key)
    return SignalsReport(**payload)


_running_tasks: Set[asyncio.Task] = set()
_run_log = logging.getLogger("agents_api")


async def _background_run(run_id: str) -> None:
    """Runs the pipeline outside the request so it can return run_id fast."""
    from app import db as app_db
    from app.agents.graph import run_pipeline

    database = app_db.get_db()
    try:
        await run_pipeline(run_id=run_id)
    except Exception as exc:  # keep a trace even if the graph itself blew up
        _run_log.exception("agent run %s failed", run_id)
        try:
            await database.agent_runs.insert_one(
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
    finally:
        # whatever happened, the next run must be allowed to start
        await release_run(run_id, database)


@router.post("/agents/run")
async def run_agents() -> dict:
    """Start one pipeline run in the background; returns its id immediately.

    One run at a time: the Redis lock (TTL AGENT_RUN_LOCK_TTL_SECONDS) and the
    "running" document in agent_runs are taken before the task is created, so a
    second call - from this worker or another one - gets 409 plus the run_id
    already in progress instead of a second, identical, billed run.
    """
    run_id = str(uuid.uuid4())
    database = get_db()
    already_running: Optional[str] = await acquire_run(run_id, database)
    if already_running:
        _run_log.info("agent run refused: %s is still running", already_running)
        return JSONResponse(
            status_code=409,
            content={
                "run_id": already_running,
                "status": "already_running",
                "detail": "an agent run is already in progress",
            },
        )
    task = asyncio.create_task(_background_run(run_id))
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)
    return {"run_id": run_id, "status": "started"}


@router.get("/leads/stuck", response_model=StuckLeadsResponse)
async def get_stuck_leads(
    days: Annotated[int | None, Query(ge=1, le=90)] = None,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> StuckLeadsResponse:
    """Leads that are stuck before becoming an order. Pure Python, no AI.

    Same filter as the lead follow-up agent: status New / Attempted to
    Contact, last activity within LEAD_ACTIVE_DAYS, a description present,
    not flagged by the lead desk. Read-only: nothing is sent or written.
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    rows = await db.leads.find(processors.LEAD_STATUS_QUERY, processors.LEAD_FIELDS).to_list(50_000)
    stuck = processors.stuck_leads(rows, now, days=days)
    return StuckLeadsResponse(
        generated_at=now.isoformat(),
        days=days or settings.LEAD_ACTIVE_DAYS,
        total=len(stuck),
        items=stuck[: limit or 100],
    )


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


# Chat limit: CHAT_RATE_LIMIT_REQUESTS per CHAT_RATE_LIMIT_WINDOW_SECONDS, in
# Redis so every worker shares it (process-local deque when Redis is down).
_chat_limiter = RateLimiter(
    "chat:rate-limit",
    int(settings.CHAT_RATE_LIMIT_REQUESTS),
    int(settings.CHAT_RATE_LIMIT_WINDOW_SECONDS),
)


async def _chat_rate_allowed() -> bool:
    """One limit shared by all workers, straight from Redis."""
    return await _chat_limiter.allow()


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
