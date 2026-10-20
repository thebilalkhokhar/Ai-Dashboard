"""Pure-Python business logic (the deterministic layer).

Everything here is ordinary arithmetic over plain dicts shaped like ERP
records. No AI/LLM code: all counting, filtering and math happen right here.
Nothing in this file talks to Mongo or the network except `build_customer_
context`, which is the thin async wrapper that feeds the pure join.

Status constants were set from real ERP data on 2026-10-20 (see the
inspection script output in the project report). Values in the data sets:
  * quote. status        4 -> "Quote Created", 5 -> "Estimate"
  * order. status        2 -> "Approved"
  * order. payment_status: 0 -> "Not Paid", 1 -> "Partially Paid",
                           2 -> "Paid", null -> no payment recorded yet
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

from app.config import settings

# --------------------------------------------------------------------------
# ERP timestamps are US Central (-05:00), sometimes with an explicit
# offset, sometimes naive. NH / naive values are read with that offset.
ERP_TZ = timezone(timedelta(hours=-5))

# --------------------------------------------------------------------------
# Quote statuses that mean the opportunity is over. Anything else counts as
# "open". Only closed values are listed, so a future new open status is
# counted as stale instead of being silently ignored.
CLOSED_QUOTE_STATUSES = frozenset(
    {"Rejected", "Cancelled", "Expired", "Lost", "Declined", "Closed"}
)

# ---------------------------------------------------------------------------
# Order statuses that mean fulfillment is finished or dead. Anything else
# counts as "in progress" for the stalled signal.
FINAL_ORDER_STATUSES = frozenset(
    {"Delivered", "Completed", "Cancelled", "Rejected", "Returned", "Closed"}
)

# payment_status codes (report matches the real ERP)
PAYMENT_STATUS_PAID = 2
PAYMENT_STATUS_PARTIAL = 1
PAYMENT_STATUS_UNPAID = 0

# Fields that do not exist on ERP order records (checked on real data).
# Orders have no artwork files, no order notes and no shipping address.
ORDER_MISSING_FIELDS = [
    "artwork_files",
    "order_notes",
    "shipping_address",
    "expected_shipment_date",
]

# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------


def parse_date(value: Any) -> Optional[datetime]:
    """Turn ERP dates ('2026-10-19T14:24:32-05:00', '2026-10-01',
    Mongo datetimes) into timezone-aware UTC datetimes.

    Naive values are read with the ERP's US Central offset. Returns None when
    the input cannot be understood.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        if not text:
            return None
        iso = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(iso)
        except ValueError:
            parsed = None
        if parsed is None:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ERP_TZ)
    return parsed.astimezone(timezone.utc)


def to_number(value: Any) -> float:
    """Money/counts can arrive as numbers or strings like '1350.00'."""
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return 0.0


def age_days(start: Optional[datetime], now: datetime) -> Optional[float]:
    """Age in exact (float) days between two aware datetimes."""
    if start is None:
        return None
    return (now - start).total_seconds() / 86400.0


def contact_to_s(value: Any) -> str:
    """Trim emails / phones."""
    return str(value).strip() if value is not None else ""


def norm_email(value: Any) -> str:
    """Lowercase, trimmed email for comparisons."""
    return contact_to_s(value).lower()


_NON_DIGIT = re.compile(r"[^0-9]")


def norm_phone(value: Any) -> str:
    """Only digits; US numbers: keep the last 10 digits ('+19127309863' and
    '(912) 730-9863' both become '9127309863')."""
    digits = _NON_DIGIT.sub("", contact_to_s(value))
    if len(digits) > 10:
        digits = digits[-10:]
    return digits


# --------------------------------------------------------------------------
# reference objects
# --------------------------------------------------------------------------


def customer_ref(record: Mapping[str, Any]) -> Optional[dict]:
    """The nested {id, company_name} object from a quote/order/payments doc."""
    nested = record.get("customer")
    if not isinstance(nested, dict):
        return None
    return {
        "id": nested.get("id"),
        "company_name": nested.get("company_name"),
    }


