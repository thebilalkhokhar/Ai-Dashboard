"""Structured output models for the agents.

The LLM may only use numbers that appear in the input JSON; every money field
below is COPIED from the input by the agent prompt and verified again by the
pipeline's own validation (app/agents/graph.py).
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Confidence = Literal["low", "medium", "high"]


class AgentOutputBase(BaseModel):
    """Fields shared by both sub-agents (validation errors become
    'needs_review' items, never crashes)."""

    customer_id: int
    customer_name: str
    confidence: Confidence
    evidence: str = Field(description="Which input facts the judgment used, one sentence")


class NegotiationOutput(AgentOutputBase):
    quote_id: int
    quote_amount: float  # must equal the input quote amount (pipeline checks)
    likely_reason: Literal[
        "price",
        "no_response",
        "new_customer_no_history",
        "repeat_customer_slow",
        "unclear",
    ]
    tactic: Literal["follow_up", "adjust_price", "bundle_offer", "let_go"]
    message_draft: str = Field(max_length=600, description="max 80 words")


class CollectionsOutput(AgentOutputBase):
    invoice_id: int
    balance: float  # must equal the input balance (pipeline checks)
    approach: Literal["soft_reminder", "firm_nudge", "hold_off"]
    # the tone CODE chose from days_overdue - the model must copy it
    tone: Literal["gentle", "standard", "firm"]
    reason: str
    message_draft: str = Field(
        max_length=600,
        description="max 80 words, customer-facing payment reminder (always required)",
    )
    escalation_note: Optional[str] = Field(
        default=None,
        description="Optional internal note for a manager; never replaces message_draft",
    )


LeadMissing = Literal["artwork", "quantity", "size", "material", "finish", "contact_info"]


class LeadFollowupOutput(BaseModel):
    """Sub-agent 3 output for one stuck lead (AgentOutputBase-like: a lead is
    not a customer yet, so there is no customer_id).

    `missing` is computed by Python from the parsed description - the model
    must copy it, never extend it. `lead_id` is filled in by the pipeline from
    the input, never trusted from the model.
    """

    lead_id: str = ""
    contact_name: Optional[str] = None
    company: Optional[str] = None
    product: Optional[str] = None
    missing: List[LeadMissing] = Field(
        default_factory=list,
        description="Copied from the input missing_fields, computed by Python",
    )
    blocker: str = Field(description="What is stopping this lead from becoming an order")
    owner_team: Literal["design", "sales", "unclear"]
    action_plan: str = Field(description="What a human rep should do next")
    message_draft: str = Field(max_length=600, description="max 80 words, for a human rep to send")
    confidence: Confidence
    evidence: str = Field(description="Which input facts the judgment used, one sentence")


class CrmEngagementOutput(BaseModel):
    """CRM Engagement agent output for one record whose last interaction
    came FROM the customer and has waited unanswered (direction computed
    by Python from the CRM timeline, never by the model).

    `record_id`, `contact_name`, `company` and `waiting_days` are filled
    in by the pipeline from the input - a CRM record is not an ERP
    customer, so there is no customer_id."""

    record_id: str = ""
    contact_name: Optional[str] = None
    company: Optional[str] = None
    waiting_days: int = 0
    situation: str = Field(description="What the customer is waiting on, input facts only")
    action_plan: str = Field(description="What a human rep should do next")
    message_draft: str = Field(max_length=600, description="max 80 words, for a human rep to send")
    confidence: Confidence
    evidence: str = Field(description="Which input facts the judgment used, one sentence")


class CrmDealWatchOutput(BaseModel):
    """CRM Deal Watch agent output for one stalled CRM project or
    estimate (idle days and amount computed by Python from the synced
    rows; the pipeline fills the identity fields)."""

    record_id: str = ""
    account_name: Optional[str] = None
    deal_name: Optional[str] = None
    stall_reason: str = Field(description="Why this deal is most likely stalled, input facts only")
    action_plan: str = Field(description="What a human rep should do next")
    message_draft: str = Field(max_length=600, description="max 80 words, for a human rep to send")
    confidence: Confidence
    evidence: str = Field(description="Which input facts the judgment used, one sentence")


class Priority(BaseModel):
    customer_name: str
    why: str
    suggested_action: str


class MasterOutput(BaseModel):
    bullets: List[str] = Field(min_length=3, max_length=3)
    priorities: List[Priority] = Field(default_factory=list, max_length=5)
    conflicts_resolved: List[str] = Field(default_factory=list)
