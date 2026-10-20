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
    pass


class SignalsSummary(Base):
    quotes_scanned: int = 0
    orders_scanned: int = 0
    data_from: Optional[str] = None          # earliest record date seen
    data_to: Optional[str] = None            # latest record date seen
    stale_quotes: StaleQuoteSummary = Field(default_factory=StaleQuoteSummary)
    unpaid_invoices: UnpaidSummary = Field(default_factory=UnpaidSummary)
    stalled_orders: StalledOrderSummary = Field(default_factory=StalledOrderSummary)


class SignalsReport(Base):
    generated_at: str
    stale_quotes: list[StaleQuote] = Field(default_factory=list)
    unpaid_invoices: list[UnpaidInvoice] = Field(default_factory=list)
    stalled_orders: list[StalledOrder] = Field(default_factory=list)
    summary: SignalsSummary = Field(default_factory=SignalsSummary)
