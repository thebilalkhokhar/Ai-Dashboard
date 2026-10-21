"""Full data ingestion from the Print247 ERP into the local Mongo *_flat collections.

What it does, in order:
  1. GET /api/get-users            -> users_flat      (one run, no paging)
  2. /api/dashboard/quotes         -> quotes_flat     (monthly windows, paged)
  3. /api/dashboard/orders         -> orders_flat     (monthly windows, paged)
  4. GET /api/get-payments         -> payments_flat   (weekly windows, no paging)
  5. Creates indexes and prints final counts vs the API totals.

Every *_flat upsert uses _id = ERP record id, so the script is safe to re-run
(Existing records get replaced, new ones inserted). The original `quotes`,
`orders`, `payments`, `users` wrapper collections are never touched.

Run it with the venv python from server/:
    .venv/bin/python -m scripts.ingest_erp
(the -m form keeps the `app` imports working; a plain file run needs cwd=server.)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # make `app...` importable

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.erp_client import ERPClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ingest")

# If one window reports this many rows (quotes/orders) the window is split -
# big windows make debugging count mismatches harder.
SUSPICIOUS_ROW_COUNT = 10_000
# Payments are not paged, so windows stay small.
PAYMENTS_WINDOW_DAYS = 7
# Split threshold for payments: more than a full page worth in one window.
SUSPICIOUS_PAYMENT_COUNT = 500


# ---------------------------------------------------------------------------
# window math
# ---------------------------------------------------------------------------


def month_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Month-long [from, to] slices of the given range; the first and last
    windows are cut off at `start` / `end`."""
    windows: list[tuple[date, date]] = []
    current = date(start.year, start.month, 1)
    while current <= end:
        first_next = date(
            current.year + (1 if current.month == 12 else 0), (current.month % 12) + 1, 1
        )
        window_end = first_next - timedelta(days=1)
        windows.append((max(current, start), min(window_end, end)))
        current = first_next
    return windows


