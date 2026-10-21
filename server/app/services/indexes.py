"""Mongo indexes, created from the app itself.

`ensure_indexes` is idempotent (it asks for an index by a fixed name, so re-
running just re-creates the same index) and runs in the FastAPI lifespan, so a
fresh database is ready for the queries the API, the agents and the scheduler
make - not only after someone remembers to run scripts/ingest_erp.py.

Keys are declared once here and shared with the ingest script.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("indexes")

A = 1
DESC = -1


def index_name(keys: tuple[tuple[str, int], ...]) -> str:
    """A stable, short name so re-creating an index never conflicts."""
    return "biztek_" + "_".join(f"{field.replace('.', '_')}{direction}" for field, direction in keys)


# collection -> list of (index name, keys) pairs
INDEXES: dict[str, tuple[tuple[str, tuple[tuple[str, int], ...]], ...]] = {
    # --- signals / agents read these flat collections ---
    "quotes_flat": (
        ("by_customer", (("customer.id", A),)),
        ("open_recent", (("status", A), ("created_at", A))),
        ("by_created", (("created_at", A),)),
        ("by_amount", (("amount", DESC),)),
    ),
    "orders_flat": (
        ("by_customer", (("customer.id", A),)),
        ("by_payment_invoice_date", (("payment_status", A), ("invoice_date", A))),
        ("by_status_label", (("status_label", A),)),
        # which quotes already converted into an order (the stale filter)
        ("by_quote", (("quote_id", A),)),
        ("by_invoice_date", (("invoice_date", A),)),
    ),
    "payments_flat": (
        ("by_customer_id", (("customer_id", A),)),
        ("by_customer", (("customer.id", A),)),
        ("by_received", (("received_at", A),)),
    ),
    "leads": (
        ("by_status_activity", (("status", A), ("lastActivityAt", A))),
        ("by_email", (("email", A),)),
        ("by_phone", (("phone", A),)),
        ("by_zoho_id", (("zohoLeadId", A),)),
    ),
    # --- agent runs: latest briefing, the cache and the run guard ---
    "agent_runs": (
        ("by_finished", (("finished_epoch", DESC),)),
        ("by_started", (("started_at", DESC),)),
        ("by_run_id", (("run_id", A),)),
        ("open_run", (("status", A), ("started_epoch", DESC))),
        # the 24h input-hash cache searches item hashes inside the run arrays
        ("negotiation_hash", (("negotiation.input_hash", A),)),
        ("collections_hash", (("collections.input_hash", A),)),
        ("lead_hash", (("lead_followup.input_hash", A),)),
    ),
    "agent_triggers": (
        ("by_trigger_day", (("trigger", A), ("day", A))),
        ("by_run_id", (("run_id", A),)),
    ),
    "snapshots": (("by_generated", (("generated_at", DESC),)),),
}


def _collection(database: Any, name: str):
    """Mongo databases support both styles; the test doubles only have one."""
    try:
        return database[name]
    except TypeError:
        return getattr(database, name)


async def _existing_keys(collection) -> set[tuple[tuple[str, int], ...]]:
    """Key specs already on the collection, whatever they were named."""
    try:
        info = await collection.index_information()
    except Exception:  # a brand new collection, or no permission to list
        return set()
    return {tuple(tuple(key) for key in spec.get("key", [])) for spec in info.values()}


async def ensure_indexes(database: Any) -> dict[str, int]:
    """Create every declared index. Returns {collection: indexes created}.

    A key spec that already exists under another name (the ingest script created
    plain single-field indexes first) is skipped, so re-running is quiet.
    """
    created: dict[str, int] = {}
    for name, indexes in INDEXES.items():
        collection = _collection(database, name)
        existing = await _existing_keys(collection)
        count = 0
        for suffix, keys in indexes:
            if keys in existing:
                continue
            try:
                await collection.create_index(list(keys), name=f"{name}_{suffix}")
                count += 1
            except Exception as exc:
                log.warning("could not create index %s on %s: %s", suffix, name, exc)
        created[name] = count
    return created
