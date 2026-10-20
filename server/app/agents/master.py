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
from app.schemas.agents import MasterOutput

MASTER_SYSTEM = """You are the Master CEO agent of the Biztek AI Command Center
for a printing company.

Input: the sub-agents' outputs, already grouped by customer by CODE. Each item
is a negotiation output (stale quote) or a collections output (unpaid invoice),
plus its pre-computed numbers. All math was done by Python - do not compute or
change any number; copy numbers exactly when you mention them.

Your job:
1. bullets: EXACTLY 3 short strings for the executive brief. Mention real
   customers from the input (with company names, not "a customer").
2. priorities: up to 5, most important first: {customer_name, why,
   suggested_action}. Only customers that appear in the input.
3. conflicts_resolved: list of strings. A conflict exists ONLY when the same
   customer_id appears in more than one sub-agent output (e.g. we owe them a
   nudge about a paid quote AND are chasing their unpaid invoice). Say what
   the combined move is. If every customer appears only once, write (in the
   list) one entry saying exactly that - no invented conflicts.
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


def master_payload(grouped: dict, signals_summary: dict) -> dict:
    return {
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


async def run_master(grouped: dict, signals_summary: dict, caller: StructuredCaller) -> Tuple[MasterOutput, dict, dict]:
    payload = master_payload(grouped, signals_summary)
    messages = [
        {"role": "system", "content": MASTER_SYSTEM},
        {"role": "user", "content": json.dumps(payload, default=str)},
    ]
    parsed, usage = await caller.ainvoke(messages)
    return parsed, payload, usage
