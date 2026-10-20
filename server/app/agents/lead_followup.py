"""Sub-agent 3: follow-up strategy for one stuck lead.

Code builds the input (processors.stuck_leads + processors.parse_lead_description
did the selecting and parsing). The AI only interprets: what is missing or
blocking this lead, who should own the next step, and a short draft message
for a HUMAN rep. It may only use facts from the input JSON; when the facts do
not support a judgment it must answer owner_team="unclear" with
confidence="low" instead of guessing.

Nothing is ever sent anywhere - the message_draft is stored text only.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Tuple

from app.agents.llm import StructuredCaller
from app.schemas.agents import LeadFollowupOutput

LEAD_FOLLOWUP_SYSTEM = """You are a lead-follow-up analyst for a printing company.

You get ONE stuck lead: a recent lead (status New or Attempted to Contact)
whose last activity was days ago. Python already extracted the structured
fields from the messy free-text description and computed the missing_fields
list. You must NOT add, change or invent any number, name, email, phone or
URL. The message_draft is for a HUMAN sales rep to send; nothing is ever sent
automatically.

Your job:
1. missing: copy the input's missing_fields list EXACTLY (same entries, same
   order). It was computed by Python - do not add entries, do not remove any.
2. blocker: one sentence naming what is most likely stopping this lead from
   becoming an order, using ONLY the input facts (a missing quantity, no
   artwork, an unanswered request for a quote, etc.). When the facts do not
   support any judgment, write exactly "unclear".
3. owner_team: who should take the next step:
   - "design"  -> the gap is artwork or design help (e.g. the lead uploaded
     nothing and needs a template or a proof)
   - "sales"   -> the gap is commercial: pricing, lead times, a quote, or a
     plain follow-up on an unanswered conversation
   - "unclear" -> the input does not support a judgment; use confidence "low"
4. action_plan: one or two short sentences describing what a HUMAN rep should
   do next. No promises, no invented prices or timelines.
5. message_draft: what the rep should say to the lead. Max 80 words. No
   prices, no discounts, no promises, and no invented numbers, names, phones,
   emails or URLs. If you address the lead by name, use only the contact_name
   from the input; otherwise address the company, or say nothing who-specific.
6. evidence: one sentence naming exactly which input facts you used.
Answer in the required JSON shape only.
"""


def lead_followup_payload(item: dict, note: Optional[str] = None) -> dict:
    """Everything the AI may use for one stuck lead row.

    Sent fields are exactly: first name, company, status, days since activity,
    the Python-parsed fields, missing_fields, artwork_present and the raw
    description truncated to 800 characters (plus the optional match note).
    Deliberately excluded: email, phone and any lead id - the model must not
    write contact details, and the pipeline fills lead_id in itself. Whether
    contact details exist travels as "contact_info" inside missing_fields.
    """
    payload = {
        "task": "assess one stuck lead and draft a follow-up message for a human rep",
        "lead": {
            "contact_name": item.get("contact_name"),
            "company": item.get("company"),
            "status": item.get("status"),
            "days_since_activity": item.get("days_since_activity"),
            "parsed": {
                "product": item.get("product"),
                "type": item.get("type"),
                "quantity": item.get("quantity"),
                "size": item.get("size"),
                "material": item.get("material"),
                "finish": item.get("finish"),
            },
            "missing_fields": list(item.get("missing") or []),
            "artwork_present": item.get("artwork_present"),
            "description": str(item.get("description") or "")[:800],
        },
    }
    if note:
        payload["lead"]["note"] = note
    return payload


async def followup_lead(
    item: dict, caller: StructuredCaller, note: Optional[str] = None
) -> Tuple[LeadFollowupOutput, dict, dict]:
    """Returns (output_model, payload_used, usage_dict)."""
    payload = lead_followup_payload(item, note)
    messages = [
        {"role": "system", "content": LEAD_FOLLOWUP_SYSTEM},
        {"role": "user", "content": json.dumps(payload, default=str)},
    ]
    parsed, usage = await caller.ainvoke(messages)
    return parsed, payload, usage
