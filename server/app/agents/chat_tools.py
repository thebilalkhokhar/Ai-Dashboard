"""Read-only Mongo tools for the CEO chat agent.

All arithmetic stays in Python. These tools never call the ERP or write to the
business collections; chat history and request logs are managed by chat.py.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Callable, Type

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app import db as app_db
from app.config import settings
from app.services import metrics, processors

log = logging.getLogger("chat_tools")

_CURRENCY_TEXT = re.compile(r"\$\s*(-?[\d,]+(?:\.\d+)?)")


def format_money(value: Any) -> str:
    amount = round(processors.to_number(value), 2)
    if amount == int(amount):
        return f"${int(amount):,}"
    return f"${amount:,.2f}"


def format_chat_result(value: Any, key: str = "") -> Any:
    """Format money and counts before a tool result is sent to the model."""
    lowered = key.casefold()
    is_money = "amount" in lowered or "balance" in lowered or lowered.endswith("_value") or lowered == "total_payments"
    is_count = lowered == "count" or lowered.endswith("_count") or lowered == "total_count"

    if isinstance(value, dict):
        return {name: format_chat_result(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [format_chat_result(item, key) for item in value]
    if is_money and isinstance(value, (int, float, str)) and value is not None:
        return format_money(value)
    if is_count and isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        return _CURRENCY_TEXT.sub(lambda match: format_money(match.group(1)), value)
    return value


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyArgs(ToolArgs):
    pass


class CustomerSearchArgs(ToolArgs):
    name: str = Field(min_length=1, max_length=120)


class CustomerContextArgs(ToolArgs):
    customer_id: int = Field(ge=1)


class DateRangeArgs(ToolArgs):
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def date_range_is_ordered(self):
        if self.date_to < self.date_from:
            raise ValueError("date_to must be on or after date_from")
        return self


class RevenueArgs(DateRangeArgs):
    rep_id: int | None = Field(default=None, ge=1)


class TopItemsArgs(ToolArgs):
    limit: int = Field(default=5, ge=1, le=10)


class StuckLeadsArgs(ToolArgs):
    days: int | None = Field(default=None, ge=1, le=90)
    limit: int | None = Field(default=None, ge=1, le=25)


class RepSummaryArgs(DateRangeArgs):
    rep_id: int | None = Field(default=None, ge=1)
    rep_name: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def exactly_one_rep_selector(self):
        if (self.rep_id is None) == (self.rep_name is None):
            raise ValueError("provide exactly one of rep_id or rep_name")
        return self


@dataclass(frozen=True)
class ChatTool:
    name: str
    description: str
    args_model: Type[BaseModel]
    function: Callable

    def model_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.args_model.model_json_schema(),
        }


def _database(database: Any = None) -> Any:
    return database if database is not None else app_db.get_db()


def _serializable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


ROW_KEYS = ("items", "results", "drafts", "records", "priorities", "briefing")


def _bounded(value: dict, max_chars: int = 10_500) -> dict:
    """Keep a single tool response below roughly 3,000 tokens.

    A too-large answer loses rows from the end (and says so) rather than losing
    its counts: the totals are computed over every matched row and the model
    must always be able to tell "this is a page" from "this is everything".
    """
    clean = _serializable(value)
    if len(json.dumps(clean, ensure_ascii=False, separators=(",", ":"))) <= max_chars:
        return clean

    trimmed = dict(clean)
    lists = {key: trimmed[key] for key in ROW_KEYS if isinstance(trimmed.get(key), list)}
    if not lists:
        return {"truncated": True, "preview": json.dumps(clean, ensure_ascii=False, default=str)[: max_chars - 80]}

    def fits(candidate: dict) -> bool:
        return len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))) <= max_chars

    while not fits(trimmed) and any(lists.values()):
        for key in lists:
            if lists[key]:
                lists[key].pop()
                trimmed[key] = lists[key]
        if any(key in trimmed for key in ("returned",)):
            trimmed["returned"] = sum(len(rows) for rows in lists.values() if isinstance(rows, list))
        trimmed["truncated"] = True
    trimmed.setdefault("truncated", True)
    return trimmed


def _same_id(left: Any, right: Any) -> bool:
    return str(left) == str(right)


def _rep_id(record: dict) -> Any:
    return processors.sale_rep_ref(record).get("id") if processors.sale_rep_ref(record) else None


def _rep_name(record: dict) -> str:
    ref = processors.sale_rep_ref(record)
    return str((ref or {}).get("name") or "")


def _in_date_range(value: Any, date_from: date, date_to: date) -> bool:
    moment = processors.parse_date(value)
    if moment is None:
        return False
    start = datetime.combine(date_from, time.min, tzinfo=processors.ERP_TZ).astimezone(timezone.utc)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=processors.ERP_TZ).astimezone(timezone.utc)
    return start <= moment < end


def _name_score(query: str, candidate: str) -> float:
    q = re.sub(r"[^\w]+", " ", query.casefold()).strip()
    c = re.sub(r"[^\w]+", " ", candidate.casefold()).strip()
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if q in c:
        return 0.95
    q_words, c_words = set(q.split()), set(c.split())
    overlap = len(q_words & c_words) / max(len(q_words), 1)
    return max(SequenceMatcher(None, q, c).ratio(), overlap * 0.9)


async def get_latest_briefing(database: Any = None) -> dict:
    EmptyArgs.model_validate({})
    rows = await _database(database).agent_runs.find().sort("started_at", -1).to_list(1)
    if not rows:
        return {"run_id": None, "message": "No agent briefing is available."}
    row = rows[0]
    master = row.get("master") or {}
    return _bounded(
        {
            "run_id": row.get("run_id"),
            "started_at": row.get("started_at"),
            "briefing": master.get("bullets", []),
            "priorities": master.get("priorities", []),
            "conflicts_resolved": master.get("conflicts_resolved", []),
        }
    )


async def get_signals_summary(database: Any = None) -> dict:
    EmptyArgs.model_validate({})
    db = _database(database)
    quotes = await db.quotes_flat.find().to_list(200_000)
    orders = await db.orders_flat.find().to_list(200_000)
    order_quote_ids = {row.get("quote_id") for row in orders if row.get("quote_id") is not None}
    now = datetime.now(timezone.utc)
    stale = processors.stale_quotes(quotes, now, order_quote_ids=order_quote_ids)
    unpaid = processors.unpaid_invoices(orders, now)
    overdue = [row for row in unpaid if row["overdue"]]
    return {
        "stale_quotes": {
            "count": len(stale),
            "total_amount": round(sum(row["amount"] for row in stale), 2),
        },
        "unpaid_invoices": {
            "count": len(unpaid),
            "total_balance": round(sum(row["balance"] for row in unpaid), 2),
        },
        "overdue_invoices": {
            "count": len(overdue),
            "total_balance": round(sum(row["balance"] for row in overdue), 2),
        },
        "order_fields_not_available": list(processors.ORDER_MISSING_FIELDS),
        "as_of": now.isoformat(),
    }


async def find_customer(name: str, database: Any = None) -> dict:
    args = CustomerSearchArgs.model_validate({"name": name})
    db = _database(database)
    found: dict[tuple[str, str], dict] = {}
    projection = {"customer.id": 1, "customer.company_name": 1, "customer_id": 1}
    for collection_name in ("quotes_flat", "orders_flat", "payments_flat"):
        rows = await getattr(db, collection_name).find({}, projection).to_list(200_000)
        for row in rows:
            customer = row.get("customer") or {}
            customer_id = customer.get("id", row.get("customer_id"))
            company = str(customer.get("company_name") or "").strip()
            if customer_id is None or not company:
                continue
            key = (str(customer_id), company.casefold())
            found[key] = {"id": customer_id, "company_name": company}
    scored = [(_name_score(args.name, row["company_name"]), row) for row in found.values()]
    matches = [item for item in scored if item[0] >= 0.45]
    matches.sort(key=lambda item: (-item[0], item[1]["company_name"].casefold()))
    return {"matches": [row for _, row in matches[:5]]}


async def get_customer_context(customer_id: int, database: Any = None) -> dict:
    args = CustomerContextArgs.model_validate({"customer_id": customer_id})
    db = _database(database)
    query = {"$or": [{"customer.id": args.customer_id}, {"customer_id": args.customer_id}]}
    quotes = await db.quotes_flat.find(query).to_list(200_000)
    orders = await db.orders_flat.find(query).to_list(200_000)
    payments = await db.payments_flat.find(query).to_list(200_000)
    # This pure join intentionally receives no ERP lead outcomes.
    context = processors.customer_context(args.customer_id, quotes, orders, payments)
    now = datetime.now(timezone.utc)
    unpaid = processors.unpaid_invoices(orders, now)
    invoice_fields = (
        "id", "inv_ref", "payment_status", "payment_status_label", "overdue",
        "days_invoice_age", "invoice_date", "due_date",
    )
    context["unpaid_invoices"] = [
        {key: row.get(key) for key in invoice_fields}
        for row in unpaid[:25]
    ]
    context["unpaid_invoice_count"] = len(unpaid)
    context["unpaid_invoices_truncated"] = len(unpaid) > 25
    context.pop("contact", None)
    context["notes"] = ["Lead outcomes are not included in read-only chat context."]
    return _bounded(context)


async def _headline_snapshot(database: Any, now: datetime | None = None) -> dict:
    """The snapshot the dashboard itself serves, so the two can never disagree.

    GET /api/metrics answers from the Redis copy the scheduler refreshes every
    REFRESH_MINUTES; reading it here means a chat headline is the same number
    (same moment) as the tile on screen. Without Redis, or before the first
    cycle, both fall back to building it from Mongo with the same function.
    """
    if now is None:
        try:
            from app.services.redis_client import get_redis
            from app.services.scheduler import SNAPSHOT_KEY

            raw = await get_redis().get(SNAPSHOT_KEY)
            if raw:
                payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, dict) and data.get("generated_at"):
                    return data
        except Exception:
            log.debug("no dashboard snapshot in Redis; building one", exc_info=True)
    return await metrics.build_snapshot(database=database, now=now)


async def get_dashboard_metrics(
    database: Any = None, now: datetime | None = None
) -> dict:
    """The exact metrics the Next.js dashboard renders (same snapshot).

    The money fields are the dashboard's, the day/month boundaries are the
    ERP's US Central business day, so "today's revenue" or "open quotes" can
    never disagree with the tile the CEO is looking at.
    """
    EmptyArgs.model_validate({})
    snapshot = await _headline_snapshot(_database(database), now)
    return {
        "generated_at": snapshot["generated_at"],
        "business_timezone": str(processors.ERP_TZ),
        "revenue_today": snapshot["revenue_today_display"],
        "revenue_month_to_date": snapshot["revenue_month_to_date_display"],
        "revenue_last_7_days": snapshot["revenue_last_7_days_display"],
        "payments_count_today": snapshot["payments_count_today"],
        # Numbers a label such as "last 7 days" may legitimately mention.
        "window_days": {"today": 1, "last_7_days": 7},
        "open_quotes_count": snapshot["open_quotes_count"],
        "open_quotes_value": snapshot["open_quotes_value_display"],
        "open_quotes_total_amount": snapshot["open_quotes_value"],
        "unpaid_total": snapshot["unpaid_total_display"],
        "unpaid_count": snapshot["unpaid_count"],
        "overdue_total": snapshot["overdue_total_display"],
        "overdue_count": snapshot["overdue_count"],
    }


async def get_revenue(
    date_from: date | str,
    date_to: date | str,
    rep_id: int | None = None,
    database: Any = None,
) -> dict:
    args = RevenueArgs.model_validate(
        {"date_from": date_from, "date_to": date_to, "rep_id": rep_id}
    )
    rows = await _database(database).payments_flat.find().to_list(200_000)
    matched = [
        row
        for row in rows
        if _in_date_range(row.get("received_at"), args.date_from, args.date_to)
        and (args.rep_id is None or _same_id(_rep_id(row), args.rep_id))
    ]
    return {
        "date_from": args.date_from.isoformat(),
        "date_to": args.date_to.isoformat(),
        "rep_id": args.rep_id,
        "payment_count": len(matched),
        "total_payments": round(sum(processors.to_number(row.get("amount")) for row in matched), 2),
    }


async def get_top_unpaid(limit: int = 5, database: Any = None) -> dict:
    args = TopItemsArgs.model_validate({"limit": limit})
    db = _database(database)
    orders = await db.orders_flat.find().to_list(200_000)
    rows = processors.unpaid_invoices(orders, datetime.now(timezone.utc))
    debts: dict[tuple[str, str], dict] = {}
    for row in rows:
        customer = row.get("customer") or {}
        key = (str(customer.get("id")), str(customer.get("company_name") or ""))
        group = debts.setdefault(
            key,
            {
                "customer_id": customer.get("id"),
                "company_name": customer.get("company_name"),
                "unpaid_invoice_count": 0,
                "overdue_invoice_count": 0,
                "total_balance": 0.0,
            },
        )
        group["unpaid_invoice_count"] += 1
        group["overdue_invoice_count"] += int(bool(row["overdue"]))
        group["total_balance"] += row["balance"]
    largest_customer_debt = max(debts.values(), key=lambda item: item["total_balance"], default=None)
    if largest_customer_debt is not None:
        largest_customer_debt["total_balance"] = round(largest_customer_debt["total_balance"], 2)
        customer_id = largest_customer_debt.get("customer_id")
        customer_query = {"$or": [{"customer.id": customer_id}, {"customer_id": customer_id}]}
        payments = await db.payments_flat.find(customer_query).to_list(200_000)
        largest_customer_debt["payments"] = {
            "count": len(payments),
            "total_amount": round(sum(processors.to_number(row.get("amount")) for row in payments), 2),
        }
        customer_orders = [
            row for row in orders
            if _same_id(
                (row.get("customer") or {}).get("id", row.get("customer_id")),
                customer_id,
            )
        ]
        largest_customer_debt["order_history"] = {
            "count": len(customer_orders),
            "amount_paid": round(sum(processors.to_number(row.get("amount_paid")) for row in customer_orders), 2),
            "balance": round(sum(processors.to_number(row.get("total_amount")) - processors.to_number(row.get("amount_paid")) for row in customer_orders), 2),
        }
    fields = (
        "id", "inv_ref", "status_label", "payment_status_label", "total_amount",
        "amount_paid", "balance", "overdue", "invoice_date", "due_date", "customer", "sale_rep",
    )
    return {
        "total_count": len(rows),
        "largest_owing_customer": largest_customer_debt,
        "items": [{key: row.get(key) for key in fields} for row in rows[: args.limit]],
        "order_fields_not_available": list(processors.ORDER_MISSING_FIELDS),
    }


async def get_stuck_leads(
    days: int | None = None, limit: int | None = None, database: Any = None
) -> dict:
    """Leads stuck before becoming an order. Read-only, Python-parsed.

    Same filter as GET /api/leads/stuck and the lead follow-up agent. No
    email or phone numbers are ever returned to the model.
    """
    args = StuckLeadsArgs.model_validate({"days": days, "limit": limit})
    db = _database(database)
    now = datetime.now(timezone.utc)
    rows = await db.leads.find(processors.LEAD_STATUS_QUERY, processors.LEAD_FIELDS).to_list(50_000)
    stuck = processors.stuck_leads(rows, now, days=args.days)
    artwork_labels = {True: "Provided", False: "Missing", None: "Not stated"}
    items = [
        {
            "lead_id": row["lead_id"],
            "contact_name": row["contact_name"],
            "company": row["company"],
            "status": row["status"],
            "days_since_activity": row["days_since_activity"],
            "product": row.get("product") or row.get("type"),
            "quantity": row.get("quantity"),
            "size": row.get("size"),
            "material": row.get("material"),
            "finish": row.get("finish"),
            "artwork": artwork_labels[row["artwork_present"]],
            "missing": row["missing"],
        }
        for row in stuck[: args.limit or 10]
    ]
    return {
        "days": args.days or settings.LEAD_ACTIVE_DAYS,
        "total_count": len(stuck),
        "items": items,
    }


async def get_top_open_quotes(limit: int = 5, database: Any = None) -> dict:
    args = TopItemsArgs.model_validate({"limit": limit})
    db = _database(database)
    quotes = await db.quotes_flat.find().to_list(200_000)
    orders = await db.orders_flat.find().to_list(200_000)
    converted = {row.get("quote_id") for row in orders if row.get("quote_id") is not None}
    rows = [
        row for row in quotes
        if str(row.get("status") or "") not in processors.CLOSED_QUOTE_STATUSES
        and row.get("id") not in converted
    ]
    rows.sort(key=lambda row: processors.to_number(row.get("amount")), reverse=True)
    fields = ("id", "ref", "status", "created_at", "amount", "customer", "sale_rep")
    return {"total_count": len(rows), "items": [{key: row.get(key) for key in fields} for row in rows[: args.limit]]}


async def _resolve_rep(rep_id: int | None, rep_name: str | None, database: Any) -> dict:
    if rep_id is not None:
        users = await database.users_flat.find({"id": rep_id}).to_list(1)
        name = str((users[0].get("name") if users else "") or "")
        return {"id": rep_id, "name": name or rep_name or ""}

    target = (rep_name or "").strip()
    users = await database.users_flat.find().to_list(20_000)
    candidates: dict[str, dict] = {}
    for user in users:
        name = str(user.get("name") or user.get("full_name") or "").strip()
        if user.get("id") is not None and name:
            candidates[str(user["id"])] = {"id": user["id"], "name": name}
    for collection_name in ("quotes_flat", "orders_flat", "payments_flat"):
        rows = await getattr(database, collection_name).find().to_list(200_000)
        for row in rows:
            ref = processors.sale_rep_ref(row) or {}
            if ref.get("id") is not None and ref.get("name"):
                candidates[str(ref["id"])] = {"id": ref["id"], "name": str(ref["name"])}
    scored = [(_name_score(target, row["name"]), row) for row in candidates.values()]
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored or scored[0][0] < 0.6:
        return {"id": None, "name": target, "error": "No matching sales representative was found."}
    if len(scored) > 1 and scored[0][0] == scored[1][0] and not _same_id(scored[0][1]["id"], scored[1][1]["id"]):
        return {"id": None, "name": target, "error": "The sales representative name is ambiguous."}
    return scored[0][1]


async def get_rep_summary(
    date_from: date | str,
    date_to: date | str,
    rep_id: int | None = None,
    rep_name: str | None = None,
    database: Any = None,
) -> dict:
    args = RepSummaryArgs.model_validate(
        {"date_from": date_from, "date_to": date_to, "rep_id": rep_id, "rep_name": rep_name}
    )
    db = _database(database)
    rep = await _resolve_rep(args.rep_id, args.rep_name, db)
    if rep.get("id") is None:
        return {"rep": rep, "date_from": args.date_from.isoformat(), "date_to": args.date_to.isoformat()}

    quotes = await db.quotes_flat.find().to_list(200_000)
    orders = await db.orders_flat.find().to_list(200_000)
    payments = await db.payments_flat.find().to_list(200_000)
    quotes = [r for r in quotes if _same_id(_rep_id(r), rep["id"]) and _in_date_range(r.get("created_at"), args.date_from, args.date_to)]
    orders = [r for r in orders if _same_id(_rep_id(r), rep["id"]) and _in_date_range(r.get("invoice_date"), args.date_from, args.date_to)]
    payments = [r for r in payments if _same_id(_rep_id(r), rep["id"]) and _in_date_range(r.get("received_at"), args.date_from, args.date_to)]
    return {
        "rep": rep,
        "date_from": args.date_from.isoformat(),
        "date_to": args.date_to.isoformat(),
        "quotes": {"count": len(quotes), "total_amount": round(sum(processors.to_number(r.get("amount")) for r in quotes), 2)},
        "orders": {"count": len(orders), "total_amount": round(sum(processors.to_number(r.get("total_amount")) for r in orders), 2)},
        "payments": {"count": len(payments), "total_amount": round(sum(processors.to_number(r.get("amount")) for r in payments), 2)},
    }


# ---------------------------------------------------------------------------
# record-level read-only tools
#
# The aggregate tools above answer "how much"; these answer "who, which record".
# Each one filters in Python over projected rows, returns at most `limit`
# (never more than MAX_TOOL_ROWS) items, and always reports the full
# total_count so the model can never claim it saw everything.
# ---------------------------------------------------------------------------

DEFAULT_TOOL_ROWS = 10
MAX_TOOL_ROWS = 50

PAYMENT_FIELDS = {
    "id": 1, "amount": 1, "payment_date": 1, "received_at": 1, "payment_ref": 1,
    "total_payment_used": 1, "customer_id": 1, "customer": 1, "users": 1,
    "payment_details": 1, "_id": 0,
}
INVOICE_FIELDS = {
    "id": 1, "inv_ref": 1, "status_label": 1, "payment_status": 1,
    "payment_status_label": 1, "total_amount": 1, "amount_paid": 1,
    "invoice_date": 1, "due_date": 1, "customer": 1, "sale_rep": 1, "_id": 0,
}
QUOTE_FIELDS = {
    "id": 1, "ref": 1, "status": 1, "status_order": 1, "amount": 1,
    "created_at": 1, "quoted_at": 1, "customer": 1, "sale_rep": 1, "_id": 0,
}
LEAD_FIELDS = {
    "zohoLeadId": 1, "firstName": 1, "lastName": 1, "company": 1, "status": 1,
    "source": 1, "description": 1, "createdAt": 1, "lastActivityAt": 1,
    "leadDesk": 1, "_id": 0,
}

# never handed to the model: Mongo internals, ERP tokens and uploaded images
PRIVATE_FIELDS = {
    "_id", "zoho_id", "zohoLeadId", "email", "phone", "phone_no",
    "complete_address", "image", "token",
}

RECORD_COLLECTIONS = {
    "payments": "payments_flat",
    "invoices": "orders_flat",
    "orders": "orders_flat",
    "quotes": "quotes_flat",
    "leads": "leads",
}


class RowsArgs(ToolArgs):
    # a model asking for 500 rows gets the safe maximum, not a validation error
    limit: int = Field(default=DEFAULT_TOOL_ROWS, ge=1)

    @field_validator("limit")
    @classmethod
    def cap_rows(cls, value: int) -> int:
        return min(value, MAX_TOOL_ROWS)


class DateFilterArgs(RowsArgs):
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def dates_are_ordered(self):
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValueError("date_to must be on or after date_from")
        return self


class PaymentArgs(DateFilterArgs):
    customer: str | None = Field(default=None, max_length=120)
    min_amount: float | None = None
    max_amount: float | None = None
    method: str | None = Field(default=None, max_length=40)


class InvoiceArgs(DateFilterArgs):
    status: str | None = Field(default=None, max_length=40)
    customer: str | None = Field(default=None, max_length=120)
    overdue_only: bool = False


class QuoteArgs(DateFilterArgs):
    status: str | None = Field(default=None, max_length=40)
    customer: str | None = Field(default=None, max_length=120)
    stale_only: bool = False


class OrderArgs(DateFilterArgs):
    status: str | None = Field(default=None, max_length=40)
    customer: str | None = Field(default=None, max_length=120)
    stalled_only: bool = False


class LeadArgs(RowsArgs):
    status: str | None = Field(default=None, max_length=40)
    stuck_only: bool = False
    search: str | None = Field(default=None, max_length=120)


class CustomerProfileArgs(ToolArgs):
    name_or_id: str = Field(min_length=1, max_length=120)


class RecordArgs(ToolArgs):
    collection: str = Field(pattern="^(payments|invoices|orders|quotes|leads)$")
    id: str = Field(min_length=1, max_length=40)


class SearchArgs(RowsArgs):
    query: str = Field(min_length=1, max_length=120)
    collections: list[str] | None = Field(default=None, max_length=5)


class BriefingArgs(RowsArgs):
    customer: str | None = Field(default=None, max_length=120)


def _customer_id_of(row: dict) -> Any:
    customer = row.get("customer") or {}
    return customer.get("id", row.get("customer_id"))


def _customer_name_of(row: dict) -> str:
    return str((row.get("customer") or {}).get("company_name") or "").strip()


def _in_window(value: Any, date_from: date | None, date_to: date | None) -> bool:
    """Python-side date filter (inclusive), ERP business day."""
    if date_from is None and date_to is None:
        return True
    return _in_date_range(value, date_from or date.min, date_to or date.max)


def _text_matches(row: dict, needle: str) -> bool:
    haystack = json.dumps(_strip_private(row), default=str).casefold()
    return needle.casefold() in haystack


def _strip_private(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_private(v) for k, v in value.items() if k not in PRIVATE_FIELDS}
    if isinstance(value, list):
        return [_strip_private(item) for item in value]
    return value


def _days_overdue(invoice_age_days: int) -> int:
    return max(0, int(invoice_age_days or 0) - settings.OVERDUE_INVOICE_DAYS)


async def _resolve_customer(database: Any, value: str | None) -> tuple[Any, str, str]:
    """(customer_id, label, note) for a `customer` argument that may be an ID or a name."""
    if not value:
        return None, "", ""
    text = value.strip()
    if re.fullmatch(r"\d+", text):
        return int(text), text, ""
    matches = (await find_customer(text, database=database)).get("matches") or []
    if not matches:
        return "none", text, f"No customer named {text!r} was found in the business data."
    best = matches[0]
    return best.get("id"), str(best.get("company_name") or text), ""


def _payment_row(row: dict) -> dict:
    invoices = [
        {
            "invoice_number": (detail.get("invoice") or {}).get("inv_ref"),
            "invoice_id": detail.get("invoice_id"),
            "amount": processors.to_number(detail.get("amount")),
        }
        for detail in (row.get("payment_details") or [])
        if isinstance(detail, dict)
    ]
    return {
        "payment_id": row.get("id"),
        "amount": processors.to_number(row.get("amount")),
        "amount_used": processors.to_number(row.get("total_payment_used")),
        "payment_date": row.get("payment_date"),
        "received_at": row.get("received_at"),
        "reference": row.get("payment_ref"),
        "customer_id": _customer_id_of(row),
        "customer_name": _customer_name_of(row) or "Customer not recorded",
        "received_by": str((row.get("users") or {}).get("name") or ""),
        "invoices": invoices,
    }


async def get_payments(
    date_from: date | None = None,
    date_to: date | None = None,
    customer: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    method: str | None = None,
    limit: int = DEFAULT_TOOL_ROWS,
    database: Any = None,
) -> dict:
    """Individual payment records: who paid, how much, when, against which invoice."""
    args = PaymentArgs.model_validate(
        {
            "date_from": date_from, "date_to": date_to, "customer": customer,
            "min_amount": min_amount, "max_amount": max_amount, "method": method,
            "limit": limit,
        }
    )
    db = _database(database)
    wanted_id, wanted_label, note = await _resolve_customer(db, args.customer)
    rows = await db.payments_flat.find({}, PAYMENT_FIELDS).sort("received_at", -1).to_list(200_000)

    matched: list[dict] = []
    if wanted_id != "none":
        for row in rows:
            amount = processors.to_number(row.get("amount"))
            if not _in_window(row.get("received_at"), args.date_from, args.date_to):
                continue
            if args.min_amount is not None and amount < args.min_amount:
                continue
            if args.max_amount is not None and amount > args.max_amount:
                continue
            if wanted_id is not None and not _same_id(_customer_id_of(row), wanted_id):
                continue
            matched.append(row)

    matched.sort(key=lambda row: str(row.get("received_at") or ""), reverse=True)
    result = {
        "total_count": len(matched),
        "returned": len(matched[: args.limit]),
        "sum_amount": round(sum(processors.to_number(row.get("amount")) for row in matched), 2),
        "items": [_payment_row(row) for row in matched[: args.limit]],
        "filters_applied": {
            "date_from": args.date_from.isoformat() if args.date_from else None,
            "date_to": args.date_to.isoformat() if args.date_to else None,
            "customer": wanted_label or None,
            "min_amount": args.min_amount,
            "max_amount": args.max_amount,
        },
    }
    if note:
        result["note"] = note
    if args.method:
        result["method_filter_not_applied"] = (
            "Payment records do not store a payment method field."
        )
    return _bounded(result)


def _invoice_row(row: dict, now: datetime) -> dict:
    total = processors.to_number(row.get("total_amount"))
    paid = processors.to_number(row.get("amount_paid"))
    age = processors.age_days(processors.parse_date(row.get("invoice_date")), now) or 0.0
    days_age = int(age)
    return {
        "invoice_id": row.get("id"),
        "invoice_number": row.get("inv_ref"),
        "customer_id": _customer_id_of(row),
        "customer_name": _customer_name_of(row) or "Customer not recorded",
        "total_amount": total,
        "amount_paid": paid,
        "balance": round(total - paid, 2),
        "payment_status": row.get("payment_status_label") or "No payment recorded",
        "order_status": row.get("status_label"),
        "invoice_date": row.get("invoice_date"),
        "due_date": row.get("due_date"),
        "days_invoice_age": days_age,
        "overdue": bool(age > settings.OVERDUE_INVOICE_DAYS),
        "days_overdue": _days_overdue(days_age),
        "sale_rep": str((processors.sale_rep_ref(row) or {}).get("name") or ""),
    }


async def get_invoices(
    status: str | None = None,
    customer: str | None = None,
    overdue_only: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = DEFAULT_TOOL_ROWS,
    database: Any = None,
) -> dict:
    """Invoice (order) rows with balance, due date and days overdue computed in Python."""
    args = InvoiceArgs.model_validate(
        {
            "status": status, "customer": customer, "overdue_only": overdue_only,
            "date_from": date_from, "date_to": date_to, "limit": limit,
        }
    )
    db = _database(database)
    wanted_id, wanted_label, note = await _resolve_customer(db, args.customer)
    now = datetime.now(timezone.utc)
    rows = await db.orders_flat.find({}, INVOICE_FIELDS).sort("invoice_date", -1).to_list(200_000)

    matched: list[dict] = []
    if wanted_id != "none":
        for row in rows:
            total = processors.to_number(row.get("total_amount"))
            balance = round(total - processors.to_number(row.get("amount_paid")), 2)
            if not _in_window(row.get("invoice_date"), args.date_from, args.date_to):
                continue
            if args.status and args.status.casefold() not in str(
                row.get("payment_status_label") or row.get("status_label") or ""
            ).casefold():
                continue
            if balance <= 0:
                continue
            age = processors.age_days(processors.parse_date(row.get("invoice_date")), now) or 0.0
            if args.overdue_only and age <= settings.OVERDUE_INVOICE_DAYS:
                continue
            matched.append(_invoice_row(row, now))

    matched.sort(key=lambda item: (item["balance"], item["days_invoice_age"]), reverse=True)
    total_balance = round(sum(item["balance"] for item in matched), 2)
    result = {
        "total_count": len(matched),
        "returned": len(matched[: args.limit]),
        "sum_balance": total_balance,
        "overdue_threshold_days": settings.OVERDUE_INVOICE_DAYS,
        "items": matched[: args.limit],
        "filters_applied": {
            "status": args.status, "customer": wanted_label or None,
            "overdue_only": args.overdue_only,
            "date_from": args.date_from.isoformat() if args.date_from else None,
            "date_to": args.date_to.isoformat() if args.date_to else None,
        },
    }
    if note:
        result["note"] = note
    return _bounded(result)


async def get_quotes(
    status: str | None = None,
    customer: str | None = None,
    stale_only: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = DEFAULT_TOOL_ROWS,
    database: Any = None,
) -> dict:
    """Quote rows; `stale_only` uses the same rules as the dashboard's open quotes."""
    args = QuoteArgs.model_validate(
        {
            "status": status, "customer": customer, "stale_only": stale_only,
            "date_from": date_from, "date_to": date_to, "limit": limit,
        }
    )
    db = _database(database)
    wanted_id, wanted_label, note = await _resolve_customer(db, args.customer)
    now = datetime.now(timezone.utc)
    rows = await db.quotes_flat.find({}, QUOTE_FIELDS).sort("created_at", -1).to_list(200_000)

    stale_ids: set[Any] = set()
    if args.stale_only:
        orders = await db.orders_flat.find({}, {"quote_id": 1, "_id": 0}).to_list(200_000)
        converted = {row.get("quote_id") for row in orders if row.get("quote_id") is not None}
        stale_ids = {
            row["id"] for row in processors.stale_quotes(rows, now, order_quote_ids=converted)
        }

    matched: list[dict] = []
    if wanted_id != "none":
        for row in rows:
            if not _in_window(row.get("created_at"), args.date_from, args.date_to):
                continue
            if args.status and args.status.casefold() not in str(row.get("status") or "").casefold():
                continue
            if args.stale_only and row.get("id") not in stale_ids:
                continue
            if wanted_id is not None and not _same_id(_customer_id_of(row), wanted_id):
                continue
            age = processors.age_days(processors.parse_date(row.get("created_at")), now) or 0.0
            matched.append(
                {
                    "quote_id": row.get("id"),
                    "ref": row.get("ref"),
                    "customer_id": _customer_id_of(row),
                    "customer_name": _customer_name_of(row) or "Customer not recorded",
                    "amount": processors.to_number(row.get("amount")),
                    "status": row.get("status"),
                    "created_at": row.get("created_at"),
                    "age_days": round(age, 1),
                    "days_stale": int(age),
                    "sale_rep": str((processors.sale_rep_ref(row) or {}).get("name") or ""),
                }
            )

    matched.sort(key=lambda item: item["amount"], reverse=True)
    result = {
        "total_count": len(matched),
        "returned": len(matched[: args.limit]),
        "sum_amount": round(sum(item["amount"] for item in matched), 2),
        "items": matched[: args.limit],
        "filters_applied": {
            "status": args.status, "customer": wanted_label or None, "stale_only": args.stale_only,
            "date_from": args.date_from.isoformat() if args.date_from else None,
            "date_to": args.date_to.isoformat() if args.date_to else None,
        },
    }
    if note:
        result["note"] = note
    return _bounded(result)


