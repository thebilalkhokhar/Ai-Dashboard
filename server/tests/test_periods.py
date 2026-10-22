"""The named-period resolver: one server-side answer for "this week".

Before this module the model chose the date range itself, so the same question
asked an hour apart produced different windows. These tests pin the calendar
rules the whole system already documents - ERP business day, Monday-to-Sunday
weeks, inclusive ends, "last ..." meaning the previous *complete* period - and
prove the tool arguments actually use the resolver.
"""

from __future__ import annotations

import calendar
from datetime import date

import pytest
from pydantic import ValidationError

from app.agents.chat_tools import DateFilterArgs, PaymentArgs, RevenueArgs
from app.services.periods import PERIOD_NAMES, detect_period_name, resolve_period

# 2026-10-22 is a Thursday; 2026-10-19 is that week's Monday.
THURSDAY = date(2026, 10, 22)


def named(name: str, today: date = THURSDAY) -> tuple[date, date]:
    return resolve_period(name, today)


def test_week_periods_run_monday_to_sunday():
    assert named("this_week") == (date(2026, 10, 19), date(2026, 10, 25))
    assert named("last_week") == (date(2026, 10, 12), date(2026, 10, 18))
    # "to date" must not reach into the future
    assert named("this_week_to_date") == (date(2026, 10, 19), date(2026, 10, 22))


def test_a_last_period_is_the_previous_complete_one():
    assert named("this_month") == (date(2026, 10, 1), date(2026, 10, 31))
    assert named("last_month") == (date(2026, 9, 1), date(2026, 9, 30))
    assert named("this_quarter") == (date(2026, 10, 1), date(2026, 12, 31))
    assert named("last_quarter") == (date(2026, 7, 1), date(2026, 9, 30))
    assert named("this_year") == (date(2026, 1, 1), date(2026, 12, 31))
    assert named("last_year") == (date(2025, 1, 1), date(2025, 12, 31))


def test_rolling_windows_are_n_days_ending_today():
    assert named("last_7_days") == (date(2026, 10, 16), date(2026, 10, 22))
    assert named("last_30_days") == (date(2026, 9, 23), date(2026, 10, 22))
    assert named("today") == (THURSDAY, THURSDAY)
    assert named("yesterday") == (date(2026, 10, 21), date(2026, 10, 21))


@pytest.mark.parametrize("year", [2024, 2025, 2026, 2027])
def test_no_period_ever_breaks_on_a_month_end(year: int):
    """The bug this guards: replacing the month on the 31st asks for April 31."""
    for month in range(1, 13):
        last_day = date(year, month, calendar.monthrange(year, month)[1])
        for name in PERIOD_NAMES:
            start, end = resolve_period(name, last_day)
            assert start <= end, (year, month, name)
            if name.endswith("month") or name.endswith("quarter") or name.endswith("year"):
                assert start.day == 1 or name.endswith("year"), (name, start)
                assert end.day == calendar.monthrange(end.year, end.month)[1], (name, end)


def test_an_unknown_period_is_a_readable_error_not_a_whole_history_scan():
    with pytest.raises(KeyError):
        resolve_period("next_fortnight")


# ---------------------------------------------------------------- arguments


def test_a_named_period_fills_the_date_pair_the_tool_uses():
    args = PaymentArgs.model_validate({"period": "this_week", "limit": 10})
    assert args.date_from == date(2026, 10, 19)
    assert args.date_to == date(2026, 10, 25)


def test_explicit_dates_always_win():
    args = PaymentArgs.model_validate(
        {"date_from": date(2026, 1, 1), "date_to": date(2026, 1, 31)}
    )
    assert (args.date_from, args.date_to) == (date(2026, 1, 1), date(2026, 1, 31))


def test_period_and_explicit_dates_cannot_be_combined():
    """Silently preferring one would make the answer's range unpredictable."""
    with pytest.raises(ValidationError) as refused:
        PaymentArgs.model_validate({"period": "this_week", "date_from": date(2026, 1, 1)})
    assert "cannot be combined" in str(refused.value)


def test_a_required_range_tool_needs_either_dates_or_a_period():
    with pytest.raises(ValidationError):
        RevenueArgs.model_validate({})
    assert RevenueArgs.model_validate({"period": "last_month"}).date_from == date(2026, 9, 1)


def test_period_reaches_the_model_schema_but_not_the_tool_call():
    """The model must see the option; the function must not receive a kwarg it
    does not accept - invoke_tool splats model_dump() straight into it."""
    schema = PaymentArgs.model_json_schema()["properties"]
    assert "period" in schema
    assert "Named period" in schema["period"]["description"]
    dumped = PaymentArgs.model_validate({"period": "this_week"}).model_dump()
    assert "period" not in dumped
    assert dumped["date_from"] == date(2026, 10, 19)


def test_an_invented_period_name_is_refused_at_validation():
    from app.agents.chat_tools import invoke_tool  # noqa: F401 - import guard

    with pytest.raises(ValidationError) as refused:
        DateFilterArgs.model_validate({"period": "last_season"})
    assert "unknown period" in str(refused.value)


def test_the_model_is_told_to_name_the_period_not_date_it():
    """A resolver nobody uses fixes nothing, so the instruction is asserted here.

    Live check: the model still sent its own 7-day window for "this week", which
    is why this is pinned rather than assumed.
    """
    from app.agents import chat

    source = chat.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "`period` argument" in text
    assert "never turn a named period into your own date_from/date_to" in text


@pytest.mark.asyncio
async def test_a_named_period_reaches_the_model_as_a_usable_option():
    from app.agents.chat_tools import CHAT_TOOL_MAP

    for name in ("get_payments", "get_invoices", "get_quotes", "get_orders", "get_revenue"):
        schema = CHAT_TOOL_MAP[name].model_schema()["parameters"]["properties"]
        assert "period" in schema, name


# ------------------------------------------------- reading the user's words
#
# The resolver only fixes the inconsistency if the request that names a period
# actually reaches it. detect_period_name is the single place that decides, from
# the user's own message, whether a tool call's window must be server-resolved.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What did customers pay this week?", "this_week"),
        ("what did customers pay THIS WEEK?", "this_week"),
        ("How many invoices are overdue this month?", "this_month"),
        ("revenue last week", "last_week"),
        ("payments today", "today"),
        ("orders yesterday please", "yesterday"),
        ("sales in the last 7 days", "last_7_days"),
        ("leads from the past 30 days", "last_30_days"),
        ("total this quarter", "this_quarter"),
        ("compare with last year", "last_year"),
        ("week to date revenue", "this_week_to_date"),
        ("so far this month", "this_month_to_date"),
    ],
)
def test_a_period_the_user_named_is_recognised(text: str, expected: str):
    assert detect_period_name(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "which customers owe us money",
        "show me the stuck leads",
        # a date the user typed themselves wins over any period reading
        "what was paid between 2026-01-01 and 2026-02-01",
        "payments on 12/01/2025",
        "how much did Acme pay on 5 March",
        # two different periods in one message: no single window is correct
        "compare this week to last week",
        "this month or last month",
    ],
)
def test_a_message_that_does_not_name_one_period_is_left_to_the_model(text: str):
    assert detect_period_name(text) is None


def test_this_week_is_never_read_as_a_nested_this_week_to_date():
    """'this week' is a substring of 'this week to date'; the longer phrase wins
    once and must not also fire the shorter name."""
    assert detect_period_name("revenue for this week to date") == "this_week_to_date"
    assert detect_period_name("revenue for this week") == "this_week"
