"""Named business periods, resolved on the ERP's own calendar.

"What did customers pay this week?" is only answerable if one place decides what
"this week" means. Before this module the model picked the range from its own
clock, so the same question an hour apart produced two different windows. This
resolves each name server-side, on `ERP_TZ`, which is the calendar the rest of
the system already documents and uses (`app/services/metrics.py`, the
`_in_date_range` window in `chat_tools.py`).

Semantics, so the names are unambiguous:

  * every range is **inclusive** of both ends, as `date_from`/`date_to` already
    are;
  * a week runs **Monday through Sunday**;
  * a "last ..." period is the previous *complete* one, never a shifted partial;
  * "last N days" is a rolling window ending on the business today;
  * the calendar is the ERP's, so the business date rolls at US Central
    midnight - not at UTC and not at the reader's local midnight.

Adding a name is a registry entry here plus nothing else: the chat tool
arguments validate against `PERIOD_NAMES`, so a new period is immediately usable
by every tool that accepts `period`.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Callable

from app.services.processors import ERP_TZ

Period = tuple[date, date]
Resolver = Callable[[date], Period]


def _today(business_today: date) -> Period:
    return business_today, business_today


def _yesterday(business_today: date) -> Period:
    return business_today - timedelta(days=1), business_today - timedelta(days=1)


def _this_week(business_today: date) -> Period:
    start = business_today - timedelta(days=business_today.weekday())
    return start, start + timedelta(days=6)


def _last_week(business_today: date) -> Period:
    start = business_today - timedelta(days=business_today.weekday() + 7)
    return start, start + timedelta(days=6)


def _week_to_date(business_today: date) -> Period:
    """Monday through the business today, so "this week so far" is not future-dated."""
    start = business_today - timedelta(days=business_today.weekday())
    return start, business_today


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _month_after(year: int, month: int) -> tuple[int, int]:
    """(year, month) of the following month, rolling the year in December."""
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _next_month_start(value: date) -> date:
    # built from (year, month) rather than value.replace(), because replacing the
    # month on the 31st of March would ask for a 31 April
    year, month = _month_after(value.year, value.month)
    return date(year, month, 1)


def _this_month(business_today: date) -> Period:
    return _month_start(business_today), _next_month_start(business_today) - timedelta(days=1)


def _month_to_date(business_today: date) -> Period:
    return _month_start(business_today), business_today


def _last_month(business_today: date) -> Period:
    previous_end = _month_start(business_today) - timedelta(days=1)
    return _month_start(previous_end), previous_end


def _quarter_start(value: date) -> date:
    return date(value.year, ((value.month - 1) // 3) * 3 + 1, 1)


def _this_quarter(business_today: date) -> Period:
    start = _quarter_start(business_today)
    year, month = _month_after(start.year, start.month + 2)
    return start, date(year, month, 1) - timedelta(days=1)


def _last_quarter(business_today: date) -> Period:
    current = _quarter_start(business_today)
    previous_start = _quarter_start(current - timedelta(days=1))
    return previous_start, current - timedelta(days=1)


def _this_year(business_today: date) -> Period:
    return date(business_today.year, 1, 1), date(business_today.year, 12, 31)


def _last_year(business_today: date) -> Period:
    return date(business_today.year - 1, 1, 1), date(business_today.year - 1, 12, 31)


def _rolling(days: int) -> Resolver:
    def resolve(business_today: date) -> Period:
        return business_today - timedelta(days=days - 1), business_today

    return resolve


PERIODS: dict[str, Resolver] = {
    "today": _today,
    "yesterday": _yesterday,
    "this_week": _this_week,
    "this_week_to_date": _week_to_date,
    "last_week": _last_week,
    "this_month": _this_month,
    "this_month_to_date": _month_to_date,
    "last_month": _last_month,
    "this_quarter": _this_quarter,
    "last_quarter": _last_quarter,
    "this_year": _this_year,
    "last_year": _last_year,
    "last_7_days": _rolling(7),
    "last_30_days": _rolling(30),
    "last_90_days": _rolling(90),
}

PERIOD_NAMES: tuple[str, ...] = tuple(PERIODS)

# One line per name, so a tool description tells the model what each resolves to
# instead of leaving it to guess the convention.
PERIOD_HINT = (
    "Named period resolved on the ERP business calendar (US Central), inclusive "
    "of both ends; weeks run Monday to Sunday and a 'last ...' period is the "
    "previous complete one. Options: " + ", ".join(PERIOD_NAMES) + "."
)


def business_today(now: date | None = None) -> date:
    """The ERP's current business date, injectable for deterministic tests."""
    if isinstance(now, date):
        return now
    from datetime import datetime

    return datetime.now(ERP_TZ).date()


