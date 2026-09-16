"""Status/query mixin for the Supervisor.

Provides status introspection (``get_supervisor_status`` / ``get_actions``)
plus the internal helpers used by sibling mixins (``_record_action``,
``_publish_supervisor_event``, operational-status accessors, unreachable
counting, evolution triggering). Accessed by sibling mixins via ``self.*``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from maop.core.scheduling.supervisor_models import (
    _EVOLUTION_TRIGGER_COOLDOWN_S,
    _EVOLUTION_TRIGGER_DEGRADED_COUNT,
    _SupervisorAgentState,
    ActionRecord,
    AgentOperationalStatus,
    HealthProbe,
    SupervisorAction,
)

logger = logging.getLogger(__name__)


class SupervisorStatusMixin:
    """Status introspection and shared internal helpers."""

    def get_supervisor_status(self) -> dict[str, Any]:
        """Return the full supervisor status (for /api/supervisor/status)."""
        with self._lock, self._supervisor_lock:
            agents = []
            for aid, state in sorted(self._agents.items()):
                health = self._agent_health_locked(aid, state)
                sv_state = self._supervisor_state.get(aid)
                agents.append({
                    **health.to_dict(),
                    "operational_status": (
                        sv_state.operational_status.value
                        if sv_state is not None
                        else health.operational_status
                    ),
                    "disabled": (
                        sv_state.disabled if sv_state is not None else False
                    ),
                    "last_probe_at": (
                        sv_state.last_probe_at if sv_state is not None else 0.0
                    ),
                })
        return {
            "agents": agents,
            "patrol": {
                "running": self.patrol_running,
                "last_patrol_at": self._last_patrol_at,
                "next_patrol_at": (
                    self._last_patrol_at + self._patrol_interval_s
                    if self._last_patrol_at > 0 else 0.0
                ),
                "patrol_interval_s": self._patrol_interval_s,
                "patrol_strategy": self._patrol_strategy,
                "last_duration_s": self._last_patrol_duration_s,
            },
            "pending_alerts": list(self._pending_alerts),
            "recent_actions": [a.model_dump() for a in list(self._actions)[-50:]],
            "config": {
                "window_size": self._window_size,
                "failure_rate_threshold": self._failure_rate_threshold,
                "timeout_threshold": self._timeout_threshold,
                "recovery_consecutive_successes": self._recovery_consecutive_successes,
                "patrol_timeout_s": self._patrol_timeout_s,
                "patrol_concurrency": self._patrol_concurrency,
            },
            "rules": [r.model_dump() for r in self._rule_engine.rules],
        }

    def get_actions(
        self,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> list[ActionRecord]:
        """Return action history (optionally filtered by agent)."""
        with self._supervisor_lock:
            actions = list(self._actions)
        if agent_id is not None:
            actions = [a for a in actions if a.agent_id == agent_id]
        return actions[-max(1, int(limit)):]

    def _record_action(
        self,
        action: SupervisorAction,
        agent_id: str,
        reason: str,
        *,
        params: dict[str, Any] | None = None,
        triggered_by: str = "patrol",
    ) -> ActionRecord:
        """Append an action to the audit history and bump the counter."""
        record = ActionRecord(
            action_id=uuid.uuid4().hex,
            action=action,
            agent_id=agent_id,
            reason=reason,
            params=params or {},
            triggered_by=triggered_by,
            created_at=time.time(),
        )
        with self._supervisor_lock:
            self._actions.append(record)
        try:
            self._actions_counter.inc(labels={"action": action.value})
        except Exception as exc:
            logger.debug("[supervisor] action counter inc failed: %s", exc)
        return record

    async def _publish_supervisor_event(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        level: str = "info",
    ) -> None:
        """Publish a supervisor event via EventBus.publish(Event).

        Supervisor methods are all async, so we can directly await the
        publish coroutine (unlike the passive detector's ``_publish_event``
        which is called from the synchronous ``record_result`` path and
        must use fire-and-forget). When no running loop exists (sync
        test context), fall back to ``publish_sync``.
        """
        if self._event_bus is None:
            return
        try:
            from maop.core.reliability.event_bus import Event

            full_payload = {**payload, "level": level, "source": "supervisor"}
            event = Event(topic=topic, data=full_payload, source="supervisor")
            try:
                asyncio.get_running_loop()
                await self._event_bus.publish(event)
            except RuntimeError:
                self._event_bus.publish_sync(event)
        except Exception:
            logger.debug("[supervisor] event publish failed")

    def _set_operational_status(
        self, agent_id: str, status: AgentOperationalStatus,
    ) -> None:
        """Update the supervisor-side operational status for an agent."""
        with self._supervisor_lock:
            sv_state = self._supervisor_state.setdefault(
                agent_id, _SupervisorAgentState(),
            )
            sv_state.operational_status = status

    def _get_operational_status(self, agent_id: str) -> AgentOperationalStatus:
        """Return the supervisor-side operational status (or NORMAL)."""
        with self._supervisor_lock:
            sv_state = self._supervisor_state.get(agent_id)
            if sv_state is None:
                return AgentOperationalStatus.NORMAL
            return sv_state.operational_status

    def _increment_unreachable(self, agent_id: str) -> int:
        """Increment and return the consecutive-unreachable patrol count."""
        with self._supervisor_lock:
            sv_state = self._supervisor_state.setdefault(
                agent_id, _SupervisorAgentState(),
            )
            sv_state.consecutive_unreachable += 1
            return sv_state.consecutive_unreachable

    def _reset_unreachable(self, agent_id: str) -> None:
        """Reset the consecutive-unreachable counter."""
        with self._supervisor_lock:
            sv_state = self._supervisor_state.get(agent_id)
            if sv_state is not None:
                sv_state.consecutive_unreachable = 0

    def _find_sole_routing_key(self, agent_id: str) -> str | None:
        """Return a routing_key for which ``agent_id`` is the only available agent.

        Returns None when no routing store is wired in or when the agent
        is not the sole available one for any key. This is a best-effort
        safety check — when the routing store API is unavailable we
        return None (do not block terminate).
        """
        if self._routing_store is None:
            return None
        try:
            # The routing store API for "list routing_keys for agent" is
            # not standardised in v1; we skip the check and rely on the
            # caller passing force=True when they are sure.
            return None
        except Exception:
            return None

    async def _maybe_trigger_evolution(
        self,
        patrol_result: list[HealthProbe],
    ) -> None:
        """Suggest an evolution cycle when many agents are degraded."""
        now = time.time()
        if now - self._last_evolution_trigger_at < _EVOLUTION_TRIGGER_COOLDOWN_S:
            return
        degraded_count = sum(
            1 for p in patrol_result if p.failure_rate > 0.2 or not p.reachable
        )
        if degraded_count >= _EVOLUTION_TRIGGER_DEGRADED_COUNT:
            self._last_evolution_trigger_at = now
            await self._publish_supervisor_event(
                "supervisor.evolution.suggested",
                {
                    "degraded_count": degraded_count,
                    "agents": [
                        p.agent_id for p in patrol_result
                        if p.failure_rate > 0.2 or not p.reachable
                    ],
                },
                level="info",
            )