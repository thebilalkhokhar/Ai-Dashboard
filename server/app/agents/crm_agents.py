"""Sub-agents 4 and 5: the Customer 360 (Zoho CRM) specialists.

Built exactly like the ERP specialists (lead_followup.py is the model):
Python selects the candidates and computes every number; the AI only
interprets and drafts for a HUMAN rep. Nothing is ever sent anywhere.

  * CRM Engagement (`crm_engagement`) - one CRM record whose LAST
    timeline interaction came from the customer (direction is a CRM
    field, read by Python) and has waited unanswered for days.
    Candidates come from the detail summaries (customer360.py), the
    same shape the chat's 360 view uses.

  * CRM Deal Watch (`crm_deal_watch`) - one stalled project or estimate
    from the locally synced module rows (processors.crm_deal_candidates
    did the selecting: open stage, idle at least 14 days).

Deliberately excluded from both payloads: email and phone. The model
must not write contact details; a human rep opens the record.
"""

from __future__ import annotations

from typing import Any

from app.services.processors import money_text

CRM_ENGAGEMENT_SYSTEM = """You are a CRM engagement analyst for a printing company.

You get ONE Zoho CRM record whose last interaction came FROM the customer
(Python read the timeline direction) and has waited unanswered for the
given number of days. You must NOT add, change or invent any number,
name, email, phone or URL. The message_draft is for a HUMAN rep to
send; nothing is ever sent automatically.

Your job:
1. situation: one or two sentences on what the customer is most likely
   waiting on, using ONLY the input facts (the last interaction's type
   and title, the open items, the waiting days). When the facts do not
   support a judgment, say so plainly and use confidence "low".
2. action_plan: one or two short sentences describing what a HUMAN rep
   should do next. No promises, no invented prices or timelines.
3. message_draft: the reply the rep should send. Max 80 words. Warm,
   specific to the last interaction, no prices, no discounts, no
   promises, and no invented numbers, names, phones, emails or URLs.
   Address the customer only by the contact name in the input, or not
   at all.
4. evidence: one sentence naming exactly which input facts you used.
Answer in the required JSON shape only.
"""


CRM_DEAL_WATCH_SYSTEM = """You are a CRM deal-watch analyst for a printing company.

You get ONE Zoho CRM project or estimate that has not moved for the
given number of days (Python computed the idle days and selected it
because its stage is still open). The amount in the input is a finished
string copied from the CRM - refer to it exactly as written if you
mention it, and never invent another figure. You must NOT add, change
or invent any number, name, email, phone or URL. The message_draft is
for a HUMAN rep to send; nothing is ever sent automatically.

Your job:
1. stall_reason: one sentence naming why this deal is most likely
   stalled, using ONLY the input facts (stage, idle days, amount, what
   the deal is). When the facts do not support a judgment, say so
   plainly and use confidence "low".
2. action_plan: one or two short sentences describing what a HUMAN rep
   should do next. No promises, no invented prices or timelines.
3. message_draft: what the rep should say to the customer. Max 80
   words. No new prices, no discounts, no promises, and no invented
   numbers, names, phones, emails or URLs.
4. evidence: one sentence naming exactly which input facts you used.
Answer in the required JSON shape only.
"""


def crm_engagement_payload(summary: dict, record_type: str, waiting_days: int) -> dict:
    """Everything the AI may use for one awaiting-reply record.

    The summary is customer360.summarize_detail's output - the same
    bounded shape chat shows - so the agent and the chat can never
    disagree about the facts. Sent fields: identity (name, company,
    status), the last interaction (type, title, when), the waiting days
    Python computed, timeline counts and open item counts. No contact
    details, no record id.
    """
    entity = summary.get("entity") or {}
    last = summary.get("last_interaction") or {}
    return {
        "task": "assess one CRM record whose last interaction came from the customer and draft the reply a human rep should send",
        "record": {
            "name": entity.get("name"),
            "company": entity.get("company"),
            "type": record_type,
            "status": entity.get("status"),
        },
        "waiting": {
            "last_interaction_type": last.get("type"),
            "last_interaction_title": last.get("title"),
            "last_interaction_at": last.get("at"),
            "waiting_days": waiting_days,
            "timeline_counts": summary.get("timeline_by_type") or {},
            "recent_titles": [
                item.get("title")
                for item in (summary.get("recent_interactions") or [])
            ][:3],
        },
        "open_items": {
            "projects": summary.get("projects_count"),
            "estimates": summary.get("estimates_count"),
            "invoices": summary.get("invoices_count"),
        },
    }


def crm_deal_payload(candidate: dict) -> dict:
    """Everything the AI may use for one stalled CRM deal.

    The candidate is processors.crm_deal_candidates' output; the amount
    leaves Python already formatted via money_text, so the draft can
    only copy a finished string.
    """
    return {
        "task": "assess one stalled CRM deal and draft the follow-up a human rep should send",
        "deal": {
            "kind": candidate.get("deal_kind"),
            "name": candidate.get("deal_name"),
            "account": candidate.get("account_name"),
            "contact": candidate.get("contact_name"),
            "stage": candidate.get("stage"),
            "amount": money_text(candidate.get("amount")),
            "days_idle": candidate.get("days_idle"),
            "last_moved_at": candidate.get("last_moved_at"),
        },
    }
