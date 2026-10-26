"""GET /api/signals without loading the whole ERP into memory.

The old endpoint did `find().to_list(100_000)` on quotes AND orders for every
request, so one page view copied the entire collection into Python. This module
splits the work:

  * the response is CACHED in Redis (`dashboard:signals`), refreshed by the
    scheduler leader every REFRESH_MINUTES, so a request normally just reads
    one cached document - memory per request no longer depends on the size of
    the collections at all;
  * on a cache miss (or a non-default query) the payload is built from Mongo
    with projections, streamed cursors and `$sort` + `$limit`. Summaries are
    accumulated while streaming, so Python holds the capped item lists plus a
    handful of running totals - never the raw collection.

Every number is still computed by the same app/services/processors.py rules.
"""

from __future__ import annotations

import inspect
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.config import settings
from app.services import processors
from app.services.processors import CLOSED_QUOTE_STATUSES, FINAL_ORDER_STATUSES
from app.services.redis_client import get_redis

log = logging.getLogger("signals")

SIGNALS_KEY = "dashboard:signals"
CACHE_VERSION = 1

# fields the signals rows actually need - nothing else leaves Mongo
QUOTE_FIELDS = {
    "id": 1, "ref": 1, "inv_ref": 1, "status": 1, "status_order": 1,
    "amount": 1, "created_at": 1, "customer": 1, "sale_rep": 1, "_id": 0,
}
ORDER_FIELDS = {
    "id": 1, "inv_ref": 1, "status": 1, "status_label": 1, "payment_status": 1,
    "payment_status_label": 1, "total_amount": 1, "amount_paid": 1,
    "invoice_date": 1, "due_date": 1, "customer": 1, "sale_rep": 1, "_id": 0,
}
ORDER_BALANCE_FIELDS = {
    "total_amount": 1, "amount_paid": 1, "payment_status": 1,
    "payment_status_label": 1, "invoice_date": 1, "_id": 0,
}
ORDER_STALLED_FIELDS = {
    "id": 1, "inv_ref": 1, "status": 1, "status_label": 1, "payment_status": 1,
    "payment_status_label": 1, "invoice_date": 1, "total_amount": 1,
    "customer": 1, "sale_rep": 1, "_id": 0,
}


def item_limit() -> int:
    return int(settings.SIGNALS_ITEM_LIMIT)


def cache_ttl_seconds() -> int:
    return max(60, int(settings.REFRESH_MINUTES) * 60 * 3)


def params_hash(
    stale_days: Optional[int],
    stalled_days: Optional[int],
    overdue_days: Optional[int],
    include_stalled: bool,
) -> str:
    """Default queries share one cache key; a custom window gets its own."""
    default = stale_days is None and stalled_days is None and overdue_days is None
    if default:
        return "default"
    return f"p:{stale_days}:{stalled_days}:{overdue_days}:{int(bool(include_stalled))}"


def cache_key(
    stale_days: Optional[int],
    stalled_days: Optional[int],
    overdue_days: Optional[int],
    include_stalled: bool,
) -> str:
    return f"{SIGNALS_KEY}:{CACHE_VERSION}:{params_hash(stale_days, stalled_days, overdue_days, include_stalled)}"


# ---------------------------------------------------------------------------
# redis cache
# ---------------------------------------------------------------------------


async def cache_store(payload: dict, key: str = "") -> None:
    try:
        await get_redis().set(
            key or f"{SIGNALS_KEY}:{CACHE_VERSION}:default",
            json.dumps(payload, default=str),
            ex=cache_ttl_seconds(),
        )
    except Exception as exc:  # the cache is an optimisation, never a failure
        log.warning("could not cache the signals payload: %s", exc)


async def cache_load(key: str) -> Optional[dict]:
    try:
        raw = await get_redis().get(key)
    except Exception as exc:
        log.warning("could not read the signals cache: %s", exc)
        return None
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# streaming helpers (constant memory regardless of collection size)
# ---------------------------------------------------------------------------


def _collection(database: Any, name: str):
    """Mongo databases take either accessor; test doubles usually only one."""
    try:
        return database[name]
    except TypeError:
        return getattr(database, name)


