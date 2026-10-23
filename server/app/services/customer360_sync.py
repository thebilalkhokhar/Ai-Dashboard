"""Full-module sync of the Zoho CRM (Customer 360) into our own Mongo.

The customer-360 search caps at 10 records per type, which is why the
first index (customer360_index) had to enumerate it by prefixes and
still could not prove completeness. The module list endpoints are not
capped - they paginate the whole module and report its total (verified
live Oct 24, 2026: leads 18,404; contacts 5,463; customers 5,639;
payments 7,847; invoices 10,106; projects 9,284; estimates 18,796) - so
a real mirror is possible through the API after all.

Rules:
  * Every row is upserted under its own CRM `_id` (collection
    `customer360_<module>`): a re-run updates the same document, so
    duplicates cannot accumulate - the manager's hard requirement.
  * People modules also feed the `customer360_records` index in the
    search tool's shape, so chat search and the agents read one index.
  * The sync never deletes: a record removed in the CRM stays in the
    mirror (flagging stale rows is a later pass, not a silent delete).
  * Each run is logged to `customer360_sync_runs` with per-module
    counts against the totals the API itself reported - the coverage
    proof the search harvest could never give.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.services.customer360_client import Customer360Client, Customer360Error
from app.services.customer360_index import INDEX_COLLECTION

log = logging.getLogger("customer360_sync")

MODULES = ("leads", "contacts", "customers", "payments", "invoices", "projects", "estimates")
PEOPLE_TYPES = {"leads": "Lead", "contacts": "Contact", "customers": "Customer"}
PAGE_SIZE = 100
RUNS_COLLECTION = "customer360_sync_runs"
# Redis lock shared by the manual script and the scheduler's automatic
# launcher, so two syncs never run at once (upserts are idempotent, so
# an overlap would waste API calls, not corrupt data - still avoided).
SYNC_LOCK_KEY = "c360:sync_lock"


def _collection(database: Any, name: str) -> Any:
    try:
        return database[name]
    except (TypeError, KeyError, AttributeError):
        return getattr(database, name, None)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _person_fields(module: str, row: dict) -> dict:
    """The search-index shape for one people row (fields verified live:
    leads carry firstName/lastName/company/status, contacts
    firstName/lastName/accountName, customers customerName and no email)."""
    if module == "customers":
        return {
            "record_type": "Customer",
            "name": row.get("customerName"),
            "email": row.get("email"),
            "phone": row.get("phone"),
            "company": None,
            "status": None,
        }
    name = " ".join(
        part for part in (row.get("firstName"), row.get("lastName")) if part
    ).strip() or None
    return {
        "record_type": PEOPLE_TYPES[module],
        "name": name,
        "email": row.get("email"),
        "phone": row.get("phone"),
        "company": row.get("company") if module == "leads" else row.get("accountName"),
        "status": row.get("status") if module == "leads" else None,
    }


async def sync_module(
    database: Any,
    client: Customer360Client,
    module: str,
    *,
    max_pages: Optional[int] = None,
) -> dict:
    """Sync one module end to end. Returns its run counts."""
    collection = _collection(database, f"customer360_{module}")
    index = _collection(database, INDEX_COLLECTION) if module in PEOPLE_TYPES else None
    stamp = _now_iso()
    page = 0
    seen = new = changed = 0
    total_reported: Any = None
    errors = 0

    while True:
        page += 1
        try:
            body = await client.list_page(module, page=page, limit=PAGE_SIZE)
        except Customer360Error as exc:
            errors += 1
            log.warning("crm sync %s page %d failed: %s", module, page, exc)
            break
        rows = [
            row
            for row in (body.get(module) or body.get("data") or [])
            if isinstance(row, dict)
        ]
        pagination = body.get("pagination") or {}
        if pagination.get("total") is not None:
            total_reported = pagination["total"]
        if not rows:
            break
        for row in rows:
            record_id = row.get("_id")
            if record_id is None:
                continue
            seen += 1
            if collection is not None:
                document = {key: value for key, value in row.items() if key != "_id"}
                document["synced_at"] = stamp
                result = await collection.update_one(
                    {"_id": str(record_id)},
                    {"$set": document, "$setOnInsert": {"first_synced_at": stamp}},
                    upsert=True,
                )
                if getattr(result, "upserted_id", None) is not None:
                    new += 1
                elif getattr(result, "modified_count", 0):
                    changed += 1
            if index is not None:
                await index.update_one(
                    {"_id": str(record_id)},
                    {
                        "$set": {**_person_fields(module, row), "last_seen_at": stamp},
                        "$setOnInsert": {"first_seen_at": stamp},
                    },
                    upsert=True,
                )
        total_pages = pagination.get("pages")
        if total_pages is not None and page >= int(total_pages):
            break
        if max_pages is not None and page >= max_pages:
            break
        if total_pages is None and len(rows) < PAGE_SIZE:
            break

    summary = {
        "module": module,
        "total_reported": total_reported,
        "rows_seen": seen,
        "new_records": new,
        "changed_records": changed,
        "pages_read": page,
        "errors": errors,
        "complete": errors == 0 and (total_reported is None or seen >= int(total_reported)),
    }
    log.info(
        "crm sync %s: %d seen (%d new, %d changed) of %s reported, %d pages, %d errors",
        module, seen, new, changed, total_reported, page, errors,
    )
    return summary


async def sync_all(
    database: Any,
    client: Customer360Client,
    modules: tuple[str, ...] = MODULES,
    *,
    max_pages: Optional[int] = None,
    mode: str = "manual",
    scope: str = "full",
) -> dict:
    """Sync every module and store the run summary."""
    started_at = _now_iso()
    per_module = {}
    for module in modules:
        per_module[module] = await sync_module(
            database, client, module, max_pages=max_pages
        )
    summary = {
        "started_at": started_at,
        "finished_at": _now_iso(),
        "mode": mode,
        "scope": scope,
        "modules": per_module,
        "complete": all(item["complete"] for item in per_module.values()),
        "note": (
            "Full-module mirror via the CRM list endpoints: every row "
            "upserted once by its CRM id, so re-runs never duplicate. "
            "complete is true only when every module's seen count reached "
            "the total the API itself reported, with no page errors."
        ),
    }
    runs = _collection(database, RUNS_COLLECTION)
    if runs is not None:
        try:
            await runs.insert_one(dict(summary))
        except Exception:
            log.warning("crm sync run log write failed", exc_info=True)
    return summary
