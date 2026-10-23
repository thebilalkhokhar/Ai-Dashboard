"""Run one Customer 360 index harvest.

Usage (from server/):  .venv/bin/python run_c360_harvest.py [max_queries]

Searches the CRM API breadth-first and upserts every record it can reach
into the `customer360_records` collection (one document per CRM record,
keyed by the CRM's own id - re-runs never duplicate). Prints the run
summary, including which queries stayed capped (proof of what the API's
per-query cap kept out of reach). Needs C360_EMAIL / C360_PASSWORD in
server/.env and a reachable Mongo.
"""

from __future__ import annotations

import asyncio
import json
import sys

from app import db as app_db
from app.services.customer360_client import Customer360Client
from app.services.customer360_index import run_harvest


async def main() -> None:
    max_queries = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    client = Customer360Client()
    try:
        summary = await run_harvest(app_db.get_db(), client, max_queries=max_queries)
    finally:
        await client.close()
        await app_db.close()
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