def sale_rep_ref(record: Mapping[str, Any]) -> Optional[dict]:
    """The nested {id, name} rep object from a quote/order doc."""
    nested = record.get("sale_rep")
    rep: dict[str, Any]
    if isinstance(nested, dict):
        rep = nested
    elif isinstance(nested, (int, float, str)):  # payments store the rep as a plain id
        rep = {"id": nested}
    else:
        return None
    return {"id": rep.get("id"), "name": rep.get("name")}


def iso_or_none(value: Any) -> Optional[str]:
    """ISO-UTC string for JSON output."""
    dt = parse_date(value)
    return dt.isoformat() if dt is not None else None


# --------------------------------------------------------------------------
# signals
# --------------------------------------------------------------------------


def stale_quotes(
    quotes: Iterable[Mapping[str, Any]],
    now: Optional[datetime] = None,
    *,
    days: Optional[int] = None,
    closed_statuses: Optional[frozenset] = None,
) -> list[dict]:
    """Quotes still open more than STALE_QUOTE_DAYS after they were created.

    Older than N days means the exact age in days is greater than N (a quote
    from exactly 3 days ago is not stale on a 3-day rule).
    """
    now = now or datetime.now(timezone.utc)
    threshold = settings.STALE_QUOTE_DAYS if days is None else days
    closed = (
        frozenset(CLOSED_QUOTE_STATUSES)
        if closed_statuses is None
        else frozenset(closed_statuses)
    )
    result = []
    for quote in quotes:
        status = contact_to_s(quote.get("status"))
        if status in closed:
            continue
        age = age_days(parse_date(quote.get("created_at")), now)
        if age is None or age <= threshold:
            continue
        result.append(
            {
                "id": quote.get("id"),
                "ref": quote.get("ref") or quote.get("inv_ref"),
                "status": status,
                "status_order": quote.get("status_order"),
                "amount": round(to_number(quote.get("amount")), 2),
                "age_days": round(age, 2),
                "days_stale": int(age),
                "created_at": iso_or_none(quote.get("created_at")),
                "customer": customer_ref(quote),
                "sale_rep": sale_rep_ref(quote),
            }
        )
    result.sort(key=lambda row: (row["age_days"], row["amount"]), reverse=True)
    return result


def unpaid_invoices(
    orders: Iterable[Mapping[str, Any]],
    now: Optional[datetime] = None,
    *,
    overdue_days: Optional[int] = None,
) -> list[dict]:
    """Orders/invoices that are not fully paid (Not Paid + Partially Paid).

    payment_status: 0 = Not Paid, 1 = Partially Paid, 2 = Paid,
    null = no payment recorded yet (seen on zero-total invoices in the data).
    `balance` = total_amount - amount_paid (`amount_paid` missing means 0).
    """
    now = now or datetime.now(timezone.utc)
    threshold = settings.OVERDUE_INVOICE_DAYS if overdue_days is None else overdue_days
    result = []
    for order in orders:
        if to_number(order.get("payment_status")) == PAYMENT_STATUS_PAID and order.get("payment_status") is not None:
            continue
        total = to_number(order.get("total_amount"))
        paid = to_number(order.get("amount_paid"))
        balance = round(total - paid, 2)
        age = age_days(parse_date(order.get("invoice_date")), now)
        if age is None:
            continue
        result.append(
            {
                "id": order.get("id"),
                "inv_ref": order.get("inv_ref"),
                "status": order.get("status"),
                "status_label": order.get("status_label"),
                "payment_status": order.get("payment_status"),
                "payment_status_label": order.get("payment_status_label"),
                "total_amount": round(total, 2),
                "amount_paid": round(paid, 2),
                "balance": balance,
                "overdue": age > threshold,
                "days_invoice_age": int(age),
                "invoice_date": iso_or_none(order.get("invoice_date")),
                "customer": customer_ref(order),
                "sale_rep": sale_rep_ref(order),
            }
        )
    # biggest balances first, then oldest invoice date
    result.sort(key=lambda row: (row["balance"], row["days_invoice_age"]), reverse=True)
    return result


