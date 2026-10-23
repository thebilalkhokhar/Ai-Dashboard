"""The automatic mirror sync: due-check math and the job's skip paths.

The point of the feature (user, Oct 24): a mirror nobody re-runs by
hand goes stale - so the server must launch the sync itself when the
last run ages past C360_SYNC_HOURS, and stay out of the way otherwise.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.services.scheduler import DashboardScheduler, _c360_sync_due

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def test_due_when_never_ran_or_stamp_unreadable():
    assert _c360_sync_due(None, NOW, 6.0) is True
    assert _c360_sync_due({}, NOW, 6.0) is True
    assert _c360_sync_due({"started_at": "not-a-date"}, NOW, 6.0) is True


def test_not_due_inside_the_interval_due_after_it():
    recent = {"started_at": (NOW - timedelta(hours=2)).isoformat()}
    old = {"started_at": (NOW - timedelta(hours=7)).isoformat()}
    assert _c360_sync_due(recent, NOW, 6.0) is False
    assert _c360_sync_due(old, NOW, 6.0) is True
    # datetime stamps and naive values are both understood
    assert _c360_sync_due({"started_at": NOW - timedelta(hours=1)}, NOW, 6.0) is False
    naive_old = (NOW - timedelta(hours=9)).replace(tzinfo=None)
    assert _c360_sync_due({"started_at": naive_old}, NOW, 6.0) is True


class RunsDb:
    """Just enough database for the job: the sync-runs collection."""

    def __init__(self, last):
        self.customer360_sync_runs = self
        self._last = last

    async def find_one(self, sort=None):
        return self._last


@pytest.mark.asyncio
async def test_job_skips_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "C360_EMAIL", "")
    monkeypatch.setattr(settings, "C360_PASSWORD", "")
    job = DashboardScheduler(database=RunsDb(None), redis=None)
    result = await job._job_c360_sync(NOW)
    assert result["skipped"] == "unconfigured"


@pytest.mark.asyncio
async def test_job_skips_when_the_mirror_is_fresh(monkeypatch):
    monkeypatch.setattr(settings, "C360_EMAIL", "owner@example.com")
    monkeypatch.setattr(settings, "C360_PASSWORD", "pw")
    recent = {"started_at": (NOW - timedelta(hours=1)).isoformat()}
    job = DashboardScheduler(database=RunsDb(recent), redis=None)
    result = await job._job_c360_sync(NOW)
    assert result["skipped"] == "not-due"
