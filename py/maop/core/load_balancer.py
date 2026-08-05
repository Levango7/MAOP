"""MAOP Load Balancer — Dynamic weighted routing with load awareness.

Provides agent selection that considers:
  - Static weights from config (capacity, priority)
  - Dynamic load metrics (active tasks, avg latency, error rate)
  - Circuit-breaker state (skip open agents)
  - Sticky sessions (route same session to same agent)

Algorithms:
  - WeightedRoundRobin: Distribute by weight, track load
  - LeastLoaded: Pick agent with fewest active tasks
  - Adaptive: Combine weight + latency + error rate

Usage::

    lb = LoadBalancer()
    lb.register("claude", weight=10)
    lb.register("codex", weight=5)
    lb.register("gpt", weight=3)

    agent = lb.select("codegen")  # weighted selection
    lb.record_start("claude", "task-123")
    lb.record_finish("claude", "task-123", duration_ms=1500, success=True)
"""

from __future__ import annotations

import contextlib
import logging
import math
import random
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from maop.core.otel import get_tracer
from maop.core.otel import span as otel_span
from maop.core.routing_decision import (
    RoutingDecisionRecord,
    get_active_span_context,
    record_decision_safe,
)

logger = logging.getLogger(__name__)


# ── Models ──────────────────────────────────────────────────────

class LBAlgorithm(str, Enum):
    WEIGHTED_ROUND_ROBIN = "weighted_round_robin"
    LEAST_LOADED = "least_loaded"
    ADAPTIVE = "adaptive"


# LB-3 fix: EWMA smoothing factor for windowed metrics used by ADAPTIVE.
# alpha=0.2 gives an effective window of roughly the last ~10 samples,
# so agents can recover from bad history instead of being penalized forever.
_EWMA_ALPHA = 0.2


