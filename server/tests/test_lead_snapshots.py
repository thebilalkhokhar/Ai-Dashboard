"""Historical lead snapshots: capture, persistence, and comparison.

Covers the capability that was missing: `compare_lead_pulls` always reported
available=false because nothing ever wrote the `lead_pulls` collection these
tests now populate through the real capture code path (no ERP, no network).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.agents import chat_tools
from app.agents.chat_tools import CHAT_TOOL_MAP, CompareArgs, compare_lead_pulls, invoke_tool
from app.services import lead_pulls
from app.services.lead_pulls import capture_lead_snapshot
from app.services.scheduler import DashboardScheduler
from test_chat import FakeCursor


def lead_doc(zoho, status, company="Acme", product="mylar bag", quantity="500", **over):
    description = f'{{"product": "{product}", "quantity": "{quantity}"}}'
    row = {
        "zohoLeadId": zoho, "status": status, "company": company,
        "description": description,
        "createdAt": "2026-10-01T09:00:00+00:00",
        "lastActivityAt": "2026-10-10T09:00:00+00:00",
        # sensitive fields that must never reach a snapshot
        "email": f"{zoho}@secret.example", "phone": "5551234567",
    }
    row.update(over)
    return row


class LeadsColl:
    def __init__(self, rows):
        self.rows = list(rows)

    def find(self, *args, **kwargs):
        return FakeCursor(self.rows)

    async def count_documents(self, _query):
        return len(self.rows)


class PullColl:
    """Minimal lead_pulls double with idempotent upsert-by-pull_id."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find(self, *args, **kwargs):
        return FakeCursor(list(self.rows))

    async def count_documents(self, _query):
        return len(self.rows)

    async def update_one(self, flt, upd, upsert=False):
        pull_id = flt.get("pull_id")
        doc = upd.get("$set", {})
        for index, row in enumerate(self.rows):
            if row.get("pull_id") == pull_id:
                self.rows[index] = doc
                return {"matched_count": 1, "modified_count": 1}
        self.rows.append(dict(doc))
        return {"matched_count": 0, "upserted_id": pull_id}


class DB:
    def __init__(self, leads=None, lead_pulls=None):
        self.leads = LeadsColl(leads or [])
        self.lead_pulls = lead_pulls if lead_pulls is not None else PullColl()


NOW1 = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
NOW2 = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- capture

@pytest.mark.asyncio
async def test_capture_persists_an_immutable_snapshot_with_parsed_fields():
    db = DB(leads=[lead_doc("L-1", "New"), lead_doc("L-2", "Attempted to Contact", quantity="1000")])

    report = await capture_lead_snapshot(database=db, now=NOW1)

    assert report["ok"] is True and report["complete"] is True
    assert report["row_count"] == 2 and report["stored"] is True
    assert len(db.lead_pulls.rows) == 1
    snap = db.lead_pulls.rows[0]
    assert snap["pull_id"] == "leads:mongo.leads:2026-10-20"
    assert snap["pulled_at"] == "2026-10-20T09:00:00+00:00"
    assert snap["source"] == "mongo.leads" and snap["schema_version"] == 1
    assert snap["complete"] is True
    by_id = {row["lead_id"]: row for row in snap["leads"]}
    assert by_id["L-1"]["status"] == "New" and by_id["L-1"]["product"] == "mylar bag"
    assert by_id["L-2"]["quantity"] == "1000"


@pytest.mark.asyncio
async def test_capture_excludes_sensitive_fields():
    db = DB(leads=[lead_doc("L-1", "New")])
    await capture_lead_snapshot(database=db, now=NOW1)

    import json

    blob = json.dumps(db.lead_pulls.rows[0])
    assert "secret.example" not in blob and "5551234567" not in blob
    assert "email" not in blob and "phone" not in blob


