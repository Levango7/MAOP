"""MAOP Supervisor — data models (enums + Pydantic schemas).

Extracted from ``supervisor.py`` to keep the main module focused on the
:class:`~maop.core.scheduling.supervisor.Supervisor` orchestration logic.
These models are intentionally framework-agnostic (Pydantic only) so they
can be reused by the rule engine, health checker, dashboard API, and
tests without pulling in asyncio / threading dependencies.

References
----------
- docs/design-supervisor-agent.md (full design)
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ── Enums ──────────────────────────────────────────────────────


class SupervisorAction(str, Enum):
    """Actions the Supervisor can execute."""

    PATROL = "patrol"
    ALERT = "alert"
    REPLACE = "replace"
    DEGRADE = "degrade"
    TERMINATE = "terminate"
    UPGRADE = "upgrade"
    NONE = "none"


class AlertLevel(str, Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AgentOperationalStatus(str, Enum):
    """Extended agent operational status (superset of passive detector states)."""

    NORMAL = "normal"
    DEGRADED = "degraded"
    DRAINED = "drained"
    RECOVERING = "recovering"
    REPLACED = "replaced"
    TERMINATED = "terminated"
    UPGRADING = "upgrading"


# ── Data models (Pydantic) ─────────────────────────────────────


class HealthProbe(BaseModel):
    """Single health probe result for one agent."""

    agent_id: str
    reachable: bool
    latency_ms: float = 0.0
    failure_rate: float = 0.0
    avg_latency: float = 0.0
    timeout_rate: float = 0.0
    breaker_open: bool = False
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    probed_at: float = Field(default_factory=time.time)


class SupervisorRule(BaseModel):
    """Supervision rule: threshold condition → trigger action.

    The ``condition`` dict is a declarative threshold description
    evaluated by :class:`~maop.core.scheduling.rule_engine.RuleEngine`.
    Keys are OR-semantics; wrap a list under ``"all"`` for AND-semantics.
    """

    rule_id: str
    name: str
    description: str = ""
    action: SupervisorAction
    alert_level: AlertLevel = AlertLevel.WARNING
    condition: dict[str, Any]
    action_params: dict[str, Any] = Field(default_factory=dict)
    cooldown_s: float = 60.0
    priority: int = 0
    enabled: bool = True


class DispatchDecision(BaseModel):
    """Pre-dispatch check result returned to the Engine."""

    allow: bool
    reason: str = ""
    fallback_agent: str | None = None
    degraded: bool = False


class ActionRecord(BaseModel):
    """Audit record for a control action."""

    action_id: str
    action: SupervisorAction
    agent_id: str
    reason: str
    params: dict[str, Any] = Field(default_factory=dict)
    triggered_by: str = "patrol"
    created_at: float
    reverted_at: float | None = None


class SupervisorActionRequest(BaseModel):
    """Manual control action request body (dashboard API)."""

    agent_id: str
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str


__all__ = [
    "ActionRecord",
    "AgentOperationalStatus",
    "AlertLevel",
    "DispatchDecision",
    "HealthProbe",
    "SupervisorAction",
    "SupervisorActionRequest",
    "SupervisorRule",
]