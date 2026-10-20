"""One shared async Redis client for the whole process.

Redis is only used for the live dashboard: the current snapshot, the update
channel, the scheduler lock. If Redis is down the API must keep working, so
every caller wraps its Redis calls in its own try/except.

`decode_responses=True` means values come back as `str`, not bytes.
"""

from __future__ import annotations

from typing import Optional

import redis.asyncio as redis_asyncio

from app.config import settings

_client: Optional[redis_asyncio.Redis] = None

# Empty REDIS_URL means "the local Redis", which is how dev runs.
DEFAULT_REDIS_URL = "redis://localhost:6379"


def get_redis() -> redis_asyncio.Redis:
    """Return the shared client, opening it on first use."""
    global _client
    if _client is None:
        _client = redis_asyncio.from_url(
            settings.REDIS_URL or DEFAULT_REDIS_URL, decode_responses=True
        )
    return _client


async def ping() -> bool:
    """True when Redis answers (used by /health style checks)."""
    try:
        return bool(await get_redis().ping())
    except Exception:
        return False


async def close_redis() -> None:
    """Close the shared client (called on shutdown)."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None
