"""ERP load cuts (shared token, 5-minute poll) and the daily file logger.

The ERP team counted our requests: every ERPClient used to mint its own
access token, so one 10-minute cycle plus an agent run plus a page view
could mean three token calls minutes apart for a token that lives for
hours. These tests pin the shared-token behaviour and the logger shape.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.services import erp_client
from app.services.erp_client import ERPClient


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        return True


class BrokenRedis:
    async def get(self, key):
        raise ConnectionError("redis is down")

    async def set(self, key, value, ex=None):
        raise ConnectionError("redis is down")


class FakeResponse:
    def __init__(self, body):
        self.status_code = 200
        self._body = body

    def json(self):
        return self._body


class CountingHttp:
    """Stands in for httpx: every POST mints the next token, and counts."""

    def __init__(self) -> None:
        self.posts = 0

    async def post(self, url, json=None, headers=None):
        self.posts += 1
        expires = datetime.now(timezone.utc) + timedelta(hours=4)
        return FakeResponse(
            {
                "status": "success",
                "token": f"tok-{self.posts}",
                "expires_at": expires.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )


def make_client(http: CountingHttp) -> ERPClient:
    client = ERPClient(refresh_token="test-refresh-token")

    async def fake_get_http():
        return http

    client._get_http = fake_get_http  # type: ignore[method-assign]
    return client


@pytest.mark.asyncio
async def test_two_clients_share_one_minted_token(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(erp_client, "get_redis", lambda: redis)
    http = CountingHttp()

    first = await make_client(http).get_access_token()
    second = await make_client(http).get_access_token()

    assert first == "tok-1"
    assert second == "tok-1"  # reused from Redis, not minted again
    assert http.posts == 1


@pytest.mark.asyncio
async def test_a_forced_refresh_replaces_the_shared_token(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(erp_client, "get_redis", lambda: redis)
    http = CountingHttp()

    await make_client(http).get_access_token()
    forced = await make_client(http).get_access_token(force=True)
    after = await make_client(http).get_access_token()

    assert forced == "tok-2"
    assert after == "tok-2"  # the shared copy now holds the new token
    assert http.posts == 2


@pytest.mark.asyncio
async def test_redis_down_still_mints_per_client(monkeypatch):
    monkeypatch.setattr(erp_client, "get_redis", lambda: BrokenRedis())
    http = CountingHttp()

    token = await make_client(http).get_access_token()

    assert token == "tok-1"
    assert http.posts == 1


def test_refresh_interval_is_five_minutes():
    from app.config import Settings

    assert Settings.model_fields["REFRESH_MINUTES"].default == 5


def _remove_marked_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_biztek_daily_file", False):
            root.removeHandler(handler)
            handler.close()


def test_daily_file_logger_writes_and_filters_health(tmp_path):
    from app.logging_setup import _DropHealthAccess, setup_logging

    _remove_marked_handlers()
    try:
        handler = setup_logging(tmp_path)
        assert handler is not None
        # idempotent: a second call returns the same single handler
        assert setup_logging(tmp_path) is handler

        logging.getLogger("test.daily").info("payments refreshed cleanly")
        handler.flush()
        text = (tmp_path / "server.log").read_text(encoding="utf-8")
        assert "payments refreshed cleanly" in text
        assert "test.daily" in text

        health = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1,
            '127.0.0.1 - "GET /health HTTP/1.1" 200', (), None,
        )
        normal = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1,
            '127.0.0.1 - "GET /api/metrics HTTP/1.1" 200', (), None,
        )
        assert _DropHealthAccess().filter(health) is False
        assert _DropHealthAccess().filter(normal) is True

        dated = handler.namer(str(tmp_path / "server.log") + ".2026-10-23")
        assert dated.endswith("server-2026-10-23.log")
    finally:
        _remove_marked_handlers()
