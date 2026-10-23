"""Customer 360 detail payloads: Python-side summary + a Mongo cache.

The raw detail payload can carry hundreds of timeline items, so the chat
tools never hand it to the model directly. `summarize_detail` computes
every number here (section counts, money totals, the last interaction) -
the same rule the rest of the dashboard follows: the LLM never does
arithmetic, and every figure a tool returns can pass the numeric guard.

The cache exists because their serverless backend is slow when cold
(~8s warm, over a minute cold). A fetched payload is stored whole in
`customer360_cache` and served fresh for C360_CACHE_MINUTES; a stale copy
is still better than no answer when the backend cannot be reached.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import settings

log = logging.getLogger("customer360")

CACHE_COLLECTION = "customer360_cache"

SECTIONS = (
    "leads", "contacts", "customers", "timeline",
    "projects", "invoices", "estimates", "payments", "whatsapp",
)

# Money fields, in the order to try them. A field only counts when it is
# numeric on EVERY row - a partial total would be a wrong total.
_INVOICE_MONEY_KEYS = ("grandTotal", "total", "balance", "amount")
_PAYMENT_MONEY_KEYS = ("amountReceived", "amount", "total", "grandTotal")


def _parse_dt(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _to_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _numeric(value: Any) -> bool:
    return _to_float(value) is not None


def _money_total(rows: list[dict], keys: tuple[str, ...]) -> tuple[Optional[float], Optional[str]]:
    """Sum one money field across rows, or (None, None) when no single
    field is numeric on every row - a partial total would be a wrong one."""
    if not rows:
        return None, None
    for key in keys:
        if all(_numeric(row.get(key)) for row in rows):
            return sum(float(_to_float(row.get(key)) or 0) for row in rows), key
    return None, None


def _by_field(rows: list[dict], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            counts[value] = counts.get(value, 0) + 1
    return counts


def _entity(payload: dict) -> dict:
    """The base record, normalised across Lead / Contact / Customer."""
    base = payload.get("baseEntity") or {}
    base_type = payload.get("baseType") or ""
    first = str(base.get("firstName") or "").strip()
    last = str(base.get("lastName") or "").strip()
    person = " ".join(part for part in (first, last) if part)
    name = person or base.get("customerName") or base.get("company") or None
    zoho_id = (
        base.get("zohoLeadId") or base.get("zohoContactId") or base.get("zohoAccountId")
    )
    entity = {
        "record_id": base.get("_id"),
        "record_type": base_type,
        "name": name,
        "email": base.get("email"),
        "phone": base.get("phone"),
        "company": base.get("company") or base.get("accountName") or base.get("customerName"),
        "status": base.get("status"),
        "source": base.get("source"),
        "created_at": base.get("createdAt"),
        "last_activity_at": base.get("lastActivityAt"),
        "zoho_id": str(zoho_id) if zoho_id is not None else None,
    }
    summary = base.get("aiSummary")
    if isinstance(summary, str) and summary.strip():
        # The CRM's own stored AI summary of this record - record text,
        # quoted as theirs, never as this system's analysis.
        entity["crm_ai_summary"] = summary.strip()[:600]
    return entity


def _timeline(rows: list[dict]) -> dict:
    """Last interaction + a small newest-first sample, computed here."""
    stamped: list[tuple[Optional[datetime], dict]] = []
    for row in rows:
        at = _parse_dt(row.get("timestamp") or row.get("createdAt"))
        stamped.append((at, row))
    dated = [(at, row) for at, row in stamped if at is not None]
    dated.sort(key=lambda pair: pair[0], reverse=True)
    ordered = [row for _, row in dated] or rows[:5]

    def brief(row: dict) -> dict:
        title = row.get("title")
        return {
            "type": row.get("type"),
            "title": str(title)[:120] if title is not None else None,
            "at": row.get("timestamp") or row.get("createdAt"),
        }

    out: dict[str, Any] = {"timeline_by_type": _by_field(rows, "type")}
    if dated:
        out["last_interaction"] = brief(dated[0][1])
    out["recent_interactions"] = [brief(row) for row in ordered[:5]]
    return out


def summarize_detail(payload: dict) -> dict:
    """One Customer 360 detail payload -> the bounded, guard-safe summary
    the chat tool returns. All numbers are computed in this function."""
    summary: dict[str, Any] = {
        "source": "customer360",
        "entity": _entity(payload),
    }
    rows_by_section: dict[str, list[dict]] = {}
    for section in SECTIONS:
        rows = payload.get(section)
        rows = rows if isinstance(rows, list) else []
        rows_by_section[section] = [row for row in rows if isinstance(row, dict)]
        summary[f"{section}_count"] = len(rows_by_section[section])

    estimates = rows_by_section["estimates"]
    if estimates:
        estimates_total, _ = _money_total(estimates, ("grandTotal",))
        if estimates_total is not None:
            summary["estimates_total_amount"] = estimates_total
        summary["estimates_by_stage"] = _by_field(estimates, "stage")

    projects = rows_by_section["projects"]
    if projects:
        projects_total, _ = _money_total(projects, ("amount",))
        if projects_total is not None:
            summary["projects_total_amount"] = projects_total
        summary["projects_by_stage"] = _by_field(projects, "stage")

    invoices_total, invoices_field = _money_total(rows_by_section["invoices"], _INVOICE_MONEY_KEYS)
    if invoices_total is not None:
        summary["invoices_total_amount"] = invoices_total
        summary["invoices_total_field"] = invoices_field
    if rows_by_section["invoices"]:
        by_status = _by_field(rows_by_section["invoices"], "status")
        if by_status:
            summary["invoices_by_status"] = by_status

    payments_total, payments_field = _money_total(rows_by_section["payments"], _PAYMENT_MONEY_KEYS)
    if payments_total is not None:
        summary["payments_total_amount"] = payments_total
        summary["payments_total_field"] = payments_field

    summary.update(_timeline(rows_by_section["timeline"]))
    return summary


# ------------------------------------------------------------------- cache

def _cache_key(record_id: str, record_type: str) -> str:
    return f"{record_type}:{record_id}"


def _collection(database: Any) -> Any:
    try:
        return database[CACHE_COLLECTION]
    except (TypeError, KeyError, AttributeError):
        return getattr(database, CACHE_COLLECTION, None)


async def get_cached_detail(
    database: Any, record_id: str, record_type: str
) -> tuple[Optional[dict], Optional[str], bool]:
    """(payload, fetched_at_iso, is_fresh) - payload None when uncached.
    Cache read failures degrade to a plain miss, never an error."""
    try:
        collection = _collection(database)
        if collection is None:
            return None, None, False
        doc = await collection.find_one({"_id": _cache_key(record_id, record_type)})
        if not doc or not isinstance(doc.get("payload"), dict):
            return None, None, False
        fetched_at = doc.get("fetched_at")
        parsed = _parse_dt(fetched_at)
        fresh = False
        if parsed is not None:
            age_minutes = (datetime.now(timezone.utc) - parsed).total_seconds() / 60
            fresh = age_minutes <= float(settings.C360_CACHE_MINUTES)
        return doc["payload"], fetched_at, fresh
    except Exception:
        log.warning("customer360 cache read failed", exc_info=True)
        return None, None, False


async def put_cached_detail(
    database: Any, record_id: str, record_type: str, payload: dict
) -> None:
    """Store the raw payload. A cache write failure never fails the tool."""
    try:
        collection = _collection(database)
        if collection is None:
            return
        await collection.update_one(
            {"_id": _cache_key(record_id, record_type)},
            {
                "$set": {
                    "record_id": record_id,
                    "record_type": record_type,
                    "payload": payload,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            },
            upsert=True,
        )
    except Exception:
        log.warning("customer360 cache write failed", exc_info=True)


# ------------------------------------------------------- CRM module lists
#
# The module list endpoints (see customer360_client.list_page) expose the
# CRM's own ledgers - payments first. Chat needs them because a payment
# can exist in the CRM while the ERP payments tool shows none for that
# day or customer (live failure Oct 24: "aaj kitni payment aayi" answered
# 0 while the CRM held four receipts that day, one of them MNJ's $4,627).

PAYMENT_PAGE_SIZE = 100
MAX_PAYMENT_PAGES = 10  # a scan reads at most 1,000 rows and says so


def _payment_row(row: dict) -> dict:
    received = str(row.get("amountReceiveDate") or "")
    return {
        "payment_name": row.get("paymentName"),
        "erp_id": row.get("erpId"),
        "customer_name": row.get("customerName"),
        "project_name": row.get("projectName"),
        "amount_received": _to_float(row.get("amountReceived")),
        "total_amount": _to_float(row.get("totalAmount")),
        "invoice_status": row.get("invoiceStatus"),
        "owner": row.get("ownerName"),
        "receive_date": received[:10] if received else None,
        "record_created_at": row.get("createdAt"),
    }


async def collect_crm_payments(
    client: Any,
    *,
    date_from: str,
    date_to: str,
    search: str = "",
) -> dict:
    """CRM payments whose receive date falls in [date_from, date_to].

    The payments list is ordered by receive date, newest first (verified
    live), so paging stops as soon as a whole page is older than the
    range. Totals are computed here, in Python, and only when every
    matched row carries the field - the same discipline as the detail
    summarizer. The coverage block states exactly how far the scan got:
    a bounded scan is never dressed up as the whole ledger.
    """
    matched: list[dict] = []
    scanned = 0
    pages_read = 0
    module_total: Any = None
    complete = False
    for page in range(1, MAX_PAYMENT_PAGES + 1):
        body = await client.list_page(
            "payments", page=page, limit=PAYMENT_PAGE_SIZE, search=search
        )
        rows = [row for row in (body.get("payments") or []) if isinstance(row, dict)]
        pagination = body.get("pagination") or {}
        if pagination.get("total") is not None:
            module_total = pagination["total"]
        pages_read = page
        if not rows:
            complete = True
            break
        scanned += len(rows)
        page_days = [str(row.get("amountReceiveDate") or "")[:10] for row in rows]
        for row, day in zip(rows, page_days):
            if date_from <= day <= date_to:
                matched.append(_payment_row(row))
        if page_days and max(page_days) < date_from:
            complete = True  # newest first: everything further is older still
            break
        total_pages = pagination.get("pages")
        if total_pages is not None and page >= int(total_pages):
            complete = True
            break

    received = [row["amount_received"] for row in matched]
    totals = [row["total_amount"] for row in matched]
    return {
        "source": "customer360",
        "ledger": "zoho_crm_payments",
        "available": True,
        "date_from": date_from,
        "date_to": date_to,
        "search": search or None,
        "total_count": len(matched),
        "sum_amount_received": (
            round(sum(v for v in received if v is not None), 2)
            if all(v is not None for v in received)
            else None
        ),
        "sum_total_amount": (
            round(sum(v for v in totals if v is not None), 2)
            if all(v is not None for v in totals)
            else None
        ),
        "coverage": {
            "module_total": module_total,
            "rows_scanned": scanned,
            "pages_read": pages_read,
            "complete_for_range": complete,
        },
        "results": matched,
        "note": (
            "Zoho CRM payments - a separate ledger from the ERP payments "
            "tool. CRM rows mirror ERP payments (each carries its erp_id) "
            "and also include CRM-only payments, so report the two ledgers "
            "under separate labels and never add their totals together. "
            "receive_date is the calendar date the CRM records; no clock "
            "time is stored for a payment. record_created_at is when the "
            "CRM record was created/synced, not when the money arrived."
        ),
    }
