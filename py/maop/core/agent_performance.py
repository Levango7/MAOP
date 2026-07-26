"""MAOP Agent Performance Tracker — Aggregate agent success rate & cost efficiency.

Provides:
  - AgentStats: per-agent success rate, avg cost, avg latency
  - AdaptiveRouter: score agents by success_rate * cost_efficiency
  - SQLite persistence for performance history

Integration:
  - Episodic Memory → success/failure outcomes
  - CostTracker → cost and latency data
  - maop_plan._route_by_config → adaptive agent selection

Usage::

    from maop.core.agent_performance import AgentPerformanceTracker

    tracker = AgentPerformanceTracker(root_dir="/path/to/MAOP")
    stats = tracker.get_agent_stats("claude")
    best = tracker.rank_agents(routing_key="code")
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class AgentStats(BaseModel):
    agent: str = ""
    total_tasks: int = 0
    success_count: int = 0
    partial_count: int = 0
    failure_count: int = 0
    success_rate: float = 0.0
    avg_cost_usd: float = 0.0
    avg_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    last_seen_at: float = 0.0


class AgentScore(BaseModel):
    agent: str = ""
    score: float = 0.0
    success_rate: float = 0.0
    cost_efficiency: float = 0.0
    reason: str = ""


_PERFORMANCE_DDL = """
CREATE TABLE IF NOT EXISTS agent_performance (
    id TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    routing_key TEXT DEFAULT '',
    outcome TEXT DEFAULT '',
    cost_usd REAL DEFAULT 0.0,
    latency_ms REAL DEFAULT 0.0,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ap_agent ON agent_performance(agent);
CREATE INDEX IF NOT EXISTS idx_ap_rk ON agent_performance(routing_key);
CREATE INDEX IF NOT EXISTS idx_ap_outcome ON agent_performance(outcome);
CREATE INDEX IF NOT EXISTS idx_ap_created ON agent_performance(created_at DESC);
"""


class AgentPerformanceTracker:
    """Track and aggregate agent performance for adaptive routing.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    success_weight : float
        Weight for success rate in scoring (0.0-1.0).
    cost_weight : float
        Weight for cost efficiency in scoring (0.0-1.0).
    recency_window_s : float
        Only consider records within this time window (seconds). 0 = all.
    """

    def __init__(
        self,
        root_dir: str | Path,
        success_weight: float = 0.7,
        cost_weight: float = 0.3,
        recency_window_s: float = 0.0,
    ) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("agent_performance")
        self._success_weight = success_weight
        self._cost_weight = cost_weight
        self._recency_window_s = recency_window_s
        self._init_db()

    def _init_db(self) -> None:
        with self._db_connect() as conn:
            conn.executescript(_PERFORMANCE_DDL)

    def _db_connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def record(
        self,
        agent: str,
        routing_key: str = "",
        outcome: str = "",
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
    ) -> str:
        """Record an agent task outcome."""
        eid = uuid.uuid4().hex[:12]
        with self._db_connect() as conn:
            conn.execute(
                """INSERT INTO agent_performance
                   (id, agent, routing_key, outcome, cost_usd, latency_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (eid, agent, routing_key, outcome, cost_usd, latency_ms, time.time()),
            )
        return eid

    def get_agent_stats(self, agent: str, routing_key: str = "") -> AgentStats:
        """Get aggregated stats for a specific agent."""
        with self._db_connect() as conn:
            sql = "SELECT outcome, cost_usd, latency_ms, created_at FROM agent_performance WHERE agent = ?"
            params: list[Any] = [agent]
            if routing_key:
                sql += " AND routing_key = ?"
                params.append(routing_key)
            if self._recency_window_s > 0:
                sql += " AND created_at >= ?"
                params.append(time.time() - self._recency_window_s)
            rows = conn.execute(sql, params).fetchall()

        if not rows:
            return AgentStats(agent=agent)

        total = len(rows)
        success = sum(1 for r in rows if r[0] == "success")
        partial = sum(1 for r in rows if r[0] == "partial")
        failure = sum(1 for r in rows if r[0] == "failure")
        costs = [r[1] for r in rows if r[1] > 0]
        latencies = [r[2] for r in rows if r[2] > 0]
        last_seen = max(r[3] for r in rows)

        return AgentStats(
            agent=agent,
            total_tasks=total,
            success_count=success,
            partial_count=partial,
            failure_count=failure,
            success_rate=round(success / total, 3) if total > 0 else 0.0,
            avg_cost_usd=round(sum(costs) / len(costs), 6) if costs else 0.0,
            avg_latency_ms=round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
            total_cost_usd=round(sum(costs), 4),
            last_seen_at=last_seen,
        )

    def rank_agents(
        self,
        agents: list[str] | None = None,
        routing_key: str = "",
        min_tasks: int = 3,
    ) -> list[AgentScore]:
        """Rank agents by combined success_rate * cost_efficiency score.

        Agents with fewer than min_tasks are given a neutral score (0.5)
        to allow new agents a chance without penalizing established ones.
        """
        if agents is None:
            with self._db_connect() as conn:
                sql = "SELECT DISTINCT agent FROM agent_performance"
                params: list[Any] = []
                if routing_key:
                    sql += " WHERE routing_key = ?"
                    params.append(routing_key)
                rows = conn.execute(sql, params).fetchall()
            agents = [r[0] for r in rows]

        all_stats = []
        for agent in agents:
            stats = self.get_agent_stats(agent, routing_key=routing_key)
            all_stats.append(stats)

        if not all_stats:
            return []

        max_cost = max(s.avg_cost_usd for s in all_stats) or 1.0

        scores: list[AgentScore] = []
        for stats in all_stats:
            if stats.total_tasks < min_tasks:
                scores.append(AgentScore(
                    agent=stats.agent,
                    score=0.5,
                    success_rate=stats.success_rate,
                    cost_efficiency=0.5,
                    reason=f"Insufficient data ({stats.total_tasks} tasks, neutral score)",
                ))
                continue

            cost_eff = 1.0 - min(stats.avg_cost_usd / max_cost, 1.0) if max_cost > 0 else 0.5
            combined = (
                stats.success_rate * self._success_weight
                + cost_eff * self._cost_weight
            )
            scores.append(AgentScore(
                agent=stats.agent,
                score=round(combined, 4),
                success_rate=stats.success_rate,
                cost_efficiency=round(cost_eff, 4),
                reason=f"SR={stats.success_rate:.2f} CE={cost_eff:.2f} (n={stats.total_tasks})",
            ))

        scores.sort(key=lambda s: s.score, reverse=True)
        return scores

    def best_agent(
        self,
        agents: list[str],
        routing_key: str = "",
        default: str = "",
    ) -> str:
        """Return the best agent from a candidate list based on performance.

        Falls back to default if no performance data exists.
        """
        scores = self.rank_agents(agents=agents, routing_key=routing_key)
        if scores and scores[0].score > 0:
            return scores[0].agent
        return default or (agents[0] if agents else "")

    def sync_from_episodic(self) -> int:
        """Sync performance data from Episodic Memory (bulk import).

        Returns the number of records synced.
        """
        try:
            from maop.core.three_layer_memory import ThreeLayerMemory
            mem = ThreeLayerMemory(root_dir=str(self._root))
            results = mem.episodic_search(top=200, apply_decay=False)
            synced = 0
            for r in results:
                e = r.entry
                if e.agent:
                    self.record(
                        agent=e.agent,
                        routing_key=e.metadata.get("routing_key", ""),
                        outcome=e.outcome,
                        cost_usd=e.metadata.get("cost_usd", 0.0),
                        latency_ms=e.metadata.get("latency_ms", 0.0),
                    )
                    synced += 1
            logger.info("[perf] Synced %d records from episodic memory", synced)
            return synced
        except Exception as exc:
            logger.warning("[perf] Episodic sync failed: %s", exc)
            return 0

    def get_all_stats(self, routing_key: str = "") -> list[AgentStats]:
        with self._db_connect() as conn:
            sql = "SELECT DISTINCT agent FROM agent_performance"
            params: list[Any] = []
            if routing_key:
                sql += " WHERE routing_key = ?"
                params.append(routing_key)
            rows = conn.execute(sql, params).fetchall()
        return [self.get_agent_stats(r[0], routing_key=routing_key) for r in rows]