async def get_orders(
    status: str | None = None,
    customer: str | None = None,
    stalled_only: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = DEFAULT_TOOL_ROWS,
    database: Any = None,
) -> dict:
    """Order rows. `stalled_only` is the ERP's unreliable signal (no status timestamps)."""
    args = OrderArgs.model_validate(
        {
            "status": status, "customer": customer, "stalled_only": stalled_only,
            "date_from": date_from, "date_to": date_to, "limit": limit,
        }
    )
    db = _database(database)
    wanted_id, wanted_label, note = await _resolve_customer(db, args.customer)
    now = datetime.now(timezone.utc)
    rows = await db.orders_flat.find({}, INVOICE_FIELDS).sort("invoice_date", -1).to_list(200_000)
    stalled_ids: set[Any] = set()
    if args.stalled_only:
        stalled_ids = {row["id"] for row in processors.stalled_orders(rows, now)}

    matched: list[dict] = []
    if wanted_id != "none":
        for row in rows:
            if not _in_window(row.get("invoice_date"), args.date_from, args.date_to):
                continue
            if args.status and args.status.casefold() not in str(row.get("status_label") or "").casefold():
                continue
            if args.stalled_only and row.get("id") not in stalled_ids:
                continue
            if wanted_id is not None and not _same_id(_customer_id_of(row), wanted_id):
                continue
            matched.append(_invoice_row(row, now))

    matched.sort(key=lambda item: (item["days_invoice_age"], item["total_amount"]), reverse=True)
    result = {
        "total_count": len(matched),
        "returned": len(matched[: args.limit]),
        "sum_amount": round(sum(item["total_amount"] for item in matched), 2),
        "items": matched[: args.limit],
        "stalled_signal_reliable": False,
        "filters_applied": {
            "status": args.status, "customer": wanted_label or None, "stalled_only": args.stalled_only,
            "date_from": args.date_from.isoformat() if args.date_from else None,
            "date_to": args.date_to.isoformat() if args.date_to else None,
        },
    }
    if note:
        result["note"] = note
    return _bounded(result)


