"""MAOP Failure Pattern Detector — sliding-window adaptive scheduling weights.

F1-02 (异常自适应调度): tracks per-agent task outcomes in a sliding window
and dynamically adjusts scheduling weights so that failing agents are
*drained* (weight → 0) and gradually *recovered* (0.3 → 0.6 → 1.0) after
they start succeeding again.

Design
------
* **Sliding window** — each agent keeps a ``deque`` of the last
  ``window_size`` outcomes (success / failure + latency). Stats
  (failure rate, average latency, timeout rate) are computed over the
  window so old behaviour ages out automatically.
* **Drain (摘流)** — when an agent's window failure rate exceeds
  ``failure_rate_threshold`` (default 30%), its weight is forced to
  ``0.0`` and an ``agent_drained`` event is published to the
  notification event bus (level=ERROR).
* **Recovery (灰度回切)** — once drained, an agent must accumulate
  ``recovery_consecutive_successes`` consecutive successes. Each time
  the threshold is reached, the weight is bumped one step along the
  *recovery ladder* ``[0.3, 0.6, 1.0]``. A single failure during
  recovery resets the consecutive-success counter (but does not
  re-drain unless the window failure rate again exceeds the threshold).
* **Prometheus** — the detector exposes three gauges via the shared
  :class:`~maop.core.monitoring.monitoring.MetricsCollector`:
  ``maop_agent_failure_rate``, ``maop_agent_weight`` and
  ``maop_agent_status`` (0=normal, 1=drained, 2=recovering).
* **Thread safety** — the detector is intended to be called from the
  scheduler's dispatch path (single-threaded asyncio); a
  :class:`threading.Lock` guards the per-agent state so the
  introspection endpoint can read concurrently.

The detector is storage-agnostic and side-effect-free apart from the
optional event-bus publish and metric gauge updates.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from maop.core.observability.metrics import get_metrics

if TYPE_CHECKING:
    from maop.enterprise.notification.event_bus import EventBus

logger = logging.getLogger(__name__)

# ── Metric names (exported on /api/prometheus via ObservabilityMetrics) ──
M_AGENT_FAILURE_RATE = "maop_agent_failure_rate"
M_AGENT_WEIGHT = "maop_agent_weight"
M_AGENT_STATUS = "maop_agent_status"
M_AGENT_TIMEOUT_RATE = "maop_agent_timeout_rate"
M_AGENT_AVG_LATENCY = "maop_agent_avg_latency_seconds"

# Recovery ladder — drained agents step through these weights on each
# successful recovery burst. Index 0 is the first grey-probe weight; the
# final value is full traffic.
_RECOVERY_LADDER: tuple[float, ...] = (0.3, 0.6, 1.0)

# Status codes for the ``maop_agent_status`` gauge (kept numeric so
# Prometheus graphing tools render them as a step function).
_STATUS_NORMAL = 0.0
_STATUS_DRAINED = 1.0
_STATUS_RECOVERING = 2.0


@dataclass
class _Outcome:
    """A single task outcome recorded in the sliding window."""

    success: bool
    latency: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentHealth:
    """Snapshot of an agent's health for the dashboard / API.

    Attributes
    ----------
    agent_id : str
        Agent identifier (matches the scheduling registry id).
    failure_rate : float
        Fraction of failed tasks in the window (0.0 – 1.0).
    avg_latency : float
        Mean task latency in seconds over the window.
    timeout_rate : float
        Fraction of tasks whose latency exceeded ``timeout_threshold``.
    weight : float
        Current scheduling weight (0.0 – 1.0).
    status : str
        ``"normal"`` / ``"drained"`` / ``"recovering"``.
    window_size : int
        Number of outcomes currently in the window.
    total_recorded : int
        Total outcomes ever recorded for this agent (monotonic).
    """

    agent_id: str
    failure_rate: float
    avg_latency: float
    timeout_rate: float
    weight: float
    status: str
    window_size: int
    total_recorded: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict view of this snapshot."""
        return {
            "agent_id": self.agent_id,
            "failure_rate": self.failure_rate,
            "avg_latency": self.avg_latency,
            "timeout_rate": self.timeout_rate,
            "weight": self.weight,
            "status": self.status,
            "window_size": self.window_size,
            "total_recorded": self.total_recorded,
        }


