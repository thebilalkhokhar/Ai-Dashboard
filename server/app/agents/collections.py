"""Sub-agent 2: collections approach for one unpaid invoice.

Code builds the input (the unpaid invoice + that customer's payment history:
how many payments, how much they paid, how many days they usually pay late -
computed in Python by processors.summarize_payment_history). The AI picks an
approach and writes the message. Numbers must be copied from the input; when
history does not support a judgment the agent must say hold_off, low
confidence. Nothing is ever sent - drafts are stored text only.
"""

from __future__ import annotations

import json
from typing import Any, Tuple

from app.config import settings
from app.agents.llm import StructuredCaller
from app.schemas.agents import CollectionsOutput
from app.services.processors import format_money_tree

COLLECTIONS_SYSTEM = """You are an accounts-receivable analyst for a printing company.

You get ONE unpaid invoice plus that customer's payment history (pre-computed
numbers). You must NOT add, change or invent any number; any amount in your
message must be copied exactly from the input.

Your job:
1. Pick ONE approach for the rep to use next time they talk to this customer:
   - "soft_reminder" -> normal customer, small/paid-late-but-pays: a friendly nudge
   - "firm_nudge"    -> meaningful balance, clearly overdue, or slow payer history
   - "hold_off"      -> history does not justify contact (tiny balance, brand new
     invoice far from overdue, or data too thin)
2. reason: one sentence using the input facts.
3. message_draft: what the rep should say. Max 80 words. Copy amounts exactly
   (e.g. $56,600 or $2,379.50 exactly as shown in "balance"). No threats, no
   fees, no invented dates.
4. evidence: one sentence naming exactly which input facts you used.
Answer in the required JSON shape only.
"""


def collections_payload(item: dict, context: dict, payment_history: dict) -> dict:
    """item: an unpaid-invoice row from processors.unpaid_invoices.
    context: customer_context dict. payment_history: summary_payment_history.

    Money leaves Python already formatted ('$56,600'), so the draft can only
    copy a finished string - the model never sees 56600.0.
    """
    payload = {
        "task": "decide how to chase this unpaid invoice and draft a rep message",
        "invoice": {
            "id": item.get("id"),
            "inv_ref": item.get("inv_ref"),
            "balance": item.get("balance"),
            "total_amount": item.get("total_amount"),
            "amount_paid": item.get("amount_paid"),
            "days_invoice_age": item.get("days_invoice_age"),
            "days_overdue": max(0, int(item.get("days_invoice_age") or 0) - settings.OVERDUE_INVOICE_DAYS),
            "escalation_required": (
                float(item.get("balance") or 0) > 50_000
                or max(0, int(item.get("days_invoice_age") or 0) - settings.OVERDUE_INVOICE_DAYS) > 90
            ),
            "overdue": item.get("overdue"),
            "invoice_date": item.get("invoice_date"),
        },
        "customer_history": {
            "customer_id": context.get("customer_id"),
            "company_name": context.get("company_name"),
            "contact": context.get("contact"),
            "open_quotes_count": context.get("quotes", {}).get("count"),
            "open_orders_value": context.get("orders", {}).get("balance"),
            "payments": payment_history,
        },
    }
    return format_money_tree(payload)


async def collect_invoice(
    item: dict, context: dict, payment_history: dict, caller: StructuredCaller
) -> Tuple[CollectionsOutput, dict, dict]:
    """Returns (output_model, payload_used, usage_dict)."""
    payload = collections_payload(item, context, payment_history)
    messages = [
        {"role": "system", "content": COLLECTIONS_SYSTEM},
        {"role": "user", "content": json.dumps(payload, default=str)},
    ]
    parsed, usage = await caller.ainvoke(messages)
    return parsed, payload, usage
