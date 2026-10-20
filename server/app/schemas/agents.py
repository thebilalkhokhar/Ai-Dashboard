"""Structured output models for the three agents.

The LLM may only use numbers that appear in the input JSON; every money field
below is COPIED from the input by the agent prompt and verified again by the
pipeline's own validation (app/agents/graph.py).
"""

from __future__ import annotations

from typing import List, Literal

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
    approach: Literal["soft_reminder", "firm_nudge", "hold_off", "escalate_to_human"]
    reason: str
    message_draft: str = Field(max_length=600, description="max 80 words")


class Priority(BaseModel):
    customer_name: str
    why: str
    suggested_action: str


class MasterOutput(BaseModel):
    bullets: List[str] = Field(min_length=3, max_length=3)
    priorities: List[Priority] = Field(default_factory=list, max_length=5)
    conflicts_resolved: List[str] = Field(default_factory=list)
