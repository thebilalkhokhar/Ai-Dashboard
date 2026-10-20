"""Read-only Mongo tools for the CEO chat agent.

All arithmetic stays in Python. These tools never call the ERP or write to the
business collections; chat history and request logs are managed by chat.py.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Callable, Type

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import db as app_db
from app.services import metrics, processors

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


def _bounded(value: dict, max_chars: int = 10_500) -> dict:
    """Keep a single tool response below roughly 3,000 tokens."""
    clean = _serializable(value)
    encoded = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= max_chars:
        return clean
    return {"truncated": True, "preview": encoded[: max_chars - 80]}


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
    context.pop("contact", None)
    context["notes"] = ["Lead outcomes are not included in read-only chat context."]
    return _bounded(context)


async def get_dashboard_metrics(
    database: Any = None, now: datetime | None = None
) -> dict:
    """The exact metrics the Next.js dashboard renders (same Python code).

    Uses metrics.build_snapshot, whose day/month boundaries are the ERP's
    US Central business day, so "today's revenue" here can never disagree with
    the dashboard tile.
    """
    EmptyArgs.model_validate({})
    snapshot = await metrics.build_snapshot(database=_database(database), now=now)
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
        "amount_paid", "balance", "overdue", "invoice_date", "customer", "sale_rep",
    )
    return {
        "total_count": len(rows),
        "largest_owing_customer": largest_customer_debt,
        "items": [{key: row.get(key) for key in fields} for row in rows[: args.limit]],
        "order_fields_not_available": list(processors.ORDER_MISSING_FIELDS),
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
    ChatTool("get_customer_context", "Get aggregate quote, order, and payment context for one customer ID.", CustomerContextArgs, get_customer_context),
    ChatTool("get_revenue", "Sum payments and count payment records in an inclusive received-date range; may filter by rep ID.", RevenueArgs, get_revenue),
    ChatTool("get_top_unpaid", "Get the customer with the largest total unpaid balance and the largest unpaid invoice rows (maximum ten).", TopItemsArgs, get_top_unpaid),
    ChatTool("get_top_open_quotes", "Get the largest unconverted open quotes, with a maximum of ten rows.", TopItemsArgs, get_top_open_quotes),
    ChatTool("get_rep_summary", "Get Python-computed quote, order, and payment counts and totals for one rep and date range.", RepSummaryArgs, get_rep_summary),
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
