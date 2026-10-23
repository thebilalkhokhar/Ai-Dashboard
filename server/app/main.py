"""Biztek AI Command Center - FastAPI entry point.

Run with:  .venv/bin/python -m uvicorn app.main:app --port 8000

The lifespan opens Mongo, cleans up agent runs left "running" by a dead
process, starts the in-process dashboard scheduler (unless
SCHEDULER_ENABLED=false) and closes everything again on shutdown.

Every /api route requires the X-API-Key header (API_KEYS in .env) except
/health; CORS_ORIGINS decides which browser origins may call it.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.api import router as api_router
from app.api import live as live_api
from app.api.security import require_api_key, warn_if_auth_disabled
from app.config import settings
from app.services.redis_client import close_redis
from app.services.indexes import ensure_indexes
from app.services.run_guard import mark_stale_runs_abandoned

log = logging.getLogger("main")


def cors_origins() -> list[str]:
    """CORS_ORIGINS is a comma-separated list; the default is the local client."""
    return [origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open Mongo, prepare indexes, clear stale runs, run the scheduler, close."""
    from app.logging_setup import setup_logging  # local: keeps imports light

    setup_logging()  # daily files under server/logs/, terminal unchanged
    db.get_client()
    warn_if_auth_disabled()
    try:
        created = await ensure_indexes(db.get_db())
        log.info("mongo indexes ready: %s", created)
    except Exception:  # a hiccup here must not stop the API from serving
        log.exception("could not create the Mongo indexes")
    try:
        await mark_stale_runs_abandoned(db.get_db())
    except Exception:  # a hiccup here must not stop the API from serving
        log.exception("could not clean up stale agent runs at startup")
    scheduler = None
    if settings.SCHEDULER_ENABLED:
        from app.services.scheduler import get_scheduler  # local: keeps imports light

        scheduler = get_scheduler()
        try:
            await scheduler.start()
        except Exception:  # a broken scheduler must not take the API down
            log.exception("scheduler failed to start")
            scheduler = None
    try:
        yield
    finally:
        if scheduler is not None:
            await scheduler.stop()
        await close_redis()
        await db.close()


app = FastAPI(title="Biztek AI Command Center API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    """API is up, plus a real MongoDB round-trip. Never requires a key."""
    mongo_ok = await db.ping()
    return {
        "status": "ok" if mongo_ok else "degraded",
        "mongo": mongo_ok,
        "database": settings.MONGO_DB,
        "scheduler": settings.SCHEDULER_ENABLED,
    }


# every /api route is behind the API key; /health above is not
API_AUTH = [Depends(require_api_key)]

app.include_router(api_router, prefix="/api", dependencies=API_AUTH)
# GET /api/live (SSE) and GET /api/metrics
app.include_router(live_api.router, prefix="/api", dependencies=API_AUTH)