async def get_leads(
    status: str | None = None,
    stuck_only: bool = False,
    search: str | None = None,
    limit: int = DEFAULT_TOOL_ROWS,
    database: Any = None,
) -> dict:
    """Lead rows. Stuck leads reuse processors.stuck_leads; no contacts are returned."""
    args = LeadArgs.model_validate(
        {"status": status, "stuck_only": stuck_only, "search": search, "limit": limit}
    )
    db = _database(database)
    now = datetime.now(timezone.utc)
    rows = await db.leads.find({}, LEAD_FIELDS).to_list(50_000)
    stuck_ids: set[str] = set()
    if args.stuck_only:
        stuck_ids = {row["lead_id"] for row in processors.stuck_leads(rows, now)}

    matched: list[dict] = []
    for row in rows:
        lead_id = str(row.get("zohoLeadId") or "")
        if args.stuck_only and lead_id not in stuck_ids:
            continue
        if args.status and args.status.casefold() not in str(row.get("status") or "").casefold():
            continue
        parsed = processors.parse_lead_description(row.get("description"))
        item = {
            "lead_id": lead_id,
            "contact_name": " ".join(
                part for part in (str(row.get("firstName") or ""), str(row.get("lastName") or "")) if part
            ) or None,
            "company": str(row.get("company") or "") or None,
            "status": str(row.get("status") or ""),
            "source": str(row.get("source") or "") or None,
            "created_at": processors.iso_or_none(row.get("createdAt")),
            "last_activity_at": processors.iso_or_none(row.get("lastActivityAt")),
            "days_since_activity": processors.age_days(
                processors.parse_date(row.get("lastActivityAt"))
                or processors.parse_date(row.get("createdAt")),
                now,
            ),
            "product": parsed.get("product") or parsed.get("type"),
            "quantity": parsed.get("quantity"),
            "size": parsed.get("size"),
            "material": parsed.get("material"),
            "finish": parsed.get("finish"),
            "missing": processors.missing_lead_fields(parsed),
            "stuck": lead_id in stuck_ids if args.stuck_only else None,
        }
        if item["days_since_activity"] is not None:
            item["days_since_activity"] = round(item["days_since_activity"], 1)
        if args.search and not _text_matches({"item": item}, args.search):
            continue
        matched.append(item)

    matched.sort(key=lambda item: item.get("days_since_activity") if item.get("days_since_activity") is not None else 9e9)
    return _bounded(
        {
            "total_count": len(matched),
            "returned": len(matched[: args.limit]),
            "stuck_lead_window_days": settings.LEAD_ACTIVE_DAYS,
            "items": matched[: args.limit],
            "filters_applied": {
                "status": args.status, "stuck_only": args.stuck_only, "search": args.search,
            },
        }
    )


