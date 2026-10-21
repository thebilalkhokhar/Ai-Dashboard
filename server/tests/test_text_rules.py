"""Unit tests for the deterministic text rules (no LLM, no Mongo).

Covers what a real run taught us: a draft may only mention THIS item's money,
the Master brief must not keep its own thinking or numbers nobody computed,
and a model that drops spaces gets one regeneration and then a code fix.
"""

from __future__ import annotations

import pytest

from app.agents.graph import allowed_amounts, invented_amounts, master_problems
from app.agents.collections import collections_payload, reminder_tone
from app.agents.master import MASTER_SYSTEM, build_master_brief
from app.agents.text_rules import (
    glued_words,
    invented_numbers,
    prompt_echo,
    reasoning_artifacts,
    repair_glued_words,
)

QUOTE_PAYLOAD = {
    "quote": {"id": 20791, "amount": "$56,800", "days_stale": 5},
    "customer_history": {
        "company_name": "Polkadot",
        "contact": {"email": "monica@example.com", "phone": "818-225-0375"},
        # a lifetime total that happens to look like a price
        "past_orders": {"count": 10, "total_amount": "$56,600", "balance": "$3,945"},
        "past_payments": {"count": 8, "total_paid": "$77,985"},
    },
}

INVOICE_PAYLOAD = {
    "invoice": {
        "id": 27608,
        "balance": "$357,500",
        "total_amount": "$400,000",
        "amount_paid": "$42,500",
        "days_overdue": 326,
        "tone": "firm",
    },
    "customer_history": {"company_name": "Pure 7", "contact": {}},
}


def test_a_draft_may_only_mention_its_own_money_fields():
    assert allowed_amounts("negotiation", QUOTE_PAYLOAD) == {56800.0}
    # the history total is data about the customer, not this quote's price
    assert invented_amounts("Checking in on the $56,600 estimate.", QUOTE_PAYLOAD, "negotiation") == [56600.0]
    assert invented_amounts("Checking in on the $56,800 estimate.", QUOTE_PAYLOAD, "negotiation") == []
    # without a kind the old whole-payload behaviour is still what callers get
    assert invented_amounts("Checking in on the $56,600 estimate.", QUOTE_PAYLOAD) == []


def test_collections_draft_may_use_balance_total_and_paid():
    allowed = allowed_amounts("collections", INVOICE_PAYLOAD)
    assert allowed == {357500.0, 400000.0, 42500.0}
    assert invented_amounts("Balance $357,500 on the $400,000 order.", INVOICE_PAYLOAD, "collections") == []
    assert invented_amounts("Please pay $350,000 now.", INVOICE_PAYLOAD, "collections") == [350000.0]


def test_lead_drafts_have_no_money_at_all():
    assert allowed_amounts("lead_followup", {"lead": {"company": "Zaskii Boyz"}}) == set()
    assert invented_amounts("We can do this for $500.", {"lead": {}}, "lead_followup") == [500.0]


def test_real_glued_typos_are_found_and_repaired():
    text = "Happy to providenext step, open todiscuss and requestartwork or the daysoverdue."
    assert glued_words(text) == ["providenext", "todiscuss", "requestartwork", "daysoverdue"]
    assert (
        repair_glued_words(text)
        == "Happy to provide next step, open to discuss and request artwork or the days overdue."
    )


@pytest.mark.parametrize(
    "text",
    [
        # genuine vocabulary that contains smaller words
        "Whenever the turnaround is tight, please look over the online invoice and",
        "notebook artwork insight install income inside together output overall",
        # a company name from the payload is data, not a typo
        "Following up on the Polkadot estimate for Saba Enterprises LLC.",
    ],
)
def test_clean_business_english_is_not_flagged(text):
    assert glued_words(text, payload_blob="polkadot saba enterprises llc") == []


def test_master_rejects_leftover_thinking_and_unknown_ids():
    payload = {"customers": [{"outputs": [{"invoice_id": 27608, "balance": 357500.0}]}]}
    garbage = {
        "bullets": [
            "Pure 7 - collections: invoice 27608 $357,500 and 33,045? (no)",
            "Wait, actually the quote is $9,000.",
            "Nothing to add here.",
        ],
        "priorities": [],
        "conflicts_resolved": [],
    }
    problems = master_problems(garbage, payload)
    assert any("? (no)" in problem for problem in problems)
    assert any("33045" in problem for problem in problems)
    assert any("9000" in problem for problem in problems)
    assert any("wait" in problem.lower() for problem in problems)


