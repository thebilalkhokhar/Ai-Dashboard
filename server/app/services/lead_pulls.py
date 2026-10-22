"""Immutable historical lead-pull snapshots.

The `leads` collection is upserted in place, so it can never answer "what
changed since the previous pull" - yesterday's rows are gone. This module keeps
one immutable document per pull in `lead_pulls`, and `compare_lead_pulls` in
app/agents/chat_tools.py reads them.

A "lead pull" here is a read of the current lead state (this feature is strictly
read-only; nothing is written back to the ERP). Each snapshot records the stable
lead id and the fields the comparison supports - status, company and the product
dimensions parsed from the free-text description - so a snapshot alone is enough
to reconstruct the lead state that was observed at that pull. Contact details
(email, phone) are deliberately not stored: they are not compared and are the
most sensitive thing a lead carries.

Design choices that match the project's conventions:

  * stable id: the ERP's `zohoLeadId` (falling back to the Mongo `_id`), never a
    name - `compare_lead_pulls` matches records by this id;
  * immutability across pulls: a new pull is a new document. Retrying the *same*
    day's pull rewrites only that day's snapshot (idempotent), never history,
    which is why `pull_id` is keyed on the UTC day and `lead_pulls` has a unique
    index on it;
  * completeness: a pull is complete only when every row in `leads` was read with
    no error. An incomplete pull is stored as evidence of the failure and is
    never compared as though it were a full picture (see compare_lead_pulls).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

from app import db as app_db
from app.services import processors

log = logging.getLogger("lead_pulls")

SNAPSHOT_SCHEMA_VERSION = 1

# The most rows one snapshot will read. Leads are a few thousand, not millions,
# but a hard ceiling keeps a runaway collection from being pulled into memory.
MAX_SNAPSHOT_ROWS = 200_000

# Only fields the comparison uses, plus the stable id and timestamps for context.
# No email, no phone, no Mongo internals.
LEAD_SNAPSHOT_PROJECTION = {
    "zohoLeadId": 1, "_id": 1, "status": 1, "company": 1, "description": 1,
    "createdAt": 1, "lastActivityAt": 1,
}


def snapshot_pull_id(pulled_on: date, source: str) -> str:
    """The one pull_id for a given UTC day + source, so a same-day retry is
    idempotent instead of piling up duplicate history."""
    safe_source = "".join(ch if (ch.isalnum() or ch in "._-") else "-" for ch in str(source or "leads"))
    return f"leads:{safe_source}:{pulled_on.isoformat()}"


def snapshot_lead_row(raw: dict) -> dict:
    """One stored lead document -> one immutable snapshot row (non-sensitive).

    A field is written only when it has a value: an unknown dimension is simply
    absent, so `compare_lead_pulls` treats it as "not captured" rather than as a
    present-but-empty difference, and two identical leads still read as unchanged.
    """
    lead_id = str(raw.get("zohoLeadId") or raw.get("_id") or "").strip()
    parsed = processors.parse_lead_description(raw.get("description"))
    row: dict[str, Any] = {"lead_id": lead_id}
    candidates = {
        "status": _text_or_none(raw.get("status")),
        "company": _text_or_none(raw.get("company")),
        "product": parsed.get("product") or parsed.get("type"),
        "quantity": parsed.get("quantity"),
        "size": parsed.get("size"),
        "material": parsed.get("material"),
        "finish": parsed.get("finish"),
        "created_at": processors.iso_or_none(raw.get("createdAt")),
        "last_activity_at": processors.iso_or_none(raw.get("lastActivityAt")),
    }
    for field, value in candidates.items():
        if value is not None:
            row[field] = value
    return row


def _text_or_none(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


def build_snapshot_doc(
    rows: list[dict],
    *,
    pulled_at: datetime,
    source: str,
    expected_count: int,
    pull_id: Optional[str] = None,
    error: Optional[str] = None,
) -> dict:
    """Assemble the immutable document for one pull from the rows actually read.

    ``complete`` is the honest verdict the comparison relies on: true only when
    the read finished without error and captured every row that was there.
    """
    captured = len(rows)
    complete = error is None and captured == expected_count
    return {
        "pull_id": pull_id or snapshot_pull_id(pulled_at.date(), source),
        "pulled_at": pulled_at.isoformat(),
        "source": source,
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "complete": complete,
        "row_count": captured,
        "expected_count": expected_count,
        "pagination": {"all_read": complete, "expected_count": expected_count, "captured_count": captured},
        "error": error,
        "leads": [snapshot_lead_row(row) for row in rows],
    }


async def capture_lead_snapshot(
    database: Any = None,
    *,
    now: Optional[datetime] = None,
    source: str = "mongo.leads",
    pull_id: Optional[str] = None,
) -> dict:
    """Read the current lead state and retain it as one immutable snapshot.

    Injectable ``database``/``now`` keep this deterministic in tests and let the
    scheduler reuse its own connections. Returns a report (never raises): a read
    or write problem is recorded in the report and in the stored document, so an
    incomplete capture is never mistaken for a successful historical snapshot.
    """
    # AsyncDatabase cannot be truth-tested, so compare against None.
    db = database if database is not None else app_db.get_db()
    pulled_at = now or datetime.now(timezone.utc)

    expected_count: Optional[int] = None
    rows: list[dict] = []
    error: Optional[str] = None
    try:
        expected_count = await db.leads.count_documents({})
        rows = await db.leads.find({}, LEAD_SNAPSHOT_PROJECTION).to_list(MAX_SNAPSHOT_ROWS)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        log.exception("could not read the leads collection for a snapshot")

    doc = build_snapshot_doc(
        rows,
        pulled_at=pulled_at,
        source=source,
        expected_count=expected_count if expected_count is not None else len(rows),
        pull_id=pull_id,
        error=error,
    )

    written = True
    try:
        # Idempotent per pull_id: a same-day retry updates that day's snapshot,
        # never another day's history.
        await db.lead_pulls.update_one(
            {"pull_id": doc["pull_id"]}, {"$set": doc}, upsert=True
        )
    except Exception as exc:
        written = False
        log.exception("could not store the lead_pulls snapshot")

    report = {
        "pull_id": doc["pull_id"],
        "pulled_at": doc["pulled_at"],
        "complete": doc["complete"],
        "row_count": doc["row_count"],
        "expected_count": doc["expected_count"],
        "stored": written,
        "ok": doc["complete"] and written,
        "error": error or (None if written else "snapshot write failed"),
    }
    if doc["complete"]:
        log.info("lead snapshot %s: %s rows", report["pull_id"], report["row_count"])
    else:
        log.warning("lead snapshot %s incomplete: %s", report["pull_id"], report.get("error"))
    return report
