"""Biztek AI Command Center - FastAPI entry point.

Run with:  .venv/bin/python -m uvicorn app.main:app --port 8000

The lifespan opens Mongo, starts the in-process dashboard scheduler (unless
SCHEDULER_ENABLED=false) and closes everything again on shutdown. CORS is open
for the local Next.js client on http://localhost:3000 only.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.api import router as api_router
from app.api import live as live_api
from app.config import settings
from app.services.redis_client import close_redis

log = logging.getLogger("main")

# The Next.js dev server runs here while everything is local.
LOCAL_CLIENT_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open Mongo, run the scheduler, then clean both up."""
    db.get_client()
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
    allow_origins=LOCAL_CLIENT_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    """API is up, plus a real MongoDB round-trip."""
    mongo_ok = await db.ping()
    return {
        "status": "ok" if mongo_ok else "degraded",
        "mongo": mongo_ok,
        "database": settings.MONGO_DB,
        "scheduler": settings.SCHEDULER_ENABLED,
    }


app.include_router(api_router, prefix="/api")
# GET /api/live (SSE) and GET /api/metrics
app.include_router(live_api.router, prefix="/api")
