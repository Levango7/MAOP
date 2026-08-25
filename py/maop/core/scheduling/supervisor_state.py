"""Internal per-agent supervisor state and supervisor-specific exceptions.

Extracted from :mod:`maop.core.scheduling.supervisor` (B4 大文件拆分第二轮
收敛) so the 1100-line supervisor module keeps only the :class:`Supervisor`
class. Names are re-exported from ``supervisor.py`` so existing
``from maop.core.scheduling.supervisor import TerminateRefusedError`` callers
keep working unchanged.
"""

from __future__ import annotations

from maop.core.scheduling.models import AgentOperationalStatus


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
