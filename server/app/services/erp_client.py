"""Async client for the Print247 ERP (read-only endpoints only).

Rules this client follows:
  * Read endpoints only. The write endpoints (POST /api/exhibition-quotes,
    POST /api/live-chat/incoming) are never called from anywhere in this app.
  * The refresh token comes from settings (server/.env) and is never logged.
  * The access token is shared by every client through Redis (one token
    serves the refresh cycle, the agent runs and the API alike), refreshed
    ~2 minutes before it expires, and once more after any 401 (retry that
    request a single time). The ERP counts token requests, so minting a
    fresh token per client was real, needless load on their server.
  * 5xx answers and network/timeout errors are retried with backoff.
  * A small delay sits between calls so we never hammer the ERP.

Time zone: the ERP uses US Central (-05:00) everywhere, including `expires_at`
which is a naive string like "2026-10-19 16:59:52".
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.config import settings
from app.services.redis_client import get_redis

log = logging.getLogger("erp_client")

# US Central as the ERP reports it (fixed -05:00).
ERP_TZ = timezone(timedelta(hours=-5))

MAX_RETRIES = 4          # attempts for 5xx / network errors
BASE_BACKOFF = 2.0       # seconds; doubled after each failed attempt
THROTTLE = float(settings.ERP_REQUEST_DELAY_SECONDS)
PAGE_LIMIT = int(settings.ERP_PAGE_LIMIT)


class ERPError(RuntimeError):
    """The ERP could not give us usable data."""


class ERPClient:
    """Small async wrapper around the ERP read endpoints."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        refresh_token: Optional[str] = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = (base_url or settings.ERP_BASE_URL).rstrip("/")
        self._refresh_token = (
            settings.ERP_REFRESH_TOKEN if refresh_token is None else refresh_token
        )
        self.timeout = timeout
        self._http: Optional[httpx.AsyncClient] = None
        self._access_token: Optional[str] = None
        self._expires_at: Optional[datetime] = None  # aware UTC
        self._next_free_at = 0.0  # monotonic clock guard between calls

    # ------------------------------------------------------------- plumbing

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.timeout)
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> "ERPClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    async def _throttle(self) -> None:
        """Sleep so that two ERP calls are a few hundred ms apart."""
        wait = self._next_free_at - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._next_free_at = time.monotonic() + THROTTLE

    def _require_refresh_token(self) -> None:
        if not self._refresh_token:
            raise ERPError("ERP_REFRESH_TOKEN is not set. Put it in server/.env")

    # ---------------------------------------------------------------- token

    _SHARED_TOKEN_KEY = "erp:access_token"

    async def _read_shared_token(self) -> bool:
        """Adopt the token another client already minted, if still fresh.

        The shared copy lives in Redis so every ERPClient in the process
        (refresh cycle, agent run, API request) reuses ONE token instead
        of each minting its own. Redis being down just means minting -
        the token endpoint stays the fallback, never a hard dependency.
        """
        try:
            raw = await get_redis().get(self._SHARED_TOKEN_KEY)
            if not raw:
                return False
            data = json.loads(raw)
            expires = datetime.fromisoformat(str(data["expires_at"]))
            if expires.tzinfo is None:
                return False
            if datetime.now(timezone.utc) < expires - timedelta(minutes=2):
                self._access_token = str(data["token"])
                self._expires_at = expires
                return True
        except Exception:
            return False
        return False

    async def _write_shared_token(self) -> None:
        """Publish the just-minted token for every other client to reuse."""
        if not self._access_token or not self._expires_at:
            return
        try:
            ttl = int((self._expires_at - datetime.now(timezone.utc)).total_seconds())
            if ttl > 60:
                await get_redis().set(
                    self._SHARED_TOKEN_KEY,
                    json.dumps(
                        {
                            "token": self._access_token,
                            "expires_at": self._expires_at.isoformat(),
                        }
                    ),
                    ex=ttl,
                )
        except Exception:
            pass

    async def get_access_token(self, force: bool = False) -> str:
        """Trade the refresh token for a short-lived access token (cached)."""
        self._require_refresh_token()
        now = datetime.now(timezone.utc)
        if (
            not force
            and self._access_token
            and self._expires_at
            and now < self._expires_at - timedelta(minutes=2)
        ):
            return self._access_token

        if not force and await self._read_shared_token():
            return self._access_token

        await self._throttle()
        current = await self._get_http()
        resp = await current.post(
            f"{self.base_url}/api/get-access-token",
            json={"refresh_token": self._refresh_token},
            headers={"Authorization": f"Bearer {self._refresh_token}"},
        )
        if resp.status_code == 401:
            raise ERPError("ERP rejected the refresh token (401). Check ERP_REFRESH_TOKEN in server/.env")
        if resp.status_code >= 400:
            raise ERPError(f"get-access-token -> HTTP {resp.status_code}")

        body = resp.json()
        if body.get("status") != "success" or not body.get("token"):
            raise ERPError(f"Could not get an access token: {body.get('message', 'no message')}")

        self._access_token = body["token"]
        try:
            expires = datetime.fromisoformat(str(body["expires_at"]).strip())
        except ValueError:
            expires = datetime.now(timezone.utc) + timedelta(minutes=30)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=ERP_TZ)  # the ERP answer is US Central
        self._expires_at = expires.astimezone(timezone.utc)
        log.info("ERP access token refreshed, expires %s (US Central %s)", self._expires_at.isoformat(), body["expires_at"])
        await self._write_shared_token()
        return self._access_token

    # -------------------------------------------------------------- requests

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        retried_after_401: bool = False,
    ) -> Any:
        """One ERP call with retry/backoff. Returns the parsed JSON body."""
        token = await self.get_access_token()
        backoff = BASE_BACKOFF
        last_error: Any = None

        for attempt in range(1, MAX_RETRIES + 1):
            await self._throttle()
            http = await self._get_http()
            try:
                resp = await http.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    json=json_body,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"network error: {type(exc).__name__}"
                log.warning("ERP %s %s failed (%s), attempt %d/%d", method, path, last_error, attempt, MAX_RETRIES)
                await asyncio.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 401 and not retried_after_401:
                # Token died early: refresh once and send this request again.
                log.info("ERP 401 on %s - refreshing the access token once", path)
                token = await self.get_access_token(force=True)
                return await self._request(method, path, params, json_body, retried_after_401=True)

            if resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
                log.warning("ERP %s %s -> %s, attempt %d/%d", method, path, last_error, attempt, MAX_RETRIES)
                await asyncio.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code >= 400:
                raise ERPError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:200]!r}")

            return resp.json()

        raise ERPError(f"{method} {path} failed after {MAX_RETRIES} attempts ({last_error})")

    # ------------------------------------------------------------- endpoints

    async def get_users(self) -> list[dict]:
        """All ERP users (sales reps + ecommerce staff)."""
        body = await self._request("GET", "/api/get-users")
        return body.get("data", []) or []

    async def _paged(
        self, path: str, date_from: str, date_to: str, rep_id: Optional[str] = None
    ):
        """Page through /api/dashboard/quotes|orders until all `total` are in.
        Returns (rows, total) - `total` is the ERP's own count for the window."""
        rows: list[dict] = []
        skip = 0
        total: Optional[int] = None
        while True:
            params: dict[str, Any] = {
                "date_from": date_from,
                "date_to": date_to,
                "skip": skip,
                "limit": PAGE_LIMIT,
            }
            if rep_id:
                params["rep_id"] = rep_id
            body = await self._request("GET", path, params=params)
            if total is None:
                total = int(body.get("total") or 0)
            data = body.get("data") or []
            rows.extend(data)
            skip += len(data)
            if not data or (total is not None and len(rows) >= total):
                break
        if total is not None and len(rows) != total:
            log.warning("ERP %s %s..%s: fetched %d rows but total says %d", path, date_from, date_to, len(rows), total)
        return rows, (total or 0)

    async def get_quotes(
        self, date_from: str, date_to: str, rep_id: Optional[str] = None
    ) -> list[dict]:
        """Every quote created between date_from and date_to (inclusive)."""
        rows, _total = await self._paged("/api/dashboard/quotes", date_from, date_to, rep_id)
        return rows

    async def get_quotes_with_total(
        self, date_from: str, date_to: str, rep_id: Optional[str] = None
    ):
        """Quotes for a window, plus the ERP's `total` for that window."""
        return await self._paged("/api/dashboard/quotes", date_from, date_to, rep_id)

    async def get_orders(
        self, date_from: str, date_to: str, rep_id: Optional[str] = None
    ) -> list[dict]:
        """Every order/invoice created between date_from and date_to (inclusive)."""
        rows, _total = await self._paged("/api/dashboard/orders", date_from, date_to, rep_id)
        return rows

    async def get_orders_with_total(
        self, date_from: str, date_to: str, rep_id: Optional[str] = None
    ):
        """Orders for a window, plus the ERP's `total` for that window."""
        return await self._paged("/api/dashboard/orders", date_from, date_to, rep_id)

    async def get_payments(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        rep_id: Optional[str] = None,
        customer_id: Optional[str] = None,
    ) -> dict:
        """Payments window. No paging is documented, so the caller keeps the
        window small and checks count/total/sum against len(data)."""
        params: dict[str, Any] = {}
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        if rep_id:
            params["rep_id"] = rep_id
        if customer_id:
            params["customer_id"] = customer_id
        return await self._request("GET", "/api/get-payments", params=params)

    async def lead_outcomes(self, leads: list[dict]) -> list[dict]:
        """Read-only lookup: lead -> matched customer + quote/order/payment
        summaries. One batch per call (the ERP takes 1-3s per batch)."""
        if not leads:
            return []
        body = await self._request("POST", "/api/dashboard/lead-outcomes", json_body={"leads": leads})
        return body.get("data", []) or []