@dataclass
class AgentMetrics:
    """Per-agent load metrics for routing decisions."""
    weight: int = 10
    active_tasks: int = 0
    total_requests: int = 0
    total_successes: int = 0
    total_failures: int = 0
    total_latency_ms: float = 0.0
    last_used: float = 0.0
    # Weighted round-robin state
    current_weight: float = 0.0
    effective_weight: int = 0
    # LB-3 fix: windowed (EWMA) stats — decay old history so ADAPTIVE recovers
    ewma_latency_ms: float = 0.0
    ewma_error_rate: float = 0.0
    ewma_initialized: bool = False

    @property
    def avg_latency_ms(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_latency_ms / self.total_requests

    @property
    def error_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_failures / self.total_requests

    @property
    def success_rate(self) -> float:
        return 1.0 - self.error_rate

    # LB-3 fix: recent (windowed) views; fall back to lifetime stats until
    # the first sample initializes the EWMA.
    @property
    def recent_latency_ms(self) -> float:
        return self.ewma_latency_ms if self.ewma_initialized else self.avg_latency_ms

    @property
    def recent_error_rate(self) -> float:
        return self.ewma_error_rate if self.ewma_initialized else self.error_rate

    @property
    def recent_success_rate(self) -> float:
        return 1.0 - self.recent_error_rate

    def record_sample(self, duration_ms: float, success: bool) -> None:
        """Update EWMA windowed stats with one finished-request sample."""
        err = 0.0 if success else 1.0
        if not self.ewma_initialized:
            self.ewma_latency_ms = duration_ms
            self.ewma_error_rate = err
            self.ewma_initialized = True
        else:
            self.ewma_latency_ms += _EWMA_ALPHA * (duration_ms - self.ewma_latency_ms)
            self.ewma_error_rate += _EWMA_ALPHA * (err - self.ewma_error_rate)


class LBStats(BaseModel):
    """Load balancer statistics."""
    agents: int = 0
    algorithm: str = ""
    total_selections: int = 0
    active_tasks: int = 0
    agent_details: dict[str, dict[str, Any]] = Field(default_factory=dict)


# ── Load Balancer ──────────────────────────────────────────────

class LoadBalancer:
    """Dynamic weighted routing with load awareness.

    Parameters
    ----------
    algorithm : LBAlgorithm
        Routing algorithm to use.
    cooldown_s : float
        Minimum seconds between selections of the same agent
        (for LEAST_LOADED to avoid thundering herd).
    sticky_sessions : bool
        When True, ``select`` honours the ``session_id`` argument: a
        non-expired entry in the sticky map is returned directly and a
        fresh selection is recorded for future calls with the same id.
    sticky_session_ttl_s : float
        Time-to-live (seconds) for sticky session entries. Entries
        older than this are treated as misses and pruned.
    """

    def __init__(
        self,
        algorithm: LBAlgorithm = LBAlgorithm.ADAPTIVE,
        cooldown_s: float = 0.0,
        *,
        sticky_sessions: bool = False,
        sticky_session_ttl_s: float = 300.0,
    ) -> None:
        self._algorithm = algorithm
        self._cooldown = cooldown_s
        self._agents: dict[str, AgentMetrics] = {}
        self._active_tasks: dict[str, set[str]] = {}  # agent -> task_ids
        self._lock = threading.Lock()
        self._total_selections = 0
        # Phase γ-5: sticky session support.
        # session_id -> (agent_name, expires_at_epoch_seconds)
        self._sticky_sessions = sticky_sessions
        self._sticky_ttl = sticky_session_ttl_s
        self._sticky_map: dict[str, tuple[str, float]] = {}

    # ── Registration ─────────────────────────────────────────

    def register(
        self,
        name: str,
        *,
        weight: int = 10,
    ) -> None:
        """Register an agent with a static weight."""
        with self._lock:
            if name not in self._agents:
                self._agents[name] = AgentMetrics(weight=weight, effective_weight=weight)
                self._active_tasks[name] = set()
                logger.info("[lb] Registered agent: %s (weight=%d)", name, weight)
            else:
                self._agents[name].weight = weight
                self._agents[name].effective_weight = weight

    def unregister(self, name: str) -> None:
        """Remove an agent from the pool."""
        with self._lock:
            self._agents.pop(name, None)
            self._active_tasks.pop(name, None)

    # ── Selection ────────────────────────────────────────────

    def select(
        self,
        routing_key: str = "",
        exclude: set[str] | None = None,
        session_id: str = "",
        candidates: list[str] | None = None,
        trace_id: str = "",
    ) -> str | None:
        """Select the best agent for a task.

        Parameters
        ----------
        routing_key : str
            Routing key hint (not used for selection yet).
        exclude : set[str] | None
            Agents to exclude (e.g. circuit-breaker open).
        session_id : str
            For sticky sessions. When ``sticky_sessions`` is enabled and
            this is non-empty, a non-expired mapping is returned
            directly; otherwise a fresh selection is made and recorded
            under this session id for future calls.
        candidates : list[str] | None
            If provided, restrict selection to these agent names.
            Agents not registered will be auto-registered with default weight.
        trace_id : str
            Optional MAOP trace id for OTel/span correlation (Phase γ-4).

        Returns
        -------
        str | None
            Selected agent name, or None if no agents available.
        """
        # Phase γ-4: wrap the selection in an OTel span and persist a
        # RoutingDecisionRecord. Best-effort — span/record failures
        # never break selection.
        tracer = get_tracer("maop.routing.load_balancer")
        _start = time.monotonic()
        algo_name = self._algorithm.value
        sticky_hit = False
        with otel_span(
            tracer, "routing.load_balancer.select", trace_id=trace_id,
            attributes={
                "routing.algorithm": algo_name,
                "routing.routing_key": routing_key,
                "routing.sticky_sessions_enabled": 1 if self._sticky_sessions else 0,
            },
        ) as _span:
            # Phase γ-5: sticky session lookup (before acquiring the main
            # lock for the selection path — we re-check under the lock
            # when writing).
            if self._sticky_sessions and session_id:
                sticky_agent = self._lookup_sticky(session_id)
                if sticky_agent is not None:
                    sticky_hit = True
                    _set_lb_span_attrs(_span, algo_name, 1, sticky_agent,
                                       sticky_hit, session_id)
                    _record_lb_decision(
                        trace_id=trace_id, algorithm=algo_name,
                        candidate_count=1, selected=sticky_agent,
                        sticky_hit=sticky_hit, session_id=session_id,
                        duration_ms=(time.monotonic() - _start) * 1000.0,
                    )
                    return sticky_agent

            with self._lock:
                # Auto-register candidates not yet known
                if candidates:
                    for name in candidates:
                        if name not in self._agents:
                            self._agents[name] = AgentMetrics(weight=10, effective_weight=10)
                            self._active_tasks[name] = set()
                            logger.info("[lb] Auto-registered candidate: %s", name)

                pool = {
                    name: m for name, m in self._agents.items()
                    if name not in (exclude or set())
                    and (candidates is None or name in candidates)
                }
                candidate_count = len(pool)
                if not pool:
                    _set_lb_span_attrs(_span, algo_name, 0, None,
                                       sticky_hit, session_id)
                    _record_lb_decision(
                        trace_id=trace_id, algorithm=algo_name,
                        candidate_count=0, selected=None,
                        sticky_hit=sticky_hit, session_id=session_id,
                        duration_ms=(time.monotonic() - _start) * 1000.0,
                    )
                    return None

                if self._algorithm == LBAlgorithm.WEIGHTED_ROUND_ROBIN:
                    selected = self._select_wrr(pool)
                elif self._algorithm == LBAlgorithm.LEAST_LOADED:
                    selected = self._select_least_loaded(pool)
                else:  # ADAPTIVE
                    selected = self._select_adaptive(pool)

                if selected:
                    self._total_selections += 1
                    self._agents[selected].last_used = time.time()
                    # Record sticky session for future lookups.
                    if self._sticky_sessions and session_id:
                        self._record_sticky(session_id, selected)

                _set_lb_span_attrs(_span, algo_name, candidate_count,
                                   selected, sticky_hit, session_id)
                _record_lb_decision(
                    trace_id=trace_id, algorithm=algo_name,
                    candidate_count=candidate_count, selected=selected,
                    sticky_hit=sticky_hit, session_id=session_id,
                    duration_ms=(time.monotonic() - _start) * 1000.0,
                )
                return selected

    # ── Sticky session helpers ──────────────────────────────

    def _lookup_sticky(self, session_id: str) -> str | None:
        """Return the sticky agent for ``session_id`` if still valid.

        Updates the hit/miss counters and prunes the entry on expiry.
        Runs without the main lock — the sticky map has its own atomic
        read/write semantics under ``self._lock`` for mutations; lookups
        here are read-only on a dict (CPython atomic) followed by a
        guarded prune.
        """
        from maop.core.monitoring import (
            MAOP_STICKY_SESSION_HIT,
            MAOP_STICKY_SESSION_MISS,
        )

        now = time.time()
        with self._lock:
            entry = self._sticky_map.get(session_id)
            if entry is None:
                MAOP_STICKY_SESSION_MISS.inc()
                return None
            agent, expires_at = entry
            if now >= expires_at:
                # Expired — prune and miss.
                self._sticky_map.pop(session_id, None)
                self._refresh_sticky_gauge_locked()
                MAOP_STICKY_SESSION_MISS.inc()
                return None
            MAOP_STICKY_SESSION_HIT.inc()
            return agent

    def _record_sticky(self, session_id: str, agent: str) -> None:
        """Record (or refresh) a sticky session entry. Caller holds ``self._lock``."""

        is_new = session_id not in self._sticky_map
        self._sticky_map[session_id] = (agent, time.time() + self._sticky_ttl)
        if is_new:
            self._refresh_sticky_gauge_locked()

    def _refresh_sticky_gauge_locked(self) -> None:
        """Sync the active-sticky gauge with the map size. Caller holds ``self._lock``."""
        from maop.core.monitoring import MAOP_STICKY_SESSION_ACTIVE

        MAOP_STICKY_SESSION_ACTIVE.set(float(len(self._sticky_map)))

    def clear_sticky_session(self, session_id: str) -> bool:
        """Remove a single sticky session entry.

        Returns True if an entry was present and removed.
        """
        with self._lock:
            removed = self._sticky_map.pop(session_id, None) is not None
            if removed:
                self._refresh_sticky_gauge_locked()
            return removed

    def clear_all_sticky_sessions(self) -> int:
        """Remove all sticky session entries. Returns the count removed."""
        with self._lock:
            count = len(self._sticky_map)
            self._sticky_map.clear()
            self._refresh_sticky_gauge_locked()
            return count

    def cleanup_expired_sticky_sessions(self) -> int:
        """Remove expired sticky session entries. Returns the count removed."""
        now = time.time()
        with self._lock:
            expired = [sid for sid, (_, exp) in self._sticky_map.items() if now >= exp]
            for sid in expired:
                self._sticky_map.pop(sid, None)
            if expired:
                self._refresh_sticky_gauge_locked()
            return len(expired)

    def get_sticky_session(self, session_id: str) -> str | None:
        """Inspect a sticky session without side effects (for tests/diagnostics).

        Returns the agent name if a non-expired entry exists, else None.
        Does NOT update hit/miss counters.
        """
        now = time.time()
        with self._lock:
            entry = self._sticky_map.get(session_id)
            if entry is None:
                return None
            agent, expires_at = entry
            if now >= expires_at:
                return None
            return agent

    def _select_wrr(self, candidates: dict[str, AgentMetrics]) -> str | None:
        """Weighted round-robin (Nginx-style smooth weighted round-robin)."""
        total = 0
        best = None

        for name, m in candidates.items():
            m.current_weight += m.effective_weight
            total += m.effective_weight
            if best is None or m.current_weight > best[1].current_weight:
                best = (name, m)

        if best is None:
            return None

        best[1].current_weight -= total
        return best[0]

    def _select_least_loaded(self, candidates: dict[str, AgentMetrics]) -> str | None:
        """Select agent with fewest active tasks."""
        best = None
        best_load = float("inf")

        now = time.time()
        for name, m in candidates.items():
            # Consider cooldown
            if self._cooldown > 0 and m.last_used > 0:
                if now - m.last_used < self._cooldown:
                    effective_load = m.active_tasks + 1000  # Penalize
                else:
                    effective_load = m.active_tasks
            else:
                effective_load = m.active_tasks

            if effective_load < best_load:
                best_load = effective_load
                best = name

        return best

    def _select_adaptive(self, candidates: dict[str, AgentMetrics]) -> str | None:
        """Adaptive: combine weight + latency + error rate.

        Score = weight * (1 - error_rate) / (1 + avg_latency_s)
        """
        best = None
        best_score = -1.0

        for name, m in candidates.items():
            # Base score from weight
            score = float(m.weight)

            # Penalize by active tasks (load)
            score /= (1.0 + m.active_tasks)

            # Penalize by error rate
            # LB-3 fix: use windowed (EWMA) stats instead of lifetime averages
            # so a temporarily degraded agent can recover its score.
            score *= m.recent_success_rate

            # Penalize by latency (normalize: 1s = baseline)
            if m.recent_latency_ms > 0:
                latency_s = m.recent_latency_ms / 1000.0
                score /= (1.0 + math.log1p(latency_s))

            # Small random jitter to break ties
            score *= (1.0 + random.uniform(-0.01, 0.01))

            if score > best_score:
                best_score = score
                best = name

        return best

    # ── Load tracking ────────────────────────────────────────

    def record_start(self, agent: str, task_id: str) -> None:
        """Record that a task has started on an agent."""
        with self._lock:
            if agent in self._active_tasks:
                self._active_tasks[agent].add(task_id)
            if agent in self._agents:
                self._agents[agent].active_tasks += 1

    def record_finish(
        self,
        agent: str,
        task_id: str,
        *,
        duration_ms: float = 0.0,
        success: bool = True,
    ) -> None:
        """Record that a task has finished on an agent."""
        with self._lock:
            if agent in self._active_tasks:
                self._active_tasks[agent].discard(task_id)
            if agent in self._agents:
                m = self._agents[agent]
                m.active_tasks = max(0, m.active_tasks - 1)
                m.total_requests += 1
                m.total_latency_ms += duration_ms
                if success:
                    m.total_successes += 1
                else:
                    m.total_failures += 1
                # LB-3 fix: feed windowed EWMA stats used by ADAPTIVE
                m.record_sample(duration_ms, success)

    # ── Query ─────────────────────────────────────────────────

    def get_metrics(self, agent: str) -> AgentMetrics | None:
        """Get metrics for a specific agent."""
        with self._lock:
            return self._agents.get(agent)

    def get_load(self, agent: str) -> int:
        """Return the current ``active_tasks`` count for ``agent``.

        Returns 0 for unknown agents so callers can treat load uniformly
        without null checks. Used by ModelSelector's load-aware path.
        """
        with self._lock:
            m = self._agents.get(agent)
            if m is None:
                return 0
            return max(0, m.active_tasks)

    def all_agents(self) -> list[str]:
        """Get all registered agent names."""
        with self._lock:
            return list(self._agents.keys())

    def stats(self) -> LBStats:
        """Get load balancer statistics."""
        with self._lock:
            details = {}
            total_active = 0
            for name, m in self._agents.items():
                active = len(self._active_tasks.get(name, set()))
                total_active += active
                details[name] = {
                    "weight": m.weight,
                    "active_tasks": active,
                    "total_requests": m.total_requests,
                    "avg_latency_ms": round(m.avg_latency_ms, 1),
                    "error_rate": round(m.error_rate, 4),
                    "success_rate": round(m.success_rate, 4),
                }
            return LBStats(
                agents=len(self._agents),
                algorithm=self._algorithm.value,
                total_selections=self._total_selections,
                active_tasks=total_active,
                agent_details=details,
            )

    def __repr__(self) -> str:
        return f"LoadBalancer(algo={self._algorithm.value}, agents={len(self._agents)})"


# ── Global singleton ───────────────────────────────────────────

_global_lb: LoadBalancer | None = None


def get_load_balancer(algorithm: LBAlgorithm = LBAlgorithm.ADAPTIVE) -> LoadBalancer:
    """Get or create the global load balancer singleton."""
    global _global_lb
    if _global_lb is None:
        _global_lb = LoadBalancer(algorithm=algorithm)
    return _global_lb


# ── Phase γ-4: span / decision-record helpers ─────────────────


def _set_span_attr(s: Any, key: str, value: Any) -> None:
    """Best-effort ``set_attribute`` on a (possibly no-op) span."""
    with contextlib.suppress(Exception):
        s.set_attribute(key, value)


def _set_lb_span_attrs(
    span: Any,
    algorithm: str,
    candidate_count: int,
    selected: str | None,
    sticky_hit: bool,
    session_id: str,
) -> None:
    _set_span_attr(span, "routing.algorithm", algorithm)
    _set_span_attr(span, "routing.candidate_count", candidate_count)
    _set_span_attr(span, "routing.selected_agent", selected or "")
    _set_span_attr(span, "routing.sticky_session_hit", 1 if sticky_hit else 0)
    if session_id:
        _set_span_attr(span, "routing.session_id", session_id)


def _record_lb_decision(
    *,
    trace_id: str,
    algorithm: str,
    candidate_count: int,
    selected: str | None,
    sticky_hit: bool,
    session_id: str,
    duration_ms: float,
) -> None:
    """Persist a :class:`RoutingDecisionRecord` for ``load_balancer.select``."""
    otel_trace_id, span_id, parent_span_id = get_active_span_context()
    effective_trace = trace_id or otel_trace_id

    if selected is not None:
        if sticky_hit:
            explanation = (
                f"Selected agent '{selected}' via sticky session hit "
                f"(algorithm={algorithm}, session_id={session_id})."
            )
        else:
            explanation = (
                f"Selected agent '{selected}' via {algorithm} algorithm. "
                f"{candidate_count} candidates evaluated. "
                f"Sticky session: miss (no prior session)."
            )
    else:
        explanation = (
            f"No agent selected via {algorithm} algorithm "
            f"(0 candidates available)."
        )

    try:
        from maop.core.monitoring import (
            MAOP_ROUTING_DECISION_DURATION_MS,
            MAOP_ROUTING_DECISION_TOTAL,
        )
        MAOP_ROUTING_DECISION_TOTAL.inc(labels={"stage": "load_balancer"})
        MAOP_ROUTING_DECISION_DURATION_MS.observe(duration_ms)
    except Exception:
        logger.debug("Silent exception in core/load_balancer.py:643", exc_info=True)

    record_decision_safe(RoutingDecisionRecord(
        trace_id=effective_trace,
        span_id=span_id,
        parent_span_id=parent_span_id,
        timestamp=time.time(),
        stage="load_balancer",
        input_summary={
            "algorithm": algorithm,
            "candidate_count": candidate_count,
            "sticky_session_hit": sticky_hit,
            "session_id": session_id,
        },
        output_summary={"selected_agent": selected},
        explanation=explanation,
        duration_ms=duration_ms,
        attributes={
            "algorithm": algorithm,
            "selected_agent": selected or "",
            "sticky_session_hit": sticky_hit,
        },
    ))