def stalled_orders(
    orders: Iterable[Mapping[str, Any]],
    now: Optional[datetime] = None,
    *,
    days: Optional[int] = None,
    final_statuses: Optional[frozenset] = None,
) -> list[dict]:
    """Orders sitting in a non-final production status for longer than
    STALLED_ORDER_DAYS.

    Order records have no last-update timestamp (checked on real data), so the
    age is taken from `invoice_date` - the only date field the ERP sends.
    """
    now = now or datetime.now(timezone.utc)
    threshold = settings.STALLED_ORDER_DAYS if days is None else days
    final = (
        frozenset(FINAL_ORDER_STATUSES)
        if final_statuses is None
        else frozenset(final_statuses)
    )
    result = []
    for order in orders:
        status_label = contact_to_s(order.get("status_label"))
        if not status_label:  # every real status has a label; guard anyway
            status_label = f"Unknown (status={order.get('status')})"
        if status_label in final:
            continue
        age = age_days(parse_date(order.get("invoice_date")), now)
        if age is None or age <= threshold:
            continue
        result.append(
            {
                "id": order.get("id"),
                "inv_ref": order.get("inv_ref"),
                "status": order.get("status"),
                "status_label": status_label,
                "payment_status": order.get("payment_status"),
                "payment_status_label": order.get("payment_status_label"),
                "invoice_date": iso_or_none(order.get("invoice_date")),
                "days_invoice_age": int(age),
                "age_days": round(age, 2),
                "total_amount": round(to_number(order.get("total_amount")), 2),
                "customer": customer_ref(order),
                "sale_rep": sale_rep_ref(order),
            }
        )
    result.sort(key=lambda row: (row["age_days"], row["total_amount"]), reverse=True)
    return result


# --------------------------------------------------------------------------
# customer context
# --------------------------------------------------------------------------


def summarize_quotes(records: Iterable[Mapping[str, Any]]) -> dict:
    """Counts/totals for a customer's quotes (amounts via to_number only)."""
    rows = list(records)
    status_counts: Counter[str] = Counter()
    dates: list[datetime] = []
    total = 0.0
    for row in rows:
        status_counts[contact_to_s(row.get("status"))] += 1
        dates.append(parse_date(row.get("created_at")))
        total += to_number(row.get("amount"))
    dates = [d for d in dates if d is not None]
    return {
        "count": len(rows),
        "total_amount": round(total, 2),
        "status_counts": dict(status_counts),
        "first_at": dates[0].isoformat() if dates else None,
        "last_at": dates[-1].isoformat() if dates else None,
    }


def summarize_orders(records: Iterable[Mapping[str, Any]]) -> dict:
    rows = list(records)
    payment_status_counts: Counter[str] = Counter()
    dates: list[datetime] = []
    total = paid = 0.0
    unpaid = partially_paid = fully_paid = 0
    for row in rows:
        label = row.get("payment_status_label") or "No payment recorded"
        payment_status_counts[label] += 1
        code = row.get("payment_status")
        if code == PAYMENT_STATUS_PAID:
            fully_paid += 1
        elif code == PAYMENT_STATUS_PARTIAL:
            partially_paid += 1
        else:
            unpaid += 1
        dates.append(parse_date(row.get("invoice_date")))
        total += to_number(row.get("total_amount"))
        paid += to_number(row.get("amount_paid"))
    dates = [d for d in dates if d is not None]
    return {
        "count": len(rows),
        "total_amount": round(total, 2),
        "amount_paid": round(paid, 2),
        "balance": round(total - paid, 2),
        "unpaid_count": unpaid,
        "partially_paid_count": partially_paid,
        "fully_paid_count": fully_paid,
        "payment_status_counts": dict(payment_status_counts),
        "first_at": dates[0].isoformat() if dates else None,
        "last_at": dates[-1].isoformat() if dates else None,
    }


