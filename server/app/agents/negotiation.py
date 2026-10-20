"""Sub-agent 1: negotiation strategy for one open (stale) quote.

Code builds the input (the quote row + that customer's pre-computed history).
The AI judges WHY the quote is probably stuck, picks a tactic and writes a
short message for the sales rep. It may only use facts from the input JSON;
when the history does not support a judgment it must answer
likely_reason="unclear" with confidence="low" instead of guessing.

Nothing is ever sent anywhere - the message_draft is stored text only.
"""

from __future__ import annotations

import json
from typing import Any, Tuple

from app.agents.llm import StructuredCaller
from app.schemas.agents import NegotiationOutput

NEGOTIATION_SYSTEM = """You are a B2B sales-negotiation analyst for a printing company.

You get ONE open quote plus that customer's numeric history. All numbers are
pre-computed - you must NOT add, change or invent any number. If a message
mentions a price, it must be copied exactly from the input.

Your job:
1. Judge ONE likely_reason the quote is stuck, using ONLY the history:
   - "price"         -> history shows price sensitivity (many cheaper paid orders, or the quote is high vs past spend)
   - "no_response"   -> history shows an engaging customer who simply went quiet (recent contact, repeat orders)
   - "new_customer_no_history" -> the customer has no orders yet (new to us)
   - "repeat_customer_slow"    -> they buy from us but historically decide slowly
   - "unclear"       -> the history does not clearly support any reason above
2. If, and only if, you chose "unclear", use confidence "low" and a cautious tactic
   that says nothing specific ("follow_up" is safest).
3. Write message_draft: what the SALES REP should send. Max 80 words. No invented
   numbers, no promises, no discounts beyond what the input shows.
4. evidence: one sentence naming exactly which input facts you used.
Answer in the required JSON shape only.
"""


def negotiation_payload(item: dict, context: dict) -> dict:
    """Everything the AI may use. context comes from customer_context (pure)."""
    return {
        "task": "explain why this open quote is stuck and draft a rep message",
        "quote": {
            "id": item.get("id"),
            "ref": item.get("ref"),
            "amount": item.get("amount"),
            "status": item.get("status"),
            "days_stale": item.get("days_stale"),
            "created_at": item.get("created_at"),
        },
        "customer_history": {
            "customer_id": context.get("customer_id"),
            "company_name": context.get("company_name"),
            "contact": context.get("contact"),
            "past_quotes": context.get("quotes"),
            "past_orders": context.get("orders"),
            "past_payments": context.get("payments"),
            "matched_leads": [
                {
                    "matched": lead.get("matched"),
                    "ambiguous": lead.get("ambiguous"),
                    "is_new_customer": lead.get("is_new_customer"),
                    "quotes_since_lead": lead.get("quotes_since_lead"),
                    "orders_since_lead": lead.get("orders_since_lead"),
                    "payments_since_lead": lead.get("payments_since_lead"),
                }
                for lead in (context.get("leads") or [])
            ],
        },
    }


async def negotiate_quote(
    item: dict, context: dict, caller: StructuredCaller
) -> Tuple[NegotiationOutput, dict, dict]:
    """Returns (output_model, payload_used, usage_dict)."""
    payload = negotiation_payload(item, context)
    messages = [
        {"role": "system", "content": NEGOTIATION_SYSTEM},
        {"role": "user", "content": json.dumps(payload, default=str)},
    ]
    parsed, usage = await caller.ainvoke(messages)
    return parsed, payload, usage
