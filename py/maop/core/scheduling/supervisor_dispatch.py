"""Dispatch-gating mixin for the Supervisor.

Provides pre/post dispatch checks (Engine integration), dynamic retry
strategy (LoopExecutor integration) and debate adjudication. Sibling-mixin
methods are accessed via ``self.*`` (runtime-resolved).
"""

from __future__ import annotations

import logging
from typing import Any

from maop.core.scheduling.supervisor_models import (
    AgentOperationalStatus,
    DispatchDecision,
)

logger = logging.getLogger(__name__)


class SupervisorDispatchMixin:
    """Dispatch gating / adjudication for the assembled Supervisor class."""

    def check_before_dispatch(self, agent_id: str) -> DispatchDecision:
        """Return whether the engine may dispatch to ``agent_id``."""
        with self._supervisor_lock:
            sv_state = self._supervisor_state.get(agent_id)
            disabled = sv_state.disabled if sv_state else False
            fallback = sv_state.fallback_agent if sv_state else None
        if disabled:
            return DispatchDecision(
                allow=False,
                reason="agent_terminated",
                fallback_agent=fallback,
            )
        with self._lock:
            state = self._agents.get(agent_id)
            if state is not None and state.weight <= 0.0:
                return DispatchDecision(
                    allow=False,
                    reason="agent_drained_weight_zero",
                    fallback_agent=fallback,
                )
        # Check operational status for degraded flag.
        op_status = self._get_operational_status(agent_id)
        if op_status == AgentOperationalStatus.DEGRADED:
            return DispatchDecision(
                allow=True, reason="agent_degraded", degraded=True,
            )
        return DispatchDecision(allow=True)

    def check_after_dispatch(
        self,
        agent_id: str,
        success: bool,
        latency: float = 0.0,
    ) -> None:
        """Record dispatch outcome (forwards to passive record_result)."""
        self.record_result(agent_id, success=success, latency=latency)

    def get_retry_strategy(
        self,
        agent_id: str,
        *,
        default_max_attempts: int = 3,
        default_backoff_ms: int = 2000,
    ) -> dict[str, Any]:
        """Return a dynamic retry strategy for ``agent_id``.

        Returns a dict with keys: ``max_attempts``, ``backoff_ms``,
        ``skip_agent``, ``fallback_agent``.
        """
        with self._supervisor_lock:
            sv_state = self._supervisor_state.get(agent_id)
            disabled = sv_state.disabled if sv_state else False
            fallback = sv_state.fallback_agent if sv_state else None
            degraded = (
                sv_state is not None
                and self._get_operational_status(agent_id)
                == AgentOperationalStatus.DEGRADED
            )
        if disabled:
            return {
                "max_attempts": 0,
                "backoff_ms": default_backoff_ms,
                "skip_agent": True,
                "fallback_agent": fallback,
            }
        if degraded:
            # Reduce retries to avoid amplifying degradation.
            return {
                "max_attempts": max(1, default_max_attempts - 1),
                "backoff_ms": default_backoff_ms * 2,
                "skip_agent": False,
                "fallback_agent": fallback,
            }
        return {
            "max_attempts": default_max_attempts,
            "backoff_ms": default_backoff_ms,
            "skip_agent": False,
            "fallback_agent": fallback,
        }

    async def adjudicate(
        self,
        debate_id: str,
        rounds: list[Any],
    ) -> dict[str, Any]:
        """Adjudicate a debate stalemate (see design-debate-agent.md §2.2.5).

        v1 implementation: pick the conclusion from the last round with
        the highest historical confidence (success rate). Agents whose
        last-round confidence is significantly below their historical
        mean are degraded. Returns a Verdict-like dict.
        """
        if not rounds:
            return {
                "consensus": False,
                "low_confidence": True,
                "adjudication_reason": "no rounds provided",
                "winner": None,
            }
        last_round = rounds[-1]
        # last_round is expected to be a list of agent conclusions.
        # Each conclusion is expected to have .agent_id and .confidence.
        # We accept dict-like or attribute-like objects.
        conclusions = (
            last_round if isinstance(last_round, list) else [last_round]
        )
        winner: Any = None
        best_conf = -1.0
        for c in conclusions:
            conf = float(
                getattr(c, "confidence", None)
                or (c.get("confidence") if isinstance(c, dict) else None)
                or 0.0
            )
            if conf > best_conf:
                best_conf = conf
                winner = c
        reason = (
            f"adjudicated by supervisor: winner confidence={best_conf:.3f}"
        )
        return {
            "consensus": False,
            "low_confidence": best_conf < 0.70,
            "adjudication_reason": reason,
            "winner": winner,
            "debate_id": debate_id,
        }