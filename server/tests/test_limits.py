"""Tests for the Redis-backed chat rate limit and SSE client cap (item 8).

Both limits must be shared across workers through Redis, and must keep working
(with a warning) when Redis is unreachable.
"""

from __future__ import annotations

import pytest

from app.services.limits import RateLimiter, SlotCounter


class SharedRedis:
    """Sorted-set Redis good enough for the two limit classes."""

    def __init__(self):
        self.zsets: dict[str, dict[str, float]] = {}
        self.expirations: dict[str, int] = {}

    async def zadd(self, key, mapping):
        members = self.zsets.setdefault(key, {})
        for member, score in mapping.items():
            members[member] = float(score)
        return len(mapping)

    async def zrem(self, key, member):
        return 1 if self.zsets.get(key, {}).pop(member, None) is not None else 0

    async def zcard(self, key):
        return len(self.zsets.get(key, {}))

    async def zremrangebyscore(self, key, low, high):
        members = self.zsets.setdefault(key, {})
        doomed = [member for member, score in members.items() if low <= score <= high]
        for member in doomed:
            members.pop(member)
        return len(doomed)

    async def expire(self, key, seconds):
        self.expirations[key] = seconds
        return True

    def pipeline(self):
        return SharedPipeline(self)


class SharedPipeline:
    def __init__(self, redis):
        self.redis = redis
        self._stack = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        method = getattr(self.redis, name)

        def queued(*args, **kwargs):
            self._stack.append(lambda: method(*args, **kwargs))
            return self

        return queued

    async def execute(self):
        results = [await call() for call in self._stack]
        self._stack.clear()
        return results


class DeadRedis:
    async def zadd(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")

    async def zrem(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")

    async def zcard(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")

    async def zremrangebyscore(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")

    async def expire(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")

    def pipeline(self):
        raise ConnectionError("redis is down")


# ------------------------------------------------------------------ rate limit


@pytest.mark.asyncio
async def test_rate_limit_is_shared_between_workers():
    """Two 'workers' on one Redis hit the same 3-request window."""
    shared = SharedRedis()
    worker_a = RateLimiter("chat:rate-limit", 3, 60, redis_factory=lambda: shared)
    worker_b = RateLimiter("chat:rate-limit", 3, 60, redis_factory=lambda: shared)

    assert await worker_a.allow()
    assert await worker_b.allow()
    assert await worker_a.allow()
    assert not await worker_b.allow()   # the window is already full
    assert shared.zsets["chat:rate-limit"], "the window lives in Redis"
    assert shared.expirations["chat:rate-limit"] == 61  # the key expires itself


@pytest.mark.asyncio
async def test_rate_limit_falls_back_to_memory_and_warns_once(monkeypatch, caplog):
    limiter = RateLimiter("chat:rate-limit", 2, 60, redis_factory=lambda: DeadRedis())

    assert await limiter.allow()
    assert await limiter.allow()
    assert not await limiter.allow()
    assert "per worker only" in caplog.text
    assert caplog.text.count("per worker only") == 1  # warned once, not per call


@pytest.mark.asyncio
async def test_rate_limit_retries_redis_after_a_failure():
    redis = SharedRedis()
    state = {"broken": True}

    def factory():
        if state["broken"]:
            raise ConnectionError("redis is down")
        return redis

    limiter = RateLimiter("chat:rate-limit", 5, 60, redis_factory=factory)
    assert await limiter.allow()  # memory fallback
    assert limiter.redis_usable() is False

    state["broken"] = False
    limiter._retry_after = 0.0  # pretend the retry window has passed
    assert await limiter.allow()
    assert limiter.redis_usable() is True
    assert "chat:rate-limit" in redis.zsets  # shared state is back


# ------------------------------------------------------------------ SSE slots


@pytest.mark.asyncio
async def test_sse_slots_are_counted_across_workers():
    shared = SharedRedis()
    worker_a = SlotCounter("live:clients", 2, 3600, redis_factory=lambda: shared)
    worker_b = SlotCounter("live:clients", 2, 3600, redis_factory=lambda: shared)

    token_a = await worker_a.acquire()
    token_b = await worker_b.acquire()
    assert token_a and token_b
    assert await worker_a.acquire() is None       # capacity is full, shared
    await worker_b.release(token_b)
    assert await worker_a.acquire() is not None   # releasing really freed it
    assert shared.zsets["live:clients"]


@pytest.mark.asyncio
async def test_expired_slots_are_dropped(monkeypatch):
    """A worker that died without releasing cannot hold the cap forever."""
    import time as time_module

    shared = SharedRedis()
    counter = SlotCounter("live:clients", 1, 100, redis_factory=lambda: shared)
    first = await counter.acquire()
    assert first

    # 200 seconds later the abandoned token is older than the slot TTL
    frozen = time_module.time() + 200
    monkeypatch.setattr(time_module, "time", lambda: frozen)
    second = await counter.acquire()
    assert second is not None
    assert first not in shared.zsets["live:clients"]


@pytest.mark.asyncio
async def test_sse_counter_falls_back_to_memory(monkeypatch, caplog):
    counter = SlotCounter("live:clients", 1, 60, redis_factory=lambda: DeadRedis())
    assert await counter.acquire()
    assert await counter.acquire() is None
    assert "per worker only" in caplog.text
