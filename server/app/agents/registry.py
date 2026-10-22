"""Registry of the agents that actually exist in this codebase.

This is not a place to invent capabilities: every entry maps to real, wired-up
code so the command center can only ever show an agent the backend can run.

  * ``master``        - the summary stage in app/agents/graph.py (run_master).
  * ``negotiation``   - stale-quote follow-up specialist (graph.py + negotiation.py).
  * ``collections``   - unpaid/overdue invoice specialist (graph.py + collections.py).
  * ``lead_followup`` - stuck-lead specialist (graph.py + lead_followup.py).
  * ``chat``          - ONE tool-calling read-only assistant (chat.py). Its
                        ``tools`` are exactly the tools chat.py can invoke
                        (chat_tools.CHAT_TOOLS); there are no chat "sub-agents".

Status is *derived from persisted events*, never asserted: an agent that has
never emitted an ``agent.*`` event is ``idle`` (unknown), never ``running``.
"""

from __future__ import annotations

from typing import Any, Optional

from app.agents.chat_tools import CHAT_TOOLS
from app.services.events import EventService, get_event_service

# Stable ids that agent events' agent_id must use.
AGENT_IDS = ("master", "negotiation", "collections", "lead_followup", "chat")

_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {
        "id": "master",
        "name": "Master",
        "kind": "orchestrator",
        "purpose": "Groups sub-agent results by customer and writes the executive brief.",
        "tools": [],
        "enabled": True,
    },
    {
        "id": "negotiation",
        "name": "Negotiation",
        "kind": "specialist",
        "purpose": "Drafts follow-ups for stale open quotes, guarded by money and contact checks.",
        "tools": [],
        "enabled": True,
    },
    {
        "id": "collections",
        "name": "Collections",
        "kind": "specialist",
        "purpose": "Drafts customer-facing payment reminders and internal escalation notes for unpaid invoices.",
        "tools": [],
        "enabled": True,
    },
    {
        "id": "lead_followup",
        "name": "Lead Follow-up",
        "kind": "specialist",
        "purpose": "Summarises stuck leads and the next action for a human rep, from Python-parsed fields.",
        "tools": [],
        "enabled": True,
    },
    {
        "id": "chat",
        "name": "Chat",
        "kind": "assistant",
        "purpose": "One read-only, tool-calling assistant that answers from Mongo with Python-computed figures.",
        "tools": [tool.name for tool in CHAT_TOOLS],
        "enabled": True,
    },
)


def list_capabilities() -> list[dict[str, Any]]:
    """The agents that exist, independent of any run state."""
    return [dict(entry) for entry in _CAPABILITIES]


def is_known_agent(agent_id: Optional[str]) -> bool:
    return agent_id in AGENT_IDS


async def agents_with_status(service: Optional[EventService] = None) -> list[dict[str, Any]]:
    """Registry entries plus a status derived from real persisted events.

    Unknown / never-run agents show as ``idle`` with ``status_source="none"`` so
    the UI can tell "no evidence yet" apart from "the backend said idle".
    """
    service = service or get_event_service()
    try:
        status_by_agent = await service.last_status_by_agent()
    except Exception:  # a broken read must not take /api/agents down
        status_by_agent = {}

    agents: list[dict[str, Any]] = []
    for entry in list_capabilities():
        status = status_by_agent.get(entry["id"])
        agents.append(
            {
                **entry,
                "status": status or "idle",
                "status_source": "events" if status else "none",
            }
        )
    return agents
