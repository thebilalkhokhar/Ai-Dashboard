"""Live dashboard: Server-Sent Events + a plain JSON snapshot endpoint.

GET /api/live     - event stream. On connect: event "snapshot" (the current
                    dashboard), then event "briefing" (the latest agent run),
                    then every Redis dashboard update as it is published.
                    A ": heartbeat" comment goes out every 15 seconds so
                    proxies and browsers know the connection is alive.
GET /api/metrics  - the latest snapshot as plain JSON (mock data for the
                    Next.js client during local development).

At most LIVE_MAX_CLIENTS streams at once; the count is kept in Redis so the
cap is shared by every worker, and every subscription is closed when its client
disconnects.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import suppress
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app import db as app_db
from app.config import settings
from app.services import metrics
from app.services.limits import SlotCounter
from app.services.redis_client import get_redis
from app.services.scheduler import SNAPSHOT_KEY, UPDATES_CHANNEL

log = logging.getLogger("live")

router = APIRouter(tags=["live"])

HEARTBEAT_SECONDS = 15

# Concurrent SSE clients, one token per connection. The count lives in Redis so
# the cap holds across workers; slots expire on their own if a worker dies
# without releasing (see app/services/limits.py).
_sse_slots = SlotCounter(
    "live:clients",
    int(settings.LIVE_MAX_CLIENTS),
    int(settings.LIVE_CLIENT_SLOT_TTL_SECONDS),
)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _decode(raw: Any) -> dict:
    """Redis message -> {'event': ..., 'data': ...}. Never raises."""
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {"event": "update", "data": raw}
    if isinstance(parsed, dict) and "event" in parsed:
        return parsed
    return {"event": "update", "data": parsed}


async def _current_snapshot() -> Optional[dict]:
    """Redis copy first (what the scheduler publishes); build it if missing."""
    try:
        raw = await get_redis().get(SNAPSHOT_KEY)
        if raw:
            return _decode(raw).get("data") or {}
    except Exception:
        log.warning("could not read the snapshot from Redis", exc_info=True)
    try:
        return await metrics.build_snapshot()
    except Exception:
        log.exception("could not build a snapshot from Mongo")
        return None


async def _latest_briefing() -> Optional[dict]:
    try:
        docs = (
            await app_db.get_db()
            .agent_runs.find({"finished_epoch": {"$exists": True}})
            .sort("finished_epoch", -1)
            .to_list(1)
        )
    except Exception:
        log.warning("could not read the latest briefing", exc_info=True)
        return None
    if not docs:
        return None
    doc = docs[0]
    doc.pop("_id", None)
    return doc


async def _acquire_client() -> Optional[str]:
    """Reserve a slot; returns its token, or None when the limit is reached."""
    return await _sse_slots.acquire()


async def _release_client(token: str) -> None:
    await _sse_slots.release(token)


async def _event_stream(client_token: str) -> AsyncIterator[str]:
    """Snapshot, briefing, then live updates. Cleans up on disconnect."""
    pubsub = None
    try:
        snapshot = await _current_snapshot()
        if snapshot is not None:
            yield _sse("snapshot", snapshot)
        else:
            yield _sse("error", {"message": "no snapshot available yet"})

        briefing = await _latest_briefing()
        if briefing is not None:
            yield _sse("briefing", briefing)

        pubsub = get_redis().pubsub()
        await pubsub.subscribe(UPDATES_CHANNEL)

        last_beat = time.monotonic()
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                envelope = _decode(message.get("data"))
                yield _sse(envelope.get("event", "update"), envelope.get("data"))
                last_beat = time.monotonic()
            elif time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
                last_beat = time.monotonic()
                yield ": heartbeat\n\n"  # SSE comment: keeps the pipe warm
    finally:
        # client is gone: drop the subscription and free the slot
        if pubsub is not None:
            with suppress(Exception):
                await pubsub.unsubscribe(UPDATES_CHANNEL)
            with suppress(Exception):
                await pubsub.aclose()
        await _release_client(client_token)


@router.get("/live")
async def live() -> StreamingResponse:
    """SSE stream of dashboard snapshots, briefings and errors."""
    client_token = await _acquire_client()
    if client_token is None:
        raise HTTPException(
            status_code=503,
            detail=f"too many live connections (max {settings.LIVE_MAX_CLIENTS})",
        )
    return StreamingResponse(
        _event_stream(client_token),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
        # Starlette runs this even when the client disappears before the stream
        # generator starts, so a slot can never leak. Releasing twice is safe.
        background=BackgroundTask(_release_client, client_token),
    )


@router.get("/metrics")
async def latest_metrics() -> dict:
    """Latest dashboard snapshot as plain JSON."""
    snapshot = await _current_snapshot()
    if snapshot is None:
        raise HTTPException(status_code=503, detail="no snapshot available yet")
    return snapshot