async def _stream(collection, pipeline: list[dict], batch_size: int = 2_000):
    """Run an aggregation and yield documents one by one.

    `aggregate` streams a cursor (it never materialises the result list), which
    is the point: the whole collection passes through Python without ever being
    held in memory at once. Motor hands back an awaitable cursor, PyMongo a
    cursor, so both shapes are handled.
    """
    cursor = collection.aggregate(pipeline, batchSize=batch_size)
    if inspect.isawaitable(cursor):  # Motor
        cursor = await cursor
    async for row in cursor:
        yield row


def _window_strings(now: datetime, days_low: int, days_high: int, slack_days: int = 1) -> tuple[str, str]:
    """ISO bounds in ERP time for 'older than days_low, younger than days_high'.

    The window is widened by `slack_days` on both sides on purpose: Mongo does a
    cheap coarse filter and the exact age rule stays in processors.py, so a
    date format Mongo and Python read differently can never drop a row.
    """
    erp_tz = processors.ERP_TZ
    late = (now - timedelta(days=max(days_low - slack_days, 0))).astimezone(erp_tz)
    early = (now - timedelta(days=days_high + slack_days)).astimezone(erp_tz)
    return early.isoformat(), late.isoformat()


# ---------------------------------------------------------------------------
# the three signals, built with bounded work 
# 
# ---------------------------------------------------------------------------


async def _converted_quote_ids(database) -> set:
    """Which quote ids already became orders (distinct ids only, not documents)."""
    pipeline = [
        {"$match": {"quote_id": {"$exists": True, "$nin": [None, ""]}}},
        {"$group": {"_id": "$quote_id"}},
    ]
    return {row["_id"] async for row in _stream(database.orders_flat, pipeline)}


async def _stale_signal(database, now: datetime, days: Optional[int]) -> tuple[list[dict], int, float]:
    """Open, unconverted, in-window quotes - newest money first."""
    threshold = int(settings.STALE_QUOTE_DAYS if days is None else days)
    max_age = int(settings.STALE_QUOTE_MAX_AGE_DAYS)
    low, high = _window_strings(now, threshold, max_age)

    candidates: list[dict] = []
    async for row in _stream(
        database.quotes_flat,
        [
            {"$match": {"status": {"$nin": sorted(CLOSED_QUOTE_STATUSES)}}},
            {"$match": {"created_at": {"$gte": low, "$lte": high}}},
            # newest first: stale_quotes() keeps one quote per customer and
            # compares rounded ages, so two quotes made minutes apart would
            # otherwise be decided by whatever order Mongo happened to use
            {"$sort": {"created_at": -1}},
            {"$project": QUOTE_FIELDS},
        ],
    ):
        candidates.append(row)

    converted = await _converted_quote_ids(database)
    rows = processors.stale_quotes(candidates, now, days=days, order_quote_ids=converted)
    total = round(sum(processors.to_number(row["amount"]) for row in rows), 2)
    return rows[: item_limit()], len(rows), total


async def _unpaid_signal(database, now: datetime, days: Optional[int]) -> tuple[list[dict], dict]:
    """Unpaid invoices: biggest balance first, totals from the whole set."""
    count = 0
    balance_total = 0.0
    overdue_count = not_paid = partial = 0
    page: list[dict] = []

    async for row in _stream(
        database.orders_flat,
        [
            {"$match": {"payment_status": {"$ne": processors.PAYMENT_STATUS_PAID}}},
            {"$project": ORDER_BALANCE_FIELDS},
        ],
    ):
        total = processors.to_number(row.get("total_amount"))
        paid = processors.to_number(row.get("amount_paid"))
        balance = round(total - paid, 2)
        if balance <= 0:
            continue
        if processors.parse_date(row.get("invoice_date")) is None:
            continue
        count += 1
        balance_total += balance
        age = processors.age_days(processors.parse_date(row.get("invoice_date")), now)
        threshold = int(settings.OVERDUE_INVOICE_DAYS if days is None else days)
        if age is not None and age > threshold:
            overdue_count += 1
        label = row.get("payment_status_label")
        if row.get("payment_status") == processors.PAYMENT_STATUS_PARTIAL or label == "Partially Paid":
            partial += 1
        else:
            not_paid += 1

    # the page: Mongo ranks it by real balance, Python applies the exact rules
    page: list[dict] = []
    async for row in _stream(
        database.orders_flat,
        [
            {"$match": {"payment_status": {"$ne": processors.PAYMENT_STATUS_PAID}}},
            {
                "$addFields": {
                    "balance": {
                        "$subtract": [
                            {"$ifNull": ["$total_amount", 0]},
                            {"$ifNull": ["$amount_paid", 0]},
                        ]
                    }
                }
            },
            {"$match": {"balance": {"$gt": 0}}},
            {"$sort": {"balance": -1, "invoice_date": 1}},
            {"$limit": item_limit() * 3},
            {"$project": ORDER_FIELDS},
        ],
    ):
        page.append(row)

    rows = processors.unpaid_invoices(page, now, overdue_days=days)
    summary = {
        "count": count,
        "total_amount": round(balance_total, 2),
        "overdue_count": overdue_count,
        "unpaid_count": not_paid,
        "partially_paid_count": partial,
    }
    return rows[: item_limit()], summary


