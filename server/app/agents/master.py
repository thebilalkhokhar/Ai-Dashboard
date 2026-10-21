"""Master CEO agent: reads the sub-agents' outputs (already grouped by
customer by code), resolves conflicts, ranks priorities and writes the
3-bullet executive brief.

It only receives pre-computed JSON. If every customer appears just once, it is
told so explicitly and must not invent conflicts.
"""

from __future__ import annotations

import json
from typing import Any, Tuple

from app.agents.llm import StructuredCaller
from app.schemas.agents import MasterOutput, Priority
from app.services.processors import format_money_tree, money_text

MASTER_SYSTEM = """You are the Master CEO agent of the Biztek AI Command Center
for a printing company.

Input: the sub-agents' outputs, already grouped by customer by CODE. Each item
is a negotiation output (stale quote) or a collections output (unpaid invoice),
plus its pre-computed numbers. A separate "leads" section holds lead-followup
outputs: those leads are NOT customers yet and must never be merged into a
customer's conflict entry, even when a note says their contact details match a
customer. All math was done by Python - do not compute or change any number;
copy numbers exactly when you mention them.

Your job:
1. bullets: EXACTLY 3 short strings for the executive brief. Mention real
   customers from the input (with company names, not "a customer"). You may
   dedicate a bullet to the stuck leads when the input contains any.
2. priorities: up to 5, most important first: {customer_name, why,
   suggested_action}. Only customers that appear in the input.
3. conflicts_resolved: list of strings. A conflict exists ONLY when the same
   customer_id appears in more than one sub-agent output (e.g. we owe them a
   nudge about a paid quote AND are chasing their unpaid invoice). Say what
   the combined move is. If every customer appears only once, write (in the
   list) one entry saying exactly that - no invented conflicts. A lead whose
   contact details match a customer is a NOTE for that list, not a conflict.
4. Never suggest sending emails/calls as AUTOMATED actions - suggested_action
   describes what a HUMAN sales rep should do next.
5. Mention ONLY dollar amounts that appear verbatim in the input. You may name
   a customer's debts side by side (e.g. "$100.0, $200.0 and $300.0") but you
   must NOT add, average or total them yourself - no arithmetic.
Answer in the required JSON shape only.
"""


def group_by_customer(outputs: list[dict]) -> dict:
    """Pure grouping: customer_id -> sub-agent outputs for that customer."""
    grouped: dict[Any, dict] = {}
    for output in outputs:
        cid = output.get("customer_id")
        grouped.setdefault(cid, {"items": [], "customer_name": output.get("customer_name")})
        grouped[cid]["items"].append(output)
    return grouped


def master_payload(grouped: dict, signals_summary: dict, lead_items: list[dict] | None = None) -> dict:
    """The master sees the same formatted money as the sub-agents.

    Lead-follow-up outputs arrive as their own section: leads are matched to
    customers only by email/phone, so they are never force-merged into the
    customer groups (a note carries the match when one exists).
    """
    payload = {
        "task": "executive brief: what the sales team should do next",
        "signal_summary": signals_summary,
        "stalled_orders_note": "the stalled-orders signal is UNRELIABLE (no status timestamps) - never use it",
        "customers": [
            {
                "customer_id": cid,
                "customer_name": group.get("customer_name"),
                "outputs": group.get("items"),
            }
            for cid, group in grouped.items()
        ],
    }
    if lead_items:
        payload["leads"] = [
            {
                key: lead.get(key)
                for key in (
                    "lead_id", "contact_name", "company", "status",
                    "days_since_activity", "missing", "blocker", "owner_team",
                    "action_plan", "confidence", "note",
                )
            }
            for lead in lead_items
        ]
    return format_money_tree(payload)


