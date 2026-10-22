"""Live dashboard: Server-Sent Events + a plain JSON snapshot endpoint.

GET /api/live     - event stream. On connect: event "snapshot" (the current
                    dashboard), then event "briefing" (the latest agent run),
                    then every Redis dashboard update as it is published.
                    A ": heartbeat" comment goes out every 15 seconds so
                    proxies and browsers know the connection is alive.
GET /api/metrics  - the latest snapshot as plain JSON (mock data for the
                    Next.js client during local development).
GET /api/tasks/{task_id}/events?after={seq}
                  - replay a task's persisted events after a cursor, then keep
                    streaming new ones (subscribe-before-replay, in seq order,
                    deduplicated). Uses its own shared Redis slot pool.

At most LIVE_MAX_CLIENTS streams at once; the count is kept in Redis so the
cap is shared by every worker, and every subscription is closed when its client
disconnects.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import suppress
from typing import Annotated, Any, AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app import db as app_db
from app.config import settings
from app.services import metrics
from app.services.events import TASK_EVENT_ENVELOPE, get_event_service
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

# A separate capacity pool for the per-task replay/live stream, reusing the same
# shared SlotCounter mechanism and the same limits, so task-event viewers can
# never starve the dashboard snapshot stream (or vice versa).
_task_event_slots = SlotCounter(
    "task:events:clients",
    int(settings.LIVE_MAX_CLIENTS),
    int(settings.LIVE_CLIENT_SLOT_TTL_SECONDS),
)

# If the live relay drops an event Mongo never got (a publish that raced a
# persistence failure), an in-order replay would stall forever waiting for the
# missing seq. After this many seconds with no contiguous head we advance past
# the gap and say so, rather than holding the rest of the timeline hostage.
_TASK_EVENT_GAP_SECONDS = 3.0


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
                # task events belong to GET /api/tasks/{id}/events, not the
                # dashboard stream: skip them here so /api/live keeps emitting
                # only snapshot/briefing/error exactly as before.
                if envelope.get("event") == TASK_EVENT_ENVELOPE:
                    last_beat = time.monotonic()
                    continue
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


# ---------------------------------------------------------------------------
# per-task replay + live event stream
# ---------------------------------------------------------------------------


def _extract_task_event(envelope: dict, task_id: str) -> Optional[dict]:
    """A published envelope -> its task-event data, or None (never raises)."""
    if envelope.get("event") != TASK_EVENT_ENVELOPE:
        return None
    data = envelope.get("data")
    if isinstance(data, dict) and data.get("task_id") == task_id:
        return data
    return None


async def _task_event_stream(
    task_id: str, after: int, client_token: str
) -> AsyncIterator[str]:
    """Replay persisted events, then continue live - without losing the handoff.

    Ordering rules that make the replay-to-live transition gap-free:

      * subscribe to Redis BEFORE reading Mongo, so an event published while we
        replay is queued and delivered instead of dropped;
      * deliver in ascending ``seq``; a live event that arrives before its lower
        neighbours waits in a reorder buffer;
      * deduplicate by ``seq`` and ``event_id`` so the replay/live overlap sends
        an event once;
      * if a genuine gap can never be filled (a live event Mongo never recorded)
        the buffer is advanced after a short timeout so the timeline is not
        stalled forever.
    """
    service = get_event_service()
    pubsub = None
    last = after
    delivered: set[int] = set()
    seen_ids: set[str] = set()
    pending: dict[int, dict] = {}
    gap_since: Optional[float] = None
    last_beat = time.monotonic()
    try:
        pubsub = get_redis().pubsub()
        await pubsub.subscribe(UPDATES_CHANNEL)

        for event in await service.get_events(task_id, after=after):
            data = event.model_dump(mode="json")
            last = max(last, int(event.seq))
            delivered.add(event.seq)
            if event.event_id:
                seen_ids.add(event.event_id)
            yield _sse("task_event", data)
            last_beat = time.monotonic()

        def flush_contiguous() -> list[dict]:
            nonlocal last
            ready: list[dict] = []
            while (last + 1) in pending:
                last += 1
                ready.append(pending.pop(last))
            return ready

        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message and message.get("type") == "message":
                data = _extract_task_event(_decode(message.get("data")), task_id)
                if data is not None:
                    seq = int(data.get("seq") or 0)
                    event_id = data.get("event_id")
                    already = seq <= last or seq in delivered or (
                        event_id and event_id in seen_ids
                    )
                    if not already:
                        pending[seq] = data
                        if event_id:
                            seen_ids.add(event_id)
                        for ready in flush_contiguous():
                            delivered.add(last)
                            yield _sse("task_event", ready)
                            last_beat = time.monotonic()
                        gap_since = None

            if pending and (last + 1) not in pending:
                if gap_since is None:
                    gap_since = time.monotonic()
                elif time.monotonic() - gap_since >= _TASK_EVENT_GAP_SECONDS:
                    # the missing head is gone for good: jump to the lowest
                    # buffered seq so the rest still reaches the client.
                    log.warning(
                        "task %s: skipping a replay gap at seq %s",
                        task_id,
                        last + 1,
                    )
                    last = min(pending) - 1
                    for ready in flush_contiguous():
                        delivered.add(last)
                        yield _sse("task_event", ready)
                        last_beat = time.monotonic()
                    gap_since = None

            if time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
                last_beat = time.monotonic()
                yield ": heartbeat\n\n"
    finally:
        if pubsub is not None:
            with suppress(Exception):
                await pubsub.unsubscribe(UPDATES_CHANNEL)
            with suppress(Exception):
                await pubsub.aclose()
        await _task_event_slots.release(client_token)


@router.get("/tasks/{task_id}/events")
async def task_events(
    task_id: Annotated[str, Path(min_length=1, max_length=128)],
    after: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    """Replay persisted events after ``after``, then keep streaming live ones.

    Reuses the API-key auth (router-level) and a shared Redis slot pool, so the
    SSE cap holds across workers. An unknown task is a 404; a negative cursor is
    rejected by validation before any work happens.
    """
    client_token = await _task_event_slots.acquire()
    if client_token is None:
        raise HTTPException(
            status_code=503,
            detail=f"too many task-event connections (max {settings.LIVE_MAX_CLIENTS})",
        )
    service = get_event_service()
    try:
        exists = await service.task_exists(task_id)
    except Exception:
        await _task_event_slots.release(client_token)
        log.warning("could not check task %s for replay", task_id, exc_info=True)
        raise HTTPException(status_code=503, detail="task event store unavailable")
    if not exists:
        await _task_event_slots.release(client_token)
        raise HTTPException(status_code=404, detail="unknown task")

    return StreamingResponse(
        _task_event_stream(task_id, int(after), client_token),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
        # free the slot even if the client vanishes before the generator runs
        background=BackgroundTask(_task_event_slots.release, client_token),
    )