async def get_customer_profile(name_or_id: str, database: Any = None) -> dict:
    """One customer's 360 view: contact, quotes, orders, invoices, payments, totals."""
    args = CustomerProfileArgs.model_validate({"name_or_id": name_or_id})
    db = _database(database)
    customer_id, label, note = await _resolve_customer(db, args.name_or_id)
    if customer_id in (None, "none"):
        return {
            "customer": None,
            "note": note or f"No customer matching {args.name_or_id!r} was found.",
        }

    context = await get_customer_context(int(customer_id), database=db)
    unpaid = await get_invoices(customer=str(customer_id), limit=MAX_TOOL_ROWS, database=db)
    payments = await get_payments(customer=str(customer_id), limit=MAX_TOOL_ROWS, database=db)
    quotes = await get_quotes(customer=str(customer_id), limit=MAX_TOOL_ROWS, database=db)
    contact_row = await db.payments_flat.find(
        {"customer.id": int(customer_id)}, {"customer.email": 1, "customer.phone_no": 1, "_id": 0}
    ).to_list(1)
    contact = contact_row[0].get("customer") if contact_row else {}
    return _bounded(
        {
            "customer_id": customer_id,
            "company_name": context.get("company_name") or label,
            "contact": {
                "email": (contact or {}).get("email"),
                "phone": (contact or {}).get("phone_no"),
            },
            "quotes": context.get("quotes"),
            "orders": context.get("orders"),
            "payments": context.get("payments"),
            "unpaid_invoices": {
                "total_count": unpaid["total_count"],
                "sum_balance": unpaid["sum_balance"],
                "items": unpaid["items"],
            },
            "recent_payments": {
                "total_count": payments["total_count"],
                "sum_amount": payments["sum_amount"],
                "items": payments["items"],
            },
            "recent_quotes": {"total_count": quotes["total_count"], "items": quotes["items"]},
            "notes": context.get("notes") or [],
        }
    )