async def run_master(
    grouped: dict,
    signals_summary: dict,
    caller: StructuredCaller,
    lead_items: list[dict] | None = None,
) -> Tuple[MasterOutput, dict, dict]:
    payload = master_payload(grouped, signals_summary, lead_items)
    messages = [
        {"role": "system", "content": MASTER_SYSTEM},
        {"role": "user", "content": json.dumps(payload, default=str)},
    ]
    parsed, usage = await caller.ainvoke(messages)
    return parsed, payload, usage


def _output_money(output: dict) -> float:
    return float(output.get("quote_amount") or output.get("balance") or 0.0)


def _quote_line(output: dict) -> str:
    name = output.get("customer_name") or "a customer"
    return f"{name} {money_text(output.get('quote_amount'))} (quote {output.get('quote_id')})"


def _invoice_line(output: dict) -> str:
    name = output.get("customer_name") or "a customer"
    return f"{name} {money_text(output.get('balance'))} (invoice {output.get('invoice_id')})"


def _lead_line(lead: dict) -> str:
    name = lead.get("company") or lead.get("contact_name") or "a lead"
    missing = ", ".join(str(item) for item in (lead.get("missing") or []) if item)
    return f"{name} waiting on {missing}" if missing else str(name)


def build_master_brief(
    grouped: dict,
    signals_summary: dict,
    lead_items: list[dict] | None = None,
) -> MasterOutput:
    """The executive brief written WITHOUT an LLM.

    Used when the model's brief failed validation even after one regeneration.
    Every number is copied from the sub-agent outputs, so the brief cannot be
    wrong about the data - it is only less fluent than the model's version.
    """
    outputs = [item for group in grouped.values() for item in group.get("items") or []]
    quotes = sorted(
        (o for o in outputs if o.get("quote_id") is not None), key=_output_money, reverse=True
    )
    invoices = sorted(
        (o for o in outputs if o.get("invoice_id") is not None), key=_output_money, reverse=True
    )
    leads = list(lead_items or [])

    if quotes:
        first = "Open quotes to chase: " + "; ".join(_quote_line(o) for o in quotes[:3])
    else:
        first = f"No open quote needs a follow-up this run ({signals_summary.get('stale_quotes_count', 0)} stale quotes)."
    if invoices:
        second = (
            "Unpaid invoices to remind: "
            + "; ".join(_invoice_line(o) for o in invoices[:3])
            + f" (unpaid total {money_text(signals_summary.get('unpaid_balance_total'))})."
        )
    else:
        second = f"No unpaid invoice needs a reminder this run ({signals_summary.get('unpaid_invoices_count', 0)} unpaid)."
    if leads:
        third = (
            f"{len(leads)} stuck lead(s) await details: "
            + "; ".join(_lead_line(lead) for lead in leads[:3])
            + "."
        )
    else:
        third = "No lead is stuck waiting for details this run."

    priorities = []
    for output in sorted(outputs, key=_output_money, reverse=True)[:5]:
        if output.get("quote_id") is not None:
            why = f"Open quote {output.get('quote_id')} for {money_text(output.get('quote_amount'))}"
            action = "A human rep reviews the quote and calls the customer about it."
        else:
            why = (
                f"Unpaid invoice {output.get('invoice_id')} balance "
                f"{money_text(output.get('balance'))}"
            )
            action = "A human rep sends the stored payment reminder and offers to discuss it."
        priorities.append(
            Priority(
                customer_name=output.get("customer_name") or "a customer",
                why=why,
                suggested_action=action,
            )
        )

    conflicts = []
    for group in grouped.values():
        items = group.get("items") or []
        if len(items) > 1:
            conflicts.append(
                f"{group.get('customer_name') or 'a customer'} appears in {len(items)} sub-agent "
                "outputs, so one combined rep conversation covers them all."
            )
    if not conflicts:
        conflicts.append("Every customer appears exactly once in this run, so there is nothing to merge.")

    return MasterOutput(
        bullets=[first, second, third],
        priorities=priorities,
        conflicts_resolved=conflicts,
    )
