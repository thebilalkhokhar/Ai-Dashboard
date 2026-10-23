"""A local index of Customer 360 records, harvested through search.

The CRM API has no list endpoint: search returns at most 10 records per
type per query (30 total), so there is no honest way to page "all
records" out of it. What CAN be built through the API is an index:
run many small searches, and upsert every record found into
`customer360_records`, keyed by the CRM's own Mongo `_id`. The same
record found by fifty queries is stored exactly once - duplicates are
impossible by construction, and a re-run only refreshes what changed.

The harvest walks a breadth-first tree of query prefixes: a query that
comes back capped (a full 10 of any type) is hiding more records, so
its one-letter extensions are queued next. Every run is logged to
`customer360_harvest_runs` with its query count and yield, and the
summary says plainly which queries stayed capped - the index is "every
record the searches could reach", never a claim of the full database.
The full database still needs their Mongo read access or an export;
until then this index is what the chat searches first (it is local,
fast and broader than one capped live query).
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

from app.services.customer360_client import Customer360Client, Customer360Error

log = logging.getLogger("customer360_index")

INDEX_COLLECTION = "customer360_records"
RUNS_COLLECTION = "customer360_harvest_runs"

CAP_PER_TYPE = 10            # the API's per-type search cap (verified live)
ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
MAX_PREFIX_LEN = 3           # prefixes grow to at most this many characters
DEFAULT_MAX_QUERIES = 400    # hard stop per run, whatever the tree wants
MAX_ERRORS = 5               # give up a run after this many failed searches
INDEX_SCAN_LIMIT = 20000     # rows considered by one local index search


def _collection(database: Any, name: str) -> Any:
    try:
        return database[name]
    except (TypeError, KeyError, AttributeError):
        return getattr(database, name, None)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _known_ids(collection: Any) -> set[str]:
    try:
        rows = await collection.find({}, {"_id": 1}).to_list(INDEX_SCAN_LIMIT)
        return {str(row.get("_id")) for row in rows if row.get("_id") is not None}
    except Exception:
        return set()


async def run_harvest(
    database: Any,
    client: Customer360Client,
    max_queries: int = DEFAULT_MAX_QUERIES,
) -> dict:
    """One harvest run. Returns (and stores) the run summary."""
    index = _collection(database, INDEX_COLLECTION)
    runs = _collection(database, RUNS_COLLECTION)
    started_at = _now_iso()
    known = await _known_ids(index) if index is not None else set()

    queue: deque[str] = deque(ALPHABET)
    queued: set[str] = set(ALPHABET)
    asked: set[str] = set()
    capped_queries: list[str] = []
    new_records = 0
    errors = 0
    queries_run = 0

    while queue and queries_run < max_queries and errors <= MAX_ERRORS:
        query = queue.popleft()
        if query in asked:
            continue
        asked.add(query)
        queries_run += 1
        try:
            results = await client.search(query)
        except Customer360Error as exc:
            errors += 1
            log.warning("harvest search %r failed: %s", query, exc)
            continue

        by_type: dict[str, int] = {}
        stamp = _now_iso()
        for row in results:
            if not isinstance(row, dict) or row.get("id") is None:
                continue
            record_id = str(row["id"])
            by_type[str(row.get("type"))] = by_type.get(str(row.get("type")), 0) + 1
            if index is not None:
                await index.update_one(
                    {"_id": record_id},
                    {
                        "$set": {
                            "record_type": row.get("type"),
                            "name": row.get("name"),
                            "email": row.get("email"),
                            "phone": row.get("phone"),
                            "company": row.get("company"),
                            "status": row.get("status"),
                            "last_seen_at": stamp,
                        },
                        "$setOnInsert": {"first_seen_at": stamp},
                    },
                    upsert=True,
                )
            if record_id not in known:
                known.add(record_id)
                new_records += 1

        capped = any(count >= CAP_PER_TYPE for count in by_type.values())
        if capped:
            capped_queries.append(query)
            if len(query) < MAX_PREFIX_LEN:
                for char in ALPHABET:
                    child = query + char
                    if child not in queued and child not in asked:
                        queued.add(child)
                        queue.append(child)

    summary = {
        "started_at": started_at,
        "finished_at": _now_iso(),
        "queries_run": queries_run,
        "unique_records": len(known),
        "new_records": new_records,
        "errors": errors,
        "capped_queries": capped_queries,
        "complete_coverage": not capped_queries and not queue,
        "note": (
            "Every record the harvest searches reached, stored once by its "
            "CRM id. complete_coverage is true only when no query stayed "
            "capped and the queue emptied - otherwise more records exist "
            "than the API's per-query cap let through."
        ),
    }
    if runs is not None:
        try:
            await runs.insert_one(dict(summary))
        except Exception:
            log.warning("harvest run log write failed", exc_info=True)
    log.info(
        "customer360 harvest: %d queries, %d unique records (%d new), %d capped, %d errors",
        queries_run, len(known), new_records, len(capped_queries), errors,
    )
    return summary


async def search_index(database: Any, query: str, limit: int = 30) -> list[dict]:
    """Substring search over the local index - the same AND-of-words rule
    the CRM search uses, over name/email/phone/company."""
    collection = _collection(database, INDEX_COLLECTION)
    if collection is None:
        return []
    words = [word.casefold() for word in query.split() if word.strip()]
    if not words:
        return []
    try:
        rows = await collection.find({}).to_list(INDEX_SCAN_LIMIT)
    except Exception:
        return []
    hits: list[dict] = []
    for row in rows:
        haystack = " ".join(
            str(row.get(field) or "") for field in ("name", "email", "phone", "company")
        ).casefold()
        if all(word in haystack for word in words):
            hits.append(
                {
                    "record_id": row.get("_id"),
                    "record_type": row.get("record_type"),
                    "name": row.get("name"),
                    "email": row.get("email"),
                    "phone": row.get("phone"),
                    "company": row.get("company"),
                    "status": row.get("status"),
                }
            )
        if len(hits) >= limit:
            break
    return hits


async def index_stats(database: Any) -> dict:
    """How much of the CRM the local index currently holds, by type."""
    collection = _collection(database, INDEX_COLLECTION)
    if collection is None:
        return {"total": 0, "by_type": {}}
    try:
        rows = await collection.find({}, {"record_type": 1}).to_list(INDEX_SCAN_LIMIT)
    except Exception:
        return {"total": 0, "by_type": {}}
    by_type: dict[str, int] = {}
    for row in rows:
        key = str(row.get("record_type") or "unknown")
        by_type[key] = by_type.get(key, 0) + 1
    return {"total": len(rows), "by_type": by_type}
