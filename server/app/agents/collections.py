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
1. Pick ONE approach for the rep: "soft_reminder" (normal customer, small or
   late-but-paying balance), "firm_nudge" (meaningful balance, clearly overdue
   or a slow-payer history), "hold_off" (history does not justify contact).
2. tone is given by CODE from the days overdue ("gentle", "standard" or "firm").
   Copy it exactly; never invent your own tone.
3. message_draft: ALWAYS a customer-facing payment reminder - this text goes to
   the customer, so write it in the given tone. Max 80 words. Copy amounts
   exactly (e.g. $56,600 or $2,379.50 exactly as shown in "balance"). No
   threats, no fees, no invented dates, and NEVER an internal note, a manager
   instruction or a mention of escalation. Every item gets this draft, even a
   very overdue one - escalation is a separate flag, not a replacement.
4. escalation_note: OPTIONAL internal note for a human manager, or null. Write
   one only when the numbers genuinely need a manager (big balance, very old
   invoice, disputed history). Keep it factual and short.
5. reason: one sentence using the input facts.
6. evidence: one sentence naming exactly which input facts you used.
Answer in the required JSON shape only.
"""


def reminder_tone(days_overdue: int) -> str:
    """The tone CODE picks from how late the invoice is (the model copies it)."""
    if days_overdue <= 0:
        return "gentle"
    if days_overdue <= 30:
        return "standard"
    return "firm"


TONE_INSTRUCTIONS = {
    "gentle": "friendly courtesy note, no pressure",
    "standard": "polite but clear payment request with a due-date reference",
    "firm": "serious, direct request for payment or a call to arrange it",
}


def collections_payload(item: dict, context: dict, payment_history: dict) -> dict:
    """item: an unpaid-invoice row from processors.unpaid_invoices.
    context: customer_context dict. payment_history: summary_payment_history.

    Money leaves Python already formatted ('$56,600'), so the draft can only
    copy a finished string - the model never sees 56600.0.
    """
    days_overdue = max(0, int(item.get("days_invoice_age") or 0) - settings.OVERDUE_INVOICE_DAYS)
    tone = reminder_tone(days_overdue)
    payload = {
        "task": "decide how to chase this unpaid invoice and draft the customer reminder",
        "invoice": {
            "id": item.get("id"),
            "inv_ref": item.get("inv_ref"),
            "balance": item.get("balance"),
            "total_amount": item.get("total_amount"),
            "amount_paid": item.get("amount_paid"),
            "days_invoice_age": item.get("days_invoice_age"),
            "days_overdue": days_overdue,
            "tone": tone,
            "tone_instruction": TONE_INSTRUCTIONS[tone],
            "escalation_required": (
                float(item.get("balance") or 0) > 50_000 or days_overdue > 90
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
