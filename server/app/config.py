"""Application settings.

Everything secret comes from `server/.env` (which is git-ignored). The ERP
refresh token is only ever read from here - it is never printed or committed.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# server/  (this file lives in server/app/)
SERVER_DIR = Path(__file__).resolve().parent.parent


def months_ago(months: int, today: Optional[date] = None) -> date:
    """The date N months before `today` (day clamped to the length of the month)."""
    today = today or date.today()
    year = today.year
    month = today.month - months
    while month <= 0:
        month += 12
        year -= 1
    first_of_next = date(year + (1 if month == 12 else 0), (month % 12) + 1, 1)
    last_day = (first_of_next - timedelta(days=1)).day
    return date(year, month, min(today.day, last_day))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(SERVER_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- MongoDB ---
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DB: str = "AI"

    # --- ERP (read-only) ---
    ERP_BASE_URL: str = "https://erp.print247.us"
    ERP_REFRESH_TOKEN: str = ""

    # --- How far back the ingestion goes (YYYY-MM-DD). Default: 12 months ago ---
    INGEST_FROM: str = Field(default_factory=lambda: months_ago(12).isoformat())

    # --- Business rules, in days ---
    STALE_QUOTE_DAYS: int = 3
    STALLED_ORDER_DAYS: int = 2
    OVERDUE_INVOICE_DAYS: int = 30

    # --- Misc / tuning ---
    REDIS_URL: str = ""
    ERP_REQUEST_DELAY_SECONDS: float = 0.3
    ERP_LEAD_BATCH_SIZE: int = 25
    ERP_PAGE_LIMIT: int = 500

    @field_validator("INGEST_FROM")
    @classmethod
    def _must_be_iso_date(cls, value: str) -> str:
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError as exc:
            raise ValueError("INGEST_FROM must look like YYYY-MM-DD") from exc


settings = Settings()
