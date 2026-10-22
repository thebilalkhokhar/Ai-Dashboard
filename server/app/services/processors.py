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

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

from app.config import settings

# --------------------------------------------------------------------------
# ERP timestamps are US Central (-05:00), sometimes with an explicit
# offset, sometimes naive. NH / naive values are read with that offset.
ERP_TZ = timezone(timedelta(hours=-5))

# The reader's calendar. Payment instants and the chat's relative dates
# ("today", "yesterday") are interpreted on this calendar, because that is
# the day the user means; the ERP's own business-date fields (invoice
# dates, quote dates) stay on ERP_TZ. Asia/Karachi has no daylight saving,
# so a fixed +05:00 is exact, not an approximation.
USER_TZ = timezone(timedelta(hours=5))

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
    """Money/counts can arrive as numbers or strings like '1350.00', '$2,000'."""
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return 0.0


# Field names that hold money in the agent payloads. Used to turn every amount
# into text BEFORE the model sees it, so drafts cannot print "36414.0".
MONEY_FIELDS = frozenset(
    {
        "amount",
        "quote_amount",
        "total_amount",
        "amount_paid",
        "balance",
        "total_paid",
        "open_orders_value",
        "open_quotes_value",
        "unpaid_balance_total",
    }
)


def money_text(value: Any) -> str:
    """Money as the agents/chat write it: '$56,600' or '$2,379.50'.

    Whole dollars lose the cents; anything with cents keeps two decimals.
    """
    amount = to_number(value)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount == int(amount):
        return f"{sign}${int(amount):,}"
    return f"{sign}${amount:,.2f}"


