"""Tests for the model-text rules: money format, contact details, greetings.

No network and no LLM: these are the deterministic parts the pipeline runs on
the payload (before the model) and on the answer (after it).
"""

from __future__ import annotations

import pytest

from app.agents.collections import collections_payload
from app.agents.graph import (
    cache_lookup,
    invented_amounts,
    invented_contacts,
    money_values,
    normalise_money_text,
    sanitise_greeting,
    stable_cache_key,
)
from app.agents.negotiation import negotiation_payload
from app.services import processors

QUOTE = {
    "id": 20791,
    "ref": "20021-2026-10",
    "amount": 56600.0,
    "status": "Estimate",
    "days_stale": 3,
    "created_at": "2026-10-16T10:00:00+00:00",
}

CONTEXT = {
    "customer_id": 3312,
    "company_name": "Polkadot",
    "contact": {"email": "monica@sciencesolutionsllc.com", "phone": "818-225-0375"},
    "quotes": {"count": 2, "total_amount": 2379.5},
    "orders": {"count": 10, "total_amount": 81930.0, "amount_paid": 81930.0, "balance": 0.0},
    "payments": {"count": 8, "total_amount": 81930.0},
    "leads": [],
}

INVOICE = {
    "id": 33349,
    "inv_ref": "33349-2026-10",
    "balance": 2379.5,
    "total_amount": 56600.0,
    "amount_paid": 54220.5,
    "days_invoice_age": 11,
    "overdue": False,
    "invoice_date": "2026-10-09T10:00:00+00:00",
}

PAYMENT_HISTORY = {"count": 3, "total_paid": 28869.0, "lateness_avg_days": 3.4}


def test_payloads_send_formatted_money_to_the_model():
    quote_payload = negotiation_payload(QUOTE, CONTEXT)
    assert quote_payload["quote"]["amount"] == "$56,600"
    assert quote_payload["customer_history"]["past_quotes"]["total_amount"] == "$2,379.50"
    assert quote_payload["customer_history"]["past_orders"]["total_amount"] == "$81,930"
    # counts stay numeric so the model's reasoning is unchanged
    assert quote_payload["quote"]["days_stale"] == 3
    assert quote_payload["customer_history"]["past_orders"]["count"] == 10

    invoice_payload = collections_payload(INVOICE, CONTEXT, PAYMENT_HISTORY)
    assert invoice_payload["invoice"]["balance"] == "$2,379.50"
    assert invoice_payload["invoice"]["total_amount"] == "$56,600"
    assert invoice_payload["customer_history"]["open_orders_value"] == "$0"
    assert invoice_payload["customer_history"]["payments"]["total_paid"] == "$28,869"
    # the numeric flags are still computed from numbers, not from the text
    assert invoice_payload["invoice"]["escalation_required"] is False
    assert invoice_payload["invoice"]["days_overdue"] == 0


def test_money_validator_accepts_formatted_strings_and_rejects_inventions():
    payload = {"invoice": {"balance": "$2,379.50", "total_amount": "$56,600"}}
    assert invented_amounts("Your balance of $2,379.50 on the $56,600 order.", payload) == []
    assert invented_amounts("A special price of $1,234.56 today.", payload) == [1234.56]


def test_formatted_payload_keeps_the_same_cache_key():
    raw = {
        "quote": {"id": 1, "amount": 56600.0, "days_stale": 5},
        "customer_history": {
            "past_quotes": {"count": 1, "total_amount": 56600.0},
            "past_orders": {"count": 0, "balance": 0.0},
            "past_payments": {"count": 0, "total_amount": 0.0},
        },
    }
    formatted = processors.format_money_tree(raw)
    assert formatted["quote"]["amount"] == "$56,600"
    # formatting alone must not invalidate the 24h cache
    assert stable_cache_key("negotiation", raw) == stable_cache_key("negotiation", formatted)


def test_raw_money_in_model_text_is_reformatted():
    payload = {"invoice": {"balance": "$36,414.0", "total_amount": 28869.0}}
    text = "Invoice 33349 has a balance of $36414.0 and payments totaling 28869.0."
    fixed = normalise_money_text(text, payload)
    assert "$36,414" in fixed
    assert "$28,869" in fixed
    assert "33349" in fixed  # an invoice number is not money


