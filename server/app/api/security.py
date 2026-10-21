"""API key auth for every /api route (X-API-Key header).

Keys come from the `API_KEYS` env var (comma-separated). Comparison is
constant-time (`hmac.compare_digest`) and a bad or missing key is a plain 401 -
the API never says which part was wrong.

`AUTH_ENABLED=false` exists for local development only, and says so loudly in
the log at startup. `/health` has no dependency on this module: it stays open
so a load balancer can probe it.

The Next.js client never holds a key: it calls same-origin `/backend/...` route
handlers that add this header server-side (see client/app/backend).
"""

from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import Header, HTTPException

from app.config import settings

log = logging.getLogger("security")

UNAUTHORIZED_DETAIL = "missing or invalid X-API-Key"


def api_keys() -> list[bytes]:
    """The configured keys as bytes (empty entries ignored)."""
    return [key.strip().encode() for key in settings.API_KEYS.split(",") if key.strip()]


def key_matches(supplied: str, allowed: list[bytes]) -> bool:
    """Constant-time comparison against every configured key."""
    candidate = supplied.encode()
    # every key is compared: bailing out early would leak which key was right
    return any(hmac.compare_digest(candidate, key) for key in allowed)


async def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """FastAPI dependency used by every /api route."""
    if not settings.AUTH_ENABLED:
        return
    if not x_api_key or not key_matches(x_api_key.strip(), api_keys()):
        raise HTTPException(status_code=401, detail=UNAUTHORIZED_DETAIL)


def warn_if_auth_disabled() -> None:
    """Shout once at startup when the API is wide open."""
    if settings.AUTH_ENABLED:
        if not api_keys():
            log.warning(
                "=" * 72
                + "\n  AUTH_ENABLED=true but API_KEYS is empty: every /api call will "
                "get 401. Set API_KEYS=key-you-generated (comma-separated).\n"
                + "=" * 72
            )
        return
    log.warning(
        "=" * 72
        + "\n  *** AUTH_ENABLED=false - THE API IS UNPROTECTED (local development "
        "only). Set AUTH_ENABLED=true and API_KEYS before exposing this server. ***\n"
        + "=" * 72
    )
