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

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app import db as app_db
from app.config import settings
from app.services import customer360 as c360_service
from app.services import customer360_index as c360_index
from app.services import metrics, periods, processors
from app.services.customer360_client import Customer360Client, Customer360Error

log = logging.getLogger("chat_tools")

_CURRENCY_TEXT = re.compile(r"\$\s*(-?[\d,]+(?:\.\d+)?)")


def format_money(value: Any) -> str:
    amount = round(processors.to_number(value), 2)
    if amount == int(amount):
        return f"${int(amount):,}"
    return f"${amount:,.2f}"


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return bool(re.fullmatch(r"-?[\d,]+(?:\.\d+)?", value.strip()))
    return False


def format_chat_result(value: Any, key: str = "") -> Any:
    """Format money and counts before a tool result is sent to the model."""
    lowered = key.casefold()
    is_money = "amount" in lowered or "balance" in lowered or lowered.endswith("_value") or lowered == "total_payments"
    is_count = lowered == "count" or lowered.endswith("_count") or lowered == "total_count"

    if isinstance(value, dict):
        return {name: format_chat_result(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [format_chat_result(item, key) for item in value]
    # A key named "..._amount" can still hold prose ("sum_amount_covers" says
    # what the total spans). Formatting that as money turned the sentence into
    # "$0", a figure the model would then have repeated - so only numbers are
    # money-formatted, whatever their key looks like.
    if is_money and _is_number(value):
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


class PeriodMixin(BaseModel):
    """One shared `period` argument, resolved server-side into the date pair.

    The model chooses which *name* the user meant; the server decides what that
    name spans, on the ERP business calendar. That keeps "this week" identical
    for every tool and every turn instead of a range the model invents from its
    own clock, and it means the resolved dates are backend-computed facts the
    numeric guard will accept.

    Explicit `date_from`/`date_to` always win, so a follow-up continuing a
    recorded range is never redirected. Passing both a name and a date is
    refused rather than silently preferring one.
    """

    # `exclude` keeps it out of model_dump(), which invoke_tool splats straight
    # into the tool function - none of which take a period argument, because the
    # validator has already turned the name into the date pair they do take.
    # It still appears in the JSON schema, so the model can use it.
    period: str | None = Field(
        default=None,
        max_length=32,
        exclude=True,
        description=periods.PERIOD_HINT + " Prefer this over guessing dates; omit it if the user gave an exact range.",
    )

    @model_validator(mode="after")
    def period_resolves_to_dates(self):
        raw = (self.period or "").strip()
        if not raw:
            return self
        if self.date_from is not None or self.date_to is not None:
            raise ValueError("period and an explicit date_from/date_to cannot be combined - use one")
        try:
            # Relative dates are read on the user's calendar (Asia/Karachi):
            # when the user in Lahore says "today" at 7 AM, the ERP's US
            # business day is still yesterday, and answering with the ERP
            # date while calling it "today" mislabels the data.
            start, end = periods.resolve_period(
                raw, today=datetime.now(processors.USER_TZ).date()
            )
        except KeyError:
            raise ValueError(
                f"unknown period {raw!r}; choose one of {', '.join(periods.PERIOD_NAMES)}"
            )
        self.date_from, self.date_to = start, end
        return self


class DateRangeArgs(PeriodMixin):
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def date_range_is_ordered(self):
        if self.date_from is None or self.date_to is None:
            raise ValueError("date_from and date_to are required unless a named period is given")
        if self.date_to < self.date_from:
            raise ValueError("date_to must be on or after date_from")
        return self


class RevenueArgs(DateRangeArgs):
    rep_id: int | None = Field(default=None, ge=1)


class TopItemsArgs(ToolArgs):
    limit: int = Field(default=5, ge=1, le=10)


class StuckLeadsArgs(ToolArgs):
    days: int | None = Field(default=None, ge=1, le=90)
    limit: int | None = Field(default=None, ge=1, le=50)
    offset: int = Field(
        default=0,
        ge=0,
        description="How many matching rows to skip before this page. To fetch the rest of a result set, "
        "re-call the same tool with the same filters and the previous page's next_offset.",
    )


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


def _collection(database: Any, name: str) -> Any:
    """A collection, or None when this database genuinely does not have it."""
    try:
        return database[name]
    except (TypeError, KeyError, AttributeError):
        return getattr(database, name, None)


def _serializable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


ROW_KEYS = ("items", "results", "drafts", "records", "priorities", "briefing")

# The three specialist drafts one agent run stores, in the order they are paged.
DRAFT_KINDS = ("negotiation", "collections", "lead_followup")


def _page_metadata(offset: int, limit: int, total: int, delivered: int) -> dict:
    """The pagination block one page of rows deserves, computed from what survived.

    ``delivered`` is the number of rows actually in the response, which can be
    smaller than the page that was sliced when the context budget trims rows off
    the end. Deriving every count from it is what keeps ``next_offset`` honest.

    ``all_records_returned`` describes the walk, not the page: it is true once
    nothing remains after it, which is why the counts of every page must be read
    together before claiming the whole set was listed.
    """
    next_offset = offset + delivered
    has_more = next_offset < total
    # A page that starts past the end delivered nothing and covered nothing, so
    # it cannot testify that the whole set was returned - even though there is
    # genuinely nothing left to fetch from that offset. Only a page that begins
    # inside (or exactly at the end of) the result set can close the walk. An
    # empty result set still counts: offset 0 of total 0 covered everything.
    covers_the_set = not has_more and offset <= total
    # "page 1 of 7" is arithmetic over the page contract, and the model may only
    # write numbers the backend computed - so the backend does this dividing too.
    # The divisor is the number of rows a page actually carried (``delivered``),
    # not the size that was asked for: a page the context budget trimmed from 50
    # to 28 really is one of ceil(29/28)=2 pages, and calling it "1 of 1" while a
    # row still remained told the model the walk was over when it was not.
    effective_page = delivered if delivered else (limit or 1)
    pages_total = max(1, -(-total // effective_page)) if total else 1
    page = min(pages_total, (offset // effective_page) + 1) if effective_page else 1
    metadata = {
        "offset": offset,
        "limit": limit,
        "total_count": total,
        "returned": delivered,
        "remaining_count": max(0, total - next_offset),
        "has_more": has_more,
        "next_offset": next_offset if has_more else None,
        "all_records_returned": covers_the_set,
        "page": page,
        "pages_total": pages_total,
    }
    if delivered < limit and has_more:
        # the page came back shorter than asked: say why, and what to ask for
        metadata["page_shorter_than_requested"] = True
        metadata["suggested_limit"] = delivered
    return metadata


def _bounded(value: dict, max_chars: int = 10_500) -> dict:
    """Keep a single tool response below roughly 3,000 tokens.

    A too-large answer loses rows from the end (and says so) rather than losing
    its counts: the totals are computed over every matched row and the model
    must always be able to tell "this is a page" from "this is everything". A
    response that already carries a page is recomputed after trimming, because a
    stale ``next_offset`` would make the caller skip rows it never received.
    """
    clean = _serializable(value)
    if len(json.dumps(clean, ensure_ascii=False, separators=(",", ":"))) <= max_chars:
        return _repage(clean)

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
        trimmed["truncated"] = True
    trimmed.setdefault("truncated", True)
    return _repage(trimmed)


def _repage(result: dict) -> dict:
    """Recompute a page's metadata from the rows that are still in the response."""
    if not isinstance(result.get("items"), list) or "has_more" not in result:
        return result
    for key in ("offset", "limit", "total_count"):
        if not isinstance(result.get(key), int):
            return result
    result.update(
        _page_metadata(
            result["offset"],
            result["limit"],
            result["total_count"],
            len(result["items"]),
        )
    )
    return result


def _paginate(
    result: dict,
    *,
    total: int,
    offset: int,
    limit: int,
    items_key: str = "items",
    delivered: int | None = None,
) -> dict:
    """Attach truthful pagination metadata to one page of record rows.

    The page is sliced by ``offset``/``limit``, but ``_bounded`` may then drop
    trailing rows to keep the response inside the model's context budget. Every
    count below is therefore recomputed from the rows that actually survived, so
    ``next_offset`` always continues where this page stopped - no row is ever
    repeated or skipped, and the model can never claim the full set was fetched
    when it was not.

    ``delivered`` overrides that count for pages whose rows are not stored under
    one flat list key.
    """
    if delivered is None:
        delivered = len(result.get(items_key) or [])
    result.update(_page_metadata(offset, limit, total, delivered))
    return result


def _top_n(result: dict, *, total: int, items_key: str = "items") -> dict:
    """Say plainly whether a ranked answer is the whole set or only its head.

    These tools are deliberately not paged - they answer "top N" - so they carry
    counts but no cursor, and must still never imply the model saw everything.
    """
    delivered = len(result.get(items_key) or [])
    result["total_count"] = total
    result["returned"] = delivered
    result["has_more"] = delivered < total
    result["all_records_returned"] = delivered >= total
    return result


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
    shown = [row for _, row in matches[:5]]
    # the counts are Python's: without them a truthful "5 matches" in an
    # answer is a number no tool returned, and the guard rejects it
    # (live failure Oct 24)
    return {"matches": shown, "match_count": len(matches), "returned": len(shown)}


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
        if _in_user_window(row.get("received_at"), args.date_from, args.date_to)
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
    return _top_n(
        {
            "largest_owing_customer": largest_customer_debt,
            "items": [{key: row.get(key) for key in fields} for row in rows[: args.limit]],
            "order_fields_not_available": list(processors.ORDER_MISSING_FIELDS),
        },
        total=len(rows),
    )


async def get_stuck_leads(
    days: int | None = None,
    limit: int | None = None,
    offset: int = 0,
    database: Any = None,
) -> dict:
    """Leads stuck before becoming an order. Read-only, Python-parsed.

    Same filter as GET /api/leads/stuck and the lead follow-up agent. No
    email or phone numbers are ever returned to the model.

    Paged like the other record tools: this list is exactly the one a
    follow-up ("show next page") must continue, so it carries the full
    page block - when it was a head-only _top_n result it recorded no
    continuation at all, and any other paged call in the same turn
    silently became the recorded query instead (the Oct 23 failure where
    "next page" fetched the unfiltered leads list).
    """
    args = StuckLeadsArgs.model_validate({"days": days, "limit": limit, "offset": offset})
    db = _database(database)
    now = datetime.now(timezone.utc)
    rows = await db.leads.find(processors.LEAD_STATUS_QUERY, processors.LEAD_FIELDS).to_list(50_000)
    stuck = processors.stuck_leads(rows, now, days=args.days)
    artwork_labels = {True: "Provided", False: "Missing", None: "Not stated"}
    page_limit = args.limit or 10
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
        for row in stuck[args.offset : args.offset + page_limit]
    ]
    result = _paginate(
        _bounded(
            {
                "days": args.days or settings.LEAD_ACTIVE_DAYS,
                "items": items,
            }
        ),
        total=len(stuck),
        offset=args.offset,
        limit=page_limit,
    )
    if args.offset and not result["items"]:
        result["note"] = "That offset is past the end of this result set; nothing more to fetch."
    return result


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
    return _top_n(
        {"items": [{key: row.get(key) for key in fields} for row in rows[: args.limit]]},
        total=len(rows),
    )


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
    payments = [r for r in payments if _same_id(_rep_id(r), rep["id"]) and _in_user_window(r.get("received_at"), args.date_from, args.date_to)]
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
# Each one filters in Python over projected rows, sorts them with a stable id
# tiebreaker, slices one `offset`-based page of at most MAX_TOOL_ROWS rows, and
# describes that page with counts computed from the rows it really delivered.
# ---------------------------------------------------------------------------

DEFAULT_TOOL_ROWS = 10
MAX_TOOL_ROWS = 50

PAGING_NOTE = (
    " Paged: the answer gives offset, limit, total_count, returned, remaining_count, has_more,"
    " next_offset and the page position (page, pages_total). To continue the same query, re-call it"
    " with identical filters and offset=next_offset."
    " Never say you listed every record while has_more is true, and never add up rows yourself - report the"
    " counts exactly as the tool returned them."
)

# Confirmed against the live view: no payments_flat row carries any of these,
# so "paid" here can only mean a stored payment row. There is no status to
# filter, no refund or reversal to net off, and no second currency to keep
# apart - the tool must say so rather than let the model imply a filter it
# cannot perform.
PAYMENT_UNAVAILABLE_FIELDS = (
    "payment method",
    "payment status (pending, failed, voided or reversed are not represented)",
    "refund or reversal records (a payment row is never negative)",
    "currency (no row carries a currency field; amounts are presented as USD)",
)

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
    limit: int = Field(
        default=DEFAULT_TOOL_ROWS,
        ge=1,
        description="Rows for this one page (maximum 50). A larger row count may come back truncated; "
        "the response always says how many rows it actually delivered.",
    )
    # records are paged forward with `offset`; the previous page's `next_offset`
    # is the only valid value, so a follow-up can continue without repeating
    # or skipping rows.
    offset: int = Field(
        default=0,
        ge=0,
        description="How many matching rows to skip before this page. To fetch the rest of a result set, "
        "re-call the same tool with the same filters and the previous page's next_offset.",
    )

    @field_validator("limit")
    @classmethod
    def cap_rows(cls, value: int) -> int:
        return min(value, MAX_TOOL_ROWS)


class DateFilterArgs(PeriodMixin, RowsArgs):
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


class Customer360SearchArgs(ToolArgs):
    query: str = Field(min_length=1, max_length=120)


class Customer360DetailArgs(ToolArgs):
    # The CRM's own vocabulary leaks into the model's calls: it passes
    # `type` for record_type and `id` for record_id, in any letter case -
    # the live CRM analysis calls died at argument validation (0 ms,
    # twice, Oct 24). Accept every honest shape; stored values stay
    # canonical.
    record_id: str = Field(
        validation_alias=AliasChoices("record_id", "id"),
        min_length=1,
        max_length=40,
    )
    record_type: str = Field(
        validation_alias=AliasChoices("record_type", "type"),
        pattern="^(Lead|Contact|Customer)$",
    )
    # the user's Oct 24 pattern: the mirror/cache serves first, but a
    # request for the latest data must be able to force a live fetch
    fresh: bool = False

    @field_validator("record_id", mode="before")
    @classmethod
    def _coerce_record_id(cls, value: Any) -> Any:
        return str(value).strip() if isinstance(value, (int, float)) else value

    @field_validator("record_type", mode="before")
    @classmethod
    def _normalize_record_type(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"lead": "Lead", "contact": "Contact", "customer": "Customer"}.get(
                value.strip().lower(), value.strip()
            )
        return value


class Customer360PaymentsArgs(DateRangeArgs):
    customer: str = Field(default="", max_length=120)


class RecordArgs(ToolArgs):
    collection: str = Field(pattern="^(payments|invoices|orders|quotes|leads)$")
    id: str = Field(min_length=1, max_length=40)


class SearchArgs(RowsArgs):
    query: str = Field(min_length=1, max_length=120)
    collections: list[str] | None = Field(default=None, max_length=5)


class BriefingArgs(RowsArgs):
    customer: str | None = Field(default=None, max_length=120)


class CompareArgs(ToolArgs):
    """Compare two stored lead pulls. With no ids the two newest complete,
    time-stamped snapshots are used; pass the ids to compare specific pulls."""

    previous_pull_id: str | None = Field(default=None, max_length=120)
    latest_pull_id: str | None = Field(default=None, max_length=120)


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


def _in_user_window(value: Any, date_from: date | None, date_to: date | None) -> bool:
    """Python-side date filter (inclusive) on the USER's calendar.

    Payment `received_at` values are instants, not business dates: an
    instant at 11:30 PM at the ERP is 9:30 AM the next day in Lahore, and
    it belongs to the user's next day. The instant is converted to
    Asia/Karachi and its calendar date compared - never the raw string
    and never the ERP's day.
    """
    if date_from is None and date_to is None:
        return True
    moment = processors.parse_date(value)
    if moment is None:
        return False
    day = moment.astimezone(processors.USER_TZ).date()
    return (date_from or date.min) <= day <= (date_to or date.max)


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


_MIDNIGHT_RE = re.compile(r"[T ]00:00:00(\.\d+)?($|[+-]\d{2}:?\d{2}|Z)")


def _payment_timestamp(value: Any) -> dict:
    """What a payment's `received_at` actually proves, stated plainly.

    The ERP keeps a separate date-only `payment_date`, and its
    `received_at` for date-recorded payments is that date at midnight in
    the ERP's offset (e.g. 2026-10-22T00:00:00-05:00, identical across
    unrelated payments). A midnight value therefore carries a DATE, not
    an arrival time: converting it to "5:00 AM PKT" would manufacture a
    precision the source never had. Only a value with a real time
    component is an instant, and its PKT rendering is computed here, in
    Python - the model never does timezone arithmetic.
    """
    if value is None:
        return {"timestamp_precision": "missing", "received_date": None}
    moment = processors.parse_date(value)
    if moment is None:
        return {"timestamp_precision": "missing", "received_date": None}
    if isinstance(value, datetime):
        wall = value if value.tzinfo else value.replace(tzinfo=processors.ERP_TZ)
        is_midnight = (wall.hour, wall.minute, wall.second, wall.microsecond) == (0, 0, 0, 0)
    elif isinstance(value, date):
        is_midnight = True
    else:
        text = str(value).strip()
        is_midnight = ("T" not in text and " " not in text) or bool(_MIDNIGHT_RE.search(text))
    pkt = moment.astimezone(processors.USER_TZ)
    if is_midnight:
        return {
            "timestamp_precision": "date",
            "received_date": pkt.date().isoformat(),
        }
    return {
        "timestamp_precision": "instant",
        "received_date": pkt.date().isoformat(),
        "received_time_pkt": pkt.strftime("%b %d, %Y, %I:%M %p PKT"),
    }


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
    stamp = _payment_timestamp(row.get("received_at"))
    if stamp["timestamp_precision"] == "missing" and row.get("payment_date"):
        # no receipt timestamp at all; the payment date is all the
        # source offers, and the precision label says exactly that
        stamp = {**stamp, "received_date": row.get("payment_date")}
    return {
        "payment_id": row.get("id"),
        "amount": processors.to_number(row.get("amount")),
        "amount_used": processors.to_number(row.get("total_payment_used")),
        "payment_date": row.get("payment_date") or stamp.get("received_date"),
        # The raw received_at is only exposed when it is a genuine
        # instant; a date-only midnight is represented by received_date
        # plus the precision contract, never as a fake receipt time.
        **({"received_at": row.get("received_at")} if stamp["timestamp_precision"] == "instant" else {}),
        **stamp,
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
    offset: int = 0,
    database: Any = None,
) -> dict:
    """Individual payment records: who paid, how much, when, against which invoice."""
    args = PaymentArgs.model_validate(
        {
            "date_from": date_from, "date_to": date_to, "customer": customer,
            "min_amount": min_amount, "max_amount": max_amount, "method": method,
            "limit": limit, "offset": offset,
        }
    )
    db = _database(database)
    wanted_id, wanted_label, note = await _resolve_customer(db, args.customer)
    rows = await db.payments_flat.find({}, PAYMENT_FIELDS).sort("received_at", -1).to_list(200_000)

    matched: list[dict] = []
    if wanted_id != "none":
        for row in rows:
            amount = processors.to_number(row.get("amount"))
            if not _in_user_window(row.get("received_at") or row.get("payment_date"), args.date_from, args.date_to):
                continue
            if args.min_amount is not None and amount < args.min_amount:
                continue
            if args.max_amount is not None and amount > args.max_amount:
                continue
            if wanted_id is not None and not _same_id(_customer_id_of(row), wanted_id):
                continue
            matched.append(row)

    # a stable, total order: without the id tiebreaker two rows sharing a
    # timestamp could swap between pages and be served twice or skipped
    matched.sort(
        key=lambda row: (str(row.get("received_at") or ""), str(row.get("id") or "")),
        reverse=True,
    )
    result = {
        # the totals cover every matched row; `items` is one page of them
        "sum_amount": round(sum(processors.to_number(row.get("amount")) for row in matched), 2),
        "sum_amount_covers": (
            "every matched payment record, not only the rows on this page"
        ),
        "items": [_payment_row(row) for row in matched[args.offset : args.offset + args.limit]],
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
    result = _paginate(
        _bounded(result), total=len(matched), offset=args.offset, limit=args.limit
    )
    if any(item["timestamp_precision"] in {"date", "missing"} for item in result["items"]):
        # stated once for the whole page, not repeated on every row
        result["timestamp_note"] = (
            "Exact arrival times are not recorded for the date-only payments "
            "above; received_date is the payment date."
        )
    if args.offset and not result["items"]:
        result["note"] = "That offset is past the end of this result set; nothing more to fetch."
    return result


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
    offset: int = 0,
    database: Any = None,
) -> dict:
    """Invoice (order) rows with balance, due date and days overdue computed in Python."""
    args = InvoiceArgs.model_validate(
        {
            "status": status, "customer": customer, "overdue_only": overdue_only,
            "date_from": date_from, "date_to": date_to, "limit": limit, "offset": offset,
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

    # id tiebreaker keeps the order total, so pages never repeat or skip a row
    matched.sort(
        key=lambda item: (item["balance"], item["days_invoice_age"], str(item["invoice_id"])),
        reverse=True,
    )
    total_balance = round(sum(item["balance"] for item in matched), 2)
    result = {
        "sum_balance": total_balance,
        "overdue_threshold_days": settings.OVERDUE_INVOICE_DAYS,
        "items": matched[args.offset : args.offset + args.limit],
        "filters_applied": {
            "status": args.status, "customer": wanted_label or None,
            "overdue_only": args.overdue_only,
            "date_from": args.date_from.isoformat() if args.date_from else None,
            "date_to": args.date_to.isoformat() if args.date_to else None,
        },
    }
    if note:
        result["note"] = note
    result = _paginate(
        _bounded(result), total=len(matched), offset=args.offset, limit=args.limit
    )
    if args.offset and not result["items"]:
        result["note"] = "That offset is past the end of this result set; nothing more to fetch."
    return result


async def get_quotes(
    status: str | None = None,
    customer: str | None = None,
    stale_only: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = DEFAULT_TOOL_ROWS,
    offset: int = 0,
    database: Any = None,
) -> dict:
    """Quote rows; `stale_only` uses the same rules as the dashboard's open quotes."""
    args = QuoteArgs.model_validate(
        {
            "status": status, "customer": customer, "stale_only": stale_only,
            "date_from": date_from, "date_to": date_to, "limit": limit, "offset": offset,
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

    # quote_id tiebreaker: equal amounts must not shuffle between pages
    matched.sort(key=lambda item: (item["amount"], str(item["quote_id"])), reverse=True)
    result = {
        "sum_amount": round(sum(item["amount"] for item in matched), 2),
        "items": matched[args.offset : args.offset + args.limit],
        "filters_applied": {
            "status": args.status, "customer": wanted_label or None, "stale_only": args.stale_only,
            "date_from": args.date_from.isoformat() if args.date_from else None,
            "date_to": args.date_to.isoformat() if args.date_to else None,
        },
    }
    if note:
        result["note"] = note
    result = _paginate(
        _bounded(result), total=len(matched), offset=args.offset, limit=args.limit
    )
    if args.offset and not result["items"]:
        result["note"] = "That offset is past the end of this result set; nothing more to fetch."
    return result


async def get_orders(
    status: str | None = None,
    customer: str | None = None,
    stalled_only: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = DEFAULT_TOOL_ROWS,
    offset: int = 0,
    database: Any = None,
) -> dict:
    """Order rows. `stalled_only` is the ERP's unreliable signal (no status timestamps)."""
    args = OrderArgs.model_validate(
        {
            "status": status, "customer": customer, "stalled_only": stalled_only,
            "date_from": date_from, "date_to": date_to, "limit": limit, "offset": offset,
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

    # invoice_id tiebreaker keeps equal rows from moving between pages
    matched.sort(
        key=lambda item: (
            item["days_invoice_age"], item["total_amount"], str(item["invoice_id"])
        ),
        reverse=True,
    )
    result = {
        "sum_amount": round(sum(item["total_amount"] for item in matched), 2),
        "items": matched[args.offset : args.offset + args.limit],
        "stalled_signal_reliable": False,
        "filters_applied": {
            "status": args.status, "customer": wanted_label or None, "stalled_only": args.stalled_only,
            "date_from": args.date_from.isoformat() if args.date_from else None,
            "date_to": args.date_to.isoformat() if args.date_to else None,
        },
    }
    if note:
        result["note"] = note
    result = _paginate(
        _bounded(result), total=len(matched), offset=args.offset, limit=args.limit
    )
    if args.offset and not result["items"]:
        result["note"] = "That offset is past the end of this result set; nothing more to fetch."
    return result


async def get_leads(
    status: str | None = None,
    stuck_only: bool = False,
    search: str | None = None,
    limit: int = DEFAULT_TOOL_ROWS,
    offset: int = 0,
    database: Any = None,
) -> dict:
    """Lead rows. Stuck leads reuse processors.stuck_leads; no contacts are returned."""
    args = LeadArgs.model_validate(
        {"status": status, "stuck_only": stuck_only, "search": search, "limit": limit, "offset": offset}
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

    # lead_id tiebreaker keeps equal ages from moving between pages
    matched.sort(
        key=lambda item: (
            item["days_since_activity"] if item["days_since_activity"] is not None else 9e9,
            str(item["lead_id"]),
        )
    )
    result = {
        "stuck_lead_window_days": settings.LEAD_ACTIVE_DAYS,
        "items": matched[args.offset : args.offset + args.limit],
        "filters_applied": {
            "status": args.status, "stuck_only": args.stuck_only, "search": args.search,
        },
    }
    result = _paginate(
        _bounded(result), total=len(matched), offset=args.offset, limit=args.limit
    )
    if args.offset and not result["items"]:
        result["note"] = "That offset is past the end of this result set; nothing more to fetch."
    return result


async def _profile_section(coro) -> tuple[dict | None, str | None]:
    """One independently-fetched profile section: a failure here marks the
    section unavailable instead of taking the whole profile down."""
    try:
        return await coro, None
    except Exception:  # noqa: BLE001 - the section reports its own failure
        return None, "unavailable"


def _page_section(result: dict | None, sum_key: str | None = None) -> dict:
    """A profile section in page terms: what exists, what is in hand, and
    whether the section is genuinely complete."""
    if result is None:
        return {"available": False, "complete": False, "items": []}
    section = {
        "available": True,
        "total_count": result["total_count"],
        "returned": result["returned"],
        "has_more": result["has_more"],
        "complete": not result["has_more"],
        "items": result["items"],
    }
    if sum_key and result.get(sum_key) is not None:
        section[sum_key] = result[sum_key]
    return section


def _bound_profile(profile: dict, max_chars: int = 10_500) -> dict:
    """Bound the whole profile by trimming the largest sections' rows.

    The generic _bounded only sees top-level row lists, and a profile's
    rows live one level down (section -> items), so an oversized profile
    used to collapse into a single truncated preview string and every
    count was lost. Here the sections shrink instead: each trimmed
    section says so, its returned count is recomputed from the rows that
    remain, and has_more flips back on - the profile can never silently
    pass a page off as the whole section.
    """
    clean = _serializable(profile)
    logical_names = {
        "unpaid_invoices": "invoices",
        "recent_payments": "payments",
        "recent_quotes": "quotes",
        "recent_orders": "orders",
    }
    sections = [
        (key, value) for key, value in clean.items()
        if isinstance(value, dict) and isinstance(value.get("items"), list)
    ]

    def size() -> int:
        return len(json.dumps(clean, ensure_ascii=False, separators=(",", ":")))

    while size() > max_chars and any(section["items"] for _, section in sections):
        key, largest = max(sections, key=lambda pair: len(pair[1]["items"]))
        largest["items"].pop()
        largest["returned"] = len(largest["items"])
        largest["has_more"] = largest["returned"] < largest.get("total_count", 0)
        largest["complete"] = not largest["has_more"]
        largest["truncated"] = True
        clean["complete"] = False
        logical = logical_names.get(key)
        if logical and logical not in clean.setdefault("missing_sections", []):
            clean["missing_sections"].append(logical)
    return clean


async def get_customer_profile(name_or_id: str, database: Any = None) -> dict:
    """One customer's 360 view: contact, quotes, orders, invoices, payments, totals.

    Every section is fetched independently and reported in page terms,
    and the result carries an explicit completeness contract: `complete`
    is true only when every section was retrieved in full, and
    `missing_sections` names anything partial or unavailable. The answer
    may call the profile complete only when this says so.
    """
    args = CustomerProfileArgs.model_validate({"name_or_id": name_or_id})
    db = _database(database)
    customer_id, label, note = await _resolve_customer(db, args.name_or_id)
    if customer_id in (None, "none"):
        return {
            "customer": None,
            "note": note or f"No customer matching {args.name_or_id!r} was found.",
        }
    cid = int(customer_id)

    context, context_error = await _profile_section(get_customer_context(cid, database=db))
    unpaid, _ = await _profile_section(
        get_invoices(customer=str(cid), limit=MAX_TOOL_ROWS, database=db)
    )
    payments, _ = await _profile_section(
        get_payments(customer=str(cid), limit=MAX_TOOL_ROWS, database=db)
    )
    quotes, _ = await _profile_section(
        get_quotes(customer=str(cid), limit=MAX_TOOL_ROWS, database=db)
    )
    orders, _ = await _profile_section(
        get_orders(customer=str(cid), limit=MAX_TOOL_ROWS, database=db)
    )
    contact_row, _ = await _profile_section(
        db.payments_flat.find(
            {"customer.id": cid}, {"customer.email": 1, "customer.phone_no": 1, "_id": 0}
        ).to_list(1)
    )
    contact = contact_row[0].get("customer") if contact_row else {}

    unpaid_section = _page_section(unpaid, "sum_balance")
    payments_section = _page_section(payments, "sum_amount")
    quotes_section = _page_section(quotes, "sum_amount")
    orders_section = _page_section(orders, "sum_amount")

    section_states = {
        "details": context is not None,
        "quotes": quotes_section.get("complete", False),
        "orders": orders_section.get("complete", False),
        "payments": payments_section.get("complete", False),
        "invoices": unpaid_section.get("complete", False),
    }
    missing = [name for name, ok in section_states.items() if not ok]
    profile = {
        "customer_id": customer_id,
        "company_name": (context or {}).get("company_name") or label,
        "contact": {
            "email": (contact or {}).get("email"),
            "phone": (contact or {}).get("phone_no"),
        },
        "quotes": (context or {}).get("quotes"),
        "orders": (context or {}).get("orders"),
        "payments": (context or {}).get("payments"),
        "balances": {
            "unpaid_total": unpaid_section.get("sum_balance"),
            "unpaid_invoice_count": unpaid_section.get("total_count"),
        },
        "unpaid_invoices": unpaid_section,
        "recent_payments": payments_section,
        "recent_quotes": quotes_section,
        "recent_orders": orders_section,
        "complete": not missing,
        "missing_sections": missing,
        "notes": [
            *((context or {}).get("notes") or []),
            *(
                ["One part of the customer record could not be loaded, so the profile is partial."]
                if context_error
                else []
            ),
        ],
    }
    bounded = _bound_profile(profile)
    bounded["complete"] = not bounded["missing_sections"] and not any(
        isinstance(value, dict) and value.get("truncated")
        for value in bounded.values()
    )
    return bounded


_c360_client_singleton: Customer360Client | None = None


def _c360_client() -> Customer360Client:
    """The shared Customer 360 client (it shares its JWT via Redis, so a
    second instance would still not cause a second login)."""
    global _c360_client_singleton
    if _c360_client_singleton is None:
        _c360_client_singleton = Customer360Client()
    return _c360_client_singleton


async def search_customer360(query: str, database: Any = None) -> dict:
    """Search the Zoho CRM (Customer 360) for leads, contacts, customers.

    This is a DIFFERENT system from the Print247 ERP the other tools read:
    CRM records and their communication history live there. The live API
    caps results at 10 per type, so the live matches are merged with the
    locally harvested record index (customer360_index), duplicates
    removed by record id - the merged list is broader than either source
    alone, and the counts say how much came from each. The note keeps the
    cap honest: even merged, this is what search could reach, not proof
    of every match in their database.
    """
    args = Customer360SearchArgs.model_validate({"query": query})
    db = _database(database)
    index_hits = await c360_index.search_index(db, args.query)
    live_error: Customer360Error | None = None
    live_rows: list[dict] = []
    try:
        live_rows = await _c360_client().search(args.query)
    except Customer360Error as exc:
        live_error = exc

    cleaned = [
        {
            "record_id": row.get("id"),
            "record_type": row.get("type"),
            "name": row.get("name"),
            "email": row.get("email"),
            "phone": row.get("phone"),
            "company": row.get("company"),
            "status": row.get("status"),
        }
        for row in live_rows
        if isinstance(row, dict)
    ]
    live_count = len(cleaned)
    seen = {row["record_id"] for row in cleaned}
    for hit in index_hits:
        if hit.get("record_id") not in seen:
            cleaned.append(hit)
            seen.add(hit.get("record_id"))

    if live_error is not None and not cleaned:
        return {
            "source": "customer360",
            "available": False,
            "query": args.query,
            "total_count": 0,
            "results": [],
            "note": f"Customer 360 could not be reached: {live_error}",
        }

    note = (
        "Matches from the Zoho CRM (Customer 360), a separate system from the "
        "Print247 ERP data: live search results merged with the locally "
        "harvested record index, duplicates removed. The CRM caps live "
        "results at 10 per type. Pass a result's record_id and record_type "
        "to get_customer360 for its full view."
    )
    if live_error is not None:
        note = (
            "The live CRM could not be reached just now, so these matches "
            "come from the local index only and may be out of date. " + note
        )
    return {
        "source": "customer360",
        "available": True,
        "query": args.query,
        "total_count": len(cleaned),
        "live_matches": live_count,
        "local_index_matches": len(index_hits),
        "results": cleaned,
        "note": note,
    }


async def get_customer360(
    record_id: str, record_type: str, fresh: bool = False, database: Any = None
) -> dict:
    """One CRM record's full 360 summary, via the Mongo fetch-through cache.

    A fresh cached copy is served without touching their slow backend -
    unless fresh=true asks for the latest data, which always re-fetches
    live (the pattern the user asked for: database first, API on
    demand). On a live-fetch failure a stale copy is served WITH a note
    saying so; with no copy at all the tool reports itself unavailable -
    it never invents a profile.
    """
    args = Customer360DetailArgs.model_validate(
        {"record_id": record_id, "record_type": record_type, "fresh": fresh}
    )
    if args.record_id.isdigit():
        # An ERP numeric id is not a CRM record id (theirs are 24-char
        # hex): their API answers 500 for it, three slow retries deep
        # (live log Oct 24, id=4902). Steer back to search immediately.
        return {
            "source": "customer360",
            "available": False,
            "record_id": args.record_id,
            "record_type": args.record_type,
            "note": (
                "That id looks like an ERP numeric id, not a Customer 360 "
                "record id (CRM ids are long hex strings). Search first "
                "with search_customer360 and pass the record_id from its "
                "results to this tool."
            ),
        }
    db = _database(database)
    payload, fetched_at, cache_fresh = await c360_service.get_cached_detail(
        db, args.record_id, args.record_type
    )
    if payload is not None and cache_fresh and not args.fresh:
        summary = c360_service.summarize_detail(payload)
        summary.update({"available": True, "from_cache": True, "fetched_at": fetched_at})
        return summary

    try:
        live = await _c360_client().detail(args.record_id, args.record_type)
    except Customer360Error as exc:
        if payload is not None:
            summary = c360_service.summarize_detail(payload)
            summary.update(
                {
                    "available": True,
                    "from_cache": True,
                    "fetched_at": fetched_at,
                    "note": (
                        "Customer 360 could not be reached just now, so this is "
                        "the last saved copy and may be out of date."
                    ),
                }
            )
            return summary
        return {
            "source": "customer360",
            "available": False,
            "record_id": args.record_id,
            "record_type": args.record_type,
            "note": f"Customer 360 could not be reached and no saved copy exists: {exc}",
        }

    await c360_service.put_cached_detail(db, args.record_id, args.record_type, live)
    summary = c360_service.summarize_detail(live)
    summary.update({"available": True, "from_cache": False})
    return summary


async def get_crm_payments(
    date_from: Any = None,
    date_to: Any = None,
    customer: str = "",
    database: Any = None,
) -> dict:
    """Payments recorded in the Zoho CRM for a date range.

    A separate ledger from the ERP payments tool - the CRM mirrors ERP
    payments (rows carry the ERP id) and also holds payments the ERP list
    never shows. Oct 24 live failure: 'how many payments came today' was
    answered 0 from the ERP alone while the CRM held four receipts that
    day. The two ledgers are reported side by side, never merged.
    """
    args = Customer360PaymentsArgs.model_validate(
        {"date_from": date_from, "date_to": date_to, "customer": customer}
    )
    try:
        return await c360_service.collect_crm_payments(
            _c360_client(),
            date_from=args.date_from.isoformat(),
            date_to=args.date_to.isoformat(),
            search=(args.customer or "").strip(),
        )
    except Customer360Error as exc:
        return {
            "source": "customer360",
            "ledger": "zoho_crm_payments",
            "available": False,
            "date_from": args.date_from.isoformat(),
            "date_to": args.date_to.isoformat(),
            "total_count": 0,
            "results": [],
            "note": f"The Zoho CRM payments ledger could not be reached: {exc}",
        }


async def get_customer360_analysis(
    record_id: str, record_type: str, database: Any = None
) -> dict:
    """The CRM's own AI analysis for one record - explicit asks only.

    The user ordered this on Oct 24: name a record in chat, say 'analyze
    it', and the chat runs the CRM's analysis and shows it. The output is
    their gpt-4o-mini's words (urgency, reasoning, a drafted email),
    returned verbatim and labeled - it is NOT verified by this
    dashboard's tools, and the note makes the model present it that way.
    apiKeyStatus decides whether it is real AI output at all.
    """
    args = Customer360DetailArgs.model_validate(
        {"record_id": record_id, "record_type": record_type}
    )
    try:
        body = await _c360_client().fetch_analysis(args.record_id, args.record_type)
    except Customer360Error as exc:
        return {
            "source": "customer360",
            "available": False,
            "record_id": args.record_id,
            "record_type": args.record_type,
            "note": f"Customer 360 analysis could not be reached: {exc}",
        }
    status = body.get("apiKeyStatus")
    verified = status == "Verified"
    note = (
        "This is the Zoho CRM's own AI analysis (gpt-4o-mini), not a "
        "figure verified by this dashboard's tools. Present it under a "
        "label like 'CRM AI analysis', quoting its reasoning and email "
        "draft as its words; never restate a number from it as a verified "
        "fact."
    )
    if not verified:
        note = (
            f"The CRM's apiKeyStatus is {status!r}, not 'Verified': the "
            "CRM is running in fallback/simulation mode, so this analysis "
            "is a generic placeholder, NOT real AI output. Tell the user "
            "that plainly before showing any of it. " + note
        )
    return {
        "source": "customer360",
        "available": True,
        "record_id": args.record_id,
        "record_type": args.record_type,
        "ai_status": status,
        "is_real_ai": verified,
        "analysis": body.get("analysis") or {},
        "note": note,
    }


async def get_crm_follow_up_queue(database: Any = None) -> dict:
    """Who is waiting for a reply - the CRM Engagement agent's queue.

    Reads the LATEST agent run's crm_engagement section: the same
    Python-computed items the review page shows (their last interaction
    was inbound and unanswered), each with the channel the customer is
    waiting on and their actual last words. No live CRM call: freshness
    is the run's own started_at, returned as as_of so the answer can
    date itself honestly. WhatsApp sorts first - the channel the team
    answers fastest - then the longest wait."""
    db = _database(database)
    run = await db.agent_runs.find_one({}, sort=[("started_at", -1)])
    items = (run or {}).get("crm_engagement") or []
    queue: list[dict] = []
    for item in items:
        if not isinstance(item, dict) or item.get("error"):
            continue
        queue.append(
            {
                "name": item.get("contact_name"),
                "company": item.get("company"),
                "channel": item.get("channel"),
                "waiting_days": item.get("waiting_days"),
                "last_customer_message": item.get("last_customer_message"),
            }
        )
    queue.sort(
        key=lambda row: (row["channel"] != "WhatsApp", -(row["waiting_days"] or 0))
    )
    by_channel: dict[str, int] = {}
    for row in queue:
        if row["channel"]:
            by_channel[row["channel"]] = by_channel.get(row["channel"], 0) + 1
    return {
        "source": "crm_follow_up_queue",
        "as_of": (run or {}).get("started_at"),
        "waiting_total": len(queue),
        "by_channel": by_channel,
        "queue": queue[:10],
    }


async def get_crm_morning_briefing(database: Any = None) -> dict:
    """Yesterday's CRM digest: payments in, who is waiting, stalled deals.

    The user's morning briefing (built Oct 24): one Python-computed
    digest over data already in this system - the CRM payments mirror
    for yesterday's business date, and the latest agent run's CRM
    Engagement / Deal Watch sections. No live CRM calls."""
    from app.services.crm_digest import build_crm_digest

    return await build_crm_digest(_database(database))


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
    record = _strip_private(rows[0])
    if args.collection == "payments":
        # the same timestamp contract as the payments list: a date-only
        # midnight must not reach the model as a raw receipt time here
        stamp = _payment_timestamp(record.get("received_at"))
        if stamp["timestamp_precision"] == "missing" and record.get("payment_date"):
            stamp = {**stamp, "received_date": record.get("payment_date")}
        if stamp["timestamp_precision"] != "instant":
            record.pop("received_at", None)
        record.update(stamp)
    return {
        "collection": args.collection,
        "id": args.id,
        "found": True,
        "record": record,
    }


async def search_records(
    query: str,
    collections: list[str] | None = None,
    limit: int = DEFAULT_TOOL_ROWS,
    offset: int = 0,
    database: Any = None,
) -> dict:
    """Substring search over the business records the agent is allowed to read."""
    wanted = collections or list(RECORD_COLLECTIONS)
    args = SearchArgs.model_validate(
        {"query": query, "collections": wanted, "limit": limit, "offset": offset}
    )
    db = _database(database)
    needle = args.query.strip()
    # one flat, deterministic sequence (collection order, then document order) so
    # a page offset advances over the whole hit set instead of restarting per
    # collection - that is what makes every match reachable without repeats.
    matched: list[dict] = []
    collection_counts: dict[str, int] = {}
    for name in args.collections:
        rows = await getattr(db, RECORD_COLLECTIONS[name]).find({}, {"_id": 0}).to_list(50_000)
        hits = [_strip_private(row) for row in rows if _text_matches(row, needle)]
        collection_counts[name] = len(hits)
        matched.extend({"collection": name, **row} for row in hits)

    result = _paginate(
        _bounded(
            {
                "query": needle,
                "collections_searched": args.collections,
                "matches_by_collection": collection_counts,
                "items": matched[args.offset : args.offset + args.limit],
            }
        ),
        total=len(matched),
        offset=args.offset,
        limit=args.limit,
    )
    if args.offset and not result["items"]:
        result["note"] = "That offset is past the end of this result set; nothing more to fetch."
    return result


# ---------------------------------------------------------------------------
# historical lead pulls
#
# A lead pull is only comparable when each pull was retained as its own
# snapshot. The current `leads` collection is upserted in place and therefore
# cannot answer "what changed since the previous pull". This tool reads the
# dedicated `lead_pulls` history when it exists; otherwise it reports the real
# limitation and the minimum capability needed, never a fabricated comparison.
# ---------------------------------------------------------------------------

LEAD_PULL_COMPARE_FIELDS = (
    "status",
    "company",
    "product",
    "quantity",
    "size",
    "material",
    "finish",
)
_MAX_LEAD_PULLS = 200
_MAX_CHANGE_ROWS = 25


def _clean_lead_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _lead_identity(row: dict) -> str:
    for key in ("lead_id", "zohoLeadId", "lead_ref", "id"):
        value = _clean_lead_value(row.get(key))
        if value:
            return value
    return ""


async def _lead_pull_snapshots(database: Any) -> list[dict]:
    """Stored pull snapshots, oldest first. Missing collection means none."""
    collection = _collection(database, "lead_pulls")
    if collection is None:
        return []
    try:
        rows = await collection.find({}, {"_id": 0}).to_list(_MAX_LEAD_PULLS)
    except Exception:
        log.exception("could not read the lead_pulls history")
        return []
    dated: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        moment = processors.parse_date(row.get("pulled_at"))
        dated.append({**row, "_pulled_at_dt": moment})
    dated.sort(
        key=lambda row: (
            row["_pulled_at_dt"].timestamp() if row.get("_pulled_at_dt") else 0.0,
            str(row.get("pull_id") or ""),
        )
    )
    return dated


async def _count_collection(database: Any, name: str) -> int | None:
    collection = _collection(database, name)
    if collection is None:
        return None
    try:
        return int(await collection.count_documents({}))
    except Exception:
        return None


def _snapshot_header(snapshot: dict | None) -> dict | None:
    if not snapshot:
        return None
    leads = snapshot.get("leads") if isinstance(snapshot.get("leads"), list) else []
    return {
        "pull_id": snapshot.get("pull_id"),
        "pulled_at": processors.iso_or_none(snapshot.get("pulled_at")),
        "source": _clean_lead_value(snapshot.get("source")),
        "leads_recorded": len(leads),
        "complete": snapshot.get("complete", True) is not False,
    }


def _lead_pull_unavailable(
    snapshots: list[dict],
    current_lead_count: int | None,
    reason: str,
) -> dict:
    return _bounded(
        {
            "available": False,
            "snapshots_found": len(snapshots),
            "latest_pull": _snapshot_header(snapshots[-1] if snapshots else None),
            "current_lead_count": current_lead_count,
            "reason": reason,
            "required_capability": (
                "Lead-pull snapshots are persisted automatically: each scheduler cycle "
                "stores one immutable lead_pulls document (pull_id, pulled_at UTC, "
                "source, and a leads array carrying the stable lead_id (zohoLeadId) plus "
                "status, company, product, quantity, size, material and finish). "
                "Comparison needs at least two such snapshots; history is captured "
                "going forward and the current upserted leads rows cannot reconstruct a "
                "pull that was never stored."
            ),
            "suggested_next_step": (
                "Use get_leads for the current lead state. A comparison becomes possible "
                "once a second complete lead pull has been retained by the scheduler; "
                "comparison runs only from the first stored snapshot onward - yesterday's "
                "state is never invented."
            ),
        }
    )


def _lead_snapshot_index(snapshot: dict) -> tuple[dict[str, dict], set[str], int]:
    """lead_id -> row, duplicate ids to exclude, rows without a usable id."""
    index: dict[str, dict] = {}
    duplicates: set[str] = set()
    incomplete = 0
    leads = snapshot.get("leads") if isinstance(snapshot.get("leads"), list) else []
    for row in leads:
        if not isinstance(row, dict):
            incomplete += 1
            continue
        lead_id = _lead_identity(row)
        if not lead_id:
            incomplete += 1
            continue
        if lead_id in index:
            duplicates.add(lead_id)
            continue
        index[lead_id] = row
    for lead_id in duplicates:
        index.pop(lead_id, None)
    return index, duplicates, incomplete


def _lead_change_row(lead_id: str, row: dict | None, **extra: Any) -> dict:
    return {
        "lead_id": lead_id,
        "company": _clean_lead_value((row or {}).get("company")),
        "status": _clean_lead_value((row or {}).get("status")),
        **extra,
    }


async def compare_lead_pulls(
    database: Any = None,
    previous_pull_id: str | None = None,
    latest_pull_id: str | None = None,
) -> dict:
    """Compare two retained lead pulls, column by column.

    With no ids, the two newest complete, time-stamped snapshots are compared.
    Pass ``previous_pull_id`` / ``latest_pull_id`` to compare specific pulls.
    Only fields present in the stored snapshots are compared. An id duplicated
    within either pull is excluded and reported rather than guessed; a field
    missing on one side makes the record incomplete instead of "unchanged". A
    pull the capture marked incomplete is never treated as a full picture: leads
    absent from it are reported as indeterminate, not "removed". Every count and
    timestamp comes from the stored snapshots.
    """
    args = CompareArgs.model_validate(
        {"previous_pull_id": previous_pull_id, "latest_pull_id": latest_pull_id}
    )
    db = _database(database)
    snapshots = await _lead_pull_snapshots(db)
    current_count = await _count_collection(db, "leads")

    if not snapshots:
        return _lead_pull_unavailable(
            snapshots, current_count,
            "No historical lead pulls are retained, so the two most recent pulls "
            "cannot be compared.",
        )

    if args.previous_pull_id or args.latest_pull_id:
        by_id = {str(s.get("pull_id")): s for s in snapshots}
        wanted = [pid for pid in (args.previous_pull_id, args.latest_pull_id) if pid]
        missing = [pid for pid in wanted if pid not in by_id]
        if missing or len(wanted) < 2:
            detail = f"requested pull(s) {', '.join(missing)} are not retained" if missing \
                else "both previous_pull_id and latest_pull_id are required to compare specific pulls"
            return _lead_pull_unavailable(
                snapshots, current_count,
                f"The specified lead pulls cannot be compared: {detail}. "
                f"Retained pull ids: {', '.join(sorted(by_id))}.",
            )
        previous = by_id[args.previous_pull_id]
        latest = by_id[args.latest_pull_id]
    else:
        if len(snapshots) < 2:
            return _lead_pull_unavailable(
                snapshots, current_count,
                "Only one lead pull is retained; a comparison needs at least two.",
            )
        # the two newest that finished completely and carry a usable pull time
        comparable = [
            s for s in snapshots
            if s.get("complete", True) is not False and s.get("_pulled_at_dt") is not None
        ]
        if len(comparable) < 2:
            return _lead_pull_unavailable(
                snapshots, current_count,
                "Comparison needs two complete, time-stamped lead pulls; "
                f"only {len(comparable)} of the {len(snapshots)} retained snapshots are usable.",
            )
        previous, latest = comparable[-2], comparable[-1]

    if previous.get("_pulled_at_dt") is None or latest.get("_pulled_at_dt") is None:
        return _lead_pull_unavailable(
            snapshots,
            current_count,
            "The two newest lead pulls do not both have a usable pulled_at timestamp, "
            "so their time window cannot be trusted.",
        )

    # An incomplete latest pull is missing rows for reasons other than removal,
    # so its absences must not be reported as removed leads.
    latest_complete = latest.get("complete", True) is not False
    previous_complete = previous.get("complete", True) is not False

    previous_index, previous_duplicates, previous_incomplete = _lead_snapshot_index(previous)
    latest_index, latest_duplicates, latest_incomplete = _lead_snapshot_index(latest)
    duplicate_ids = previous_duplicates | latest_duplicates

    previous_ids = set(previous_index)
    latest_ids = set(latest_index)
    raw_added = sorted(latest_ids - previous_ids)
    raw_removed = sorted(previous_ids - latest_ids)
    # A lead only counts as added/removed when the pull it is absent from was
    # itself complete; otherwise its absence is indeterminate, not a real change.
    added_ids = raw_added if previous_complete else []
    removed_ids = raw_removed if latest_complete else []
    indeterminate_added = 0 if previous_complete else len(raw_added)
    indeterminate_removed = 0 if latest_complete else len(raw_removed)
    common_ids = sorted(previous_ids & latest_ids)

    all_fields = set(LEAD_PULL_COMPARE_FIELDS)
    fields_present = {
        field
        for field in LEAD_PULL_COMPARE_FIELDS
        if any(field in row for row in (*previous_index.values(), *latest_index.values()))
    }
    comparable_fields = sorted(fields_present - {"status"})

    reclassified: list[dict] = []
    changed: list[dict] = []
    unchanged = 0
    incomplete = previous_incomplete + latest_incomplete
    missing_field_notes: set[str] = set()

    for lead_id in common_ids:
        before = previous_index[lead_id]
        after = latest_index[lead_id]
        before_status = _clean_lead_value(before.get("status"))
        after_status = _clean_lead_value(after.get("status"))

        if before_status is None or after_status is None:
            incomplete += 1
            missing_field_notes.add("status")
            continue
        if before_status.casefold() != after_status.casefold():
            reclassified.append(
                _lead_change_row(
                    lead_id, after, previous_status=before_status, current_status=after_status
                )
            )
            continue

        differences: dict[str, dict[str, str | None]] = {}
        missing_on_one_side = False
        for field in comparable_fields:
            if field not in before and field not in after:
                continue
            before_value = _clean_lead_value(before.get(field))
            after_value = _clean_lead_value(after.get(field))
            if before_value is None or after_value is None:
                missing_on_one_side = True
                missing_field_notes.add(field)
                continue
            if before_value != after_value:
                differences[field] = {"from": before_value, "to": after_value}
        if differences:
            changed.append(_lead_change_row(lead_id, after, fields=differences))
        elif missing_on_one_side:
            incomplete += 1
        else:
            unchanged += 1

    if fields_present != all_fields:
        for field in sorted(all_fields - fields_present):
            missing_field_notes.add(field)

    summary = {
        "added": len(added_ids),
        "removed": len(removed_ids),
        "reclassified": len(reclassified),
        "changed": len(changed),
        "unchanged": unchanged,
        "incomplete": incomplete,
        "duplicate_ids_excluded": len(duplicate_ids),
    }
    if indeterminate_added:
        summary["indeterminate_added"] = indeterminate_added
    if indeterminate_removed:
        summary["indeterminate_removed"] = indeterminate_removed
    notes = [
        "Comparisons use only fields present in the stored snapshots.",
        "Records with duplicate lead ids were excluded instead of guessed.",
        "Records missing a lead id are counted as incomplete.",
    ]
    if not previous_complete:
        notes.append(
            "The earlier pull was flagged incomplete, so leads present only in the "
            "newer pull are reported as indeterminate additions, not confirmed new."
        )
    if not latest_complete:
        notes.append(
            "The later pull was flagged incomplete, so leads present only in the "
            "earlier pull are reported as indeterminate, not confirmed removals."
        )
    if missing_field_notes:
        notes.append(
            "Some records could not be compared for: "
            + ", ".join(sorted(missing_field_notes))
            + "."
        )

    return _bounded(
        {
            "available": True,
            "window": {
                "from": processors.iso_or_none(previous.get("pulled_at")),
                "to": processors.iso_or_none(latest.get("pulled_at")),
            },
            "previous_pull": _snapshot_header(previous),
            "latest_pull": _snapshot_header(latest),
            "fields_compared": comparable_fields,
            "summary": summary,
            "added": [_lead_change_row(lead_id, latest_index[lead_id]) for lead_id in added_ids[:_MAX_CHANGE_ROWS]],
            "removed": [_lead_change_row(lead_id, previous_index[lead_id]) for lead_id in removed_ids[:_MAX_CHANGE_ROWS]],
            "reclassified": reclassified[:_MAX_CHANGE_ROWS],
            "changed": changed[:_MAX_CHANGE_ROWS],
            "duplicate_ids": sorted(duplicate_ids)[:_MAX_CHANGE_ROWS],
            "rows_truncated": (
                len(added_ids) > _MAX_CHANGE_ROWS
                or len(removed_ids) > _MAX_CHANGE_ROWS
                or len(reclassified) > _MAX_CHANGE_ROWS
                or len(changed) > _MAX_CHANGE_ROWS
            ),
            "notes": notes,
        }
    )


async def describe_data(database: Any = None) -> dict:
    """What the tools can actually see: collections, fields and date ranges."""
    EmptyArgs.model_validate({})
    db = _database(database)

    def collection(name: str):
        return _collection(db, name)

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

    # Lead-pull history is a separate capability from the current lead rows.
    # Report it explicitly so "compare the last two pulls" cannot be answered
    # as if the current snapshot were a historical comparison.
    pull_rows = await _lead_pull_snapshots(db)
    described["lead_pull_history"] = {
        "snapshots_retained": len(pull_rows),
        "comparison_available": len(pull_rows) >= 2,
        "latest_pull_at": (
            processors.iso_or_none(pull_rows[-1].get("pulled_at")) if pull_rows else None
        ),
    }
    described["not_available"] = {
        "orders": list(processors.ORDER_MISSING_FIELDS),
        "payments": list(PAYMENT_UNAVAILABLE_FIELDS),
        "lead_pull_history": (
            []
            if len(pull_rows) >= 2
            else [
                "Stored snapshots of each lead pull (lead_pulls). The current leads "
                "collection alone cannot be compared with an earlier pull."
            ]
        ),
    }
    described["business_timezone"] = str(processors.ERP_TZ)
    return _bounded(described)


async def get_agent_briefing(
    customer: str | None = None,
    limit: int = DEFAULT_TOOL_ROWS,
    offset: int = 0,
    database: Any = None,
) -> dict:
    """The latest CEO brief and the draft recommendations behind it."""
    args = BriefingArgs.model_validate({"customer": customer, "limit": limit, "offset": offset})
    db = _database(database)
    rows = await db.agent_runs.find(
        {"finished_epoch": {"$exists": True}}, {"_id": 0}
    ).sort("finished_epoch", -1).to_list(1)
    if not rows:
        return {"run_id": None, "note": "No agent run has been saved yet."}
    run = rows[0]
    master = run.get("master") or {}
    # drafts are paged as one flat sequence in a fixed kind order, then re-grouped
    # for the response, so next_offset advances the whole set without repeats.
    flat: list[tuple[str, dict]] = []
    draft_totals: dict[str, int] = {}
    for kind in DRAFT_KINDS:
        rows_out = run.get(kind) or []
        if args.customer:
            needle = args.customer.strip().casefold()
            rows_out = [
                row for row in rows_out
                if needle in str(row.get("customer_name") or row.get("company") or row.get("contact_name") or "").casefold()
            ]
        draft_totals[kind] = len(rows_out)
        flat.extend((kind, _strip_private(row)) for row in rows_out)
    page = flat[args.offset : args.offset + args.limit]
    items = {kind: [row for row_kind, row in page if row_kind == kind] for kind in DRAFT_KINDS}
    result = _paginate(
        _bounded(
            {
                "run_id": run.get("run_id"),
                "started_at": run.get("started_at"),
                "counts": run.get("counts"),
                "briefing": master.get("bullets", []),
                "priorities": master.get("priorities", []),
                "conflicts_resolved": master.get("conflicts_resolved", []),
                "drafts": items,
                "draft_totals": draft_totals,
                "drafts_are_text_only": True,
            }
        ),
        total=len(flat),
        offset=args.offset,
        limit=args.limit,
        delivered=len(page),
    )
    if args.offset and not page:
        result["note"] = "That offset is past the end of this result set; nothing more to fetch."
    return result


# ------------------------------------------------------------ comparisons

# The date field each compared metric is bucketed by. These are the fields
# the existing tools already filter on - no new source is invented.
_METRIC_DATE_FIELDS = {
    "payments": ("payments_flat", "received_at", "amount"),
    "invoices": ("orders_flat", "invoice_date", None),
    "quotes": ("quotes_flat", "created_at", "amount"),
    "orders": ("orders_flat", "invoice_date", "total_amount"),
    "leads": ("leads", "createdAt", None),
}


class ComparePeriodsArgs(DateRangeArgs):
    metric: str


class AnomalyArgs(ToolArgs):
    metric: str = "payments"
    recent_days: int = 7
    baseline_days: int = 56


async def _metric_window(database: Any, metric: str, date_from: date, date_to: date) -> dict:
    """One metric over one window, computed in Python over the full match.

    Invoices report both what was invoiced and what is still owed; the
    compared value is the outstanding balance, matching the spec of the
    metric and the dashboard's own unpaid-invoice definition.
    """
    collection_name, date_field, amount_field = _METRIC_DATE_FIELDS[metric]
    db = _database(database)
    rows = await _collection(db, collection_name).find().to_list(200_000)
    if metric == "payments":
        # payment instants are bucketed on the user's calendar, exactly as
        # get_payments filters them - one question, one calendar
        matched = [row for row in rows if _in_user_window(row.get(date_field), date_from, date_to)]
    else:
        matched = [row for row in rows if _in_date_range(row.get(date_field), date_from, date_to)]
    if metric == "invoices":
        invoiced = round(sum(processors.to_number(row.get("total_amount")) for row in matched), 2)
        balance = round(
            sum(
                processors.to_number(row.get("total_amount")) - processors.to_number(row.get("amount_paid"))
                for row in matched
            ),
            2,
        )
        return {"total": balance, "total_invoiced": invoiced, "count": len(matched)}
    if metric == "leads":
        return {"total": None, "count": len(matched)}
    total = round(sum(processors.to_number(row.get(amount_field)) for row in matched), 2)
    return {"total": total, "count": len(matched)}


async def compare_periods(metric: str, date_from=None, date_to=None, period=None, database: Any = None) -> dict:
    """Current period vs the equal-length period immediately before it.

    Both sides are computed by the backend over the full matched sets; the
    change figures are arithmetic on those two computed values, never on
    listed rows. A zero previous total yields a null percentage (division
    by zero is reported as 'new activity', not infinity).
    """
    args = ComparePeriodsArgs.model_validate(
        {"metric": metric, "date_from": date_from, "date_to": date_to, "period": period}
    )
    if args.metric not in _METRIC_DATE_FIELDS:
        return {
            "available": False,
            "metric": args.metric,
            "note": f"Cannot compare {args.metric!r}: supported metrics are {sorted(_METRIC_DATE_FIELDS)}.",
        }
    start, end = args.date_from, args.date_to
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    current = await _metric_window(database, args.metric, start, end)
    previous = await _metric_window(database, args.metric, prev_start, prev_end)
    cur_value = current["count"] if args.metric == "leads" else current["total"]
    prev_value = previous["count"] if args.metric == "leads" else previous["total"]
    absolute = round(cur_value - prev_value, 2)
    if prev_value == 0:
        percent = 0.0 if cur_value == 0 else None
        direction = "flat" if cur_value == 0 else "new"
    else:
        percent = round((cur_value - prev_value) / prev_value * 100, 1)
        direction = "up" if absolute > 0 else ("down" if absolute < 0 else "flat")
    return {
        "metric": args.metric,
        "current": {"date_from": start.isoformat(), "date_to": end.isoformat(), **current},
        "previous": {"date_from": prev_start.isoformat(), "date_to": prev_end.isoformat(), **previous},
        "change": {"absolute": absolute, "percent": percent, "direction": direction},
        "compared_value": "lead count" if args.metric == "leads" else ("outstanding balance" if args.metric == "invoices" else "total amount"),
        "complete": True,
        "notes": [
            "The previous period is the equal-length range immediately before the current one.",
            "Both totals are computed over every matched record, not over a listed page.",
        ],
    }


async def detect_anomalies(metric: str = "payments", recent_days: int = 7, baseline_days: int = 56, database: Any = None) -> dict:
    """Deterministic deviation check: a recent window vs a daily baseline.

    An anomaly is flagged only when the recent total deviates from the
    baseline expectation by at least 30% AND by at least two baseline
    standard deviations of daily totals - both conditions, so ordinary
    noise never raises one. With too little history the tool says so
    instead of guessing: a reliable trend cannot be established.
    """
    args = AnomalyArgs.model_validate(
        {"metric": metric, "recent_days": recent_days, "baseline_days": baseline_days}
    )
    if args.metric not in _METRIC_DATE_FIELDS:
        return {"available": False, "metric": args.metric,
                "note": f"Cannot scan {args.metric!r}: supported metrics are {sorted(_METRIC_DATE_FIELDS)}."}
    recent_days = max(1, min(args.recent_days, 30))
    baseline_days = max(7, min(args.baseline_days, 180))
    today = datetime.now(processors.ERP_TZ).date()
    recent_start = today - timedelta(days=recent_days - 1)
    baseline_end = recent_start - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=baseline_days - 1)

    # Load the source rows once and bucket them by day in Python; a
    # per-day database scan would multiply reads for no new information.
    collection_name, date_field, amount_field = _METRIC_DATE_FIELDS[args.metric]
    all_rows = await _collection(_database(database), collection_name).find().to_list(200_000)

    def day_value(day: date) -> float:
        if args.metric == "payments":
            matched = [row for row in all_rows if _in_user_window(row.get(date_field), day, day)]
        else:
            matched = [row for row in all_rows if _in_date_range(row.get(date_field), day, day)]
        if args.metric == "leads":
            return float(len(matched))
        if args.metric == "invoices":
            return float(round(sum(
                processors.to_number(row.get("total_amount")) - processors.to_number(row.get("amount_paid"))
                for row in matched
            ), 2))
        return float(round(sum(processors.to_number(row.get(amount_field)) for row in matched), 2))

    def daily(start: date, days: int) -> list[float]:
        return [day_value(start + timedelta(days=offset)) for offset in range(days)]

    baseline = daily(baseline_start, baseline_days)
    recent = daily(recent_start, recent_days)
    baseline_total = sum(baseline)
    recent_total = sum(recent)
    if baseline_total == 0 and recent_total == 0:
        return {
            "available": False, "metric": args.metric,
            "note": f"No {args.metric} activity in the baseline or recent window, so no trend can be established.",
        }
    mean = baseline_total / baseline_days
    variance = sum((value - mean) ** 2 for value in baseline) / baseline_days
    std = variance ** 0.5
    expected = mean * recent_days
    deviation = None if expected == 0 else round((recent_total - expected) / expected * 100, 1)
    sigma_gap = 0.0 if std == 0 else abs(recent_total / recent_days - mean) / std
    anomaly = bool(
        deviation is not None and abs(deviation) >= 30 and (std == 0 or sigma_gap >= 2)
    )
    return {
        "metric": args.metric,
        "available": True,
        "anomaly": anomaly,
        "direction": "up" if recent_total > expected else ("down" if recent_total < expected else "flat"),
        "recent": {"date_from": recent_start.isoformat(), "date_to": today.isoformat(),
                   "total": round(recent_total, 2), "days": recent_days},
        "baseline": {"date_from": baseline_start.isoformat(), "date_to": baseline_end.isoformat(),
                     "daily_average": round(mean, 2), "daily_std": round(std, 2), "days": baseline_days},
        "expected_recent_total": round(expected, 2),
        "deviation_percent": deviation,
        "threshold": "flagged when the recent total deviates at least 30% from the baseline expectation and at least 2 standard deviations of daily totals",
        "notes": [
            "A deviation is a pattern in the numbers, not a cause; the records do not say why it moved.",
        ],
    }


async def get_priorities(database: Any = None) -> dict:
    """Today's prioritized, evidence-grounded recommendations.

    Composed entirely from the existing Python-computed signals: every
    recommendation carries the metrics and record ids it rests on, a
    confidence tied to how direct the evidence is, and its limitation.
    A recommendation with no evidence behind it is not emitted.
    """
    EmptyArgs.model_validate({})
    db = _database(database)
    signals = await get_signals_summary(database=db)
    top_unpaid = await get_top_unpaid(5, database=db)
    top_quotes = await get_top_open_quotes(5, database=db)
    stuck = await get_stuck_leads(database=db)

    recommendations: list[dict] = []
    overdue = signals.get("overdue_invoices") or {}
    if (overdue.get("count") or 0) > 0:
        largest = top_unpaid.get("largest_owing_customer") or {}
        recommendations.append({
            "priority": len(recommendations) + 1,
            "action": "Collect overdue balances, starting with the largest owing customer",
            "why": "Overdue balances are money already earned but not received; the oldest and largest age further every day they sit.",
            "metrics": {
                "overdue_invoice_count": overdue.get("count"),
                "overdue_total_balance": overdue.get("total_balance"),
                "largest_owing_customer": largest.get("company_name"),
                "largest_customer_balance": largest.get("total_balance"),
            },
            "record_ids": [row.get("id") for row in (top_unpaid.get("items") or []) if row.get("id") is not None],
            "confidence": "high - computed from current invoice balances and due dates",
            "limitations": "A balance being overdue does not say why it is unpaid; check the record before contacting the customer.",
            "next_step": "Open the largest unpaid invoice and confirm its status with the customer today.",
        })
    stale = signals.get("stale_quotes") or {}
    if (stale.get("count") or 0) > 0:
        recommendations.append({
            "priority": len(recommendations) + 1,
            "action": "Follow up the largest stale open quotes before they go cold",
            "why": "Open quotes that sit unconverted represent priced demand; each stale day lowers the chance they convert to orders.",
            "metrics": {
                "stale_quote_count": stale.get("count"),
                "stale_quote_total": stale.get("total_amount"),
            },
            "record_ids": [row.get("id") for row in (top_quotes.get("items") or []) if row.get("id") is not None],
            "confidence": "high - the stale set uses the dashboard's own open-quote rules",
            "limitations": "Staleness is age-based; a quote may already be in conversation outside the records.",
            "next_step": "Contact the customer on the largest stale quote and record the outcome.",
        })
    if (stuck.get("total_count") or 0) > 0:
        missing_fields: dict[str, int] = {}
        for row in stuck.get("items") or []:
            for field in row.get("missing_fields") or []:
                missing_fields[field] = missing_fields.get(field, 0) + 1
        recommendations.append({
            "priority": len(recommendations) + 1,
            "action": "Qualify the stuck leads: collect the missing fields and make first contact",
            "why": "Leads stuck before ordering cannot convert; most are missing a small set of qualification fields.",
            "metrics": {
                "stuck_lead_count": stuck.get("total_count"),
                "most_common_missing_fields": missing_fields,
            },
            "record_ids": [row.get("id") for row in (stuck.get("items") or []) if row.get("id") is not None],
            "confidence": "medium - lead fields are parsed from free-text descriptions, so a parsed field can occasionally be wrong",
            "limitations": "Lead records carry no contact outcome history here; a lead may already have been contacted off-system.",
            "next_step": "Work the oldest stuck lead first and fill its missing fields.",
        })
    return {
        "recommendations": recommendations,
        "as_of": signals.get("as_of"),
        "sources": ["signals_summary", "top_unpaid", "top_open_quotes", "stuck_leads"],
        "notes": [
            "Recommendations are ranked by money at risk, then revenue pending, then pipeline health.",
            "Nothing here modifies a record or contacts a customer; each next step is for a human to take.",
        ],
    }


class RecallArgs(ToolArgs):
    query: str = ""
    limit: int = 5


async def recall_conversations(query: str = "", limit: int = 5, database: Any = None) -> dict:
    """Search the stored conversations with this assistant.

    This is how JARVIS remembers across chats: every conversation is kept
    in chat_threads, and a follow-up about an earlier discussion ('what
    did we say about Garcoa', 'the leads we checked last time') is answered
    from these excerpts. Excerpts are past conversation, not fresh
    business data - the chat layer deliberately keeps them out of the
    numeric evidence set, so a figure quoted from an old answer still has
    to be re-checked with a data tool before it is cited as current.
    With no query, the most recent conversations are listed by title.
    """
    args = RecallArgs.model_validate({"query": query, "limit": limit})
    db = _database(database)
    docs = await db.chat_threads.find().to_list(500)
    cap = max(1, min(args.limit, 10))
    needle = args.query.strip().lower()
    found: list[dict] = []
    for doc in docs:
        turns = doc.get("turns") or []
        if not turns:
            continue
        title = doc.get("title") or str(turns[0].get("question") or "")[:80]
        if not needle:
            found.append({
                "thread_id": doc.get("thread_id"),
                "title": title,
                "updated_at": doc.get("updated_at"),
                "turn_count": len(turns),
                "matches": [],
            })
            continue
        hits: list[dict] = []
        for turn in turns:
            question = str(turn.get("question") or "")
            answer = str(turn.get("answer") or "")
            at = (question + "\n" + answer).lower().find(needle)
            if at < 0:
                continue
            if at <= len(question):
                excerpt = answer[:300]
            else:
                start = max(0, at - len(question) - 1 - 80)
                excerpt = answer[start:start + 300]
            hits.append({"question": question[:200], "answer_excerpt": excerpt})
            if len(hits) >= 2:
                break
        if hits:
            found.append({
                "thread_id": doc.get("thread_id"),
                "title": title,
                "updated_at": doc.get("updated_at"),
                "turn_count": len(turns),
                "matches": hits,
            })
    found.sort(key=lambda item: item["updated_at"] or "", reverse=True)
    return _bounded({
        "query": args.query,
        "conversations_matched": len(found) if needle else 0,
        "conversations": found[:cap],
        "note": (
            "Excerpts from past stored conversations - context, not new business data. "
            "Re-check any figure with a data tool before citing it as current."
            if needle else
            "The most recent stored conversations, by title. Pass a query to search inside them."
        ),
    })


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
        "Get leads that are stuck before becoming an order: status New or Attempted to Contact, recent activity, a description present, not flagged by the lead desk. Returns Python-parsed product/quantity/size/material/finish, the missing-field list and artwork state (Provided/Missing/Not stated). No email or phone numbers are returned." + PAGING_NOTE,
        StuckLeadsArgs,
        get_stuck_leads,
    ),
    ChatTool(
        "get_payments",
        "List individual payment records (who paid): customer name and ID, amount, payment date, received timestamp, payment reference and the invoice numbers the payment was applied to. Filters: received-date range, customer name or ID, min/max amount. Answer 'who paid today' with this tool." + PAGING_NOTE,
        PaymentArgs,
        get_payments,
    ),
    ChatTool(
        "get_invoices",
        "List invoices with balance, due date, days overdue and customer. Filters: payment status text, customer, overdue_only, invoice-date range." + PAGING_NOTE,
        InvoiceArgs,
        get_invoices,
    ),
    ChatTool(
        "get_quotes",
        "List quotes with amount, status, age and customer. Filters: status text, customer, stale_only (the same open-quote rules the dashboard uses), created-date range." + PAGING_NOTE,
        QuoteArgs,
        get_quotes,
    ),
    ChatTool(
        "get_orders",
        "List orders with amounts, status and customer. Filters: order status text, customer, stalled_only (this signal is unreliable - the ERP stores no order status timestamps), invoice-date range." + PAGING_NOTE,
        OrderArgs,
        get_orders,
    ),
    ChatTool(
        "get_leads",
        "List lead records with parsed product/quantity/size/material/finish and the missing-field list. Filters: status text, stuck_only, search text. No contact details are returned." + PAGING_NOTE,
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
        "search_customer360",
        "Search the Zoho CRM (Customer 360 - a separate system from the Print247 ERP) for leads, contacts and customers by name, email, phone or company. Use it when a question is about a CRM record or its communication history, or when the ERP tools find nobody. Also call it WITHOUT asking the user first whenever the ERP finds the person but has no email or phone for them - missing contact details are exactly what this source is for; search here, then answer. Live results (capped at 10 per type by that API) are merged with the locally harvested record index, duplicates removed; each match carries the record_id and record_type that get_customer360 needs.",
        Customer360SearchArgs,
        search_customer360,
    ),
    ChatTool(
        "get_customer360",
        "Full Customer 360 view for one CRM record (record_id + record_type from search_customer360): identity and status, related leads/contacts/customers, the interaction timeline (calls, emails, SMS, notes: counts by type, the last interaction, the five most recent), projects/deals, estimates, invoices and payments - with backend-computed totals where the records carry a consistent money field. It also returns `conversation`: the newest timeline entries WITH their message bodies (WhatsApp/SMS/email text, notes, call outcomes and durations), each marked Inbound (from the customer) or Outbound (from the team) - when the user asks what was said, what the customer replied, or for a WhatsApp/SMS/chat history, present that thread from the conversation entries, quoting the bodies; never say the messages are unavailable when conversation entries exist. A saved copy may be served for speed: when the user asks for the latest, fresh or taaza data, pass fresh=true to re-fetch the record live from the CRM instead. This is Zoho CRM data, separate from the ERP quotes/orders/payments tools. Present the answer as a compact contact card: name, email, phone, company and status first, then the counts and the last interaction - do not dump raw field names, and do not print the record_id hex unless the user asks for it.",
        Customer360DetailArgs,
        get_customer360,
    ),
    ChatTool(
        "get_crm_payments",
        "Payments recorded in the Zoho CRM (Customer 360) for a date range, optionally filtered by a customer/name search. This is a SEPARATE ledger from the ERP payments tool: the CRM mirrors ERP payments (each row carries its erp_id) and also holds CRM-only payments, so for any question about payments received - today, this week, from a customer - call BOTH this and get_payments and report each ledger under its own label with its own total; NEVER add the two totals together and never report 'no payments' from one ledger alone. Receive dates are calendar dates with no clock time recorded; record_created_at is the CRM record's creation/sync time, not the payment time.",
        Customer360PaymentsArgs,
        get_crm_payments,
    ),
    ChatTool(
        "get_customer360_analysis",
        "Run the Zoho CRM's own AI follow-up analysis for ONE CRM record (record_id + record_type from search_customer360) and show it: urgency, reasoning, the action needed and a drafted email. Call this ONLY when the user names a record and explicitly asks for its analysis ('analyze him', 'is ki analysis karo') - it is slow (up to a minute cold), calls OpenAI on the CRM side every time, and is never a background or speculative call. Its words are the CRM AI's, not verified facts: present them labeled as the CRM AI analysis, and if ai_status is not 'Verified' say plainly that the CRM returned a placeholder analysis.",
        Customer360DetailArgs,
        get_customer360_analysis,
    ),
    ChatTool(
        "get_crm_follow_up_queue",
        "List the customers currently waiting for a reply - the CRM Engagement queue from the latest agent run - with the channel each customer is waiting on (WhatsApp first), how many days they have waited, and the customer's own last message. Use for 'who is waiting', 'WhatsApp follow-ups', 'follow-up queue' questions. Costs no CRM call; the result's as_of is when the queue was computed - date the answer with it.",
        EmptyArgs,
        get_crm_follow_up_queue,
    ),
    ChatTool(
        "get_crm_morning_briefing",
        "The CRM morning briefing for yesterday (business date): payments that landed in the Zoho CRM with their total, who is waiting for a reply (WhatsApp counted separately), and the biggest stalled deals. Use for 'morning briefing', 'aaj ki briefing', 'kal kya hua' questions. All figures are computed by the backend; the waiting/deals blocks carry as_of_run, the time of the agent run they came from.",
        EmptyArgs,
        get_crm_morning_briefing,
    ),
    ChatTool(
        "get_record",
        "Fetch one record by id from payments, invoices, orders, quotes or leads when a specific number is needed.",
        RecordArgs,
        get_record,
    ),
    ChatTool(
        "search_records",
        "Substring search across payments, invoices, orders, quotes and leads for a name, reference or amount text. Returns one flat list of matched rows, each tagged with its collection, plus matches_by_collection." + PAGING_NOTE,
        SearchArgs,
        search_records,
    ),
    ChatTool(
        "compare_lead_pulls",
        "Compare two retained lead-pull snapshots and report added, removed, reclassified (status changed) and changed (product/quantity/size/material/finish) leads, plus unchanged counts, aligned by the stable lead id. Use for 'compare today's leads with yesterday's', 'what new leads were added since yesterday', 'which leads were removed', 'which leads changed status or product', or 'show the changes between two lead pulls'. With no arguments it uses the two newest complete snapshots; pass previous_pull_id and latest_pull_id to compare specific pulls (the pull ids and timestamps come back in previous_pull / latest_pull). Cite only the counts, ids and status changes this tool returned. When it returns available=false it names exactly what is missing (how many complete snapshots are retained) - explain that history is captured going forward and comparison becomes possible after the second stored snapshot; never describe that as a failed figure check and never invent yesterday's state.",
        CompareArgs,
        compare_lead_pulls,
    ),
    ChatTool(
        "describe_data",
        "Describe what data exists: collections, row counts, their field names, date ranges, retained lead-pull history, and the fields that are genuinely not stored. Call this before saying something is unavailable.",
        EmptyArgs,
        describe_data,
    ),
    ChatTool(
        "compare_periods",
        "Compare one metric (payments, invoices, quotes, orders, leads) between a period and the equal-length period immediately before it. Backend-computed totals for both sides plus absolute and percentage change. Pass a named period or explicit dates like any other dated tool. Use for 'compared with last week/month', 'are payments up or down', trend questions.",
        ComparePeriodsArgs,
        compare_periods,
    ),
    ChatTool(
        "detect_anomalies",
        "Deterministic deviation scan for one metric (payments, invoices, quotes, orders, leads): the recent window (default 7 days) against a daily baseline (default 56 days). Flags an anomaly only on a >=30% deviation that is also >=2 standard deviations. Use for 'anything unusual', 'why did payments drop', spike/drop questions. It reports patterns, never causes.",
        AnomalyArgs,
        detect_anomalies,
    ),
    ChatTool(
        "get_priorities",
        "Today's prioritized recommendations computed from verified signals (overdue balances, stale quotes, stuck leads), each with its metrics, record ids, confidence and limitations. Use for 'what should we prioritize today', 'what should we do next', 'how can we improve collections'.",
        EmptyArgs,
        get_priorities,
    ),
    ChatTool(
        "recall_conversations",
        "Search the stored conversations with this assistant (every chat is kept). Use for 'what did we discuss about X', 'the customer we looked at last time', 'did we check Y before'. Returns bounded excerpts from past questions and answers. With no query it lists the most recent conversations by title. Excerpts are context, not fresh data - re-check figures with a data tool before citing them.",
        RecallArgs,
        recall_conversations,
    ),
    ChatTool(
        "get_agent_briefing",
        "Get the latest saved CEO brief plus the agent drafts behind it (negotiation, collections, lead follow-up), optionally filtered by customer, so a question like 'what did the collections agent suggest for X?' can be answered. Drafts are stored text only. The draft rows are one paged set across the three kinds." + PAGING_NOTE,
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
