"""Copy the 100-record wrapper samples into the *_flat collections.

The ERP refreshes `quotes` / `orders` / `payments` / `users` wrapper documents
({status, total, data:[...]}) in this database hold one small sample each.
scripts/ingest_erp.py is the real ingestion and needs the ERP refresh token;
this script is the no-token fallback that flattens those samples so the API
can return sensible numbers while the token is still missing.

The original wrapper collections are only read, never changed.

Run:   .venv/bin/python -m scripts.flatten
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from pymongo import ReplaceOne  # noqa: E402

WRAPPER_TO_FLAT = {
    "quotes": "quotes_flat",
    "orders": "orders_flat",
    "payments": "payments_flat",
    "users": "users_flat",
}


def unpack(collection) -> list[dict]:
    """Kept for clarity: every record inside one wrapper document."""
    return collection.get("data") or []


async def flatten_async() -> None:
    for source, target in WRAPPER_TO_FLAT.items():
        records: list[dict] = []
        async for wrapper in db.get_db()[source].find({}):
            records.extend(wrapper.get("data") or [])
        if not records:
            print(f"{source}: no wrapper data found, skipping")
            continue
        operations = [
            ReplaceOne({"_id": record["id"]}, {**record, "_id": record["id"]}, upsert=True)
            for record in records
        ]
        result = await db.get_db()[target].bulk_write(operations, ordered=False)
        print(f"{source} -> {target}: {len(records)} records (upserted={result.upserted_count})")


def main() -> None:
    asyncio.run(flatten_async())


if __name__ == "__main__":
    main()
