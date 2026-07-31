"""MAOP SLA Monitor — SLA tier derivation, metrics recording, and preemption tracking.

Extracted from dispatcher.py (N2 refactor) to separate concerns:
  - Dispatcher: scheduling, circuit-breaker, driver execution
  - AgentResolver: agent config resolution
  - SLAMonitor: SLA policy + observability

All metric failures are non-blocking (debug-level log) so that SLA bookkeeping
can never prevent or corrupt a dispatch.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# Lazy imports for optional monitoring dependency
def _import_metrics():
    """Import monitoring counters; return None if unavailable."""
    try:
        from maop.core.monitoring import (
            MAOP_TASK_PRIORITY_DISTRIBUTION,
            MAOP_TASK_SLA_TIER_DISTRIBUTION,
            MAOP_TASK_SLA_VIOLATION_TOTAL,
            MAOP_TASK_DEADLINE_SECONDS,
            MAOP_TASK_PREEMPTION_TOTAL,
        )
        return {
            "priority_dist": MAOP_TASK_PRIORITY_DISTRIBUTION,
            "sla_tier_dist": MAOP_TASK_SLA_TIER_DISTRIBUTION,
            "sla_violation": MAOP_TASK_SLA_VIOLATION_TOTAL,
            "deadline_seconds": MAOP_TASK_DEADLINE_SECONDS,
            "preemption": MAOP_TASK_PREEMPTION_TOTAL,
        }
    except ImportError:
        return None


_metrics = None


def _get_metrics():
    global _metrics
    if _metrics is None:
        _metrics = _import_metrics()
    return _metrics


class SLAMonitor:
    """SLA tracking helper for dispatch operations.

    Usage::

        sla = SLAMonitor()
        tier = sla.tier_from_priority(1)       # "critical"
        sla.record_start(1, "critical")         # increment gauges
        sla.record_end(1, "critical", deadline_ms=...)  # decrement + violation check
        sla.record_preemption(1, [3, 4])      # soft-preemption counter
    """

    @staticmethod
    def tier_from_priority(priority: int) -> str:
        """Derive a default SLA tier from a priority level.

        Mapping:
          - priority 1       -> ``critical``
          - priority 2..3    -> ``standard``
          - priority 4..5     -> ``best_effort``
        """
        if priority <= 1:
            return "critical"
        if priority <= 3:
            return "standard"
        return "best_effort"

    def record_start(self, priority: int, sla_tier: str) -> None:
        """Record SLA metrics at task dispatch start.

        Increments in-flight gauges for priority and SLA tier.
        Failures are non-blocking.
        """
        m = _get_metrics()
        if m is None:
            return
        try:
            m["priority_dist"].inc(labels={"priority": str(priority)})
            m["sla_tier_dist"].inc(labels={"tier": sla_tier})
        except Exception as exc:
            logger.debug("[sla] start-metric record failed: %s", exc)

    def record_end(
        self,
        priority: int,
        sla_tier: str,
        *,
        deadline_ms: int | None = None,
    ) -> None:
        """Record SLA metrics at task dispatch completion.

        Decrements in-flight gauges and checks deadline violation.
        """
        m = _get_metrics()
        if m is None:
            return
        try:
            m["priority_dist"].dec(labels={"priority": str(priority)})
            m["sla_tier_dist"].dec(labels={"tier": sla_tier})
            if deadline_ms is not None:
                now_ms = int(time.time() * 1000)
                remaining_s = (deadline_ms - now_ms) / 1000.0
                if now_ms > deadline_ms:
                    m["sla_violation"].inc()
                    m["deadline_seconds"].observe(remaining_s)
                    logger.warning(
                        "SLA violation: deadline_ms=%d now_ms=%d remaining_s=%.3fs",
                        deadline_ms, now_ms, remaining_s,
                    )
        except Exception as exc:
            logger.debug("[sla] end-metric record failed: %s", exc)

    def record_preemption(
        self,
        incoming_priority: int,
        running_priorities: list[int],
    ) -> None:
        """Record a soft-preemption event (demand signal, no interruption)."""
        m = _get_metrics()
        if m is None:
            return
        try:
            if not running_priorities:
                return
            if incoming_priority < min(running_priorities):
                m["preemption"].inc()
        except Exception as exc:
            logger.debug("[sla] preemption metric record failed: %s", exc)
