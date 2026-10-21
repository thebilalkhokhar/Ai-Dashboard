"""Only one agent run at a time, across every worker and every restart.

A run is guarded by two things:

  * a Redis lock (`agents:run:lock`, value = run_id, with a TTL so a crashed
    process cannot hold it forever);
  * a `status: "running"` document in `agent_runs`, which is also the fallback
    when Redis is down or not configured.

`acquire_run` returns None when the caller may start its run, otherwise the
run_id already in progress - the endpoint answers 409 with it. The background
task must always call `release_run`, and any `running` document left behind by
a killed process is marked abandoned at startup.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import settings
from app.services.redis_client import get_redis

log = logging.getLogger("run_guard")

LOCK_KEY = "agents:run:lock"
RUNNING = "running"


def ttl_seconds() -> int:
    return int(settings.AGENT_RUN_LOCK_TTL_SECONDS)


def running_document(run_id: str) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "run_id": run_id,
        "status": RUNNING,
        "started_at": now.isoformat(),
        "started_epoch": now.timestamp(),
    }


async def _try_redis_lock(run_id: str) -> Optional[bool]:
    """True when the lock was taken, False when someone holds it, None when
    Redis did not answer (the caller then falls back to Mongo)."""
    try:
        client = get_redis()
        return bool(await client.set(LOCK_KEY, run_id, nx=True, ex=ttl_seconds()))
    except Exception as exc:
        log.warning("Redis lock unavailable (%s): falling back to Mongo", exc)
        return None


async def _redis_owner() -> Optional[str]:
    try:
        return await get_redis().get(LOCK_KEY)
    except Exception:
        return None


async def _running_in_mongo(database: Any) -> Optional[str]:
    """The freshest non-stale 'running' document, if there is one."""
    cutoff = datetime.now(timezone.utc).timestamp() - ttl_seconds()
    query = {"status": RUNNING, "started_epoch": {"$gt": cutoff}}
    try:
        rows = await database.agent_runs.find(query).sort("started_epoch", -1).to_list(1)
    except AttributeError:
        rows = [row for row in await database.agent_runs.find(query).to_list(10)]
    except Exception as exc:
        log.warning("could not read the running document: %s", exc)
        return None
    return str(rows[0]["run_id"]) if rows else None


async def acquire_run(run_id: str, database: Any) -> Optional[str]:
    """None when this run_id may start; otherwise the run_id already running."""
    taken = await _try_redis_lock(run_id)
    if taken is False:
        return await _redis_owner() or await _running_in_mongo(database) or run_id
    if taken is None:
        existing = await _running_in_mongo(database)
        if existing:
            return existing
    await database.agent_runs.insert_one(running_document(run_id))
    return None


async def release_run(run_id: str, database: Any) -> None:
    """Drop the guard so the next run may start."""
    try:
        await database.agent_runs.delete_many({"run_id": run_id, "status": RUNNING})
    except Exception as exc:
        log.warning("could not delete the running document for %s: %s", run_id, exc)
    try:
        client = get_redis()
        if await client.get(LOCK_KEY) == run_id:
            await client.delete(LOCK_KEY)
    except Exception as exc:
        # the TTL releases it eventually
        log.warning("could not release the Redis lock: %s", exc)


async def mark_stale_runs_abandoned(database: Any) -> int:
    """Clean up 'running' documents left behind by a process that died."""
    try:
        result = await database.agent_runs.update_many(
            {"status": RUNNING},
            {
                "$set": {
                    "status": "abandoned",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "errors": ["this run was still marked running when the API started"],
                }
            },
        )
        count = int(getattr(result, "modified_count", 0) or 0)
    except Exception as exc:
        log.warning("could not clean up stale agent runs: %s", exc)
        return 0
    if count:
        log.warning("marked %d stale 'running' agent run(s) abandoned at startup", count)
        try:
            await get_redis().delete(LOCK_KEY)
        except Exception:
            pass
    return count