async def get_record(collection: str, id: str, database: Any = None) -> dict:
    """One record by its business id, from a whitelisted read-only view."""
    args = RecordArgs.model_validate({"collection": collection, "id": id})
    db = _database(database)
    name = RECORD_COLLECTIONS[args.collection]
    key = "zohoLeadId" if name == "leads" else "id"
    value: Any = args.id.strip()
    if key == "id" and re.fullmatch(r"\d+", value):
        value = int(value)
    rows = await db[name].find({key: value}, {"_id": 0}).to_list(1)
    if not rows:
        return {
            "collection": args.collection,
            "id": args.id,
            "found": False,
            "note": f"No {args.collection} record with id {args.id} exists in the business data.",
        }
    return {
        "collection": args.collection,
        "id": args.id,
        "found": True,
        "record": _strip_private(rows[0]),
    }


async def search_records(
    query: str,
    collections: list[str] | None = None,
    limit: int = DEFAULT_TOOL_ROWS,
    database: Any = None,
) -> dict:
    """Substring search over the business records the agent is allowed to read."""
    wanted = collections or list(RECORD_COLLECTIONS)
    args = SearchArgs.model_validate({"query": query, "collections": wanted, "limit": limit})
    db = _database(database)
    needle = args.query.strip()
    per_collection: dict[str, list[dict]] = {}
    total = 0
    for name in args.collections:
        rows = await getattr(db, RECORD_COLLECTIONS[name]).find({}, {"_id": 0}).to_list(50_000)
        hits = [_strip_private(row) for row in rows if _text_matches(row, needle)]
        total += len(hits)
        per_collection[name] = hits[: args.limit]
    return _bounded(
        {
            "query": needle,
            "collections_searched": args.collections,
            "total_count": total,
            "results": {name: rows for name, rows in per_collection.items() if rows},
        }
    )


