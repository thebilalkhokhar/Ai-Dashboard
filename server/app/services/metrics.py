"""Dashboard metrics - every number is computed here, in Python.

No LLM, no network: these are plain functions over the `*_flat` Mongo
documents (quotes, orders, payments). `build_snapshot` is the only async part
and it just loads the collections.

Definitions (all day/month boundaries are the ERP's US Central day, because
that is the business day the ERP timestamps use):

revenue_today          sum of payment.amount received today
revenue_month_to_date  sum of payment.amount from the 1st of this month
revenue_last_7_days    sum of payment.amount over the last 7 calendar days
                       (today plus the 6 days before it)
payments_count_today   number of payment records received today
open_quotes_*          quotes from processors.stale_quotes - the tightened
                       rules: not closed, not converted into an order, 3 to 30
                       days old, only the newest open quote per customer
unpaid_*               processors.unpaid_invoices - anything with a balance
                       greater than zero
overdue_*              the subset of those invoices older than
                       OVERDUE_INVOICE_DAYS
top_reps_this_month    payments per rep this month, top 5 by amount
quotes_per_day         quotes created per day, last 14 calendar days
revenue_per_day        payments received per day, last 14 calendar days

Money in text fields uses the same "$357,500" convention as the agents/chat.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from app import db as app_db
from app.services import processors
from app.services.processors import ERP_TZ, parse_date, to_number

SERIES_DAYS = 14
TOP_REP_LIMIT = 5
MAX_DOCS = 200_000


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def money_text(value: Any) -> str:
    """'$357,500' for whole dollars, '$357,500.25' otherwise (chat style).

    Delegates to the deterministic layer so dashboard tiles and agent text use
    exactly the same money format.
    """
    return processors.money_text(value)


def _utc_now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(timezone.utc)


def day_start(now: datetime) -> datetime:
    """Midnight of `now`'s day in the ERP timezone, returned as UTC."""
    local = now.astimezone(ERP_TZ)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def month_start(now: datetime) -> datetime:
    """First day of `now`'s month in the ERP timezone, returned as UTC."""
    local = now.astimezone(ERP_TZ)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def _payment_time(payment: dict) -> Optional[datetime]:
    return parse_date(payment.get("received_at"))


def _payment_amount(payment: dict) -> float:
    return to_number(payment.get("amount"))


def _sum_between(payments: Iterable[dict], start: datetime, end: datetime) -> tuple[float, int]:
    total = 0.0
    count = 0
    for payment in payments:
        received = _payment_time(payment)
        if received is None or received < start or received > end:
            continue
        total += _payment_amount(payment)
        count += 1
    return round(total, 2), count


# ---------------------------------------------------------------------------
# revenue tiles
# ---------------------------------------------------------------------------


def revenue_today(payments: Iterable[dict], now: Optional[datetime] = None) -> float:
    now = _utc_now(now)
    return _sum_between(payments, day_start(now), now)[0]


def revenue_month_to_date(payments: Iterable[dict], now: Optional[datetime] = None) -> float:
    now = _utc_now(now)
    return _sum_between(payments, month_start(now), now)[0]


def revenue_last_7_days(payments: Iterable[dict], now: Optional[datetime] = None) -> float:
    """Today plus the six days before it (calendar days, not a rolling 168h)."""
    now = _utc_now(now)
    start = day_start(now - timedelta(days=6))
    return _sum_between(payments, start, now)[0]


def payments_count_today(payments: Iterable[dict], now: Optional[datetime] = None) -> int:
    now = _utc_now(now)
    return _sum_between(payments, day_start(now), now)[1]


# ---------------------------------------------------------------------------
# open quotes / invoices (the tightened processor rules)
# ---------------------------------------------------------------------------


def open_quotes(
    quotes: Iterable[dict], orders: Iterable[dict], now: Optional[datetime] = None
) -> dict:
    """Open quote count + value using processors.stale_quotes' rules."""
    now = _utc_now(now)
    quotes = list(quotes)
    orders = list(orders)
    converted = {row["quote_id"] for row in orders if row.get("quote_id") is not None}
    rows = processors.stale_quotes(quotes, now, order_quote_ids=converted)
    total = round(sum(to_number(row.get("amount")) for row in rows), 2)
    return {"count": len(rows), "value": total, "value_display": money_text(total)}


def invoice_totals(orders: Iterable[dict], now: Optional[datetime] = None) -> dict:
    """Unpaid and overdue balances, straight from processors.unpaid_invoices."""
    now = _utc_now(now)
    rows = processors.unpaid_invoices(orders, now)
    unpaid_total = round(sum(row.get("balance", 0.0) for row in rows), 2)
    overdue_rows = [row for row in rows if row.get("overdue")]
    overdue_total = round(sum(row.get("balance", 0.0) for row in overdue_rows), 2)
    return {
        "unpaid_total": unpaid_total,
        "unpaid_count": len(rows),
        "unpaid_total_display": money_text(unpaid_total),
        "overdue_total": overdue_total,
        "overdue_count": len(overdue_rows),
        "overdue_total_display": money_text(overdue_total),
    }


