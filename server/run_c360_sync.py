"""Run one full Customer 360 module sync.

Usage (from server/):  .venv/bin/python run_c360_sync.py [module ...]

With no arguments it syncs all seven modules (leads, contacts,
customers, payments, invoices, projects, estimates) through the CRM's
paginated list endpoints, upserting every row into Mongo under its CRM
id - re-runs update in place and never duplicate. Prints the run
summary with each module's seen count against the total the API itself
reported. Needs C360_EMAIL / C360_PASSWORD in server/.env and a
reachable Mongo. A full run is ~750 pages; expect it to take a while.

You rarely need to run this by hand: the server launches it on its own
whenever the mirror is older than C360_SYNC_HOURS (see the scheduler's
c360 auto-sync). This script is for an immediate refresh. A Redis lock
(shared with the automatic launcher) keeps two syncs from overlapping;
`--auto` is how the scheduler marks its launches in the run log, and
`--recent` runs the cheap pass instead: only the first C360_RECENT_PAGES
pages of each module - the lists are newest-first, so a minute or two
of work freshens everything new without the full 750-page run.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid

from app import db as app_db
from app.config import settings
from app.services.customer360_client import Customer360Client
from app.services.customer360_sync import MODULES, SYNC_LOCK_KEY, sync_all
from app.services.redis_client import get_redis


async def _claim_sync_lock() -> tuple[bool, str | None]:
    """(may proceed, lock token). Another sync holding the lock means
    skip; Redis being unreachable never blocks a manual run - it only
    loses the overlap protection, and upserts make an overlap harmless."""
    token = uuid.uuid4().hex
    try:
        redis = get_redis()
        if await redis.set(SYNC_LOCK_KEY, token, nx=True, ex=3 * 3600):
            return True, token
        return False, None
    except Exception:
        print("warning: Redis unreachable, running without the sync lock")
        return True, None


async def _release_sync_lock(token: str | None) -> None:
    if not token:
        return
    try:
        redis = get_redis()
        current = await redis.get(SYNC_LOCK_KEY)
        if isinstance(current, bytes):
            current = current.decode()
        if current == token:  # only the owner releases it
            await redis.delete(SYNC_LOCK_KEY)
    except Exception:
        pass


async def main() -> None:
    flags = {"--auto", "--recent"}
    argv = [arg for arg in sys.argv[1:] if arg not in flags]
    mode = "auto" if "--auto" in sys.argv[1:] else "manual"
    recent = "--recent" in sys.argv[1:]
    modules = tuple(argv) or MODULES
    unknown = [name for name in modules if name not in MODULES]
    if unknown:
        raise SystemExit(f"unknown module(s): {', '.join(unknown)}; choose from {', '.join(MODULES)}")
    proceed, token = await _claim_sync_lock()
    if not proceed:
        print(json.dumps({"skipped": "another Customer 360 sync is already running"}))
        return
    client = Customer360Client()
    try:
        summary = await sync_all(
            app_db.get_db(), client, modules, mode=mode,
            scope="recent" if recent else "full",
            max_pages=int(settings.C360_RECENT_PAGES) if recent else None,
        )
    finally:
        await client.close()
        await app_db.close()
        await _release_sync_lock(token)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