def test_master_accepts_a_brief_copied_from_the_data():
    payload = {
        "customers": [
            {"outputs": [{"invoice_id": 27608, "balance": 357500.0, "customer_name": "Pure 7"}]}
        ],
        "signal_summary": {"unpaid_invoices_count": 1},
    }
    brief = {
        "bullets": [
            "Pure 7 owes $357,500 on invoice 27608.",
            "1 unpaid invoice needs a reminder.",
            "No lead is stuck waiting for details this run.",
        ],
        "priorities": [
            {
                "customer_name": "Pure 7",
                "why": "Unpaid invoice 27608 balance $357,500",
                "suggested_action": "A human rep sends the stored payment reminder.",
            }
        ],
        "conflicts_resolved": ["Every customer appears exactly once in this run."],
    }
    assert master_problems(brief, payload) == []


def test_master_rejects_its_own_instructions_repeated():
    echoed = "If every customer appears only once, state that explicitly."
    problems = master_problems(
        {"bullets": [echoed, "b", "c"], "priorities": [], "conflicts_resolved": []},
        {},
    )
    assert any("copies its own instructions" in problem for problem in problems)
    assert prompt_echo(echoed, MASTER_SYSTEM)
    # the exact sentence a real run produced
    real_run = (
        "No customer appears in more than one sub-agent output with conflicting "
        "actions; if any, summarize here."
    )
    assert prompt_echo(real_run, MASTER_SYSTEM)


def test_invented_numbers_ignore_years_and_small_counts():
    allowed = {27608.0, 357500.0}
    # a money amount is allowed as '$357,500' and as the raw '357500.0'
    assert invented_numbers(["invoice 27608 for 357,500, created 2026-10-09, 3 days"], allowed) == []
    assert invented_numbers(["invoice 33045 for 357,500"], allowed) == [33045.0]


def test_master_may_copy_a_number_that_sits_in_the_text_it_was_given():
    """'111 days overdue' inside an escalation note is data the master can read."""
    payload = {
        "customers": [
            {
                "outputs": [
                    {
                        "invoice_id": 30963,
                        "balance": 29575.0,
                        "customer_name": "MNJ Enterprise LLC",
                        "escalation_note": (
                            "Internal note to sales manager: invoice 30963 ($29,575 balance; "
                            "111 days overdue)."
                        ),
                    }
                ]
            }
        ]
    }
    brief = {
        "bullets": [
            "MNJ Enterprise LLC: invoice 30963 $29,575 is 111 days overdue.",
            "Two open quotes need a follow-up.",
            "No lead is stuck this run.",
        ],
        "priorities": [],
        "conflicts_resolved": [],
    }
    assert master_problems(brief, payload) == []
    brief["bullets"][0] = "MNJ Enterprise LLC: invoice 30963 $29,575 is 121 days overdue."
    assert any("121" in problem for problem in master_problems(brief, payload))


def test_tone_comes_from_days_overdue():
    assert reminder_tone(0) == "gentle"
    assert reminder_tone(12) == "standard"
    assert reminder_tone(95) == "firm"


def test_collections_payload_gives_the_model_one_tone_to_copy():
    item = {
        "id": 27608,
        "balance": 357500.0,
        "total_amount": 400000.0,
        "amount_paid": 42500.0,
        "days_invoice_age": 356,
        "overdue": True,
    }
    payload = collections_payload(item, {"customer_id": 7, "company_name": "Pure 7"}, {"count": 0})
    assert payload["invoice"]["days_overdue"] == 326
    assert payload["invoice"]["tone"] == "firm"
    assert payload["invoice"]["escalation_required"] is True
    assert payload["invoice"]["balance"] == "$357,500"


def test_template_brief_is_built_from_the_data_only():
    grouped = {
        10: {
            "customer_name": "Pure 7",
            "items": [
                {"customer_name": "Pure 7", "invoice_id": 27608, "balance": 357500.0},
                {"customer_name": "Pure 7", "quote_id": 20791, "quote_amount": 56800.0},
            ],
        }
    }
    summary = {"unpaid_balance_total": 357500.0, "stale_quotes_count": 1, "unpaid_invoices_count": 1}
    brief = build_master_brief(grouped, summary, [])
    assert len(brief.bullets) == 3
    assert any("27608" in bullet and "$357,500" in bullet for bullet in brief.bullets)
    assert any("20791" in bullet and "$56,800" in bullet for bullet in brief.bullets)
    # the same customer appears twice, so the conflict is stated, not invented
    assert len(brief.conflicts_resolved) == 1
    assert "appears in 2 sub-agent outputs" in brief.conflicts_resolved[0]
    assert brief.priorities[0].customer_name == "Pure 7"
    # and the template passes the Master validator by construction
    payload = {
        "customers": [{"outputs": [item for group in grouped.values() for item in group["items"]]}],
        "signal_summary": summary,
    }
    assert master_problems(brief.model_dump(), payload) == []
