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

import logging
import math
import random
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Models ──────────────────────────────────────────────────────

class LBAlgorithm(str, Enum):
    WEIGHTED_ROUND_ROBIN = "weighted_round_robin"
    LEAST_LOADED = "least_loaded"
    ADAPTIVE = "adaptive"


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
    """

    def __init__(
        self,
        algorithm: LBAlgorithm = LBAlgorithm.ADAPTIVE,
        cooldown_s: float = 0.0,
    ) -> None:
        self._algorithm = algorithm
        self._cooldown = cooldown_s
        self._agents: dict[str, AgentMetrics] = {}
        self._active_tasks: dict[str, set[str]] = {}  # agent -> task_ids
        self._lock = threading.Lock()
        self._total_selections = 0

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
    ) -> str | None:
        """Select the best agent for a task.

        Parameters
        ----------
        routing_key : str
            Routing key hint (not used for selection yet).
        exclude : set[str] | None
            Agents to exclude (e.g. circuit-breaker open).
        session_id : str
            For sticky sessions (not implemented yet).
        candidates : list[str] | None
            If provided, restrict selection to these agent names.
            Agents not registered will be auto-registered with default weight.

        Returns
        -------
        str | None
            Selected agent name, or None if no agents available.
        """
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
            if not pool:
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

            return selected

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
            score *= m.success_rate

            # Penalize by latency (normalize: 1s = baseline)
            if m.avg_latency_ms > 0:
                latency_s = m.avg_latency_ms / 1000.0
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

    # ── Query ─────────────────────────────────────────────────

    def get_metrics(self, agent: str) -> AgentMetrics | None:
        """Get metrics for a specific agent."""
        with self._lock:
            return self._agents.get(agent)

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