@dataclass
class _AgentState:
    """Mutable per-agent state (guarded by the detector lock)."""

    window: deque[_Outcome] = field(default_factory=deque)
    weight: float = 1.0
    drained: bool = False
    # Recovery progress: -1 = not started (still fully drained),
    # 0 = first grey-probe (weight 0.3), 1 = second step (0.6),
    # 2 = full traffic (1.0). Index into _RECOVERY_LADDER.
    recovery_step: int = -1
    consecutive_successes: int = 0
    total_recorded: int = 0
    last_drain_at: float = 0.0
    last_recovery_at: float = 0.0


class FailurePatternDetector:
    """Sliding-window failure-rate detector with adaptive weights.

    Parameters
    ----------
    window_size : int
        Number of recent outcomes kept per agent (default 50).
    failure_rate_threshold : float
        Window failure rate above which an agent is drained (default 0.30).
    timeout_threshold : float
        Latency in seconds above which a task counts as a timeout for
        :attr:`AgentHealth.timeout_rate` (default 30.0).
    recovery_consecutive_successes : int
        Consecutive successes required to advance one step on the
        recovery ladder (default 5).
    event_bus : EventBus | None
        Optional notification event bus. When supplied, drain / recovery
        events are published as ``agent_drained`` / ``agent_recovered``.
    tenant_id : str
        Tenant scope for emitted events (multi-tenant isolation).
    """

    def __init__(
        self,
        *,
        window_size: int = 50,
        failure_rate_threshold: float = 0.30,
        timeout_threshold: float = 30.0,
        recovery_consecutive_successes: int = 5,
        event_bus: EventBus | None = None,
        tenant_id: str = "",
    ) -> None:
        self._window_size = max(1, int(window_size))
        self._failure_rate_threshold = float(failure_rate_threshold)
        self._timeout_threshold = float(timeout_threshold)
        self._recovery_consecutive_successes = max(1, int(recovery_consecutive_successes))
        self._event_bus = event_bus
        self._tenant_id = tenant_id
        self._agents: dict[str, _AgentState] = {}
        self._lock = threading.Lock()
        # Lazily-grabbed metrics singleton — registered against the
        # global collector so the gauges appear on /api/prometheus.
        self._metrics = get_metrics()
        self._failure_rate_gauge = self._metrics.collector.gauge(
            M_AGENT_FAILURE_RATE,
            "Per-agent task failure rate over the sliding window (labels: agent)",
        )
        self._weight_gauge = self._metrics.collector.gauge(
            M_AGENT_WEIGHT,
            "Per-agent scheduling weight (labels: agent)",
        )
        self._status_gauge = self._metrics.collector.gauge(
            M_AGENT_STATUS,
            "Per-agent status: 0=normal, 1=drained, 2=recovering (labels: agent)",
        )
        self._timeout_rate_gauge = self._metrics.collector.gauge(
            M_AGENT_TIMEOUT_RATE,
            "Per-agent timeout rate over the sliding window (labels: agent)",
        )
        self._avg_latency_gauge = self._metrics.collector.gauge(
            M_AGENT_AVG_LATENCY,
            "Per-agent average task latency in seconds (labels: agent)",
        )

    # ── Recording ────────────────────────────────────────────────

    def record_result(
        self,
        agent_id: str,
        success: bool,
        latency: float = 0.0,
    ) -> None:
        """Record the outcome of one task execution on ``agent_id``.

        Updates the sliding window and recomputes the agent's weight
        (drain / recovery transitions are applied inside the lock so
        the weight read by the scheduler is always consistent with the
        recorded outcomes).

        Parameters
        ----------
        agent_id : str
            Agent identifier (any hashable string).
        success : bool
            ``True`` if the task succeeded, ``False`` on failure or timeout.
        latency : float
            Task wall-clock latency in seconds (used for avg-latency and
            timeout-rate stats).
        """
        if not agent_id:
            return
        outcome = _Outcome(success=bool(success), latency=max(0.0, float(latency)))
        with self._lock:
            state = self._agents.setdefault(agent_id, _AgentState())
            window = state.window
            window.append(outcome)
            if len(window) > self._window_size:
                window.popleft()
            state.total_recorded += 1
            self._update_state_locked(agent_id, state, outcome)

    def _update_state_locked(
        self,
        agent_id: str,
        state: _AgentState,
        last_outcome: _Outcome,
    ) -> None:
        """Apply drain / recovery transitions (caller holds the lock)."""
        stats = self._compute_stats_locked(state)
        failure_rate = stats["failure_rate"]

        # Drain: failure rate exceeds threshold → force weight 0.
        if not state.drained and failure_rate > self._failure_rate_threshold:
            state.drained = True
            state.weight = 0.0
            state.recovery_step = -1
            state.consecutive_successes = 0
            state.last_drain_at = time.time()
            logger.warning(
                "[failure-detector] agent %s drained (failure_rate=%.3f > %.3f)",
                agent_id, failure_rate, self._failure_rate_threshold,
            )
            self._publish_event(
                "agent_drained",
                {
                    "agent_id": agent_id,
                    "failure_rate": round(failure_rate, 4),
                    "threshold": self._failure_rate_threshold,
                    "window_size": len(state.window),
                },
                level="error",
            )
            self._update_metrics_locked(agent_id, state, stats)
            return

        if state.drained:
            if last_outcome.success:
                state.consecutive_successes += 1
                if state.consecutive_successes >= self._recovery_consecutive_successes:
                    # Advance one step on the recovery ladder.
                    next_step = state.recovery_step + 1
                    if next_step < len(_RECOVERY_LADDER):
                        state.recovery_step = next_step
                    state.weight = _RECOVERY_LADDER[state.recovery_step]
                    state.consecutive_successes = 0
                    state.last_recovery_at = time.time()
                    fully_recovered = (
                        state.recovery_step == len(_RECOVERY_LADDER) - 1
                    )
                    if fully_recovered:
                        state.drained = False
                        state.recovery_step = -1
                        logger.info(
                            "[failure-detector] agent %s fully recovered (weight=1.0)",
                            agent_id,
                        )
                        self._publish_event(
                            "agent_recovered",
                            {"agent_id": agent_id, "weight": 1.0},
                            level="info",
                        )
                    else:
                        logger.info(
                            "[failure-detector] agent %s grey-recovery step %d (weight=%.2f)",
                            agent_id, state.recovery_step, state.weight,
                        )
                        self._publish_event(
                            "agent_recovering",
                            {
                                "agent_id": agent_id,
                                "weight": state.weight,
                                "step": state.recovery_step,
                            },
                            level="info",
                        )
            else:
                # Failure during recovery — reset the consecutive-success
                # counter but stay on the current weight (do not re-drain
                # unless the window failure rate again exceeds threshold).
                state.consecutive_successes = 0

        self._update_metrics_locked(agent_id, state, stats)

    # ── Weight query ─────────────────────────────────────────────

    def get_weight(self, agent_id: str) -> float:
        """Return the current scheduling weight for ``agent_id``.

        Unknown agents default to ``1.0`` (full traffic) — never block a
        new agent before it has any recorded outcomes.
        """
        with self._lock:
            state = self._agents.get(agent_id)
            if state is None:
                return 1.0
            return state.weight

    # ── Stats / introspection ────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of all agents' health.

        Shape::

            {
              "agents": [ {agent_id, failure_rate, avg_latency,
                            timeout_rate, weight, status,
                            window_size, total_recorded}, ... ],
              "config": {window_size, failure_rate_threshold,
                         timeout_threshold, recovery_consecutive_successes},
              "total_agents": int,
            }
        """
        with self._lock:
            agents = [
                self._agent_health_locked(aid, state).to_dict()
                for aid, state in sorted(self._agents.items())
            ]
        return {
            "agents": agents,
            "config": {
                "window_size": self._window_size,
                "failure_rate_threshold": self._failure_rate_threshold,
                "timeout_threshold": self._timeout_threshold,
                "recovery_consecutive_successes": self._recovery_consecutive_successes,
            },
            "total_agents": len(agents),
        }

    def get_agent_health(self, agent_id: str) -> AgentHealth | None:
        """Return the health snapshot for a single agent (or ``None``)."""
        with self._lock:
            state = self._agents.get(agent_id)
            if state is None:
                return None
            return self._agent_health_locked(agent_id, state)

    # ── Maintenance ──────────────────────────────────────────────

    def reset(self, agent_id: str | None = None) -> None:
        """Clear recorded state.

        If ``agent_id`` is ``None`` all agents are reset (mainly for
        tests); otherwise only the named agent is reset.
        """
        with self._lock:
            if agent_id is None:
                self._agents.clear()
            else:
                self._agents.pop(agent_id, None)

    # ── Internal helpers (all called with the lock held) ─────────

    def _compute_stats_locked(self, state: _AgentState) -> dict[str, float]:
        window = state.window
        n = len(window)
        if n == 0:
            return {
                "failure_rate": 0.0,
                "avg_latency": 0.0,
                "timeout_rate": 0.0,
            }
        failures = sum(1 for o in window if not o.success)
        timeouts = sum(1 for o in window if o.latency > self._timeout_threshold)
        total_latency = sum(o.latency for o in window)
        return {
            "failure_rate": failures / n,
            "avg_latency": total_latency / n,
            "timeout_rate": timeouts / n,
        }

    def _agent_health_locked(self, agent_id: str, state: _AgentState) -> AgentHealth:
        stats = self._compute_stats_locked(state)
        if state.drained:
            if state.weight >= 1.0:
                status = "normal"
            else:
                status = "recovering" if state.weight > 0.0 else "drained"
        else:
            status = "normal"
        return AgentHealth(
            agent_id=agent_id,
            failure_rate=round(stats["failure_rate"], 4),
            avg_latency=round(stats["avg_latency"], 4),
            timeout_rate=round(stats["timeout_rate"], 4),
            weight=state.weight,
            status=status,
            window_size=len(state.window),
            total_recorded=state.total_recorded,
        )

    def _update_metrics_locked(
        self,
        agent_id: str,
        state: _AgentState,
        stats: dict[str, float],
    ) -> None:
        labels = {"agent": agent_id}
        try:
            self._failure_rate_gauge.set(stats["failure_rate"], labels=labels)
            self._weight_gauge.set(state.weight, labels=labels)
            self._timeout_rate_gauge.set(stats["timeout_rate"], labels=labels)
            self._avg_latency_gauge.set(stats["avg_latency"], labels=labels)
            if state.drained:
                status_code = (
                    _STATUS_RECOVERING if state.weight > 0.0 else _STATUS_DRAINED
                )
            else:
                status_code = _STATUS_NORMAL
            self._status_gauge.set(status_code, labels=labels)
        except Exception as exc:  # noqa: BLE001 — metrics must never break scheduling
            logger.debug("[failure-detector] metric update failed: %s", exc)

    def _publish_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        level: str = "info",
    ) -> None:
        """Publish a notification event (best-effort, non-blocking).

        Uses :meth:`EventBus.emit` when an event bus is wired in. When a
        running asyncio loop exists (production scheduler path), the
        emit coroutine is scheduled on it so the synchronous
        ``record_result`` caller is not blocked. When no loop is running
        (tests, sync scripts), the coroutine is run to completion on a
        fresh loop so events still land in the bus history buffer.
        """
        if self._event_bus is None:
            return
        full_payload = {**payload, "level": level, "source": "failure_detector"}
        try:
            import asyncio

            coro = self._event_bus.emit(
                event_type, full_payload, tenant_id=self._tenant_id,
            )
            try:
                asyncio.get_running_loop()
                # A loop is running — schedule fire-and-forget so the
                # synchronous caller is not blocked.
                asyncio.ensure_future(coro)  # noqa: RUF006
            except RuntimeError:
                # No running loop — run to completion on a fresh loop so
                # tests / sync callers still see the event in history.
                asyncio.run(coro)
        except Exception as exc:  # noqa: BLE001 — notification must never break scheduling
            logger.debug("[failure-detector] event publish failed: %s", exc)


# ── Module-level singleton (lazy) ────────────────────────────────────
_detector_instance: FailurePatternDetector | None = None
_detector_lock = threading.Lock()


def get_failure_detector() -> FailurePatternDetector:
    """Return the process-wide :class:`FailurePatternDetector` singleton."""
    global _detector_instance
    with _detector_lock:
        if _detector_instance is None:
            _detector_instance = FailurePatternDetector()
        return _detector_instance


def set_failure_detector(detector: FailurePatternDetector | None) -> None:
    """Replace (or clear with ``None``) the global singleton.

    Used by tests to inject a configured detector without touching
    module-level state between test cases.
    """
    global _detector_instance
    with _detector_lock:
        _detector_instance = detector



__all__ = [
    "AgentHealth",
    "FailurePatternDetector",
    "M_AGENT_AVG_LATENCY",
    "M_AGENT_FAILURE_RATE",
    "M_AGENT_STATUS",
    "M_AGENT_TIMEOUT_RATE",
    "M_AGENT_WEIGHT",
    "get_failure_detector",
    "set_failure_detector",
]