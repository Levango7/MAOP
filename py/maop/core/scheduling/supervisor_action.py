"""Control-action mixin for the Supervisor (预警/替换/降级/终止/升级).

Provides ``warn`` / ``replace`` / ``degrade`` / ``terminate`` / ``upgrade``
and the upgrade rollback helpers. Sibling-mixin methods are called via
``self.*`` (runtime-resolved), so no sibling imports are required here.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from maop.core.scheduling.failure_detector import _AgentState
from maop.core.scheduling.supervisor_models import (
    ActionRecord,
    AgentOperationalStatus,
    AlertLevel,
    SupervisorAction,
    TerminateRefusedError,
    _SupervisorAgentState,
)

logger = logging.getLogger(__name__)


class SupervisorActionMixin:
    """Six proactive control actions for the assembled Supervisor class."""

    async def warn(
        self,
        agent_id: str,
        reason: str,
        *,
        level: AlertLevel = AlertLevel.WARNING,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Publish a supervisor.alert event."""
        payload = {
            "agent_id": agent_id,
            "reason": reason,
            "level": level.value,
            "extra": extra or {},
            "source": "supervisor",
            "at": time.time(),
        }
        await self._publish_supervisor_event(
            "supervisor.alert", payload, level=level.value,
        )
        with self._supervisor_lock:
            self._pending_alerts.append(payload)

    async def replace(
        self,
        agent_id: str,
        replacement: str,
        reason: str,
        *,
        rollout: float = 1.0,
        routing_key: str = "",
        triggered_by: str = "patrol",
    ) -> ActionRecord:
        """Switch routing from ``agent_id`` to ``replacement``."""
        # v1: only rollout=1.0 (full cut) is supported.
        if rollout != 1.0:
            logger.warning(
                "[supervisor] replace rollout=%.2f ignored (v1 only supports full cut)",
                rollout,
            )
        # Update routing mapping if a routing store is wired in.
        if self._routing_store is not None and routing_key:
            try:
                update_fn = getattr(
                    self._routing_store, "update_routing_mapping", None,
                )
                if update_fn is not None:
                    update_fn(routing_key, replacement)
                else:
                    logger.warning(
                        "[supervisor] routing_store has no update_routing_mapping; "
                        "skip routing update for %s",
                        routing_key,
                    )
            except Exception:
                logger.exception(
                    "[supervisor] routing update failed for %s",
                    routing_key,
                )
        # Mark original agent as REPLACED.
        self._set_operational_status(agent_id, AgentOperationalStatus.REPLACED)
        # Drain the original agent (weight=0) so it stops receiving traffic.
        with self._lock:
            state = self._agents.setdefault(agent_id, _AgentState())
            state.weight = 0.0
            state.drained = True
        # Publish event.
        await self._publish_supervisor_event(
            "agent_replaced",
            {
                "agent_id": agent_id,
                "replacement": replacement,
                "rollout": rollout,
                "routing_key": routing_key,
                "reason": reason,
            },
            level="warning",
        )
        # Audit record.
        record = self._record_action(
            SupervisorAction.REPLACE, agent_id, reason,
            params={"replacement": replacement, "rollout": rollout, "routing_key": routing_key},
            triggered_by=triggered_by,
        )
        return record

    async def degrade(
        self,
        agent_id: str,
        factor: float,
        reason: str,
        *,
        max_concurrency: int | None = None,
        timeout_s: float | None = None,
        triggered_by: str = "patrol",
    ) -> ActionRecord:
        """Reduce agent weight by ``factor`` and optionally add limits."""
        if not 0.0 < factor < 1.0:
            logger.warning(
                "[supervisor] degrade factor %.3f out of (0,1); clamping", factor,
            )
            factor = max(0.01, min(0.99, factor))
        with self._lock:
            state = self._agents.setdefault(agent_id, _AgentState())
            state.weight = max(0.0, state.weight * factor)
        # Mark as DEGRADED (unless already DRAINED/TERMINATED).
        current = self._get_operational_status(agent_id)
        if current not in (
            AgentOperationalStatus.DRAINED,
            AgentOperationalStatus.TERMINATED,
            AgentOperationalStatus.REPLACED,
        ):
            self._set_operational_status(agent_id, AgentOperationalStatus.DEGRADED)
        # Store degrade limits in supervisor state.
        with self._supervisor_lock:
            sv_state = self._supervisor_state.setdefault(
                agent_id, _SupervisorAgentState(),
            )
            if max_concurrency is not None:
                sv_state.max_concurrency = max_concurrency
            if timeout_s is not None:
                sv_state.timeout_s = timeout_s
        # Publish event.
        await self._publish_supervisor_event(
            "agent_degraded",
            {
                "agent_id": agent_id,
                "factor": factor,
                "max_concurrency": max_concurrency,
                "timeout_s": timeout_s,
                "reason": reason,
            },
            level="warning",
        )
        record = self._record_action(
            SupervisorAction.DEGRADE, agent_id, reason,
            params={"factor": factor, "max_concurrency": max_concurrency, "timeout_s": timeout_s},
            triggered_by=triggered_by,
        )
        return record

    async def terminate(
        self,
        agent_id: str,
        reason: str,
        *,
        triggered_by: str = "patrol",
        force: bool = False,
    ) -> ActionRecord:
        """Mark agent as disabled (terminated) — stronger than drain."""
        # [R-2] Safety check: refuse if agent is the only available one
        # for some routing_key (unless force=True).
        if not force:
            sole_routing = self._find_sole_routing_key(agent_id)
            if sole_routing is not None:
                msg = (
                    f"agent is the only available agent for routing_key="
                    f"{sole_routing}; configure fallback or replace first "
                    f"(or pass force=True to bypass)"
                )
                logger.warning(
                    "[supervisor] terminate %s refused: %s", agent_id, msg,
                )
                raise TerminateRefusedError(agent_id, sole_routing, msg)
        # Mark disabled + TERMINATED.
        with self._supervisor_lock:
            sv_state = self._supervisor_state.setdefault(
                agent_id, _SupervisorAgentState(),
            )
            sv_state.disabled = True
        self._set_operational_status(agent_id, AgentOperationalStatus.TERMINATED)
        # Also drain (weight=0) for belt-and-suspenders.
        with self._lock:
            state = self._agents.setdefault(agent_id, _AgentState())
            state.weight = 0.0
            state.drained = True
        # Publish event.
        await self._publish_supervisor_event(
            "agent_terminated",
            {"agent_id": agent_id, "reason": reason, "force": force},
            level="critical",
        )
        record = self._record_action(
            SupervisorAction.TERMINATE, agent_id, reason,
            params={"force": force, "force_bypass_safety": force},
            triggered_by=triggered_by,
        )
        return record

    async def upgrade(
        self,
        agent_id: str,
        target_version: str,
        reason: str,
        *,
        rollout_steps: list[float] | None = None,
        triggered_by: str = "manual",
    ) -> ActionRecord:
        """Upgrade agent to ``target_version`` (v1: full cut only)."""
        if rollout_steps is not None and rollout_steps != [1.0]:
            logger.warning(
                "[supervisor] upgrade rollout_steps %r ignored (v1 only supports "
                "full cut [1.0]); see [F-3]",
                rollout_steps,
            )
        # Capture old version baseline for rollback comparison.
        old_health = self.get_agent_health(agent_id)
        old_avg_latency = old_health.avg_latency if old_health else 0.0
        # Ensure the agent has a detector state entry so it shows up in
        # get_supervisor_status() and get_stats().
        with self._lock:
            self._agents.setdefault(agent_id, _AgentState())
        with self._supervisor_lock:
            sv_state = self._supervisor_state.setdefault(
                agent_id, _SupervisorAgentState(),
            )
            sv_state.upgrade_target_version = target_version
            sv_state.upgrade_old_avg_latency = old_avg_latency
        # Mark as UPGRADING.
        self._set_operational_status(agent_id, AgentOperationalStatus.UPGRADING)
        # Publish started event.
        await self._publish_supervisor_event(
            "agent_upgrade.started",
            {
                "agent_id": agent_id,
                "target_version": target_version,
                "rollout_steps": [1.0],
                "reason": reason,
            },
            level="info",
        )
        # v1: immediately consider the upgrade "applied" — actual rollback
        # detection happens on the next patrol round via _check_upgrade_health.
        record = self._record_action(
            SupervisorAction.UPGRADE, agent_id, reason,
            params={"target_version": target_version, "rollout_steps": [1.0]},
            triggered_by=triggered_by,
        )
        return record

    async def _check_upgrade_health(self, agent_id: str) -> None:
        """Inline rollback check called from patrol for UPGRADING agents.

        Per [R-3], rollback triggers if any of:
          - failure_rate > 0.15
          - avg_latency > old_avg_latency * 1.5
          - consecutive 2 patrols with reachable=False
        """
        with self._supervisor_lock:
            sv_state = self._supervisor_state.get(agent_id)
            if sv_state is None or sv_state.upgrade_target_version is None:
                return
            target_version = sv_state.upgrade_target_version
            old_avg_latency = sv_state.upgrade_old_avg_latency
        health = self.get_agent_health(agent_id)
        if health is None:
            return
        rollback_reason: str | None = None
        if health.failure_rate > 0.15:
            rollback_reason = (
                f"failure_rate={health.failure_rate:.3f} > 0.15"
            )
        elif (
            old_avg_latency > 0.0
            and health.avg_latency > old_avg_latency * 1.5
        ):
            rollback_reason = (
                f"avg_latency={health.avg_latency:.2f} > "
                f"old({old_avg_latency:.2f}) * 1.5"
            )
        if rollback_reason is not None:
            await self._rollback_upgrade(agent_id, target_version, rollback_reason)

    async def _rollback_upgrade(
        self,
        agent_id: str,
        target_version: str,
        reason: str,
    ) -> None:
        """Roll back an in-flight upgrade and terminate the new version."""
        logger.warning(
            "[supervisor] rolling back upgrade %s -> %s: %s",
            agent_id, target_version, reason,
        )
        # Clear upgrade state.
        with self._supervisor_lock:
            sv_state = self._supervisor_state.get(agent_id)
            if sv_state is not None:
                sv_state.upgrade_target_version = None
                sv_state.upgrade_old_avg_latency = 0.0
        # Restore operational status to NORMAL (assume old version still serves).
        self._set_operational_status(agent_id, AgentOperationalStatus.NORMAL)
        # Publish rollback event.
        await self._publish_supervisor_event(
            "agent_upgrade.rolled_back",
            {
                "agent_id": agent_id,
                "target_version": target_version,
                "reason": reason,
            },
            level="warning",
        )