def summarize_payments(records: Iterable[Mapping[str, Any]]) -> dict:
    rows = list(records)
    dates = [d for d in (parse_date(r.get("received_at")) for r in rows) if d is not None]
    total = sum(to_number(r.get("amount")) for r in rows)
    return {
        "count": len(rows),
        "total_amount": round(total, 2),
        "first_at": dates[0].isoformat() if dates else None,
        "last_at": dates[-1].isoformat() if dates else None,
    }


def _same_customer(customer_id: Any, record: Mapping[str, Any]) -> bool:
    """True when the record belongs to `customer_id` (nested customer.id or a
    flat customer_id field, which is what payments carry)."""
    nested = record.get("customer")
    if isinstance(nested, dict) and nested.get("id") == customer_id:
        return True
    return record.get("customer_id") == customer_id


def customer_context(
    customer_id: Any,
    quotes: Iterable[Mapping[str, Any]],
    orders: Iterable[Mapping[str, Any]],
    payments: Iterable[Mapping[str, Any]],
    lead_outcomes: Iterable[Mapping[str, Any]] = (),
    contact: Optional[Mapping[str, Any]] = None,
    notes: Optional[list[str]] = None,
) -> dict:
    """One joined snapshot for a customer.

    Quotes / orders / payments come from the *_flat Mongo collections (linked
    by customer id). `lead_outcomes` are ERP lead-outcomes answers whose
    customer is this one (ambiguous matches kept with a flag). All amounts are
    computed here; nothing is left to an AI to compute.

    Only this customer's rows are joined - rows for other customers are
    dropped even when the caller passed them in.
    """
    quote_list = [row for row in quotes if _same_customer(customer_id, row)]
    order_list = [row for row in orders if _same_customer(customer_id, row)]
    payment_list = [row for row in payments if _same_customer(customer_id, row)]
    outcome_list = list(lead_outcomes)

    if contact is None:
        # the ERP puts customer email / phone_no on payment records only
        for payment_row in payment_list:
            nested = payment_row.get("customer") or {}
            if contact_to_s(nested.get("email")) or contact_to_s(nested.get("phone_no")):
                contact = {
                    "email": contact_to_s(nested.get("email")) or None,
                    "phone": contact_to_s(nested.get("phone_no")) or None,
                }
                break

    company_name = next(
        (
            contact_to_s(p.get("customer", {}).get("company_name"))
            for p in payment_list + order_list + quote_list
            if contact_to_s(p.get("customer", {}).get("company_name"))
        ),
        "",
    )

    return {
        "customer_id": customer_id,
        "company_name": company_name,
        "contact": dict(contact or {}),
        "quotes": summarize_quotes(quote_list),
        "orders": summarize_orders(order_list),
        "payments": summarize_payments(payment_list),
        "leads": [
            {
                "lead_ref": row.get("lead_ref"),
                "matched": row.get("matched"),
                "matched_by": row.get("matched_by"),
                "ambiguous": bool(row.get("ambiguous")),
                "customer_id": (row.get("customer") or {}).get("id"),
                "customer_name": (row.get("customer") or {}).get("company_name"),
                "is_new_customer": row.get("is_new_customer"),
                "quotes_since_lead": row.get("quotes") or {},
                "orders_since_lead": row.get("orders") or {},
                "payments_since_lead": row.get("payments") or {},
            }
            for row in outcome_list
        ],
        "notes": list(notes or []),
    }


# --------------------------------------------------------------------------
# async wrapper used by the API (the only part that touches Mongo / the ERP)
# --------------------------------------------------------------------------


