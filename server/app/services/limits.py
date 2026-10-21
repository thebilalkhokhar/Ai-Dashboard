"""Rate limiting and connection caps that survive more than one worker.

Both limits live in Redis so every worker sees the same number:

  * `RateLimiter` - a sliding window (a sorted set of timestamps trimmed to the
    window), used for POST /api/chat;
  * `SlotCounter` - a capacity counter of live tokens, used for the SSE client
    cap on GET /api/live. Entries expire on their own after a TTL, so a worker
    that dies without releasing cannot leak the slot forever.

When Redis cannot be reached, each falls back to process-local memory and logs
one warning: the limit still applies per worker, it is just no longer shared.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from typing import Optional

from app.services.redis_client import get_redis

log = logging.getLogger("limits")


class RedisLimits:
    """Shared warning-once behaviour for both limit kinds."""

    # after a Redis failure, try Redis again every this many seconds
    RETRY_AFTER_SECONDS = 30.0

    def __init__(self, label: str, redis_factory=None) -> None:
        self.label = label
        self._warned = False
        self._retry_after = 0.0
        # injectable so tests never touch a real Redis
        self.redis_factory = redis_factory or get_redis

    def _redis(self):
        return self.redis_factory()

    def redis_usable(self) -> bool:
        return time.time() >= self._retry_after

    def mark_redis_down(self, exc: Exception) -> None:
        self._retry_after = time.time() + self.RETRY_AFTER_SECONDS
        if self._warned:
            log.debug("%s: Redis still unavailable (%s)", self.label, exc)
            return
        self._warned = True
        log.warning(
            "%s: Redis is unavailable (%s) - the limit is now per worker only, "
            "which under-counts when the API runs with several workers",
            self.label,
            exc,
        )


class RateLimiter(RedisLimits):
    """At most `limit` calls per `window_seconds`, shared through Redis."""

    def __init__(self, key: str, limit: int, window_seconds: int, redis_factory=None) -> None:
        super().__init__(f"rate limit {key}", redis_factory)
        self.key = key
        self.limit = int(limit)
        self.window_seconds = int(window_seconds)
        self._local: deque[float] = deque()

    async def allow(self) -> bool:
        if self.redis_usable():
            try:
                return await self._redis_allow()
            except Exception as exc:
                self.mark_redis_down(exc)
        return self._local_allow()

    async def _redis_allow(self) -> bool:
        now = time.time()
        client = self._redis()
        member = f"{now}-{uuid.uuid4().hex}"
        pipe = client.pipeline()
        pipe.zremrangebyscore(self.key, 0, now - self.window_seconds)
        pipe.zadd(self.key, {member: now})
        pipe.zcard(self.key)
        pipe.expire(self.key, self.window_seconds + 1)
        _, _, count, _ = await pipe.execute()
        if int(count) > self.limit:
            await client.zrem(self.key, member)
            return False
        return True

    def _local_allow(self) -> bool:
        now = time.monotonic()
        while self._local and now - self._local[0] >= self.window_seconds:
            self._local.popleft()
        if len(self._local) >= self.limit:
            return False
        self._local.append(now)
        return True

    def reset(self) -> None:
        """Forget everything (tests and process restarts)."""
        self._local.clear()
        self._warned = False
        self._retry_after = 0.0


class SlotCounter(RedisLimits):
    """At most `capacity` live tokens, released explicitly or by TTL."""

    def __init__(self, key: str, capacity: int, slot_ttl_seconds: int, redis_factory=None) -> None:
        super().__init__(f"slot counter {key}", redis_factory)
        self.key = key
        self.capacity = int(capacity)
        self.slot_ttl_seconds = int(slot_ttl_seconds)
        self._local: set[str] = set()

    async def acquire(self) -> Optional[str]:
        token = uuid.uuid4().hex
        if self.redis_usable():
            try:
                return await self._redis_acquire(token)
            except Exception as exc:
                self.mark_redis_down(exc)
        return self._local_acquire(token)

    async def _redis_acquire(self, token: str) -> Optional[str]:
        now = time.time()
        client = self._redis()
        await client.zremrangebyscore(self.key, 0, now - self.slot_ttl_seconds)
        await client.zadd(self.key, {token: now})
        used = int(await client.zcard(self.key))
        await client.expire(self.key, self.slot_ttl_seconds + 1)
        if used > self.capacity:
            await client.zrem(self.key, token)
            return None
        return token

    def _local_acquire(self, token: str) -> Optional[str]:
        if len(self._local) >= self.capacity:
            return None
        self._local.add(token)
        return token

    async def release(self, token: str) -> None:
        self._local.discard(token)
        if not self.redis_usable():
            return
        try:
            await self._redis().zrem(self.key, token)
        except Exception as exc:
            self.mark_redis_down(exc)

    def reset(self) -> None:
        self._local.clear()
        self._warned = False
        self._retry_after = 0.0
