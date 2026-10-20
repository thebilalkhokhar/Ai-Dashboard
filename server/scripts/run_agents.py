"""Runs the agent pipeline ONCE from the command line and prints a summary.

    .venv/bin/python -m scripts.run_agents

Nothing is ever sent to customers: the drafts are stored text only (Mongo
`agent_runs`). The LLM only reads pre-computed JSON from Python.
"""

from __future__ import annotations

import asyncio
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.agents.graph import run_pipeline  # noqa: E402

import argparse  # noqa: E402


def show(label: str, items: list[dict], limit: int = 3) -> None:
    print(f"\n=== {label} (showing up to {limit}) ===")
    for output in items[:limit]:
        print(json.dumps(
            {k: v for k, v in output.items() if k != "input_hash"},
            indent=2, default=str,
        ))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the agent pipeline once")
    parser.add_argument(
        "--fresh", action="store_true", help="ignore the 24h cache and call the LLM for every item"
    )
    args = parser.parse_args()
    result = await run_pipeline(ignore_cache=args.fresh)
    counts = result.get("counts", {})
    usage = result.get("usage", {}) or {}

    print(f"run_id: {result['run_id']}")
    print(f"started: {result['started_at']}   errors: {result.get('errors')}")
    print(
        f"counts: {json.dumps(counts)}   "
        f"tokens: {usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out"
    )
    show("NEGOTIATION", result.get("negotiation", []))
    negotiation = result.get("negotiation", [])
    print("\n=== NEGOTIATION DISTRIBUTION (all items in this run) ===")
    tactic_counts = Counter(item.get("tactic", "missing") for item in negotiation)
    reason_counts = Counter(item.get("likely_reason", "missing") for item in negotiation)
    print(f"tactic: {dict.fromkeys(('follow_up', 'adjust_price', 'bundle_offer', 'let_go'), 0) | tactic_counts}")
    print(
        "likely_reason: "
        f"{dict.fromkeys(('price', 'no_response', 'new_customer_no_history', 'repeat_customer_slow', 'unclear'), 0) | reason_counts}"
    )
    show("COLLECTIONS", result.get("collections", []))
    print("\n=== MASTER BRIEF ===")
    master = result.get("master") or {}
    for bullet in master.get("bullets", []):
        print(f"  - {bullet}")
    for priority in master.get("priorities", []):
        print(f"  priority: {priority}")
    print(f"conflicts_resolved: {master.get('conflicts_resolved')}")


if __name__ == "__main__":
    asyncio.run(main())