@pytest.mark.asyncio
async def test_capture_is_idempotent_per_day_but_new_document_per_day():
    db = DB(leads=[lead_doc("L-1", "New")])
    await capture_lead_snapshot(database=db, now=NOW1)
    # retry the same day, even with different content: the day's snapshot updates,
    # it does not add a duplicate
    await capture_lead_snapshot(database=db, now=NOW1.replace(hour=12))
    assert len(db.lead_pulls.rows) == 1
    assert db.lead_pulls.rows[0]["pulled_at"] == "2026-10-20T12:00:00+00:00"

    # the next day is a new, immutable snapshot; the first day is left intact
    db2 = DB(leads=[lead_doc("L-1", "New"), lead_doc("L-2", "New")])
    await capture_lead_snapshot(database=db2, now=NOW1)
    first_day = dict(db2.lead_pulls.rows[0])
    await capture_lead_snapshot(database=db2, now=NOW2)
    assert len(db2.lead_pulls.rows) == 2
    assert {r["pull_id"] for r in db2.lead_pulls.rows} == {
        "leads:mongo.leads:2026-10-20", "leads:mongo.leads:2026-10-21",
    }
    assert db2.lead_pulls.rows[0] == first_day  # earlier snapshot untouched


@pytest.mark.asyncio
async def test_capture_of_an_unreadable_leads_collection_is_recorded_incomplete():
    class BrokenLeads:
        async def count_documents(self, _q):
            raise RuntimeError("mongo down")

        def find(self, *a, **k):
            raise RuntimeError("mongo down")

    db = DB()
    db.leads = BrokenLeads()

    report = await capture_lead_snapshot(database=db, now=NOW1)

    assert report["complete"] is False and report["ok"] is False
    assert report["stored"] is True  # the failure is itself retained as evidence
    assert db.lead_pulls.rows[0]["complete"] is False
    assert "RuntimeError" in (report["error"] or "")


# ------------------------------------------------------------- comparison

@pytest.mark.asyncio
async def test_compare_reports_added_removed_reclassified_changed_after_real_captures():
    db = DB(leads=[
        lead_doc("L-1", "New"), lead_doc("L-2", "New"),
        lead_doc("L-3", "New"), lead_doc("L-4", "New", quantity="750"),
    ])
    await capture_lead_snapshot(database=db, now=NOW1)
    # day 2: L-1 unchanged, L-2 reclassified, L-4 product+qty changed, L-3 removed, L-5 added
    db.leads = LeadsColl([
        lead_doc("L-1", "New"), lead_doc("L-2", "Contacted"),
        lead_doc("L-4", "New", product="paper bag", quantity="750"),
        lead_doc("L-5", "New"),
    ])
    await capture_lead_snapshot(database=db, now=NOW2)

    result = await compare_lead_pulls(database=db)

    assert result["available"] is True
    assert {s["pull_id"] for s in (result["previous_pull"], result["latest_pull"])} == {
        "leads:mongo.leads:2026-10-20", "leads:mongo.leads:2026-10-21",
    }
    assert result["summary"]["added"] == 1        # L-5
    assert result["summary"]["removed"] == 1      # L-3
    assert result["summary"]["reclassified"] == 1  # L-2 New -> Contacted
    assert result["summary"]["changed"] == 1      # L-4 product mylar bag -> paper bag
    assert result["summary"]["unchanged"] == 1     # L-1
    assert {r["lead_id"] for r in result["added"]} == {"L-5"}
    assert {r["lead_id"] for r in result["removed"]} == {"L-3"}
    assert result["reclassified"][0]["previous_status"] == "New"
    assert result["reclassified"][0]["current_status"] == "Contacted"
    assert result["changed"][0]["fields"]["product"] == {"from": "mylar bag", "to": "paper bag"}


@pytest.mark.asyncio
async def test_compare_can_select_specific_pull_ids():
    db = DB()
    db.lead_pulls = PullColl([
        lead_pulls.build_snapshot_doc(
            [lead_doc("L-1", "New", company="A")],
            pulled_at=NOW1, source="mongo.leads", expected_count=1,
        ),
        lead_pulls.build_snapshot_doc(
            [lead_doc("L-1", "Contacted", company="A")],
            pulled_at=NOW2, source="mongo.leads", expected_count=1,
        ),
    ])

    result = await compare_lead_pulls(
        database=db,
        previous_pull_id="leads:mongo.leads:2026-10-20",
        latest_pull_id="leads:mongo.leads:2026-10-21",
    )
    assert result["available"] is True and result["summary"]["reclassified"] == 1


