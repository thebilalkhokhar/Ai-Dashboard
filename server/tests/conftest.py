"""Test defaults.

The scheduler starts inside the FastAPI lifespan and would immediately hit the
ERP and Redis. Tests run it explicitly (with fakes), so keep it off for every
test process - including the TestClient tests that go through the lifespan.

Auth is off by default for the same reason (no key in every fixture); the auth
tests set settings.AUTH_ENABLED back to true with monkeypatch.

The task-event service is instrumented into the graph, chat and scheduler. Its
process-wide default would otherwise open a real MongoDB/Redis connection from
unit tests that never opted in, so an autouse fixture installs a no-op event
service. Tests that assert on events call ``set_event_service`` (or pass a
service) themselves and override this for the duration of the test.
"""

from __future__ import annotations

import os

# Must happen before app.config.settings is created.
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["AUTH_ENABLED"] = "false"

import pytest  # noqa: E402


class NoopEventService:
    """A task-event service that records nothing and touches no I/O."""

    async def emit(self, **_kwargs):
        return None

    async def last_status_by_agent(self, *_a, **_k):
        return {}

    async def get_events(self, *_a, **_k):
        return []

    async def recent_tasks(self, *_a, **_k):
        return []

    async def task_exists(self, *_a, **_k):
        return False

    async def ensure_indexes(self):
        return None


@pytest.fixture(autouse=True)
def _default_noop_event_service():
    from app.services.events import set_event_service

    set_event_service(NoopEventService())
    yield
    set_event_service(None)