def format_money_tree(value: Any) -> Any:
    """Copy of a payload with every money field formatted as text.

    Only `MONEY_FIELDS` are touched, so counts, IDs, ages and booleans stay
    numeric for the cards and for the pipeline's own checks.
    """
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in MONEY_FIELDS and isinstance(item, (int, float)) and not isinstance(item, bool):
                result[key] = money_text(item)
            else:
                result[key] = format_money_tree(item)
        return result
    if isinstance(value, list):
        return [format_money_tree(item) for item in value]
    return value


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
    max_age_days: Optional[int] = None,
    order_quote_ids: Optional[Iterable[int]] = None,
    closed_statuses: Optional[frozenset] = None,
) -> list[dict]:
    """Quotes worth chasing, tightened in v2 to stop flooding the agents:

      * a quote that already has an order (orders.quote_id -> quotes.id) is
        NOT stale - it converted;
      * the age window matters: older than STALE_QUOTE_DAYS (3) but younger
        than STALE_QUOTE_MAX_AGE_DAYS (30). Really old open quotes are
        dead history, not sales opportunities;
      * only the LATEST open quote per customer is kept (one conversation
        per customer, not one per quote);
      * sorted by amount descending so agents work the biggest deals first.
    """
    now = now or datetime.now(timezone.utc)
    threshold = settings.STALE_QUOTE_DAYS if days is None else days
    max_age = settings.STALE_QUOTE_MAX_AGE_DAYS if max_age_days is None else max_age_days
    closed = (
        frozenset(CLOSED_QUOTE_STATUSES)
        if closed_statuses is None
        else frozenset(closed_statuses)
    )
    converted_ids = set(order_quote_ids or ())

    kept_by_customer: dict[Any, dict] = {}
    for quote in quotes:
        status = contact_to_s(quote.get("status"))
        if status in closed:
            continue
        if quote.get("id") in converted_ids:
            continue
        age = age_days(parse_date(quote.get("created_at")), now)
        if age is None or age <= threshold or age > max_age:
            continue
        row = {
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
        customer_key = quote.get("customer").get("id") if isinstance(quote.get("customer"), dict) else None
        current = kept_by_customer.get(customer_key)
        # keep the newest quote per customer (age_days: smaller = newer)
        if current is None or row["age_days"] < current["age_days"]:
            kept_by_customer[customer_key] = row

    result = list(kept_by_customer.values())
    result.sort(key=lambda row: row["amount"], reverse=True)
    return result


def unpaid_invoices(
    orders: Iterable[Mapping[str, Any]],
    now: Optional[datetime] = None,
    *,
    overdue_days: Optional[int] = None,
) -> list[dict]:
    """Orders worth chasing for money, tightened in v2:

      * payment_status 0 (Not Paid), 1 (Partially Paid), or null WITH a
        non-zero total (null on a zero-total invoice is nothing to chase);
      * balance must be strictly greater than 0;
      * sorted by balance descending (biggest debt first).
    """
    now = now or datetime.now(timezone.utc)
    threshold = settings.OVERDUE_INVOICE_DAYS if overdue_days is None else overdue_days
    result = []
    for order in orders:
        payment_status = order.get("payment_status")
        if payment_status == PAYMENT_STATUS_PAID:
            continue
        total = to_number(order.get("total_amount"))
        if payment_status is None and total == 0:
            continue  # nothing to collect on an empty invoice
        paid = to_number(order.get("amount_paid"))
        balance = round(total - paid, 2)
        if balance <= 0:
            continue  # paid up (or overpaid) already
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
                "due_date": iso_or_none(order.get("due_date")),
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
    """UNRELIABLE SIGNAL - do not build decisions on it.

    Kept for compatibility only (see /api/signals "reliable": false). The ERP
    gives orders no last-update date and everything in the window sits in
    'Approved', so every order older than STALLED_ORDER_DAYS is counted. That
    is normal production flow, not a delay. Agents must ignore this signal.
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
# leads (Zoho lead documents already mirrored in the Mongo `leads` collection)
# --------------------------------------------------------------------------

# Lead statuses that mean the lead is still sitting at the top of the funnel.
STUCK_LEAD_STATUSES = frozenset({"New", "Attempted to Contact"})

# The Mongo query + projection both the pipeline and GET /api/leads/stuck use
# to fetch stuck-lead candidates (everything else is filtered in Python).
LEAD_STATUS_QUERY = {"status": {"$in": sorted(STUCK_LEAD_STATUSES)}}
LEAD_FIELDS = {
    "zohoLeadId": 1, "firstName": 1, "lastName": 1, "company": 1,
    "email": 1, "phone": 1, "status": 1, "source": 1, "description": 1,
    "createdAt": 1, "lastActivityAt": 1, "leadDesk": 1, "_id": 0,
}

# Values in a free-text lead description that mean "the lead did not say".
_LEAD_EMPTY_VALUES = frozenset(
    {
        "", "-", "\u2014", "n/a", "na", "none", "no",
        "not given", "not provided", "not stated", "not specified",
        "unspecified", "unknown",
    }
)

# The description keys Python can extract (everything else is ignored).
_LEAD_KEY_MAP = {
    "product": "product",
    "product name": "product",
    "item": "product",
    "type": "type",
    "product type": "type",
    "box type": "type",
    "quantity": "quantity",
    "qty": "quantity",
    "quantities": "quantity",
    "size": "size",
    "sizes": "size",
    "dimension": "size",
    "dimensions": "size",
    "material": "material",
    "finish": "finish",
    "finishes": "finish",
    "lamination": "finish",
}

_LEAD_KEY_RE = re.compile(
    r"(?i)(product\s*name|product\s*type|box\s*type|product|item|quantities|quantity|qty|"
    r"dimensions|dimension|sizes|size|materials|material|finishes|finish|lamination|type)\s*:\s*"
)


def _lead_clean(value: Any) -> Optional[str]:
    """Trimmed description value, or None when it means 'not given'."""
    if value is None:
        return None
    text = str(value).strip()
    # strip em-dash / hyphen prefixes used by the chat summaries ("— not given")
    while text[:1] in {"-", "\u2014"}:
        text = text[1:].strip()
    return None if text.casefold() in _LEAD_EMPTY_VALUES else text


def _lead_join(value: Any) -> Optional[str]:
    """Flatten scalars / lists from JSON descriptions into one clean string."""
    if isinstance(value, (list, tuple)):
        parts = [_lead_clean(item) for item in value]
        return ", ".join(part for part in parts if part) or None
    return _lead_clean(value)


def _lead_dimensions_text(value: Any) -> Optional[str]:
    """'width x height (x gusset) unit' from a dimensions dict or string."""
    if isinstance(value, dict):
        width = _lead_clean(value.get("width"))
        height = _lead_clean(value.get("height"))
        gusset = _lead_clean(value.get("gusset"))
        unit = _lead_clean(value.get("unit"))
        parts = [part for part in (width, height) if part]
        if gusset and gusset not in {"0", "0.0"}:
            parts.append(gusset)
        text = " x ".join(parts)
        if text and unit:
            text = f"{text} {unit}"
        return text or None
    return _lead_clean(value)


def parse_lead_description(text: Any) -> dict:
    """Parse one lead's free-text description into fixed fields. Pure Python.

    Descriptions come in three shapes and only these are read:
      (a) a JSON string such as {"quantity":"1000","description":"square 6x6 inch",
          "artwork":[{url...}],...},
      (b) "key: value" lines / inline pairs (type, material, phone_no, email),
      (c) prose - nothing structured is extracted.

    Returns a dict with:
      shape            "json" | "key_value" | "prose"
      product/type/quantity/size/material/finish   str or None
      artwork_present   True / False / None

    RULE: artwork_present is False ONLY when the description is JSON with an
    explicit empty "artwork" list; True when it has a non-empty list; None
    (unknown) for prose or key:value text. Never guess.
    """
    result = {
        "shape": "prose",
        "product": None,
        "type": None,
        "quantity": None,
        "size": None,
        "material": None,
        "finish": None,
        "artwork_present": None,
    }
    raw = str(text or "").strip()
    if not raw:
        return result

    data: Optional[dict] = None
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            data = loaded
    except (ValueError, TypeError):
        data = None

    if data is not None:
        result["shape"] = "json"
        options = data.get("attributeOptions") if isinstance(data.get("attributeOptions"), dict) else {}
        result["product"] = _lead_join(data.get("product"))
        result["type"] = _lead_join(data.get("product_type") or data.get("type"))
        result["quantity"] = _lead_join(data.get("quantity"))
        result["size"] = (
            _lead_dimensions_text(data.get("dimensions"))
            or _lead_join(data.get("size"))
            or _lead_dimensions_text((options or {}).get("dimensions"))
        )
        result["material"] = _lead_join(data.get("material")) or _lead_join((options or {}).get("material"))
        result["finish"] = _lead_join(data.get("finishes") or data.get("finish")) or _lead_join(
            (options or {}).get("finishes") or (options or {}).get("finish")
        )
        artwork = data.get("artwork")
        if isinstance(artwork, list):
            result["artwork_present"] = bool(artwork)
        return result

    pairs: list[tuple[str, str, int, int]] = []  # (canonical key, value, start, end)
    matches = list(_LEAD_KEY_RE.finditer(raw))
    for index, match in enumerate(matches):
        canonical = _LEAD_KEY_MAP.get(" ".join(match.group(1).lower().split()))
        if canonical is None:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        # a value runs to the next known key, but never past its first line
        value = raw[match.end() : end].split("\n", 1)[0].strip()
        pairs.append((canonical, value, match.start(), match.end()))

    if pairs:
        result["shape"] = "key_value"
        for canonical, value, _start, _end in pairs:
            if result[canonical] is None:
                result[canonical] = _lead_clean(value)
    return result


def missing_lead_fields(parsed: Mapping[str, Any]) -> list[str]:
    """Fields the lead did not provide - computed in Python, never by the AI.

    A field is missing only when we can actually tell:
      * json / key_value descriptions enumerate what the lead gave, so an
        absent quantity/size/material/finish was not provided;
      * artwork counts as missing ONLY when the JSON "artwork" list is
        explicitly empty (None means unknown, not missing);
      * prose tells us nothing, so nothing is called missing.
    """
    missing: list[str] = []
    if parsed.get("artwork_present") is False:
        missing.append("artwork")
    if parsed.get("shape") in {"json", "key_value"}:
        for field in ("quantity", "size", "material", "finish"):
            if not parsed.get(field):
                missing.append(field)
    return missing


def stuck_leads(
    leads: Iterable[Mapping[str, Any]],
    now: Optional[datetime] = None,
    *,
    days: Optional[int] = None,
    statuses: Optional[frozenset] = None,
) -> list[dict]:
    """Leads that are stuck before becoming an order. Pure Python.

    A lead counts when:
      * its status is "New" or "Attempted to Contact" (still top-of-funnel);
      * its last activity is within the last `days` days (LEAD_ACTIVE_DAYS by
        default) - recent activity means the lead is alive, not dead history;
      * it has a description (nothing to interpret without one);
      * leadDesk has not flagged it (flagReason) and its stage is still "open".

    Rows are sorted by newest activity first. The caller caps the list.
    """
    now = now or datetime.now(timezone.utc)
    window = settings.LEAD_ACTIVE_DAYS if days is None else days
    allowed = frozenset(STUCK_LEAD_STATUSES) if statuses is None else frozenset(statuses)

    result: list[dict] = []
    for lead in leads:
        status = contact_to_s(lead.get("status"))
        if status not in allowed:
            continue
        description = str(lead.get("description") or "").strip()
        if not description:
            continue  # nothing for the agent to interpret
        desk = lead.get("leadDesk") if isinstance(lead.get("leadDesk"), Mapping) else {}
        if contact_to_s(desk.get("flagReason")):
            continue  # a human already flagged this lead
        stage = contact_to_s(desk.get("stage"))
        if stage and stage != "open":
            continue  # the desk already moved it out of the open stage
        activity = parse_date(lead.get("lastActivityAt")) or parse_date(lead.get("createdAt"))
        if activity is None:
            continue
        age = age_days(activity, now)
        if age is None or age < 0 or age > window:
            continue  # too old to be an alive-but-stuck lead

        parsed = parse_lead_description(description)
        email = contact_to_s(lead.get("email"))
        phone = contact_to_s(lead.get("phone"))
        missing = missing_lead_fields(parsed)
        if not email and not phone:
            missing.append("contact_info")
        first = contact_to_s(lead.get("firstName"))
        last = contact_to_s(lead.get("lastName"))
        contact_name = " ".join(part for part in (first, last) if part) or None
        result.append(
            {
                "lead_id": contact_to_s(lead.get("zohoLeadId")) or str(lead.get("_id") or ""),
                "contact_name": contact_name,
                "company": contact_to_s(lead.get("company")) or None,
                "status": status,
                "source": contact_to_s(lead.get("source")) or None,
                "days_since_activity": int(age),
                "last_activity_at": iso_or_none(lead.get("lastActivityAt")),
                "created_at": iso_or_none(lead.get("createdAt")),
                "product": parsed.get("product"),
                "type": parsed.get("type"),
                "quantity": parsed.get("quantity"),
                "size": parsed.get("size"),
                "material": parsed.get("material"),
                "finish": parsed.get("finish"),
                "artwork_present": parsed.get("artwork_present"),
                "description_shape": parsed.get("shape"),
                "missing": missing,
                "has_contact_details": bool(email or phone),
                "description": description,
            }
        )
    # newest activity first
    result.sort(key=lambda row: row["days_since_activity"])
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
        "first_at": min(dates).isoformat() if dates else None,
        "last_at": max(dates).isoformat() if dates else None,
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
        "first_at": min(dates).isoformat() if dates else None,
        "last_at": max(dates).isoformat() if dates else None,
    }


def summarize_payments(records: Iterable[Mapping[str, Any]]) -> dict:
    rows = list(records)
    dates = [d for d in (parse_date(r.get("received_at")) for r in rows) if d is not None]
    total = sum(to_number(r.get("amount")) for r in rows)
    return {
        "count": len(rows),
        "total_amount": round(total, 2),
        "first_at": min(dates).isoformat() if dates else None,
        "last_at": max(dates).isoformat() if dates else None,
    }


def payment_lateness_days(payments: Iterable[Mapping[str, Any]]) -> list[float]:
    """How many days each paid invoice waited, in pure Python.

    Payment records know the invoice date (payment_details[].invoice.date,
    a naive US Central string) and the received date. The gap is the customer's
    payment lateness. Deposits that arrive BEFORE the invoice date come out
    negative and are kept as-is (real data - it means very fast payment)."""
    gaps: list[float] = []
    for payment in payments:
        received = parse_date(payment.get("received_at"))
        if received is None:
            continue
        for detail in payment.get("payment_details") or []:
            invoice = detail.get("invoice") or {}
            invoice_date = parse_date(invoice.get("date"))
            if invoice_date is None:
                continue
            gaps.append((received - invoice_date).total_seconds() / 86400.0)
    return gaps


def summarize_payment_history(payments: Iterable[Mapping[str, Any]]) -> dict:
    """Numbers-only payment summary for the collections agent."""
    rows = list(payments)
    gaps = [g for g in payment_lateness_days(rows) if g >= 0]
    total_paid = sum(to_number(row.get("total_payment_used") or row.get("amount")) for row in rows)
    return {
        "count": len(rows),
        "total_paid": round(total_paid, 2),
        "lateness_avg_days": round(sum(gaps) / len(gaps), 1) if gaps else None,
        "lateness_median_days": round(sorted(gaps)[len(gaps) // 2], 1) if gaps else None,
        "lateness_samples": len(gaps),
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