# ---------------------------------------------------------------------------
# reps + daily series
# ---------------------------------------------------------------------------


def top_reps_this_month(
    payments: Iterable[dict], now: Optional[datetime] = None, limit: int = TOP_REP_LIMIT
) -> list[dict]:
    """Top reps by money received this month (payments carry the rep)."""
    now = _utc_now(now)
    start = month_start(now)
    per_rep: dict[Any, dict] = {}
    for payment in payments:
        received = _payment_time(payment)
        if received is None or received < start or received > now:
            continue
        rep = processors.sale_rep_ref(payment) or {}
        rep_id = rep.get("id")
        if rep_id is None:
            continue
        # payments store the rep as an id; the nested `users` object has the name
        name = rep.get("name") or (payment.get("users") or {}).get("name") or f"Rep {rep_id}"
        entry = per_rep.setdefault(
            rep_id, {"rep_id": rep_id, "name": name, "total": 0.0, "count": 0}
        )
        entry["total"] += _payment_amount(payment)
        entry["count"] += 1

    rows = sorted(per_rep.values(), key=lambda row: row["total"], reverse=True)[:limit]
    for row in rows:
        row["total"] = round(row["total"], 2)
        row["total_display"] = money_text(row["total"])
    return rows


def _days(now: datetime, days: int) -> list[date]:
    """The last `days` ERP-local calendar dates, oldest first, today last."""
    today = now.astimezone(ERP_TZ).date()
    return [today - timedelta(days=days - 1 - offset) for offset in range(days)]


def quotes_per_day(
    quotes: Iterable[dict], now: Optional[datetime] = None, days: int = SERIES_DAYS
) -> list[dict]:
    """Quotes created per day, zero-filled for every day in the window."""
    now = _utc_now(now)
    buckets = {day.isoformat(): 0 for day in _days(now, days)}
    for quote in quotes:
        created = parse_date(quote.get("created_at"))
        if created is None:
            continue
        key = created.astimezone(ERP_TZ).date().isoformat()
        if key in buckets:
            buckets[key] += 1
    return [{"date": key, "count": value} for key, value in buckets.items()]


def revenue_per_day(
    payments: Iterable[dict], now: Optional[datetime] = None, days: int = SERIES_DAYS
) -> list[dict]:
    """Money received per day, zero-filled for every day in the window."""
    now = _utc_now(now)
    buckets = {day.isoformat(): 0.0 for day in _days(now, days)}
    for payment in payments:
        received = _payment_time(payment)
        if received is None:
            continue
        key = received.astimezone(ERP_TZ).date().isoformat()
        if key in buckets:
            buckets[key] += _payment_amount(payment)
    return [
        {"date": key, "total": round(value, 2), "total_display": money_text(value)}
        for key, value in buckets.items()
    ]


# ---------------------------------------------------------------------------
# the snapshot
# ---------------------------------------------------------------------------


def build_snapshot_from_docs(
    quotes: Iterable[dict],
    orders: Iterable[dict],
    payments: Iterable[dict],
    now: Optional[datetime] = None,
) -> dict:
    """Pure snapshot builder - the testable half of build_snapshot."""
    now = _utc_now(now)
    quotes = list(quotes)
    orders = list(orders)
    payments = list(payments)

    revenue_today_value = revenue_today(payments, now)
    mtd_value = revenue_month_to_date(payments, now)
    last_7_value = revenue_last_7_days(payments, now)
    open_quote_totals = open_quotes(quotes, orders, now)
    invoices = invoice_totals(orders, now)

    return {
        "generated_at": now.isoformat(),
        "revenue_today": revenue_today_value,
        "revenue_today_display": money_text(revenue_today_value),
        "revenue_month_to_date": mtd_value,
        "revenue_month_to_date_display": money_text(mtd_value),
        "revenue_last_7_days": last_7_value,
        "revenue_last_7_days_display": money_text(last_7_value),
        "payments_count_today": payments_count_today(payments, now),
        "open_quotes_count": open_quote_totals["count"],
        "open_quotes_value": open_quote_totals["value"],
        "open_quotes_value_display": open_quote_totals["value_display"],
        **invoices,
        "top_reps_this_month": top_reps_this_month(payments, now),
        "quotes_per_day": quotes_per_day(quotes, now),
        "revenue_per_day": revenue_per_day(payments, now),
        "scanned": {
            "quotes": len(quotes),
            "orders": len(orders),
            "payments": len(payments),
        },
    }


async def build_snapshot(database: Any = None, now: Optional[datetime] = None) -> dict:
    """Load the three flat collections and return the whole dashboard dict."""
    # pymongo's AsyncDatabase cannot be truth-tested, hence the explicit None check
    database = database if database is not None else app_db.get_db()
    quotes = await database.quotes_flat.find().to_list(MAX_DOCS)
    orders = await database.orders_flat.find().to_list(MAX_DOCS)
    payments = await database.payments_flat.find().to_list(MAX_DOCS)
    return build_snapshot_from_docs(quotes, orders, payments, now)
