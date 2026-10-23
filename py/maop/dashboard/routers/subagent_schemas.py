"""Response models for ``/api/subagent/*`` — the 5 endpoints the dashboard calls.

The producers live behind ``maop.dashboard.services.agent_service``:
``SubAgentManager`` (``maop/core/agent/delegation/subagent_lifecycle.py``) is
the only thing that knows what a subagent row and a transcript entry look
like, so its models are reused verbatim below instead of being re-typed here.

Note on the ``status`` defaults: every success path writes ``"status": "ok"``,
while the ``@handle_api_errors(error_value=...)`` fallbacks omit ``status``
(``/api/subagent/list`` returns ``{"agents": [], "count": 0, "error": "..."}``).
Those fallback dicts are returned as plain dicts, so they ARE validated by
``response_model`` — an ``"ok"`` default would label an error response as a
success. Defaulting to ``"error"`` keeps the wire truthful on both paths.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from maop.core.agent.delegation.subagent_lifecycle import AgentResult, TranscriptEntry


class SubAgentListItem(BaseModel):
    """One subagent row.

    ``SubAgentManager.list_agents()`` returns raw DB columns (``id``,
    ``name``); the router renames them to the words the rest of this API
    already uses for the same entity (``spawn`` and ``cancel`` both answer
    with ``agent_id``, and the spawn request body calls the name ``agent``).
    """

    agent_id: str = ""
    agent: str = ""
    role: str = ""
    model: str = ""
    task: str = ""
    status: str = ""
    created_at: str = ""


class SubAgentListEnvelope(BaseModel):
    status: str = "error"
    agents: list[SubAgentListItem] = Field(default_factory=list)
    count: int = 0
    error: str = ""


class SubAgentIdEnvelope(BaseModel):
    """``spawn`` and ``cancel`` both answer with the subagent id."""

    status: str = "error"
    agent_id: str = ""
    error: str = ""


class SubAgentWaitEnvelope(BaseModel):
    """``wait`` carries the finished agent's result.

    The producer dumps the ``AgentResult`` when it has one and falls back to
    ``str(result)`` otherwise, so both shapes are declared.
    """

    status: str = "error"
    result: AgentResult | str = ""
    error: str = ""


class SubAgentTranscriptEnvelope(BaseModel):
    """``transcript`` carries the live transcript entries."""

    status: str = "error"
    transcript: list[TranscriptEntry] = Field(default_factory=list)
    error: str = ""