async def describe_data(database: Any = None) -> dict:
    """What the tools can actually see: collections, fields and date ranges."""
    EmptyArgs.model_validate({})
    db = _database(database)

    def collection(name: str):
        try:
            return db[name]
        except TypeError:
            return getattr(db, name)

    described: dict[str, Any] = {}
    for name, (source, date_field) in (
        ("payments", ("payments_flat", "received_at")),
        ("invoices", ("orders_flat", "invoice_date")),
        ("orders", ("orders_flat", "invoice_date")),
        ("quotes", ("quotes_flat", "created_at")),
        ("leads", ("leads", "lastActivityAt")),
        ("agent_runs", ("agent_runs", "started_at")),
    ):
        rows = await collection(source).find({}, {"_id": 0}).to_list(1)
        described[name] = {
            "count": await collection(source).count_documents({}),
            "fields": sorted(key for key in (rows[0].keys() if rows else []) if key != "_id"),
        }
        if date_field:
            bounds: list[Any] = []
            for direction in (1, -1):
                edge = await collection(source).find({}, {date_field: 1, "_id": 0}).sort(
                    date_field, direction
                ).to_list(1)
                bounds.append(processors.iso_or_none((edge or [{}])[0].get(date_field)))
            described[name]["date_range"] = {date_field: bounds}

    described["not_available"] = {
        "orders": list(processors.ORDER_MISSING_FIELDS),
        "payments": ["payment method"],
    }
    described["business_timezone"] = str(processors.ERP_TZ)
    return _bounded(described)