def sub_windows(window: tuple[date, date], pieces: int) -> list[tuple[date, date]]:
    """Cut one window into `pieces` smaller (near-equal) windows."""
    start, end = window
    span = (end - start).days + 1
    chunk = max(1, -(-span // pieces))
    result: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        piece_end = min(cursor + timedelta(days=chunk - 1), end)
        result.append((cursor, piece_end))
        cursor = piece_end + timedelta(days=1)
    return result


# ---------------------------------------------------------------------------
# upsert helpers
# ---------------------------------------------------------------------------


async def upsert_records(collection, records: list[dict]) -> int:
    """Replace-or-insert every record, keyed by its ERP id."""
    from pymongo import ReplaceOne

    if not records:
        return 0
    operations = [ReplaceOne({"_id": row["id"]}, {**row, "_id": row["id"]}, upsert=True) for row in records]
    await collection.bulk_write(operations, ordered=False)
    return len(operations)


# ---------------------------------------------------------------------------
# window fetchers (with the "split on doubt" rule)
# ---------------------------------------------------------------------------


def split_if_dubious(
    window: tuple[date, date],
    rows: list[dict],
    expected: int,
    threshold: int,
) -> list[tuple[date, date]]:
    """More windows when the record count looks wrong or simply too big."""
    count = len(rows)
    if count == expected and count < threshold:
        return []
    span_days = (window[1] - window[0]).days + 1
    if span_days <= 1:
        return []  # cannot split a single day any further
    log.warning(
        "window %s..%s fetched %d, total says %d -> splitting",
        window[0], window[1], count, expected,
    )
    # keep halving until the pieces are a day wide
    return sub_windows(window, max(2, span_days // 7))


async def fetch_paged_collection(
    erp: ERPClient,
    fetcher,
    window: tuple[date, date],
    depth: int = 0,
) -> tuple[list[dict], int]:
    """Quotes/orders for a window; splits itself when counts look dubious."""
    rows, total = await fetcher(window[0].isoformat(), window[1].isoformat())
    pieces = split_if_dubious(window, rows, total, SUSPICIOUS_ROW_COUNT)
    if pieces and depth < 4:
        merged_rows: list[dict] = []
        seen: set[int] = set()
        merged_total = 0
        for piece in pieces:
            piece_rows, piece_total = await fetch_paged_collection(erp, fetcher, piece, depth + 1)
            merged_total += piece_total
            for row in piece_rows:
                seen.add(row["id"])
                merged_rows.append(row)
        return merged_rows, merged_total
    return rows, total


async def fetch_payments_window(
    erp: ERPClient,
    window: tuple[date, date],
    depth: int = 0,
) -> tuple[list[dict], dict]:
    """Payments for a window. There is no paging, so the returned data length
    is compared with the reported count/total; if they disagree the window is
    split until the numbers add up (or a single day is reached)."""
    body = await erp.get_payments(window[0].isoformat(), window[1].isoformat())
    data = body.get("data") or []
    count = int(body.get("count") or 0)
    stats = {
        "count": count,
        "total": int(body.get("total") or 0),
        "sum": float(body.get("sum") or 0),
        "data_len": len(data),
    }
    looks_short = len(data) < count or count > SUSPICIOUS_PAYMENT_COUNT
    span_days = (window[1] - window[0]).days + 1
    if looks_short and span_days > 1 and depth < 6:
        log.warning(
            "payments %s..%s: %d rows but count=%d -> splitting",
            window[0], window[1], len(data), count,
        )
        merged_rows: list[dict] = []
        seen: set[int] = set()
        merged = {"count": 0, "total": 0, "sum": 0.0, "data_len": 0}
        for piece in sub_windows(window, max(2, span_days // PAYMENTS_WINDOW_DAYS)):
            piece_rows, piece_stats = await fetch_payments_window(erp, piece, depth + 1)
            merged["count"] += piece_stats["count"]
            merged["total"] += piece_stats["total"]
            merged["sum"] += piece_stats["sum"]
            merged["data_len"] += piece_stats["data_len"]
            for row in piece_rows:
                if row["id"] not in seen:
                    seen.add(row["id"])
                    merged_rows.append(row)
        return merged_rows, merged
    if len(data) != count:
        log.error(
            "payments %s..%s still mismatched after splitting: %d rows, count=%d",
            window[0], window[1], len(data), count,
        )
    return data, stats


# ---------------------------------------------------------------------------
# index creation (the definitions live in app/services/indexes.py, which the
# FastAPI lifespan also runs, so an ingest is never the only way to get them)
# ---------------------------------------------------------------------------


async def create_indexes(database) -> None:
    from app.services.indexes import INDEXES, ensure_indexes

    created = await ensure_indexes(database)
    for name, indexes in INDEXES.items():
        keys = ", ".join(suffix for suffix, _ in indexes)
        print(f"  indexes ok: {name} ({keys}) - {created.get(name, 0)} created")


async def prune_not_fetched_records(database, fetched_ids: dict[str, set]) -> int:
    """Delete derived ("*_flat") rows the ERP did NOT return this run.

    The ERP can place a quote inside a monthly window while its created_at
    sits outside of it (checked on real data), so a date-based cleanup would
    delete good rows. Deleting ids that were not fetched this run is exact:
    every in-window record is upserted first, whatever the ERP no longer
    reports for the window goes. Re-running the script is still safe.
    Original collections are never touched.
    """
    plan = {
        "quotes_flat": fetched_ids.get("quotes_flat", set()),
        "orders_flat": fetched_ids.get("orders_flat", set()),
        "payments_flat": fetched_ids.get("payments_flat", set()),
    }
    deleted = 0
    for name, ids in plan.items():
        result = await database[name].delete_many({"_id": {"$nin": list(ids)}})
        if result.deleted_count:
            print(f"  pruned {result.deleted_count} stale record(s) from {name}")
        deleted += result.deleted_count
    return deleted


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def run_async() -> int:
    today = date.today()
    start = date.fromisoformat(settings.INGEST_FROM)
    if start > today:
        log.error("INGEST_FROM (%s) is in the future", start)
        return 2

    totals_report: dict[str, dict[str, Any]] = {}
    exit_code = 0

    async with ERPClient() as erp:
        # --- 1. users -----------------------------------------------------
        users = await erp.get_users()
        await upsert_records(db.get_db().users_flat, users)
        totals_report["users_flat"] = {"api_total": len(users)}
        print(f"users: fetched {len(users)}")

        # --- 2. quotes ----------------------------------------------------
        quote_rows: list[dict] = []
        quote_ids: set[int] = set()
        quote_api_total = 0
        for window in month_windows(start, today):
            rows, total = await erp.get_quotes_with_total(window[0].isoformat(), window[1].isoformat())
            fresh = sum(1 for row in rows if row["id"] not in quote_ids)
            for row in rows:
                quote_ids.add(row["id"])
            quote_rows.extend(rows)
            quote_api_total += total
            print(f"quotes {window[0]} .. {window[1]}: total={total} fetched={len(rows)} new_ids={fresh}")
        await upsert_records(db.get_db().quotes_flat, quote_rows)
        totals_report["quotes_flat"] = {"api_total": quote_api_total, "fetched": len(quote_rows)}
        print(f"quotes: fetched {len(quote_rows)} rows for a combined API total of {quote_api_total}")

        # --- 3. orders ----------------------------------------------------
        order_rows: list[dict] = []
        order_ids: set[int] = set()
        order_api_total = 0
        for window in month_windows(start, today):
            rows, total = await erp.get_orders_with_total(window[0].isoformat(), window[1].isoformat())
            fresh = sum(1 for row in rows if row["id"] not in order_ids)
            for row in rows:
                order_ids.add(row["id"])
            order_rows.extend(rows)
            order_api_total += total
            print(f"orders {window[0]} .. {window[1]}: total={total} fetched={len(rows)} new_ids={fresh}")
        await upsert_records(db.get_db().orders_flat, order_rows)
        totals_report["orders_flat"] = {"api_total": order_api_total, "fetched": len(order_rows)}
        print(f"orders: fetched {len(order_rows)} rows for a combined API total of {order_api_total}")
        # --- 4. payments (weekly) -----------------------------------------
        payment_rows: list[dict] = []
        payment_api_total = 0
        payment_api_sum = 0.0
        current = start
        while current <= today:
            window_end = min(current + timedelta(days=PAYMENTS_WINDOW_DAYS - 1), today)
            rows, stats = await fetch_payments_window(erp, (current, window_end))
            payment_rows.extend(rows)
            payment_api_total += stats["count"]
            payment_api_sum += stats["sum"]
            print(
                f"payments {current} .. {window_end}: count={stats['count']} "
                f"rows={len(rows)} sum={stats['sum']:.2f}"
            )
            current = window_end + timedelta(days=1)
        await upsert_records(db.get_db().payments_flat, payment_rows)
        totals_report["payments_flat"] = {
            "api_total": payment_api_total,
            "api_sum": round(payment_api_sum, 2),
            "fetched": len(payment_rows),
        }
        print(f"payments: fetched {len(payment_rows)} rows for a combined API total of {payment_api_total}")

    # --- 5. prune rows the ERP no longer returns, then counts vs totals --
    await prune_not_fetched_records(
        db.get_db(),
        {
            "quotes_flat": quote_ids,
            "orders_flat": order_ids,
            "payments_flat": {row["id"] for row in payment_rows},
        },
    )
    print("\n=== FINAL COUNTS VS ERP TOTALS ===")
    mongo_counts = {
        "users_flat": await db.get_db().users_flat.count_documents({}),
        "quotes_flat": await db.get_db().quotes_flat.count_documents({}),
        "orders_flat": await db.get_db().orders_flat.count_documents({}),
        "payments_flat": await db.get_db().payments_flat.count_documents({}),
    }
    for name, report in totals_report.items():
        wanted = report.get("api_total") or 0
        stored = mongo_counts.get(name, 0)
        fetched = report.get("fetched", wanted)
        status = "OK" if wanted == stored else "MISMATCH"
        print(
            f"{name:14} ERP total={wanted:7} fetched={fetched:7} stored={stored:7} -> {status}"
        )
        if name == "payments_flat" and "api_sum" in report:
            aggregate_cursor = await db.get_db().payments_flat.aggregate(
                [
                    {
                        "$group": {
                            "_id": None,
                            "amount": {
                                "$sum": {
                                    "$convert": {
                                        "input": "$amount",
                                        "to": "double",
                                        "onError": 0,
                                        "onNull": 0,
                                    }
                                }
                            },
                        }
                    }
                ]
            )
            aggregate_rows = await aggregate_cursor.to_list(1)
            stored_sum = round((aggregate_rows or [{"amount": 0}])[0].get("amount", 0), 2)
            print(f"{'':14} ERP sum={report['api_sum']:.2f} stored sum={stored_sum:.2f}")
        if wanted != stored:
            exit_code = 1

    # --- 6. indexes -------------------------------------------------------
    print("\n=== INDEXES ===")
    await create_indexes(db.get_db())
    return exit_code


def main() -> None:
    if not settings.ERP_REFRESH_TOKEN:
        print("ERP_REFRESH_TOKEN is not set. Add it to server/.env first.")
        raise SystemExit(2)
    exit_code = asyncio.run(run_async())
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
