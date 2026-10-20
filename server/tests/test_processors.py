"""Small, deterministic pytest suite for the pure processors.

Run with:  .venv/bin/python -m pytest tests -q
No Mongo and no network are touched: every test feeds plain dicts in.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.services import processors

NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)


def quote(**over):
    """A quote doc shaped like the ERP answer."""
    row = {
        "id": 1,
        "ref": "Q-1",
        "status": "Quote Created",
        "status_order": 4,
        "created_at": (NOW - timedelta(days=5)).isoformat(),
        "amount": 1000.0,
        "customer": {"id": 10, "company_name": "ACME"},
        "sale_rep": {"id": 5, "name": "Sue"},
    }
    row.update(over)
    return row


def order(**over):
    """An order doc shaped like the ERP answer."""
    row = {
        "id": 100,
        "inv_ref": "O-100",
        "status": 2,
        "status_label": "Approved",
        "payment_status": 0,
        "payment_status_label": "Not Paid",
        "invoice_date": (NOW - timedelta(days=1)).isoformat(),
        "total_amount": 500.0,
        "amount_paid": 0.0,
        "customer": {"id": 10, "company_name": "ACME"},
        "sale_rep": {"id": 5, "name": "Sue"},
    }
    row.update(over)
    return row


# ---------------------------------------------------------------- parse_date


def test_parse_date_iso_with_offset_becomes_utc():
    dt = processors.parse_date("2026-10-19T14:24:32-05:00")
    assert dt is not None
    assert dt.tzinfo is timezone.utc
    assert dt.isoformat() == "2026-10-19T19:24:32+00:00"


def test_parse_date_naive_is_read_as_us_central():
    dt = processors.parse_date("2026-10-09 05:02:29")
    assert dt.tzinfo is timezone.utc
    assert dt.isoformat() == "2026-10-09T10:02:29+00:00"  # -05:00 applied


def test_parse_date_date_only_and_none():
    assert processors.parse_date("2026-10-01").isoformat().startswith("2026-10-01T05:00:00+00:00")
    assert processors.parse_date(None) is None
    assert processors.parse_date("") is None
    assert processors.parse_date("not a date") is None


def test_parse_date_keeps_aware_datetime():
    dt = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)
    assert processors.parse_date(dt) is dt


# -------------------------------------------------------------- stale_quotes


def test_stale_quotes_uses_status_and_age():
    stale = processors.stale_quotes(
        [
            quote(id=1, status="Quote Created", created_at=(NOW - timedelta(days=10)).isoformat()),
            quote(id=2, status="Quote Created", created_at=(NOW - timedelta(days=2)).isoformat()),  # too young
            quote(id=3, status="Rejected", created_at=(NOW - timedelta(days=90)).isoformat()),  # closed
            quote(id=4, created_at=None),  # no date, ignored
        ],
        NOW,
    )
    assert [row["id"] for row in stale] == [1]
    assert stale[0]["amount"] == 1000.0
    assert stale[0]["days_stale"] == 10
    assert stale[0]["customer"]["company_name"] == "ACME"


def test_stale_quotes_respects_threshold_argument():
    stale = processors.stale_quotes(
        [quote(created_at=(NOW - timedelta(days=5)).isoformat())], NOW, days=5
    )
    # exactly 5 full days old is not "older than 5 days"
    assert stale == []


def test_stale_quotes_drops_converted_quotes():
    """A quote that already has an order is not stale - it converted."""
    stale = processors.stale_quotes(
        [
            quote(id=1, created_at=(NOW - timedelta(days=10)).isoformat()),
            quote(id=2, created_at=(NOW - timedelta(days=10)).isoformat()),
        ],
        NOW,
        order_quote_ids=[1],
    )
    assert [row["id"] for row in stale] == [2]


def test_stale_quotes_respects_max_age():
    """Quotes older than STALE_QUOTE_MAX_AGE_DAYS are dead history."""
    stale = processors.stale_quotes(
        [quote(created_at=(NOW - timedelta(days=40)).isoformat())], NOW
    )
    assert stale == []


def test_stale_quotes_keeps_latest_per_customer_and_sorts_by_amount():
    """One conversation per customer, biggest deals first."""
    stale = processors.stale_quotes(
        [
            quote(id=1, amount=100.0, created_at=(NOW - timedelta(days=10)).isoformat()),
            quote(id=2, amount=900.0, created_at=(NOW - timedelta(days=9)).isoformat()),  # newer, same customer
            quote(id=3, amount=500.0, customer={"id": 11, "company_name": "BETA"}, created_at=(NOW - timedelta(days=8)).isoformat()),
        ],
        NOW,
    )
    # customer 10 keeps only quote 2 (newest of its two), customer 11 keeps 3;
    # result sorted by amount descending
    assert [(row["id"], row["amount"]) for row in stale] == [(2, 900.0), (3, 500.0)]


# ----------------------------------------------------------- unpaid_invoices


def test_unpaid_invoices_balance_and_overdue_flag():
    rows = processors.unpaid_invoices(
        [
            order(id=1, payment_status=0, invoice_date=(NOW - timedelta(days=40)).isoformat()),
            order(
                id=2,
                payment_status=1,
                payment_status_label="Partially Paid",
                total_amount=1000.0,
                amount_paid=400.0,
                invoice_date=(NOW - timedelta(days=10)).isoformat(),
            ),
            order(id=3, payment_status=2, payment_status_label="Paid"),  # excluded
        ],
        NOW,
    )
    # sorted by balance (biggest first): order 2 has the larger balance
    assert [row["id"] for row in rows] == [2, 1]
    assert rows[0]["balance"] == 600.0
    assert rows[0]["overdue"] is False
    assert rows[1]["balance"] == 500.0
    assert rows[1]["overdue"] is True


def test_unpaid_invoices_drops_zero_total_nulls_and_zero_balance():
    """Empty invoices and paid-up invoices are not worth chasing."""
    rows = processors.unpaid_invoices(
        [
            order(id=1, payment_status=None, total_amount=0.0),        # empty invoice
            order(id=2, payment_status=0, total_amount=300.0, amount_paid=300.0),  # zero balance
            order(id=3, payment_status=None, total_amount=250.0),      # must pass
        ],
        NOW,
    )
    assert [(row["id"], row["balance"]) for row in rows] == [(3, 250.0)]


# ------------------------------------------------------------ stalled_orders


def test_stalled_orders_non_final_status_and_age():
    rows = processors.stalled_orders(
        [
            order(id=1, status_label="In Production", invoice_date=(NOW - timedelta(days=3)).isoformat()),
            order(id=2, status_label="In Production", invoice_date=(NOW - timedelta(days=1)).isoformat()),
            order(id=3, status_label="Delivered", invoice_date=(NOW - timedelta(days=30)).isoformat()),
            order(id=4, invoice_date=(NOW - timedelta(days=30)).isoformat(), status_label=None, status=9),
        ],
        NOW,
    )
    # sorted by age (stalled the longest first)
    assert [row["id"] for row in rows] == [4, 1]  # too young + final status drop out
    assert rows[0]["status_label"] == "Unknown (status=9)"


# ---------------------------------------------------------- customer_context


def test_customer_context_joins_and_ignores_other_customers():
    row = processors.customer_context(
        10,
        quotes=[quote(amount=200.0), quote(amount=300.0, customer={"id": 99, "company_name": "OTHER"})],
        orders=[order(total_amount=500.0, amount_paid=100.0), order(total_amount=0.0, amount_paid=0.0, payment_status=2, payment_status_label="Paid")],
        payments=[{"customer": {"id": 10, "company_name": "ACME", "email": "a@b.c", "phone_no": "(912) 730-9863"}, "amount": "250.00", "received_at": None}],
        lead_outcomes=[
            {
                "lead_ref": "zoho-1",
                "matched": True,
                "matched_by": "email",
                "ambiguous": False,
                "customer": {"id": 10, "company_name": "ACME"},
                "quotes": {"count": 1, "total_amount": 900},
            },
            {
                "lead_ref": "zoho-2",
                "matched": True,
                "matched_by": "phone",
                "ambiguous": True,
                "customer": {"id": 10, "company_name": "ACME"},
            },
        ],
        notes=["hello"],
    )
    assert row["quotes"]["count"] == 1  # only customer 10's quotes are joined
    assert row["quotes"]["total_amount"] == 200.0
    assert row["orders"]["count"] == 2
    assert row["orders"]["balance"] == 400.0
    assert row["payments"]["total_amount"] == 250.0
    assert row["contact"]["email"] == "a@b.c"  # taken from the payments nested customer
    assert [lead["ambiguous"] for lead in row["leads"]] == [False, True]
    assert row["notes"] == ["hello"]


def test_money_strings_and_missing_values_are_tolerated():
    row = processors.customer_context(
        10,
        quotes=[{"amount": "1,250.50", "customer": {"id": 10}}],
        orders=[{"total_amount": "750", "amount_paid": None, "customer": {"id": 10}}],
        payments=[{"amount": "$100"}],
    )
    assert row["quotes"]["total_amount"] == 1250.5
    assert row["orders"]["balance"] == 750.0  # missing amount_paid means 0
    assert row["payments"]["total_amount"] == 0.0  # junk string is not invented money


def test_customer_context_date_bounds_use_parsed_datetimes():
    customer = {"id": 10}
    earlier = "2026-10-18T20:00:00+02:00"
    later = "2026-10-20T04:00:00-05:00"

    row = processors.customer_context(
        10,
        quotes=[
            {"customer": customer, "created_at": later},
            {"customer": customer, "created_at": earlier},
        ],
        orders=[
            {"customer": customer, "invoice_date": later},
            {"customer": customer, "invoice_date": earlier},
        ],
        payments=[
            {"customer_id": 10, "received_at": later},
            {"customer_id": 10, "received_at": earlier},
        ],
    )

    expected_first = "2026-10-18T18:00:00+00:00"
    expected_last = "2026-10-20T09:00:00+00:00"
    for summary in (row["quotes"], row["orders"], row["payments"]):
        assert summary["first_at"] == expected_first
        assert summary["last_at"] == expected_last
        assert processors.parse_date(summary["first_at"]) <= processors.parse_date(
            summary["last_at"]
        )


# ------------------------------------------------------- lead descriptions


def test_parse_lead_description_json_shape():
    text = json.dumps(
        {
            "product": "Pouch",
            "product_type": "Pinch Bottom Pouch",
            "quantity": ["5000"],
            "dimensions": {"width": "4", "height": "6", "gusset": "0", "unit": "In"},
            "attributeOptions": {
                "material": "Foil",
                "finishes": ["Softouch Lamination", "Spot Gloss"],
            },
            "artwork": [{"url": "https://example.com/a.pdf"}],
        }
    )
    parsed = processors.parse_lead_description(text)
    assert parsed["shape"] == "json"
    assert parsed["product"] == "Pouch"
    assert parsed["type"] == "Pinch Bottom Pouch"
    assert parsed["quantity"] == "5000"
    assert parsed["size"] == "4 x 6 In"
    assert parsed["material"] == "Foil"
    assert parsed["finish"] == "Softouch Lamination, Spot Gloss"
    assert parsed["artwork_present"] is True
    assert processors.missing_lead_fields(parsed) == []


def test_parse_lead_description_json_empty_artwork_is_missing():
    text = json.dumps({"product": "mylar bag", "quantity": "1000", "artwork": []})
    parsed = processors.parse_lead_description(text)
    assert parsed["shape"] == "json"
    assert parsed["artwork_present"] is False
    assert parsed["quantity"] == "1000"
    # size/material/finish are enumerable in JSON but were not provided
    assert processors.missing_lead_fields(parsed) == ["artwork", "size", "material", "finish"]


def test_parse_lead_description_key_value_lines():
    text = "type: mylar bag\nmaterial: soft touch\nquantity: 500\nphone_no: 5551234567"
    parsed = processors.parse_lead_description(text)
    assert parsed["shape"] == "key_value"
    assert parsed["type"] == "mylar bag"
    assert parsed["material"] == "soft touch"
    assert parsed["quantity"] == "500"
    missing = processors.missing_lead_fields(parsed)
    assert "quantity" not in missing and "material" not in missing
    assert "size" in missing and "finish" in missing
    assert parsed["artwork_present"] is None
    assert "artwork" not in missing  # never guessed from key:value text


def test_parse_lead_description_not_given_values_are_missing():
    text = "Product: mylar bag\nMaterial: \u2014 not given\nQuantity: not given\nSizes: \u2014 not given"
    parsed = processors.parse_lead_description(text)
    assert parsed["shape"] == "key_value"
    assert parsed["product"] == "mylar bag"
    assert parsed["material"] is None
    assert parsed["quantity"] is None
    assert parsed["size"] is None
    assert processors.missing_lead_fields(parsed) == ["quantity", "size", "material", "finish"]


def test_parse_lead_description_not_specified_is_treated_as_missing():
    """The website-chat summaries write 'Quantity: not specified' - that is a
    missing value, not a literal quantity."""
    text = "Product: Die Cut Business cards\nMaterial: not specified\nQuantity: not specified"
    parsed = processors.parse_lead_description(text)
    assert parsed["material"] is None
    assert parsed["quantity"] is None
    assert "quantity" in processors.missing_lead_fields(parsed)
    assert "material" in processors.missing_lead_fields(parsed)


def test_parse_lead_description_inline_key_value_pairs():
    text = "Product: mylar bag Material: soft touch Quantity: not given"
    parsed = processors.parse_lead_description(text)
    assert parsed["shape"] == "key_value"
    assert parsed["product"] == "mylar bag"
    assert parsed["material"] == "soft touch"
    assert parsed["quantity"] is None


def test_parse_lead_description_pure_prose_extracts_nothing():
    text = "We need custom printed boxes for our bakery, please call me."
    parsed = processors.parse_lead_description(text)
    assert parsed["shape"] == "prose"
    assert parsed["product"] is None
    assert parsed["artwork_present"] is None
    # prose tells us nothing, so nothing is called missing
    assert processors.missing_lead_fields(parsed) == []


def test_parse_lead_description_key_value_artwork_line_is_never_missing():
    """'Artwork Files: No files uploaded' is key:value text - the strict rule
    only allows an explicit empty JSON artwork list to mean Missing."""
    text = "Product: 3.5g Mylar Bags\nQuantity: 500\nArtwork Files: No files uploaded"
    parsed = processors.parse_lead_description(text)
    assert parsed["shape"] == "key_value"
    assert parsed["artwork_present"] is None
    assert "artwork" not in processors.missing_lead_fields(parsed)


# -------------------------------------------------------------- stuck_leads


def lead(**over):
    row = {
        "zohoLeadId": "L-1",
        "firstName": "Keiwan",
        "lastName": "Bentley",
        "company": "Zaskii Boyz",
        "email": "keiwan@example.com",
        "phone": "5551234567",
        "status": "New",
        "source": "Ecommerce",
        "description": json.dumps(
            {"product": "mylar bag", "quantity": "500", "artwork": []}
        ),
        "createdAt": (NOW - timedelta(days=5)).isoformat(),
        "lastActivityAt": (NOW - timedelta(days=3)).isoformat(),
        "leadDesk": {"stage": "open", "flagReason": ""},
    }
    row.update(over)
    return row


def test_stuck_leads_filters_status_recency_flag_stage_and_junk():
    rows = processors.stuck_leads(
        [
            lead(),  # selected
            lead(zohoLeadId="L-2", status="Converted"),  # wrong status
            lead(zohoLeadId="L-3", status="Attempted to Contact"),  # selected
            lead(zohoLeadId="L-4", lastActivityAt=(NOW - timedelta(days=20)).isoformat()),  # too old
            lead(zohoLeadId="L-5", leadDesk={"stage": "open", "flagReason": "duplicate"}),  # flagged
            lead(zohoLeadId="L-6", leadDesk={"stage": "closed", "flagReason": ""}),  # stage not open
            lead(zohoLeadId="L-7", description=""),  # junk: no description
            lead(zohoLeadId="L-8", lastActivityAt=None, createdAt=(NOW - timedelta(days=2)).isoformat()),  # createdAt fallback
            lead(zohoLeadId="L-9", lastActivityAt="not a date", createdAt=None),  # no usable date
        ],
        NOW,
    )
    # newest activity first; leads without a leadDesk stay in (nothing to exclude them)
    assert [row["lead_id"] for row in rows] == ["L-8", "L-1", "L-3"]
    assert rows[0]["days_since_activity"] == 2


def test_stuck_leads_row_carries_parsed_fields_and_missing():
    rows = processors.stuck_leads([lead()], NOW)
    assert len(rows) == 1
    row = rows[0]
    assert row["contact_name"] == "Keiwan Bentley"
    assert row["company"] == "Zaskii Boyz"
    assert row["product"] == "mylar bag"
    assert row["quantity"] == "500"
    assert row["artwork_present"] is False
    assert row["missing"] == ["artwork", "size", "material", "finish"]
    assert row["has_contact_details"] is True
    assert row["days_since_activity"] == 3


def test_stuck_leads_days_argument_and_contact_info_missing():
    rows = processors.stuck_leads(
        [lead(email="", phone="", lastActivityAt=(NOW - timedelta(days=10)).isoformat())],
        NOW,
        days=7,
    )
    assert rows == []  # 10 days old is outside a 7-day window
    row = processors.stuck_leads([lead(email="", phone="")], NOW)[0]
    assert "contact_info" in row["missing"]
    assert row["has_contact_details"] is False
