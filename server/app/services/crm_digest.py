"""The CRM morning digest: yesterday at a glance, computed in Python.

Three blocks, all from data already inside this system - no CRM calls:

* payments that landed in the Zoho CRM yesterday (the business date,
  America/Chicago - the calendar the CRM's receive dates use), read
  from the local mirror with a Python total;
* who is waiting for a reply, from the latest agent run's CRM
  Engagement section (WhatsApp counted separately, longest waits
  first within the channel order the queue tool uses);
* the biggest stalled deals, from the same run's CRM Deal Watch
  section.

Every block degrades to zeros on missing data: a digest that fails
because one collection is empty would fail every morning it matters.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("America/Chicago")


def _business_yesterday(now: datetime | None = None) -> date:
    current = now or datetime.now(timezone.utc)
    return current.astimezone(BUSINESS_TZ).date() - timedelta(days=1)


def _money(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


async def _payments_block(database: Any, day: date) -> dict:
    rows: list[dict] = []
    try:
        cursor = database.customer360_payments.find(
            {
                "amountReceiveDate": {
                    "$gte": f"{day.isoformat()}T00:00:00",
                    "$lt": f"{(day + timedelta(days=1)).isoformat()}T00:00:00",
                }
            }
        )
        rows = [row for row in await cursor.to_list(20_000) if isinstance(row, dict)]
    except Exception:
        rows = []
    # the range query narrows; Python confirms the calendar date itself
    matched = [
        row
        for row in rows
        if str(row.get("amountReceiveDate") or "")[:10] == day.isoformat()
    ]
    amounts = [_money(row.get("amountReceived")) for row in matched]
    total = (
        round(sum(amount for amount in amounts if amount is not None), 2)
        if matched and all(amount is not None for amount in amounts)
        else None
    )
    top = sorted(
        matched,
        key=lambda row: -(_money(row.get("amountReceived")) or 0.0),
    )[:3]
    return {
        "date": day.isoformat(),
        "count": len(matched),
        "total_received": total,
        "top": [
            {
                "customer": row.get("customerName"),
                "amount": _money(row.get("amountReceived")),
            }
            for row in top
        ],
    }


def _waiting_block(run: dict | None) -> dict:
    items = [
        item
        for item in ((run or {}).get("crm_engagement") or [])
        if isinstance(item, dict) and not item.get("error")
    ]
    ordered = sorted(
        items,
        key=lambda item: (
            item.get("channel") != "WhatsApp",
            -(item.get("waiting_days") or 0),
        ),
    )
    return {
        "total": len(items),
        "whatsapp_total": sum(
            1 for item in items if item.get("channel") == "WhatsApp"
        ),
        "top": [
            {
                "name": item.get("contact_name"),
                "company": item.get("company"),
                "channel": item.get("channel"),
                "waiting_days": item.get("waiting_days"),
                "last_customer_message": item.get("last_customer_message"),
            }
            for item in ordered[:3]
        ],
    }


def _deals_block(run: dict | None) -> dict:
    items = [
        item
        for item in ((run or {}).get("crm_deal_watch") or [])
        if isinstance(item, dict) and not item.get("error")
    ]
    ordered = sorted(
        items, key=lambda item: -(_money(item.get("amount")) or 0.0)
    )
    return {
        "total": len(items),
        "top": [
            {
                "name": item.get("deal_name"),
                "account": item.get("account_name"),
                "amount": _money(item.get("amount")),
                "days_idle": item.get("days_idle"),
            }
            for item in ordered[:3]
        ],
    }


async def build_crm_digest(
    database: Any, *, now: datetime | None = None
) -> dict:
    """The full digest for yesterday (business date)."""
    day = _business_yesterday(now)
    run: dict | None = None
    try:
        run = await database.agent_runs.find_one(
            {}, sort=[("started_at", -1)]
        )
    except Exception:
        run = None
    return {
        "source": "crm_morning_briefing",
        "date": day.isoformat(),
        "payments": await _payments_block(database, day),
        "waiting": _waiting_block(run),
        "stalled_deals": _deals_block(run),
        "as_of_run": (run or {}).get("started_at"),
    }
