"""The payment-timestamp scenario, reproduced exactly.

On the morning of October 23, 2026 (PKT) the user asked for today's
payments and was shown October 22 data, then told each of four payments
"arrived at 5:00 AM PKT" - because every source value is
`2026-10-22T00:00:00-05:00`, midnight in the ERP's offset, and the ERP
also keeps a separate date-only `payment_date`. Midnight here is a date
wearing a timestamp's clothes: the exact arrival time does not exist in
the source, and no layer may manufacture one.

These tests pin the honest contract:
  * date-only values surface as dates, with the limitation stated;
  * genuine instants are converted to the system timezone, 24-hour,
    by the backend, not the model;
  * payment date filtering follows the user's calendar (Asia/Karachi),
    so a late-evening ERP instant belongs to the PKT day it lands in;
  * October 23 returns October 23 records only - never October 22
    substituted silently;
  * an answer may not state an exact time the evidence does not hold.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from langchain_core.messages import AIMessage

from app.agents import chat, chat_tools
from app.agents.chat import _answer_consistency_errors, run_chat
from app.services import processors
from app.agents.chat_tools import get_payments, get_revenue
from test_chat import FakeCollection, FakeModel
from test_chat_data_tools import business_db, payment, tool_call

MIDNIGHT = "2026-10-22T00:00:00-05:00"


def scenario_payment(payment_id: int, amount: float) -> dict:
    return payment(
        1,
        id=payment_id,
        amount=amount,
        payment_date="2026-10-22",
        received_at=MIDNIGHT,
        payment_ref=f"{payment_id}-2026-10",
    )


def scenario_db():
    return business_db(payments_flat=FakeCollection([
        scenario_payment(7266, 1000.0),
        scenario_payment(7265, 10404.0),
        scenario_payment(7264, 250.0),
        scenario_payment(7263, 250.0),
    ]))


# ------------------------------------------------------------ the four rows

@pytest.mark.anyio
async def test_date_only_payments_report_dates_not_times():
    result = await get_payments(date_from=date(2026, 10, 22), date_to=date(2026, 10, 22),
                                 database=scenario_db())
    assert result["total_count"] == 4
    assert result["sum_amount"] == 11904.0
    for item in result["items"]:
        assert item["timestamp_precision"] == "date"
        assert item["received_date"] == "2026-10-22"
        # no exact time is exposed for the model to narrate
        assert "received_time" not in item
        assert item.get("received_at") is None


@pytest.mark.anyio
async def test_october_23_returns_no_october_22_payments():
    result = await get_payments(date_from=date(2026, 10, 23), date_to=date(2026, 10, 23),
                                 database=scenario_db())
    assert result["total_count"] == 0
    assert result["items"] == []
    revenue = await get_revenue(date(2026, 10, 23), date(2026, 10, 23), database=scenario_db())
    assert revenue["payment_count"] == 0
    assert revenue["total_payments"] == 0


# ------------------------------------------------------- genuine timestamps

@pytest.mark.anyio
async def test_genuine_instant_is_converted_to_pkt_by_the_backend():
    database = business_db(payments_flat=FakeCollection([
        payment(1, id=8001, amount=75.0, payment_date="2026-10-22",
                received_at="2026-10-22T14:24:32-05:00"),
    ]))
    result = await get_payments(date_from=date(2026, 10, 23), date_to=date(2026, 10, 23),
                                 database=database)
    # 14:24:32 at -05:00 is 00:24:32 PKT on October 23 - the payment
    # belongs to the user's October 23, not the ERP's October 22.
    assert result["total_count"] == 1
    item = result["items"][0]
    assert item["timestamp_precision"] == "instant"
    # Clock time: system timezone, 24-hour, never 12-hour AM/PM, never raw UTC.
    shown = processors.parse_date("2026-10-22T14:24:32-05:00").astimezone(
        processors.system_timezone()
    )
    assert item["received_time"] == shown.strftime(
        f"%b %d, %Y, %H:%M {processors.system_tz_label()}"
    )
    assert "AM" not in item["received_time"] and "PM" not in item["received_time"]
    assert item["received_date"] == "2026-10-23"
    on_erp_day = await get_payments(date_from=date(2026, 10, 22), date_to=date(2026, 10, 22),
                                    database=database)
    assert on_erp_day["total_count"] == 0


@pytest.mark.anyio
async def test_missing_and_naive_timestamps_are_classified_honestly():
    database = business_db(payments_flat=FakeCollection([
        payment(1, id=8002, amount=10.0, payment_date="2026-10-22", received_at=None),
        payment(2, id=8003, amount=20.0, payment_date="2026-10-22",
                received_at="2026-10-22 09:15:00"),
    ]))
    result = await get_payments(date_from=date(2026, 10, 22), date_to=date(2026, 10, 22),
                                 database=database)
    by_id = {item["payment_id"]: item for item in result["items"]}
    assert by_id[8002]["timestamp_precision"] == "missing"
    # a naive source value is read on the ERP's wall clock and converted
    assert by_id[8003]["timestamp_precision"] == "instant"
    shown = processors.parse_date("2026-10-22 09:15:00").astimezone(
        processors.system_timezone()
    )
    assert by_id[8003]["received_time"] == shown.strftime(
        f"%b %d, %Y, %H:%M {processors.system_tz_label()}"
    )


# ------------------------------------------------------------- guard clause

def _results_with_precision(precision: str) -> list[dict]:
    return [{"tool": "get_payments", "result": {"items": [{"timestamp_precision": precision}]}}]


def test_answer_cannot_invent_an_exact_time_for_date_only_evidence():
    claim = "All four payments arrived at 5:00 AM PKT."
    errors = _answer_consistency_errors(claim, _results_with_precision("date"), None)
    assert any("exact payment time" in error for error in errors)
    disclosed = "All four payments are dated October 22; the exact arrival time is not recorded."
    assert _answer_consistency_errors(disclosed, _results_with_precision("date"), None) == []
    genuine = _answer_consistency_errors(claim, _results_with_precision("instant"), None)
    assert genuine == []


# ------------------------------------------------- the scenario, end to end

# 07:00 PKT on October 23 is 02:00 UTC (and still October 22 at the ERP).
FROZEN_NOW = datetime(2026, 10, 23, 2, 0, tzinfo=timezone.utc)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW if tz is None else FROZEN_NOW.astimezone(tz)


@pytest.fixture
def frozen_clock(monkeypatch):
    for module in (chat, chat_tools):
        monkeypatch.setattr(module, "datetime", FrozenDateTime)


@pytest.mark.anyio
async def test_today_means_the_users_october_23_not_the_erps_october_22(frozen_clock):
    model = FakeModel([
        tool_call("get_payments", {"period": "today"}),
        AIMessage(content="No payments were received on 2026-10-23."),
    ])
    result = await run_chat("thread-ts", "Today's payments and tell me the exact time each payment came in.",
                            database=scenario_db(), model=model)
    assert result["text"] == "No payments were received on 2026-10-23."
    payments_result = next(
        entry["result"] for entry in result["tool_results"] if entry["tool"] == "get_payments"
    )
    assert payments_result["total_count"] == 0
    assert payments_result["filters_applied"]["date_from"] == "2026-10-23"


@pytest.mark.anyio
async def test_yesterdays_date_only_payments_cannot_gain_a_time(frozen_clock):
    model = FakeModel([
        tool_call("get_payments", {"period": "yesterday"}),
        AIMessage(content="Four payments totaling $11,904 arrived at 5:00 AM PKT."),
        AIMessage(content="Four payments totaling $11,904 are dated 2026-10-22. The exact arrival time is not recorded."),
    ])
    result = await run_chat("thread-ts", "What about yesterday's payments?",
                            database=scenario_db(), model=model)
    # the fabricated time was rejected and regenerated into the honest one
    assert "5:00 AM" not in result["text"]
    assert "not recorded" in result["text"]
    assert "$11,904" in result["text"]


class SubscriptableDb:
    """get_record addresses collections as db[name]; the shared fakes use
    attributes, so this thin wrapper exposes both."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __getitem__(self, name):
        return getattr(self._inner, name)


@pytest.mark.anyio
async def test_single_payment_record_cannot_leak_a_fake_time():
    from app.agents.chat_tools import get_record

    record = await get_record("payments", "7265", database=SubscriptableDb(scenario_db()))
    assert record["found"] is True
    assert record["record"]["timestamp_precision"] == "date"
    assert "received_at" not in record["record"]
    assert record["record"]["received_date"] == "2026-10-22"