async def get_agent_briefing(
    customer: str | None = None,
    limit: int = DEFAULT_TOOL_ROWS,
    database: Any = None,
) -> dict:
    """The latest CEO brief and the draft recommendations behind it."""
    args = BriefingArgs.model_validate({"customer": customer, "limit": limit})
    db = _database(database)
    rows = await db.agent_runs.find(
        {"finished_epoch": {"$exists": True}}, {"_id": 0}
    ).sort("finished_epoch", -1).to_list(1)
    if not rows:
        return {"run_id": None, "note": "No agent run has been saved yet."}
    run = rows[0]
    master = run.get("master") or {}
    items: dict[str, list[dict]] = {}
    for kind in ("negotiation", "collections", "lead_followup"):
        rows_out = run.get(kind) or []
        if args.customer:
            needle = args.customer.strip().casefold()
            rows_out = [
                row for row in rows_out
                if needle in str(row.get("customer_name") or row.get("company") or row.get("contact_name") or "").casefold()
            ]
        items[kind] = [_strip_private(row) for row in rows_out[: args.limit]]
    return _bounded(
        {
            "run_id": run.get("run_id"),
            "started_at": run.get("started_at"),
            "counts": run.get("counts"),
            "briefing": master.get("bullets", []),
            "priorities": master.get("priorities", []),
            "conflicts_resolved": master.get("conflicts_resolved", []),
            "drafts": items,
            "drafts_are_text_only": True,
        }
    )


