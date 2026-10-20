"""Pydantic output models for GET /api/signals.

Only fields the ERP actually sends are included; everything is Optional so a
missing value can never 500 the endpoint.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SaleRep(Base):
    id: Optional[int] = None
    name: Optional[str] = None


class CustomerRef(Base):
    id: Optional[int] = None
    company_name: Optional[str] = None


class StaleQuote(Base):
    id: Optional[int] = None
    ref: Optional[str] = None
    status: Optional[str] = None
    status_order: Optional[int] = None
    amount: float = 0.0
    age_days: Optional[float] = None
    days_stale: int = 0
    created_at: Optional[str] = None
    customer: Optional[CustomerRef] = None
    sale_rep: Optional[SaleRep] = None


class UnpaidInvoice(Base):
    id: Optional[int] = None
    inv_ref: Optional[str] = None
    status: Optional[int] = None
    status_label: Optional[str] = None
    payment_status: Optional[int] = None
    payment_status_label: Optional[str] = None
    total_amount: float = 0.0
    amount_paid: float = 0.0
    balance: float = 0.0
    overdue: bool = False
    days_invoice_age: int = 0
    invoice_date: Optional[str] = None
    customer: Optional[CustomerRef] = None
    sale_rep: Optional[SaleRep] = None


class StalledOrder(Base):
    id: Optional[int] = None
    inv_ref: Optional[str] = None
    status: Optional[int] = None
    status_label: Optional[str] = None
    payment_status: Optional[int] = None
    payment_status_label: Optional[str] = None
    invoice_date: Optional[str] = None
    days_invoice_age: int = 0
    age_days: Optional[float] = None
    total_amount: float = 0.0
    customer: Optional[CustomerRef] = None
    sale_rep: Optional[SaleRep] = None


class TotalRow(Base):
    count: int = 0
    total_amount: float = 0.0


class StaleQuoteSummary(TotalRow):
    pass


class UnpaidSummary(TotalRow):
    overdue_count: int = 0
    unpaid_count: int = 0            # payment_status 0 / null
    partially_paid_count: int = 0    # payment_status 1


class StalledOrderSummary(TotalRow):
    # the stalled list is unreliable (see processors.stalled_orders docstring)
    reliable: bool = False


class SignalsSummary(Base):
    quotes_scanned: int = 0
    orders_scanned: int = 0
    data_from: Optional[str] = None          # earliest record date seen
    data_to: Optional[str] = None            # latest record date seen
    stale_quotes: StaleQuoteSummary = Field(default_factory=StaleQuoteSummary)
    unpaid_invoices: UnpaidSummary = Field(default_factory=UnpaidSummary)
    stalled_orders: StalledOrderSummary = Field(default_factory=StalledOrderSummary)


class StalledOrderList(Base):
    """Wrapper marking the stalled list as unreliable for its consumers."""

    reliable: bool = False
    items: list[StalledOrder] = Field(default_factory=list)


class SignalsReport(Base):
    generated_at: str
    stale_quotes: list[StaleQuote] = Field(default_factory=list)
    unpaid_invoices: list[UnpaidInvoice] = Field(default_factory=list)
    stalled_orders: StalledOrderList = Field(default_factory=StalledOrderList)
    summary: SignalsSummary = Field(default_factory=SignalsSummary)


class StuckLead(Base):
    """One stuck lead from GET /api/leads/stuck (parsed by pure Python).

    artwork_present is True/False only when the description stated it
    explicitly; None means "not stated" - the backend never guesses.
    Email and phone are intentionally absent: the UI never needs them and the
    agent never sends anything.
    """

    lead_id: str = ""
    contact_name: Optional[str] = None
    company: Optional[str] = None
    status: str = ""
    source: Optional[str] = None
    days_since_activity: int = 0
    last_activity_at: Optional[str] = None
    created_at: Optional[str] = None
    product: Optional[str] = None
    type: Optional[str] = None
    quantity: Optional[str] = None
    size: Optional[str] = None
    material: Optional[str] = None
    finish: Optional[str] = None
    artwork_present: Optional[bool] = None
    description_shape: Optional[str] = None
    missing: list[str] = Field(default_factory=list)
    has_contact_details: bool = False


class StuckLeadsResponse(Base):
    generated_at: str
    days: int
    total: int                       # matched rows before the limit was applied
    items: list[StuckLead] = Field(default_factory=list)
