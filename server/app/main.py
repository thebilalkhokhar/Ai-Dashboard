"""Biztek AI Command Center - FastAPI entry point.

Run with:  .venv/bin/python -m uvicorn app.main:app --port 8000
Only deterministic endpoints live here. No AI/agent code yet.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.api import router as api_router
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the Mongo connection pool on startup, close it on shutdown."""
    db.get_client()
    yield
    await db.close()


app = FastAPI(title="Biztek AI Command Center API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    """API is up, plus a real MongoDB round-trip."""
    mongo_ok = await db.ping()
    return {
        "status": "ok" if mongo_ok else "degraded",
        "mongo": mongo_ok,
        "database": settings.MONGO_DB,
    }


app.include_router(api_router, prefix="/api")