def resolve_period(name: str, today: date | None = None) -> Period:
    """(date_from, date_to) for one named period, or KeyError if it is not known.

    `KeyError` is deliberate: the argument validators turn it into a validation
    error the model can read, so an invented period never silently becomes a
    whole-history scan.
    """
    key = (name or "").strip().casefold()
    resolver = PERIODS.get(key)
    if resolver is None:
        raise KeyError(name)
    return resolver(business_today(today))


# --------------------------------------------------------------------------
# recognising a period in the user's own words
#
# The resolver is only authoritative if the request that names a period actually
# reaches it. Left to the model, "this week" could be answered with a range the
# model invented from its own clock (a rolling 7 days, or a Monday-anchored one,
# differing every hour) - the exact inconsistency the resolver exists to remove.
# So before a tool runs, the user's message is read here: a single recognised
# period phrase is resolved deterministically on the server, whatever dates the
# model chose to pass.
#
# Precedence, matching `PeriodMixin`: a date the *user* typed is theirs and wins,
# so a message carrying a literal date is not treated as naming a period at all.
# A message naming two different periods (a comparison) is left to the model too,
# because no single window is the right answer for it.
# --------------------------------------------------------------------------

# Ordered so a longer phrasing is matched before one nested inside it; a matched
# span is blanked out so "this week" cannot also fire inside "this week to date".
_PERIOD_PHRASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:this\s+week\s+to\s+date|week\s+to\s+date|so\s+far\s+this\s+week|this\s+week\s+so\s+far)\b"), "this_week_to_date"),
    (re.compile(r"\b(?:this\s+month\s+to\s+date|month\s+to\s+date|so\s+far\s+this\s+month|this\s+month\s+so\s+far)\b"), "this_month_to_date"),
    (re.compile(r"\b(?:last|previous)\s+week\b"), "last_week"),
    (re.compile(r"\b(?:this|current|the)\s+week\b"), "this_week"),
    (re.compile(r"\b(?:last|previous)\s+month\b"), "last_month"),
    (re.compile(r"\b(?:this|current)\s+month\b"), "this_month"),
    (re.compile(r"\b(?:last|previous)\s+quarter\b"), "last_quarter"),
    (re.compile(r"\b(?:this|current)\s+quarter\b"), "this_quarter"),
    (re.compile(r"\b(?:last|previous)\s+year\b"), "last_year"),
    (re.compile(r"\b(?:this|current)\s+year\b"), "this_year"),
    (re.compile(r"\b(?:last|past)\s+(?:7|seven)\s+days\b"), "last_7_days"),
    (re.compile(r"\b(?:last|past)\s+(?:30|thirty)\s+days\b"), "last_30_days"),
    (re.compile(r"\b(?:last|past)\s+(?:90|ninety)\s+days\b"), "last_90_days"),
    (re.compile(r"\byesterday\b"), "yesterday"),
    (re.compile(r"\btoday\b"), "today"),
)

# A date the user wrote themselves: ISO, m/d/y, or an explicit "12 March 2026".
# Any of these makes the range the user's, not a named period's.
_EXPLICIT_DATE_RE = re.compile(
    r"\b\d{4}-\d{1,2}(?:-\d{1,2})?\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    r"|\b\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\b",
    re.I,
)


def detect_period_name(text: str) -> str | None:
    """The one named period the user's message clearly asks for, or None.

    Returns a name in `PERIOD_NAMES` only when the message expresses exactly one
    period and the user did not type a date themselves. Ambiguity, an explicit
    date, or no recognised phrase all return None, which leaves the range to
    whatever the model passed - the resolver only overrides a recognised name.
    """
    lowered = (text or "").casefold()
    if not lowered or _EXPLICIT_DATE_RE.search(lowered):
        return None

    found: set[str] = set()
    scanned = lowered
    for pattern, name in _PERIOD_PHRASES:
        match = pattern.search(scanned)
        if match is None:
            continue
        found.add(name)
        scanned = scanned[: match.start()] + " " + scanned[match.end() :]
        if len(found) > 1:
            return None
    return next(iter(found)) if len(found) == 1 else None
