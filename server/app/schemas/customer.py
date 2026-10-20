"""Pydantic output models for GET /api/customers/{id}/context."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Contact(Base):
    email: Optional[str] = None
    phone: Optional[str] = None


class QuoteSummary(Base):
    count: int = 0
    total_amount: float = 0.0
    status_counts: dict[str, int] = Field(default_factory=dict)
    first_at: Optional[str] = None
    last_at: Optional[str] = None


class OrderSummary(QuoteSummary):
    amount_paid: float = 0.0
    balance: float = 0.0
    unpaid_count: int = 0
    partially_paid_count: int = 0
    fully_paid_count: int = 0
    payment_status_counts: dict[str, int] = Field(default_factory=dict)


class PaymentSummary(Base):
    count: int = 0
    total_amount: float = 0.0
    first_at: Optional[str] = None
    last_at: Optional[str] = None


class LeadOutcome(Base):
    lead_ref: Optional[str] = None
    matched: Optional[bool] = None
    matched_by: Optional[str] = None          # "email" | "phone"
    ambiguous: bool = False
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None
    is_new_customer: Optional[bool] = None
    quotes_since_lead: dict = Field(default_factory=dict)
    orders_since_lead: dict = Field(default_factory=dict)
    payments_since_lead: dict = Field(default_factory=dict)


class CustomerContext(Base):
    customer_id: int
    company_name: str = ""
    contact: Contact = Field(default_factory=Contact)
    quotes: QuoteSummary = Field(default_factory=QuoteSummary)
    orders: OrderSummary = Field(default_factory=OrderSummary)
    payments: PaymentSummary = Field(default_factory=PaymentSummary)
    leads: list[LeadOutcome] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