def _lead_match_query(email: str, phone: str) -> dict:
    """Mongo query that finds leads whose email or phone matches. This is an
    exact contact match - never a company-name guess."""
    clauses = []
    if email:
        clauses.append({"email": re.compile("^" + re.escape(email) + "$", re.I)})
    core = norm_phone(phone)
    if len(core) >= 7:
        # allow +/-, spaces, dashes and brackets inside the stored phone:
        # US numbers with or without a leading country code '1'.
        pattern = "1?[^0-9]*" + "[^0-9]*".join(core) + "[^0-9]*$"
        clauses.append({"phone": re.compile(pattern)})
    return {"$or": clauses} if clauses else {"__no_contact__": True}


async def build_customer_context(
    customer_id: int,
    database: Any = None,
    erp: Any = None,
    max_leads: int = 100,
) -> dict:
    """Fetch the customer's rows, find matching leads via the ERP
    lead-outcomes endpoint, and call the pure join."""
    if database is None:
        from app import db as app_db  # noqa: PLC0415 - imports kept local to stay testable
        database = app_db.get_db()

    cid = int(customer_id)
    quotes = await database.quotes_flat.find({"customer.id": cid}).to_list(max_leads + 200)
    orders = await database.orders_flat.find({"customer.id": cid}).to_list(max_leads + 200)
    payments = await database.payments_flat.find(
        {"$or": [{"customer_id": cid}, {"customer.id": cid}]}
    ).to_list(max_leads + 200)

    # contact details only exist on payment records
    email = phone = ""
    for payment_row in payments:
        nested = payment_row.get("customer") or {}
        email = contact_to_s(nested.get("email")) or email
        phone = contact_to_s(nested.get("phone_no")) or phone
        if email and phone:
            break

    contact = {"email": email or None, "phone": phone or None}
    notes: list[str] = []

    outcomes: list[dict] = []
    ambiguous = 0
    if erp is None:
        notes.append("ERP client is not available, so leads cannot be linked right now.")
    else:
        match_query = _lead_match_query(email, phone)
        if "$or" not in match_query:
            notes.append("No email or phone found for this customer, so leads cannot be linked.")
        else:
            fields = {"zohoLeadId": 1, "email": 1, "phone": 1, "createdAt": 1, "_id": 0}
            leads = await database.leads.find(match_query, fields).to_list(max_leads)
            skipped_leads = 0
            batch_size = int(settings.ERP_LEAD_BATCH_SIZE)
            for start in range(0, len(leads), batch_size):
                batch = leads[start : start + batch_size]
                payload = [
                    {
                        "lead_ref": str(row.get("zohoLeadId") or row.get("_id")),
                        "created_at": (
                            row["createdAt"].isoformat() if isinstance(row.get("createdAt"), datetime) else None
                        ) or iso_or_none(row.get("createdAt")),
                        "email": contact_to_s(row.get("email")),
                        "phone": contact_to_s(row.get("phone")),
                    }
                    for row in batch
                ]
                answers = await erp.lead_outcomes(payload)
                for answer in answers:
                    if answer.get("matched") and (answer.get("customer") or {}).get("id") == cid:
                        outcomes.append(answer)
                    elif answer.get("ambiguous"):
                        ambiguous += 1
                        outcomes.append(answer)
                    else:
                        skipped_leads += 1
            if len(leads) == max_leads:
                notes.append(f"Lead lookup stopped early (checked {max_leads} leads, the cap).")
            if skipped_leads:
                notes.append(
                    f"{skipped_leads} leads with matching contact details belong to a different customer."
                )
            if ambiguous:
                notes.append(
                    f"{ambiguous} lead matches were ambiguous - the ERP picked the oldest customer, so a human should check."
                )

    return customer_context(
        cid,
        quotes=quotes,
        orders=orders,
        payments=payments,
        lead_outcomes=outcomes,
        contact=contact,
        notes=notes,
    )
