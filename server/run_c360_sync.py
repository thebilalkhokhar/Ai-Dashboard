"""Run one full Customer 360 module sync.

Usage (from server/):  .venv/bin/python run_c360_sync.py [module ...]

With no arguments it syncs all seven modules (leads, contacts,
customers, payments, invoices, projects, estimates) through the CRM's
paginated list endpoints, upserting every row into Mongo under its CRM
id - re-runs update in place and never duplicate. Prints the run
summary with each module's seen count against the total the API itself
reported. Needs C360_EMAIL / C360_PASSWORD in server/.env and a
reachable Mongo. A full run is ~750 pages; expect it to take a while.
"""

from __future__ import annotations

import asyncio
import json
import sys

from app import db as app_db
from app.services.customer360_client import Customer360Client
from app.services.customer360_sync import MODULES, sync_all


async def main() -> None:
    modules = tuple(sys.argv[1:]) or MODULES
    unknown = [name for name in modules if name not in MODULES]
    if unknown:
        raise SystemExit(f"unknown module(s): {', '.join(unknown)}; choose from {', '.join(MODULES)}")
    client = Customer360Client()
    try:
        summary = await sync_all(app_db.get_db(), client, modules)
    finally:
        await client.close()
        await app_db.close()
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
