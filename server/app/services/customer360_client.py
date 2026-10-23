"""Async client for the Customer 360 backend (the Zoho-synced CRM).

Rules this client follows:
  * Read endpoints only. The class deliberately has NO methods for the
    write endpoints (POST /customer-360/trigger-call places a real phone
    call; POST /customer-360/save-draft writes into a rep's mailbox) and
    none for /customer-360/analysis (their LLM output bypasses this app's
    numeric guard, so it never enters a chat answer).
  * Auth is a real user's email + password (settings, server/.env only)
    traded for a 7-day JWT at POST /auth/login - the API has no key flow.
    The token is shared through Redis exactly like the ERP token (one
    login serves every client), and re-minted once after any 401.
  * The password and the token are never logged.
  * Their serverless backend is slow when cold (a warm detail answer
    takes ~8s; a cold one can pass a minute), so the read timeout is
    long and timeouts are NOT retried - a retry would triple the wait.
    Callers cache detail payloads (see customer360.py) instead.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.config import settings
from app.services.redis_client import get_redis

log = logging.getLogger("customer360_client")

MAX_5XX_RETRIES = 2        # extra attempts for 5xx answers only
BASE_BACKOFF = 2.0         # seconds; doubled after each failed attempt
TOKEN_MARGIN = timedelta(hours=1)   # stop trusting a token this early
FALLBACK_TOKEN_LIFE = timedelta(days=6)   # when the JWT exp cannot be read


class Customer360Error(RuntimeError):
    """Customer 360 could not give us usable data."""


def _jwt_expiry(token: str) -> Optional[datetime]:
    """The exp claim of a JWT, read without verifying the signature.

    We only need to know when to stop reusing the token; the server does
    the real verification on every call. Returns None when undecodable.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode()))
        exp = claims.get("exp")
        if isinstance(exp, (int, float)):
            return datetime.fromtimestamp(exp, tz=timezone.utc)
    except Exception:
        pass
    return None


class Customer360Client:
    """Small async wrapper around the Customer 360 read endpoints."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        email: Optional[str] = None,
        password: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.base_url = (base_url or settings.C360_BASE_URL).rstrip("/")
        self._email = settings.C360_EMAIL if email is None else email
        self._password = settings.C360_PASSWORD if password is None else password
        self.timeout = float(settings.C360_TIMEOUT_SECONDS) if timeout is None else timeout
        self._http: Optional[httpx.AsyncClient] = None
        self._token: Optional[str] = None
        self._expires_at: Optional[datetime] = None  # aware UTC

    # ------------------------------------------------------------- plumbing

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.timeout)
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> "Customer360Client":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    def _require_credentials(self) -> None:
        if not self._email or not self._password:
            raise Customer360Error(
                "C360_EMAIL / C360_PASSWORD are not set. Put them in server/.env"
            )

    # ---------------------------------------------------------------- token

    _SHARED_TOKEN_KEY = "c360:access_token"

    async def _read_shared_token(self) -> bool:
        """Adopt the token another client already minted, if still fresh.
        Redis being down just means logging in again - never a hard stop."""
        try:
            raw = await get_redis().get(self._SHARED_TOKEN_KEY)
            if not raw:
                return False
            data = json.loads(raw)
            expires = datetime.fromisoformat(str(data["expires_at"]))
            if expires.tzinfo is None:
                return False
            if datetime.now(timezone.utc) < expires - TOKEN_MARGIN:
                self._token = str(data["token"])
                self._expires_at = expires
                return True
        except Exception:
            return False
        return False

    async def _write_shared_token(self) -> None:
        """Publish the just-minted token for every other client to reuse."""
        if not self._token or not self._expires_at:
            return
        try:
            ttl = int((self._expires_at - datetime.now(timezone.utc)).total_seconds())
            if ttl > 60:
                await get_redis().set(
                    self._SHARED_TOKEN_KEY,
                    json.dumps(
                        {"token": self._token, "expires_at": self._expires_at.isoformat()}
                    ),
                    ex=ttl,
                )
        except Exception:
            pass

    async def get_token(self, force: bool = False) -> str:
        """Log in (or reuse a still-fresh token) and return the JWT."""
        self._require_credentials()
        now = datetime.now(timezone.utc)
        if (
            not force
            and self._token
            and self._expires_at
            and now < self._expires_at - TOKEN_MARGIN
        ):
            return self._token

        if not force and await self._read_shared_token():
            return self._token

        http = await self._get_http()
        try:
            resp = await http.post(
                f"{self.base_url}/auth/login",
                json={"email": self._email, "password": self._password},
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise Customer360Error(
                f"Customer 360 login failed: network error: {type(exc).__name__}"
            ) from exc
        if resp.status_code == 401:
            raise Customer360Error(
                "Customer 360 rejected the login (401). Check C360_EMAIL / C360_PASSWORD in server/.env"
            )
        if resp.status_code >= 400:
            raise Customer360Error(f"Customer 360 login -> HTTP {resp.status_code}")

        body = resp.json()
        token = body.get("token")
        if not body.get("success") or not token:
            raise Customer360Error("Customer 360 login did not return a token")

        self._token = str(token)
        self._expires_at = _jwt_expiry(self._token) or (
            datetime.now(timezone.utc) + FALLBACK_TOKEN_LIFE
        )
        log.info("Customer 360 token minted, expires %s", self._expires_at.isoformat())
        await self._write_shared_token()
        return self._token

    # -------------------------------------------------------------- requests

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        retried_after_401: bool = False,
    ) -> Any:
        """One Customer 360 call. Returns the parsed JSON body.

        5xx answers get two extra attempts with backoff. Timeouts and
        network errors raise at once: their cold starts are slow enough
        that a silent retry would hold a chat turn hostage for minutes -
        the caller decides (cache / stale copy / honest failure).
        """
        token = await self.get_token()
        backoff = BASE_BACKOFF

        for attempt in range(1, MAX_5XX_RETRIES + 2):
            http = await self._get_http()
            try:
                resp = await http.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise Customer360Error(
                    f"Customer 360 {method} {path} failed: network error: {type(exc).__name__}"
                ) from exc

            if resp.status_code == 401 and not retried_after_401:
                log.info("Customer 360 401 on %s - logging in again once", path)
                token = await self.get_token(force=True)
                return await self._request(method, path, params, retried_after_401=True)

            if resp.status_code >= 500:
                log.warning(
                    "Customer 360 %s %s -> HTTP %d, attempt %d/%d",
                    method, path, resp.status_code, attempt, MAX_5XX_RETRIES + 1,
                )
                if attempt <= MAX_5XX_RETRIES:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue

            if resp.status_code >= 400:
                raise Customer360Error(
                    f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:200]!r}"
                )

            return resp.json()

        raise Customer360Error(f"{method} {path} failed after retries (HTTP 5xx)")

    # ------------------------------------------------------------- endpoints

    async def search(self, query: str) -> list[dict]:
        """Search Leads, Contacts and Customers by name/email/company/phone.
        The API caps results at 10 per type (30 total)."""
        body = await self._request("GET", "/customer-360/search", params={"q": query})
        return body.get("results", []) or []

    async def detail(self, record_id: str, record_type: str) -> dict:
        """The full 360 aggregation for one record: related entities,
        timeline, projects, invoices, estimates and payments."""
        body = await self._request(
            "GET",
            "/customer-360/detail",
            params={"id": record_id, "type": record_type},
        )
        if not body.get("success"):
            raise Customer360Error("Customer 360 detail did not return success")
        return body
