"""Test defaults.

The scheduler starts inside the FastAPI lifespan and would immediately hit the
ERP and Redis. Tests run it explicitly (with fakes), so keep it off for every
test process - including the TestClient tests that go through the lifespan.
"""

from __future__ import annotations

import os

# Must happen before app.config.settings is created.
os.environ["SCHEDULER_ENABLED"] = "false"
