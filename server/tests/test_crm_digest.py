"""The CRM morning digest: yesterday's payments from the mirror plus
the latest run's waiting queue and stalled deals - Python numbers,
honest degradation, no CRM calls."""

from datetime import datetime, timezone

import pytest

from app.services.crm_digest import build_crm_digest


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, _limit):
        return list(self._rows)


class _Payments:
    def __init__(self, rows):
        self._rows = rows

    def find(self, _query):
        return _Cursor(self._rows)


class _Runs:
    def __init__(self, doc):
        self._doc = doc

    async def find_one(self, _query, sort=None):
        return self._doc


class _Db:
    def __init__(self, payments, run):
        self.customer360_payments = _Payments(payments)
        self.agent_runs = _Runs(run)


# 15:00 UTC = 10:00 in Chicago - yesterday is 2026-10-23 there
NOW = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)

PAYMENTS = [
    {"customerName": "MNJ Enterprise LLC", "amountReceived": 4627.0, "amountReceiveDate": "2026-10-23T00:00:00.000Z"},
    {"customerName": "Polkadot", "amountReceived": 14150.0, "amountReceiveDate": "2026-10-23T00:00:00.000Z"},
    {"customerName": "Old Co", "amountReceived": 999.0, "amountReceiveDate": "2026-10-22T00:00:00.000Z"},
]

RUN = {
    "started_at": "2026-10-24T03:33:07+00:00",
    "crm_engagement": [
        {"contact_name": "SMS Person", "channel": "SMS", "waiting_days": 9, "last_customer_message": "any update?"},
        {"contact_name": "WA Person", "channel": "WhatsApp", "waiting_days": 2, "last_customer_message": "price please"},
        {"contact_name": "Broken", "error": "boom", "channel": "WhatsApp"},
    ],
    "crm_deal_watch": [
        {"deal_name": "Small deal", "account_name": "A Co", "amount": 5000, "days_idle": 20},
        {"deal_name": "Big deal", "account_name": "B Co", "amount": 12000, "days_idle": 30},
    ],
}


@pytest.mark.asyncio
async def test_digest_for_yesterday_business_date():
    digest = await build_crm_digest(_Db(PAYMENTS, RUN), now=NOW)
    assert digest["date"] == "2026-10-23"
    payments = digest["payments"]
    assert payments["count"] == 2
    assert payments["total_received"] == 18777.0
    assert payments["top"][0]["customer"] == "Polkadot"
    waiting = digest["waiting"]
    assert waiting["total"] == 2
    assert waiting["whatsapp_total"] == 1
    assert waiting["top"][0]["name"] == "WA Person"
    deals = digest["stalled_deals"]
    assert deals["total"] == 2
    assert deals["top"][0]["name"] == "Big deal"
    assert digest["as_of_run"] == "2026-10-24T03:33:07+00:00"


@pytest.mark.asyncio
async def test_digest_total_is_none_when_a_row_has_no_amount():
    poisoned = PAYMENTS + [
        {"customerName": "No Amount Co", "amountReceiveDate": "2026-10-23T00:00:00.000Z"}
    ]
    digest = await build_crm_digest(_Db(poisoned, RUN), now=NOW)
    assert digest["payments"]["count"] == 3
    assert digest["payments"]["total_received"] is None


@pytest.mark.asyncio
async def test_digest_degrades_to_zeros_without_data():
    digest = await build_crm_digest(_Db([], None), now=NOW)
    assert digest["payments"]["count"] == 0
    assert digest["waiting"]["total"] == 0
    assert digest["stalled_deals"]["total"] == 0
    assert digest["as_of_run"] is None
