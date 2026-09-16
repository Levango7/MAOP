"""Data models, enums and shared constants for the MAOP Supervisor.

Extracted from ``supervisor.py`` to keep every file under 500 lines while
preserving full backward compatibility at the ``supervisor`` import surface.

This module is a *leaf* dependency: it must not import any sibling mixin
module (``supervisor_rules`` / ``supervisor_health`` / ``supervisor_patrol`` /
``supervisor_action`` / ``supervisor_dispatch`` / ``supervisor_status``) so
that no import cycle can arise.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ── Metric names (supervisor-specific, prefixed maop_supervisor_*) ──
M_SUPERVISOR_PATROL_DURATION = "maop_supervisor_patrol_duration_seconds"
M_SUPERVISOR_PATROL_AGENTS = "maop_supervisor_patrol_agents_checked"
M_SUPERVISOR_PATROL_ISSUES = "maop_supervisor_patrol_issues_found"
M_SUPERVISOR_ACTIONS_TOTAL = "maop_supervisor_actions_total"

# Maximum number of action records kept in memory (ring buffer).
_MAX_ACTION_HISTORY = 500
# Maximum number of pending alerts kept in memory.
_MAX_PENDING_ALERTS = 200
# Default patrol concurrency (parallel probes per patrol round).
_DEFAULT_PATROL_CONCURRENCY = 10
# Number of consecutive unreachable patrols before terminate triggers.
_UNREACHABLE_TERMINATE_THRESHOLD = 3
# Cooldown for evolution trigger (avoid feedback storm with EvolutionLoop).
_EVOLUTION_TRIGGER_COOLDOWN_S = 600.0
# Number of degraded agents that triggers an evolution suggestion.
_EVOLUTION_TRIGGER_DEGRADED_COUNT = 3


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
    evaluated by :class:`RuleEngine`. Keys are OR-semantics; wrap a
    list under ``"all"`` for AND-semantics.
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


# ── Internal per-agent supervisor state ───────────────────────


class _SupervisorAgentState:
    """Mutable per-agent supervisor state (guarded by supervisor lock)."""

    __slots__ = (
        "consecutive_unreachable",
        "disabled",
        "fallback_agent",
        "last_probe_at",
        "max_concurrency",
        "operational_status",
        "timeout_s",
        "upgrade_old_avg_latency",
        "upgrade_target_version",
    )

    def __init__(self) -> None:
        self.operational_status: AgentOperationalStatus = AgentOperationalStatus.NORMAL
        self.disabled: bool = False
        self.fallback_agent: str | None = None
        self.max_concurrency: int | None = None
        self.timeout_s: float | None = None
        self.consecutive_unreachable: int = 0
        self.last_probe_at: float = 0.0
        self.upgrade_target_version: str | None = None
        self.upgrade_old_avg_latency: float = 0.0


# ── Exceptions ────────────────────────────────────────────────


class TerminateRefusedError(Exception):
    """Raised when terminate is refused because the agent is the only available one."""

    def __init__(self, agent_id: str, routing_key: str, reason: str) -> None:
        self.agent_id = agent_id
        self.routing_key = routing_key
        self.reason = reason
        super().__init__(f"terminate refused for {agent_id}: {reason}")