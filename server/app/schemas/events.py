"""Versioned task-event schema.

Every event the JARVIS command center shows (agent state, execution timeline,
network activity) is one of these. They are emitted only from real backend
activity - never from mock or decorative state - and are persisted to Mongo's
``task_events`` collection and relayed over the existing Redis update channel.

Guarantees encoded here:

  * a schema version (``v``) so future consumers can tell old from new events;
  * a per-task monotonically increasing ``seq`` (allocated atomically by the
    event service, never in-process);
  * a UTC timestamp that is normalised on construction;
  * a closed set of ``type`` values - no free-form event names;
  * a bounded ``payload``: no credentials, no refresh tokens, no API keys, no
    full customer records, and never an unreasonably large blob.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The event contract version. Bump only with a documented, backward-compatible
# change; consumers read `v` before trusting any optional payload field.
SCHEMA_VERSION = 1

# The full, closed set of event types. Task/agent/tool/erp lifecycle events,
# plus the chat data-lifecycle events added when the command center learned
# to draw fetching/filtering/aggregation/validation as real stages: each of
# those is emitted at the actual boundary in the chat loop (a paged tool's
# match counts, a Python-computed aggregate, the numeric guard's verdict,
# the start of an answer generation). There is no free-form type.
EventType = Literal[
    "task.created",
    "task.started",
    "task.completed",
    "task.failed",
    "task.cancelled",
    "agent.queued",
    "agent.started",
    "agent.progress",
    "agent.completed",
    "agent.failed",
    "tool.started",
    "tool.completed",
    "erp.sync_started",
    "erp.sync_completed",
    "erp.sync_failed",
    "data.filter_completed",
    "data.aggregation_completed",
    "chat.validation_started",
    "chat.validation_completed",
    "chat.response_started",
]

EVENT_TYPES: frozenset[str] = frozenset(
    (
        "task.created",
        "task.started",
        "task.completed",
        "task.failed",
        "task.cancelled",
        "agent.queued",
        "agent.started",
        "agent.progress",
        "agent.completed",
        "agent.failed",
        "tool.started",
        "tool.completed",
        "erp.sync_started",
        "erp.sync_completed",
        "erp.sync_failed",
        "data.filter_completed",
        "data.aggregation_completed",
        "chat.validation_started",
        "chat.validation_completed",
        "chat.response_started",
    )
)

# Event types that report on one agent (registry status is derived from these).
AGENT_EVENT_TYPES: frozenset[str] = frozenset(
    (
        "agent.queued",
        "agent.started",
        "agent.progress",
        "agent.completed",
        "agent.failed",
    )
)

# A payload may not carry a secret-bearing key, and the whole serialised payload
# is capped, so a credential or bulk PII can never ride in on an event that is
# broadcast to every dashboard client.
#
# The check is a substring test, which is deliberately strict - but that strictness
# also collides with legitimate usage counters: `total_tokens` contains "token"
# without being one. Because every payload key here is emitted by our own
# instrumentation, the safe resolution is a narrow allow-list of known
# non-secret key names rather than loosening the rule for everything.
_SENSITIVE_KEY_MARKERS = (
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "api_key",
    "apikey",
    "authorization",
    "refresh",
)
# Token *counts* (billing telemetry), never token *values*. Anything else whose
# name contains a sensitive marker is still rejected.
ALLOWED_SENSITIVE_SUBSTRING_KEYS = frozenset(
    {
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
    }
)
_PAYLOAD_MAX_CHARS = 1500
_MAX_STRING_LEN = 300
_MAX_SEQUENCE_ITEMS = 50
_MAX_DICT_KEYS = 40
_MAX_DEPTH = 4


def is_sensitive_key(key: Any) -> bool:
    """True when a payload key names a credential rather than a safe counter."""
    lowered = str(key).casefold().strip()
    if lowered in ALLOWED_SENSITIVE_SUBSTRING_KEYS:
        return False
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _reject_sensitive(node: Any, depth: int = 0) -> None:
    """Raise if a payload nests too deep, is too big, or hides a secret key."""
    if depth > _MAX_DEPTH:
        raise ValueError("payload is nested too deeply")
    if isinstance(node, dict):
        if len(node) > _MAX_DICT_KEYS:
            raise ValueError("payload has too many keys")
        for key, value in node.items():
            if is_sensitive_key(key):
                raise ValueError(f"payload key {key!r} may not appear in an event")
            _reject_sensitive(value, depth + 1)
    elif isinstance(node, (list, tuple)):
        if len(node) > _MAX_SEQUENCE_ITEMS:
            raise ValueError("payload list is too long")
        for item in node:
            _reject_sensitive(item, depth + 1)
    elif isinstance(node, str) and len(node) > _MAX_STRING_LEN:
        raise ValueError("payload string field is too long")


class TaskEvent(BaseModel):
    """One versioned, bounded event in a task's execution timeline."""

    model_config = ConfigDict(str_strip_whitespace=True)

    v: int = SCHEMA_VERSION
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    task_id: str = Field(min_length=1, max_length=128)
    seq: int = Field(ge=1, description="monotonic within the task, from the event service")
    ts: datetime
    type: EventType
    agent_id: Optional[str] = Field(default=None, max_length=64)
    payload: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("v")
    @classmethod
    def _version_is_known(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError(f"unsupported event schema version {value}")
        return value

    @field_validator("event_id")
    @classmethod
    def _event_id_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("event_id must not be blank")
        return value

    @field_validator("ts")
    @classmethod
    def _ts_is_utc(cls, value: datetime) -> datetime:
        # Normalise to aware UTC so replays sort and render identically for
        # every client, whatever clock emitted the event.
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_validator("type")
    @classmethod
    def _type_is_known(cls, value: str) -> str:
        if value not in EVENT_TYPES:
            raise ValueError(f"unknown event type {value!r}")
        return value

    @field_validator("payload")
    @classmethod
    def _payload_is_bounded(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        _reject_sensitive(value)
        try:
            size = len(json.dumps(value, default=str))
        except (TypeError, ValueError) as exc:  # non-serialisable is not allowed
            raise ValueError("payload must be JSON-serialisable") from exc
        if size > _PAYLOAD_MAX_CHARS:
            raise ValueError("payload is too large to broadcast")
        return value

    def to_document(self) -> Dict[str, Any]:
        """The Mongo document shape (ts stays a real BSON datetime)."""
        doc = self.model_dump()
        doc["ts"] = self.ts  # keep aware UTC datetime, not its string form
        return doc

    @classmethod
    def from_document(cls, doc: Dict[str, Any]) -> "TaskEvent":
        """Rebuild an event from a persisted Mongo document (ignores _id)."""
        data = {key: value for key, value in doc.items() if key != "_id"}
        return cls.model_validate(data)