CHAT_TOOLS = [
    ChatTool("get_latest_briefing", "Get the latest saved Master brief and its priorities.", EmptyArgs, get_latest_briefing),
    ChatTool(
        "get_dashboard_metrics",
        "Get the same pre-computed dashboard metrics the UI shows: revenue today (ERP business day), month to date, last 7 days, payments count today, open quotes, unpaid and overdue totals. Use this for 'today's revenue', 'this month', 'last 7 days' and any dashboard KPI question instead of passing a local date to get_revenue.",
        EmptyArgs,
        get_dashboard_metrics,
    ),
    ChatTool("get_signals_summary", "Get Python-computed counts and totals for stale quotes, unpaid invoices, and overdue invoices. Also use it to check whether artwork, shipping, order notes, or line-item fields are available; it lists order fields missing from the source data.", EmptyArgs, get_signals_summary),
    ChatTool("find_customer", "Fuzzy-search company names in quotes, orders, and payments. Returns up to five customer IDs and names.", CustomerSearchArgs, find_customer),
    ChatTool("get_customer_context", "Get aggregate quote, order, and payment context for one customer ID, plus the total unpaid_invoice_count and up to 25 unpaid invoice references, ages, invoice and due dates, overdue flags, and payment statuses.", CustomerContextArgs, get_customer_context),
    ChatTool("get_revenue", "Sum payments and count payment records in an inclusive received-date range; may filter by rep ID.", RevenueArgs, get_revenue),
    ChatTool("get_top_unpaid", "Get the customer with the largest total unpaid balance and the largest unpaid invoice rows (maximum ten).", TopItemsArgs, get_top_unpaid),
    ChatTool("get_top_open_quotes", "Get the largest unconverted open quotes, with a maximum of ten rows.", TopItemsArgs, get_top_open_quotes),
    ChatTool("get_rep_summary", "Get Python-computed quote, order, and payment counts and totals for one rep and date range.", RepSummaryArgs, get_rep_summary),
    ChatTool(
        "get_stuck_leads",
        "Get leads that are stuck before becoming an order: status New or Attempted to Contact, recent activity, a description present, not flagged by the lead desk. Returns Python-parsed product/quantity/size/material/finish, the missing-field list and artwork state (Provided/Missing/Not stated). No email or phone numbers are returned.",
        StuckLeadsArgs,
        get_stuck_leads,
    ),
    ChatTool(
        "get_payments",
        "List individual payment records (who paid): customer name and ID, amount, payment date, received timestamp, payment reference and the invoice numbers the payment was applied to. Filters: received-date range, customer name or ID, min/max amount. Answer 'who paid today' with this tool.",
        PaymentArgs,
        get_payments,
    ),
    ChatTool(
        "get_invoices",
        "List invoices with balance, due date, days overdue and customer. Filters: payment status text, customer, overdue_only, invoice-date range. Up to 50 rows with the full total_count.",
        InvoiceArgs,
        get_invoices,
    ),
    ChatTool(
        "get_quotes",
        "List quotes with amount, status, age and customer. Filters: status text, customer, stale_only (the same open-quote rules the dashboard uses), created-date range.",
        QuoteArgs,
        get_quotes,
    ),
    ChatTool(
        "get_orders",
        "List orders with amounts, status and customer. Filters: order status text, customer, stalled_only (this signal is unreliable - the ERP stores no order status timestamps), invoice-date range.",
        OrderArgs,
        get_orders,
    ),
    ChatTool(
        "get_leads",
        "List lead records with parsed product/quantity/size/material/finish and the missing-field list. Filters: status text, stuck_only, search text. No contact details are returned.",
        LeadArgs,
        get_leads,
    ),
    ChatTool(
        "get_customer_profile",
        "Full 360 view for one customer (name or ID): contact, quote/order/payment totals, unpaid invoices and their balances, recent payments with references and invoice numbers.",
        CustomerProfileArgs,
        get_customer_profile,
    ),
    ChatTool(
        "get_record",
        "Fetch one record by id from payments, invoices, orders, quotes or leads when a specific number is needed.",
        RecordArgs,
        get_record,
    ),
    ChatTool(
        "search_records",
        "Substring search across payments, invoices, orders, quotes and leads for a name, reference or amount text. Returns matched rows and how many there are.",
        SearchArgs,
        search_records,
    ),
    ChatTool(
        "describe_data",
        "Describe what data exists: collections, row counts, their field names, date ranges, and the fields that are genuinely not stored. Call this before saying something is unavailable.",
        EmptyArgs,
        describe_data,
    ),
    ChatTool(
        "get_agent_briefing",
        "Get the latest saved CEO brief plus the agent drafts behind it (negotiation, collections, lead follow-up), optionally filtered by customer, so a question like 'what did the collections agent suggest for X?' can be answered. Drafts are stored text only.",
        BriefingArgs,
        get_agent_briefing,
    ),
]

CHAT_TOOL_MAP = {tool.name: tool for tool in CHAT_TOOLS}


async def invoke_tool(name: str, raw_args: dict, database: Any = None, registry: dict | None = None) -> dict:
    tools = registry or CHAT_TOOL_MAP
    tool = tools[name]
    validated = tool.args_model.model_validate(raw_args or {})
    result = await tool.function(**validated.model_dump(), database=database)
    if not isinstance(result, dict):
        result = {"result": result}
    return _bounded(format_chat_result(result))