def test_contact_details_must_be_verbatim_input():
    payload = {"customer_history": {"contact": CONTEXT["contact"]}}

    real = "Hi, questions? Call 818-225-0375 or email monica@sciencesolutionsllc.com."
    assert invented_contacts(real, payload) == []

    invented = "Call (818) 225-9999 or email sales@example.com, see https://example.com/pay"
    flagged = invented_contacts(invented, payload)
    assert "(818) 225-9999" in flagged
    assert "sales@example.com" in flagged
    assert "https://example.com/pay" in flagged


def test_greeting_name_must_come_from_contact_data():
    payload = {"customer_history": {"company_name": "Garcoa Inc.", "contact": CONTEXT["contact"]}}

    # "Monica" is in the contact email -> the greeting stays
    assert sanitise_greeting("Hi Monica, a quick note.", payload) == "Hi Monica, a quick note."

    # "Arthur" appears nowhere in the contact data -> neutral greeting
    fixed = sanitise_greeting("Hi Arthur, a quick note about the order.", payload)
    assert fixed == "Hello Garcoa Inc. team, a quick note about the order."

    # a trailing 'team' is swallowed, never duplicated
    fixed = sanitise_greeting("Hi Arthur team, a quick note.", payload)
    assert fixed == "Hello Garcoa Inc. team, a quick note."


def test_company_greeting_is_not_treated_as_a_first_name():
    payload = {"customer_history": {"company_name": "Saba Enterprises LLC", "contact": {}}}
    assert sanitise_greeting("Hello Saba team, following up.", payload) == (
        "Hello Saba team, following up."
    )
    assert sanitise_greeting("Hello Saba, following up.", payload) == "Hello Saba, following up."


def test_multi_word_company_greeting_is_left_alone():
    """The old one-word match turned 'Hello X LLC team' into a duplicated mess."""
    payload = {"customer_history": {"company_name": "Saba Enterprises LLC", "contact": {}}}
    draft = "Hello Saba Enterprises LLC team, following up on the quote."
    assert sanitise_greeting(draft, payload) == draft
    unknown = "Hello Someone Else team, following up."
    assert sanitise_greeting(unknown, payload) == "Hello Saba Enterprises LLC team, following up."


def test_money_after_a_greeting_is_never_eaten():
    """'$22,990,' has a comma inside the number - it is not an address."""
    payload = {"customer_history": {"company_name": "GKA SNACKS", "contact": {}}}
    draft = "Hello GKA SNACKS team $22,990, and thanks for your continued interest."
    assert sanitise_greeting(draft, payload) == draft


def test_day_counts_are_not_money():
    payload = {"payments": {"lateness_avg_days": 39.3, "total_paid": "$847,859", "count": 12}}
    assert 39.3 not in money_values(payload)
    assert 847859.0 in money_values(payload)
    # a day count dressed as money is rejected by the money validator
    assert invented_amounts("average lateness of $39.30 days", payload) == [39.3]
    assert invented_amounts("12 payments totaling $847,859", payload) == []


def test_cache_lookup_skips_outputs_with_raw_money():
    item = {"input_hash": "h1"}
    recent = [
        {
            "finished_epoch": 100.0,
            "collections": [
                {
                    "input_hash": "h1",
                    "source": "agent",
                    "flags": [],
                    "message_draft": "Balance $36414.0 is open.",  # raw money
                    "reason": "unpaid",
                    "evidence": "input",
                }
            ],
        }
    ]
    assert cache_lookup(recent, [dict(item)], 0.0) == {}

    recent[0]["collections"][0]["message_draft"] = "Balance $36,414 is open."
    assert set(cache_lookup(recent, [dict(item)], 0.0)) == {"h1"}


def test_money_text_uses_the_agents_format():
    assert processors.money_text(56600) == "$56,600"
    assert processors.money_text(2379.5) == "$2,379.50"
    assert processors.money_text("$1,234.00") == "$1,234"
