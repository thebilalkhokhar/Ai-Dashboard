"""Incremental ERP refresh - only the last few days, only upserts.

The full ingest (`scripts/ingest_erp.py`) walks the whole INGEST_FROM range
once. This module does the small version that the scheduler runs every few
minutes: re-pull the last REFRESH_LOOKBACK_DAYS days of quotes, orders and
payments and upsert them into the same `*_flat` collections, with the same
`_id = ERP record id` keys.

Nothing is duplicated: the window helpers, the "split when counts look
dubious" fetchers and the ReplaceOne upsert all come from the ingest script.

Read-only on the ERP side. No writes are ever sent back.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app import db as app_db
from app.config import settings
from app.services.erp_client import ERPClient

log = logging.getLogger("refresh")

# Mongo only wants reasonably sized $in lists.
ID_CHUNK = 500


# ---------------------------------------------------------------------------
# ingest helpers (imported lazily so importing this module has no side effects:
# importing scripts/ingest_erp.py configures logging and touches sys.path)
# ---------------------------------------------------------------------------


def _ingest_helpers():
    from scripts.ingest_erp import (  # noqa: PLC0415 - lazy on purpose
        fetch_paged_collection,
        fetch_payments_window,
    )

    return fetch_paged_collection, fetch_payments_window


def _upsert_records():
    from scripts.ingest_erp import upsert_records  # noqa: PLC0415 - lazy on purpose

    return upsert_records


# ---------------------------------------------------------------------------
# change counting
# ---------------------------------------------------------------------------


def _materially_different(stored: dict, incoming: dict) -> bool:
    """True when the ERP row differs from what Mongo already holds.

    Compared field by field (the `*_flat` docs are full replacements, so a
    dropped field is a change too). Numbers coming back as int and float are
    not treated as different.
    """
    merged = {**incoming, "_id": incoming["id"]}
    if set(stored) != set(merged):
        return True
    for key, value in merged.items():
        old = stored.get(key)
        if isinstance(old, (int, float)) and isinstance(value, (int, float)):
            if float(old) == float(value):
                continue
        if old != value:
            return True
    return False


async def _upsert_and_count(collection, rows: list[dict]) -> dict:
    """Compare with Mongo, then upsert. Returns fetched/new/updated/unchanged."""
    if not rows:
        return {"fetched": 0, "new": 0, "updated": 0, "unchanged": 0}

    ids = list({row["id"] for row in rows})
    stored: dict[Any, dict] = {}
    for start in range(0, len(ids), ID_CHUNK):
        chunk = ids[start : start + ID_CHUNK]
        found = await collection.find({"_id": {"$in": chunk}}).to_list(len(chunk))
        for doc in found:
            stored[doc["_id"]] = doc

    new = updated = 0
    for row in rows:
        old = stored.get(row["id"])
        if old is None:
            new += 1
        elif _materially_different(old, row):
            updated += 1

    await _upsert_records()(collection, rows)

    return {
        "fetched": len(rows),
        "new": new,
        "updated": updated,
        "unchanged": len(rows) - new - updated,
    }


# ---------------------------------------------------------------------------
# the refresh itself
# ---------------------------------------------------------------------------


def lookback_window(lookback_days: Optional[int] = None, today: Optional[date] = None):
    """(from, to) dates for the last N days, today included."""
    days = max(1, int(lookback_days if lookback_days is not None else settings.REFRESH_LOOKBACK_DAYS))
    today = today or date.today()
    return today - timedelta(days=days - 1), today


async def run_incremental_refresh(
    lookback_days: Optional[int] = None,
    database: Any = None,
    erp: Any = None,
) -> dict:
    """Re-pull the recent ERP rows and upsert them; log counts to refresh_logs.

    `database` / `erp` are injectable so tests never touch the network.
    A refresh failure is recorded and returned - it never raises, because the
    scheduler loop must keep going.
    """
    # note: pymongo's AsyncDatabase cannot be truth-tested, so compare with None
    database = database if database is not None else app_db.get_db()
    owned_erp = erp is None
    client = ERPClient() if owned_erp else erp

    fetch_paged_collection, fetch_payments_window = _ingest_helpers()
    date_from, date_to = lookback_window(lookback_days)
    started_at = datetime.now(timezone.utc)

    report: dict[str, Any] = {
        "started_at": started_at.isoformat(),
        "window": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "lookback_days": max(1, int(lookback_days if lookback_days is not None else settings.REFRESH_LOOKBACK_DAYS)),
        "ok": True,
        "error": None,
    }

    try:
        # The same fetchers the full ingest uses: they page, and they split a
        # window when the ERP's own count disagrees with what came back.
        quote_rows, _quote_total = await fetch_paged_collection(
            client, client.get_quotes_with_total, (date_from, date_to)
        )
        order_rows, _order_total = await fetch_paged_collection(
            client, client.get_orders_with_total, (date_from, date_to)
        )
        payment_rows, _payment_stats = await fetch_payments_window(
            client, (date_from, date_to)
        )

        report["quotes"] = await _upsert_and_count(database.quotes_flat, quote_rows)
        report["orders"] = await _upsert_and_count(database.orders_flat, order_rows)
        report["payments"] = await _upsert_and_count(database.payments_flat, payment_rows)
    except Exception as exc:  # ERP down / token broken: log, keep the loop alive
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        log.exception("incremental refresh failed")
    finally:
        if owned_erp:
            await client.close()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()

    try:
        await database.refresh_logs.insert_one(dict(report))
    except Exception:  # logging must never break the refresh
        log.exception("could not write the refresh log")

    if report.get("ok"):
        log.info(
            "refresh %s..%s quotes=%s orders=%s payments=%s",
            report["window"]["from"],
            report["window"]["to"],
            report.get("quotes"),
            report.get("orders"),
            report.get("payments"),
        )
    return report