@pytest.mark.asyncio
async def test_compare_missing_pull_id_is_actionable_not_fabricated():
    db = DB()
    db.lead_pulls = PullColl([
        lead_pulls.build_snapshot_doc([], pulled_at=NOW1, source="mongo.leads", expected_count=0),
    ])
    result = await compare_lead_pulls(
        database=db, previous_pull_id="nope", latest_pull_id="leads:mongo.leads:2026-10-20"
    )
    assert result["available"] is False
    assert "nope" in result["reason"]
    assert "available" in result and result.get("added") is None  # no fabricated comparison body


@pytest.mark.asyncio
async def test_compare_treats_absence_in_incomplete_pull_as_indeterminate_not_removed():
    complete = lead_pulls.build_snapshot_doc(
        [lead_doc("L-1", "New", company="A"), lead_doc("L-2", "New", company="B")],
        pulled_at=NOW1, source="mongo.leads", expected_count=2,
    )
    incomplete = lead_pulls.build_snapshot_doc(
        [lead_doc("L-1", "New", company="A")],
        pulled_at=NOW2, source="mongo.leads", expected_count=2, error="page dropped",
    )
    assert incomplete["complete"] is False
    db = DB()
    db.lead_pulls = PullColl([complete, incomplete])

    result = await compare_lead_pulls(database=db)
    # the newest snapshot is incomplete, so the two-newest-complete default path
    # has only one usable snapshot and correctly refuses to compare
    assert result["available"] is False
    assert result["snapshots_found"] == 2

    # when explicitly selecting the incomplete latest, absence is indeterminate
    selected = await compare_lead_pulls(
        database=db,
        previous_pull_id="leads:mongo.leads:2026-10-20",
        latest_pull_id="leads:mongo.leads:2026-10-21",
    )
    assert selected["available"] is True
    assert selected["summary"].get("removed", 0) == 0
    assert selected["summary"]["indeterminate_removed"] == 1
    assert any("indeterminate" in note for note in selected["notes"])


@pytest.mark.asyncio
async def test_compare_with_no_snapshots_explains_history_starts_now():
    result = await compare_lead_pulls(database=DB())
    assert result["available"] is False
    assert result["snapshots_found"] == 0
    assert "No historical lead pulls are retained" in result["reason"]
    assert "lead_pulls" in result["required_capability"]
    assert "captured" in result["required_capability"]  # capture is now automatic


# ------------------------------------------------------ registration + agent

def test_compare_tool_registered_with_compare_args():
    tool = CHAT_TOOL_MAP["compare_lead_pulls"]
    assert tool.args_model is CompareArgs
    schema = tool.model_schema()["parameters"]["properties"]
    assert {"previous_pull_id", "latest_pull_id"} <= set(schema)


@pytest.mark.asyncio
async def test_invoke_tool_passes_pull_ids_through():
    db = DB()
    db.lead_pulls = PullColl([
        lead_pulls.build_snapshot_doc(
            [lead_doc("L-1", "New", company="A")],
            pulled_at=NOW1, source="mongo.leads", expected_count=1,
        ),
        lead_pulls.build_snapshot_doc(
            [lead_doc("L-1", "Contacted", company="A")],
            pulled_at=NOW2, source="mongo.leads", expected_count=1,
        ),
    ])
    result = await invoke_tool(
        "compare_lead_pulls",
        {"previous_pull_id": "leads:mongo.leads:2026-10-20",
         "latest_pull_id": "leads:mongo.leads:2026-10-21"},
        database=db,
    )
    assert result["available"] is True
    assert result["summary"]["reclassified"] == 1


def test_scheduler_runs_lead_snapshot_job_and_survives_failure():
    calls = {}

    async def capture(database=None):
        calls["ran"] = True
        return {"ok": True, "complete": True, "stored": True, "pull_id": "x"}

    scheduler = DashboardScheduler(lead_snapshot_func=capture)
    report = asyncio.run(scheduler._job_lead_snapshots())
    assert report["ok"] is True and calls.get("ran") is True

    async def boom(database=None):
        raise RuntimeError("capture exploded")

    failing = DashboardScheduler(lead_snapshot_func=boom)
    result = asyncio.run(failing._job_lead_snapshots())
    assert result["ok"] is False and result["stored"] is False
