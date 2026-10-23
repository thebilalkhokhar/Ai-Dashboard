"""Daily file logging, so the terminal is no longer the only record.

`setup_logging()` (called once from the app lifespan) attaches ONE file
handler that rolls at midnight: the live file is `logs/server.log`, and
each finished day is kept as `logs/server-YYYY-MM-DD.log`, 30 days back.
The terminal keeps behaving exactly as before - this only ADDS the file.

The /health access spam (the client polls it every 30s per open tab) is
kept out of the file; everything else - app modules, uvicorn, ERP calls,
chat warnings - is written with timestamps so any day can be reviewed
later without anyone copying terminal output by hand.
"""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

_MARK = "_biztek_daily_file"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class _DropHealthAccess(logging.Filter):
    """Keep the constant /health poll out of the daily file."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            return '"GET /health' not in record.getMessage()
        except Exception:
            return True


def _find_marked() -> Optional[logging.Handler]:
    for handler in logging.getLogger().handlers:
        if getattr(handler, _MARK, False):
            return handler
    return None


def setup_logging(log_dir: Optional[Path] = None) -> Optional[logging.Handler]:
    """Attach the daily file handler (idempotent). Returns it, or None."""
    existing = _find_marked()
    if existing is not None:
        return existing

    directory = (
        Path(log_dir)
        if log_dir is not None
        else Path(__file__).resolve().parent.parent / "logs"
    )
    try:
        directory.mkdir(parents=True, exist_ok=True)
        base = directory / "server.log"
        handler = TimedRotatingFileHandler(
            base, when="midnight", backupCount=30, encoding="utf-8"
        )
    except Exception:
        return None

    handler.suffix = "%Y-%m-%d"

    def _namer(default_name: str) -> str:
        # default: ".../server.log.2026-10-23" -> ".../server-2026-10-23.log"
        date_part = default_name[len(str(base)) + 1:]
        return str(base.with_name(f"server-{date_part}.log"))

    handler.namer = _namer  # type: ignore[method-assign]
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    handler.addFilter(_DropHealthAccess())
    setattr(handler, _MARK, True)

    # Root catches every app logger (they propagate). Uvicorn's own
    # loggers only get the handler directly when they do not propagate
    # (uvicorn.access is configured that way), so nothing is written twice.
    targets = [logging.getLogger()]
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        if not logger.propagate and handler not in logger.handlers:
            targets.append(logger)
    for logger in targets:
        logger.addHandler(handler)
    if logging.getLogger().level in (logging.NOTSET, logging.WARNING):
        logging.getLogger().setLevel(logging.INFO)
    return handler
