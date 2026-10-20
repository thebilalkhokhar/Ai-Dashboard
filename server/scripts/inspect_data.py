"""Read-only inspection of the local Mongo data + the ERP lead sample.

Prints:
  * distinct values with counts for the status fields
  * link checks: orders.quote_id -> quotes.id,
    payments.invoices_ids / payment_details[].invoice_id -> orders.id
  * a small cross-check between the /api/signals math and direct Mongo counts
  * ERP lead-outcomes on 50 leads that have an email or phone

Run it with the venv python from server/:
    .venv/bin/python -m scripts.inspect_data [--no-erp]

Nothing here writes to the original collections.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.erp_client import ERPClient  # noqa: E402
from app.services import processors  # noqa: E402


def print_distinct(rows: list[dict], field: str, label: str) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        key = repr(row.get(field))
        counts[key] = counts.get(key, 0) + 1
    print(f"{label}: {sum(counts.values())} rows")
    for key in sorted(counts, key=counts.get, reverse=True):
        print(f"    {key}: {counts[key]}")


async def main_async(use_erp: bool) -> None:
    aio = db.get_db()

    quote_rows = await aio.quotes_flat.find().to_list(200_000)
    order_rows = await aio.orders_flat.find().to_list(200_000)
    payment_rows = await aio.payments_flat.find().to_list(200_000)
    lead_rows = await aio.leads.find().to_list(200_000)
    print(
        f"row counts: quotes_flat={len(quote_rows)} orders_flat={len(order_rows)} "
        f"payments_flat={len(payment_rows)} leads={len(lead_rows)}"
    )

    print("\n=== DISTINCT STATUS VALUES ===")
    print_distinct(quote_rows, "status", "quotes.status")
    print_distinct(quote_rows, "status_order", "quotes.status_order")
    print_distinct(order_rows, "status", "orders.status")
    print_distinct(order_rows, "status_label", "orders.status_label")
    print_distinct(order_rows, "payment_status", "orders.payment_status")
    print_distinct(order_rows, "payment_status_label", "orders.payment_status_label")
    print_distinct(lead_rows, "status", "leads.status")

    # payment_status vs label consistency (evidence for the report)
    pairs: dict[tuple, int] = {}
    for row in order_rows:
        pair = (repr(row.get("payment_status")), repr(row.get("payment_status_label")))
        pairs[pair] = pairs.get(pair, 0) + 1
    print("\npayment_status / label pairs:")
    for (code, label), count in sorted(pairs.items(), key=lambda kv: -kv[1]):
        print(f"    {code} + {label}: {count}")

    print("\n=== LINK CHECKS ===")
    quote_ids = {row["id"] for row in quote_rows}
    order_ids = {row["id"] for row in order_rows}
    with_quote_id = [row for row in order_rows if row.get("quote_id") is not None]
    matched = [row for row in with_quote_id if row["quote_id"] in quote_ids]
    print(
        f"orders.quote_id -> quotes.id: {len(matched)}/{len(with_quote_id)} orders with a quote_id "
        f"find their quote ({100.0 * len(matched) / len(with_quote_id):.1f}%)"
    )

    invoices_touched = 0
    invoices_found = 0
    detail_invoices_touched = 0
    detail_invoices_found = 0
    for row in payment_rows:
        raw = row.get("invoices_ids")
        if raw:
            for invoice in str(raw).split(","):
                invoice = str(invoice).strip()
                if not invoice:
                    continue
                invoices_touched += 1
                if invoice.isdigit() and int(invoice) in order_ids:
                    invoices_found += 1
        for detail in row.get("payment_details") or []:
            invoice_id = detail.get("invoice_id")
            if invoice_id is None:
                continue
            detail_invoices_touched += 1
            if int(invoice_id) in order_ids:
                detail_invoices_found += 1
    if invoices_touched:
        print(
            f"payments.invoices_ids -> orders.id: {invoices_found}/{invoices_touched} "
            f"({100.0 * invoices_found / invoices_touched:.1f}%)"
        )
    if detail_invoices_touched:
        print(
            f"payment_details[].invoice_id -> orders.id: {detail_invoices_found}/"
            f"{detail_invoices_touched} ({100.0 * detail_invoices_found / detail_invoices_touched:.1f}%)"
        )

    print("\n=== SIGNAL CROSS-CHECK (processors vs direct Mongo counts) ===")
    now = datetime.now(timezone.utc)
    stale = processors.stale_quotes(quote_rows, now)
    unpaid = processors.unpaid_invoices(order_rows, now)
    print(f"stale_quotes (processors): {len(stale)}")
    print(f"unpaid_invoices (processors): {len(unpaid)}")
    paid_direct = await aio.orders_flat.count_documents({"payment_status": 2})
    not_paid_direct = await aio.orders_flat.count_documents({"payment_status": {"$ne": 2}})
    print(
        f"direct mongo: payment_status==2 (fully paid) = {paid_direct}; "
        f"!=2 (unpaid or partial) = {not_paid_direct}"
    )

    print("\n=== ERP LEAD SAMPLE (50 leads with an email or phone) ===")
    if not use_erp:
        print("skipped (--no-erp)")
        return
    sample_cursor = await aio.leads.aggregate(
        [
            {
                "$match": {
                    "$or": [
                        {"email": {"$type": "string", "$ne": ""}},
                        {"phone": {"$type": "string", "$ne": ""}},
                    ]
                }
            },
            {"$sample": {"size": 50}},
        ]
    )
    sample = await sample_cursor.to_list(50)
    payload = [
        {
            "lead_ref": row.get("zohoLeadId") or str(row.get("_id")),
            "created_at": row["createdAt"].isoformat() if row.get("createdAt") else None,
            "email": row.get("email") or "",
            "phone": row.get("phone") or "",
        }
        for row in sample
    ]
    if not payload:
        print("no leads with an email or phone found")
        return
    matched = ambiguous = 0
    async with ERPClient() as erp:
        batch_size = int(settings.ERP_LEAD_BATCH_SIZE)
        for start in range(0, len(payload), batch_size):
            batch = payload[start : start + batch_size]
            answers = await erp.lead_outcomes(batch)
            for answer in answers:
                matched += 1 if answer.get("matched") else 0
                ambiguous += 1 if answer.get("ambiguous") else 0
            print(f"  batch {start // batch_size + 1}: {len(batch)} leads sent")
    print(
        f"lead-outcomes on {len(payload)} sampled leads: matched={matched} ambiguous={ambiguous}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-erp", action="store_true", help="skip the ERP lead sample")
    args = parser.parse_args()
    asyncio.run(main_async(not args.no_erp))


if __name__ == "__main__":
    main()
