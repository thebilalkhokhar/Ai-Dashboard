"""MongoDB access layer.

One shared async client for the whole process (pymongo's AsyncMongoClient).
Collections used by the app:
    leads         - flat lead documents (already in the database)
    quotes_flat   - one document per ERP quote   (_id = quote id)
    orders_flat   - one document per ERP order   (_id = order id)
    payments_flat - one document per ERP payment (_id = payment id)
    users_flat    - one document per ERP rep    (_id = rep id)
"""

from __future__ import annotations

from typing import Optional

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.config import settings

_client: Optional[AsyncMongoClient] = None


def get_client() -> AsyncMongoClient:
    """Return the shared client, opening it on first use."""
    global _client
    if _client is None:
        # tz_aware=True -> Mongo datetimes come back as timezone-aware UTC values
        _client = AsyncMongoClient(settings.MONGO_URI, tz_aware=True)
    return _client


def get_db() -> AsyncDatabase:
    """Return the configured database (AI by default)."""
    return get_client()[settings.MONGO_DB]


async def ping() -> bool:
    """True when MongoDB answers."""
    try:
        await get_client().admin.command("ping")
        return True
    except Exception:
        return False


async def close() -> None:
    """Close the shared client (called on shutdown)."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
