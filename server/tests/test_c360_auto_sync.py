"""The automatic mirror sync: due-check math and the job's launch paths.

The point of the feature (user, Oct 24): a mirror nobody re-runs by
hand goes stale - so the server must launch the sync itself. Two
speeds: a FULL sync every C360_SYNC_HOURS, and the cheap RECENT pass
(first pages only) every C360_RECENT_SYNC_MINUTES in between, so new
data lands within minutes while the full run stays rare.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.services.scheduler import (
    DashboardScheduler,
    _c360_recent_due,
    _c360_sync_due,
)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def stamp(**kwargs) -> dict:
    return {"started_at": (NOW - timedelta(**kwargs)).isoformat()}


def test_full_due_when_never_ran_or_stamp_unreadable():
    assert _c360_sync_due(None, NOW, 6.0) is True
    assert _c360_sync_due({}, NOW, 6.0) is True
    assert _c360_sync_due({"started_at": "not-a-date"}, NOW, 6.0) is True


def test_full_not_due_inside_the_interval_due_after_it():
    assert _c360_sync_due(stamp(hours=2), NOW, 6.0) is False
    assert _c360_sync_due(stamp(hours=7), NOW, 6.0) is True
    # datetime stamps and naive values are both understood
    assert _c360_sync_due({"started_at": NOW - timedelta(hours=1)}, NOW, 6.0) is False
    naive_old = (NOW - timedelta(hours=9)).replace(tzinfo=None)
    assert _c360_sync_due({"started_at": naive_old}, NOW, 6.0) is True


def test_recent_due_uses_the_fresher_of_the_two_clocks():
    assert _c360_recent_due(None, None, NOW, 15) is True
    # a recent pass five minutes ago: not due
    assert _c360_recent_due(stamp(minutes=5), None, NOW, 15) is False
    # a full sync five minutes ago also counts as fresh
    assert _c360_recent_due(None, stamp(minutes=5), NOW, 15) is False
    # both stale: due
    assert _c360_recent_due(stamp(minutes=40), stamp(hours=3), NOW, 15) is True


class RunsDb:
    """Just enough database for the job: the sync-runs collection."""

    def __init__(self, full=None, recent=None):
        self.customer360_sync_runs = self
        self._full = full
        self._recent = recent

    async def find_one(self, filter=None, sort=None):
        if filter and filter.get("scope") == "recent":
            return self._recent
        return self._full


class FakeProc:
    async def wait(self):
        return 0


def capture_launches(monkeypatch) -> list:
    calls: list = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


def configured(monkeypatch):
    monkeypatch.setattr(settings, "C360_EMAIL", "owner@example.com")
    monkeypatch.setattr(settings, "C360_PASSWORD", "pw")


@pytest.mark.asyncio
async def test_job_skips_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "C360_EMAIL", "")
    monkeypatch.setattr(settings, "C360_PASSWORD", "")
    job = DashboardScheduler(database=RunsDb(), redis=None)
    result = await job._job_c360_sync(NOW)
    assert result["skipped"] == "unconfigured"


@pytest.mark.asyncio
async def test_job_skips_when_everything_is_fresh(monkeypatch):
    configured(monkeypatch)
    calls = capture_launches(monkeypatch)
    job = DashboardScheduler(
        database=RunsDb(full=stamp(minutes=5), recent=stamp(minutes=5)), redis=None
    )
    result = await job._job_c360_sync(NOW)
    assert result["skipped"] == "not-due"
    assert calls == []


@pytest.mark.asyncio
async def test_job_launches_the_recent_pass_when_only_it_is_owed(monkeypatch):
    configured(monkeypatch)
    calls = capture_launches(monkeypatch)
    job = DashboardScheduler(
        database=RunsDb(full=stamp(hours=1), recent=None), redis=None
    )
    result = await job._job_c360_sync(NOW)
    assert result["scope"] == "recent"
    assert "--recent" in calls[0]


@pytest.mark.asyncio
async def test_job_launches_a_full_sync_when_the_full_clock_expires(monkeypatch):
    configured(monkeypatch)
    calls = capture_launches(monkeypatch)
    job = DashboardScheduler(
        database=RunsDb(full=stamp(hours=9), recent=stamp(minutes=1)), redis=None
    )
    result = await job._job_c360_sync(NOW)
    assert result["scope"] == "full"
    assert "--recent" not in calls[0]