async def _stalled_signal(database, now: datetime, days: Optional[int]) -> tuple[list[dict], int, float]:
    """Stalled orders (UNRELIABLE signal): totals streamed, page capped."""
    threshold = int(settings.STALLED_ORDER_DAYS if days is None else days)
    total_amount = 0.0
    count = 0
    page: list[dict] = []

    async for row in _stream(
        database.orders_flat,
        [
            {"$match": {"status_label": {"$nin": sorted(FINAL_ORDER_STATUSES)}}},
            {"$project": ORDER_STALLED_FIELDS},
            {"$sort": {"invoice_date": 1}},
        ],
    ):
        age = processors.age_days(processors.parse_date(row.get("invoice_date")), now)
        if age is None or age <= threshold:
            continue
        count += 1
        total_amount += processors.to_number(row.get("total_amount"))
        if len(page) < item_limit() * 3:
            page.append(row)

    rows = processors.stalled_orders(page, now, days=days)
    return rows[: item_limit()], count, round(total_amount, 2)


async def _min_max_iso(collection, field: str) -> tuple[Optional[str], Optional[str]]:
    """Earliest and latest ISO date in one field, computed by Mongo."""
    pipeline = [
        {"$match": {field: {"$type": "string"}}},
        {"$group": {"_id": None, "min": {"$min": f"${field}"}, "max": {"$max": f"${field}"}}},
    ]
    async for row in _stream(collection, pipeline):
        low = processors.iso_or_none(row.get("min"))
        high = processors.iso_or_none(row.get("max"))
        if low and high:
            return low, high
    return None, None


async def _date_span(database, now: datetime) -> tuple[str, str]:
    """The range the report says it covers.

    Quotes decide it when there are any, orders only when there are none - the
    rule GET /api/signals has always used, kept so the response does not shift.
    """
    low, high = await _min_max_iso(_collection(database, "quotes_flat"), "created_at")
    if low and high:
        return low, high
    low, high = await _min_max_iso(_collection(database, "orders_flat"), "invoice_date")
    if low and high:
        return low, high
    return now.isoformat(), now.isoformat()


# ---------------------------------------------------------------------------
# payload
# ---------------------------------------------------------------------------


async def build_signals_payload(
    database: Any,
    now: Optional[datetime] = None,
    *,
    stale_days: Optional[int] = None,
    stalled_days: Optional[int] = None,
    overdue_days: Optional[int] = None,
    include_stalled: bool = True,
) -> dict:
    """The same JSON GET /api/signals has always returned, built with less RAM."""
    now = now or datetime.now(timezone.utc)
    limit = item_limit()

    quotes_scanned = await database.quotes_flat.count_documents({})
    orders_scanned = await database.orders_flat.count_documents({})
    stale_rows, stale_count, stale_amount = await _stale_signal(database, now, stale_days)
    unpaid_rows, unpaid_summary = await _unpaid_signal(database, now, overdue_days)
    stalled_rows, stalled_count, stalled_amount = await _stalled_signal(database, now, stalled_days)
    data_from, data_to = await _date_span(database, now)

    return {
        "generated_at": now.isoformat(),
        "stale_quotes": stale_rows,
        "unpaid_invoices": unpaid_rows,
        "stalled_orders": {"reliable": False, "items": stalled_rows if include_stalled else []},
        "summary": {
            "quotes_scanned": quotes_scanned,
            "orders_scanned": orders_scanned,
            "data_from": data_from,
            "data_to": data_to,
            "stale_quotes": {"count": stale_count, "total_amount": stale_amount},
            "unpaid_invoices": unpaid_summary,
            "stalled_orders": {
                "count": stalled_count,
                "total_amount": stalled_amount,
                "reliable": False,
            },
            # the capped lists are what the client got; this says if it was capped
            "item_limit": limit,
        },
    }
