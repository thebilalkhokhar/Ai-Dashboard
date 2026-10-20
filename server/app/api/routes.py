"""Read-only API endpoints. The frontend consumes these later.

GET /api/signals                    - all attention-needed items in one JSON
GET /api/customers/{id}/context     - one customer: quotes, orders, payments,
                                      leads + the ambiguous flag

Every number is computed in app/services/processors.py with ordinary Python.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Dict, Any

from fastapi import APIRouter, HTTPException, Path, Query

from app.db import get_db
from app.schemas.signals import (
    SignalsReport,
    SignalsSummary,
    StaleQuoteSummary,
    StalledOrderSummary,
    UnpaidSummary,
)
from app.schemas.customer import CustomerContext
from app.services.erp_client import ERPClient, ERPError
from app.services import processors

router = APIRouter()


def _round2(value: Any) -> float:
    return processors.to_number(value)


def summarize(row_list: list[dict]) -> tuple[int, float]:
    """count + amount for a signal list."""
    return len(row_list), round(sum(_round2(row["amount"]) for row in row_list), 2)


def summarize_unpaid(row_list: list[dict]) -> tuple[int, float, int, int, int]:
    """count, balance total, overdue count, not-paid count, partially-paid count."""
    count = len(row_list)
    balance = round(sum(_round2(row["balance"]) for row in row_list), 2)
    overdue = sum(1 for row in row_list if row["overdue"])
    not_paid = sum(
        1
        for row in row_list
        if row["payment_status"] in (0, None) and row["payment_status_label"] != "Partially Paid"
    )
    partial = sum(1 for row in row_list if row["payment_status"] == 1)
    return count, balance, overdue, not_paid, partial


def summarize_stalled(row_list: list[dict]) -> tuple[int, float]:
    return len(row_list), round(sum(_round2(row["total_amount"]) for row in row_list), 2)


@router.get("/signals", response_model=SignalsReport)
async def get_signals(
    stale_days: Annotated[int | None, Query(ge=0)] = None,
    stalled_days: Annotated[int | None, Query(ge=0)] = None,
    overdue_days: Annotated[int | None, Query(ge=0)] = None,
) -> SignalsReport:
    """Everything that needs attention, in one deterministic JSON."""
    db = get_db()
    now = datetime.now(timezone.utc)

    quotes = await db.quotes_flat.find().to_list(100_000)
    orders = await db.orders_flat.find().to_list(100_000)

    stale = processors.stale_quotes(quotes, now, days=stale_days)
    unpaid = processors.unpaid_invoices(orders, now, overdue_days=overdue_days)
    stalled = processors.stalled_orders(orders, now, days=stalled_days)

    stale_count, stale_amount = summarize(stale)
    unpaid_count, unpaid_balance, overdue_count, not_paid, partial = summarize_unpaid(unpaid)
    stalled_count, stalled_amount = summarize_stalled(stalled)

    quote_dates = sorted(d for d in (processors.iso_or_none(q.get("created_at")) for q in quotes) if d)
    order_dates = sorted(d for d in (processors.iso_or_none(o.get("invoice_date")) for o in orders) if d)

    return SignalsReport(
        generated_at=now.isoformat(),
        stale_quotes=stale,
        unpaid_invoices=unpaid,
        stalled_orders=stalled,
        summary=SignalsSummary(
            quotes_scanned=len(quotes),
            orders_scanned=len(orders),
            data_from=min(quote_dates or order_dates or [""]),
            data_to=max(quote_dates or order_dates or [""]),
            stale_quotes=StaleQuoteSummary(count=stale_count, total_amount=stale_amount),
            unpaid_invoices=UnpaidSummary(
                count=unpaid_count,
                total_amount=unpaid_balance,
                overdue_count=overdue_count,
                unpaid_count=not_paid,
                partially_paid_count=partial,
            ),
            stalled_orders=StalledOrderSummary(count=stalled_count, total_amount=stalled_amount),
        ),
    )


@router.get("/customers/{customer_id}/context", response_model=CustomerContext)
async def get_customer_context(
    customer_id: Annotated[int, Path(ge=1)],
    lead_limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> CustomerContext:
    """Everything the system knows about one customer, joined in Python."""
    async with ERPClient() as erp:
        try:
            data = await processors.build_customer_context(
                customer_id, database=get_db(), erp=erp, max_leads=lead_limit or 100
            )
        except ERPError as exc:  # ERP down or token broken: still return the DB part
            data = await processors.build_customer_context(
                customer_id, database=get_db(), erp=None
            )
            data["notes"].append(f"ERP lookup failed: {exc}")
    return CustomerContext(**data)
