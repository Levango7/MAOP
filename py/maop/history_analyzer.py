"""MAOP Execution History Analyzer.

Analyzes execution history from multiple data sources to identify:
  - Success/failure patterns (by agent, routing_key, task_type)
  - Performance bottlenecks (slow agents, high cost tasks)
  - Cost drivers (expensive models, wasteful calls)
  - Failure root causes (clustering similar failures)

Data sources:
  - TimeSeriesStore: loop_duration_ms, plan/exec/verify durations, analysis_complexity
  - CostTracker: token usage and cost by model/agent/session
  - delegations table: agent success rates and routing patterns

Usage:
    analyzer = HistoryAnalyzer(root_dir="/path/to/MAOP")
    report = analyzer.analyze(hours=24)
    # report contains: success_patterns, failure_patterns, bottlenecks, cost_drivers
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FailureCluster:
    """A cluster of similar failures."""
    pattern: str  # e.g., "timeout:agent=claude"
    count: int
    agents: list[str] = field(default_factory=list)
    recent_examples: list[dict] = field(default_factory=list)
    root_cause_hypothesis: str = ""


@dataclass
class BottleneckReport:
    """Identified performance bottleneck."""
    component: str  # e.g., "agent:claude", "phase:execute"
    avg_duration_ms: float
    p95_duration_ms: float
    impact_score: float  # 0-1, higher = more impactful


@dataclass
class CostDriver:
    """Identified cost driver."""
    dimension: str  # "model" or "agent"
    dimension_value: str  # e.g., "gpt-4o"
    total_cost: float
    total_tokens: int
    call_count: int
    avg_cost_per_call: float


@dataclass
class AnalysisReport:
    """Complete execution history analysis report."""
    period_hours: int
    generated_at: float
    total_loops: int
    success_rate: float
    failure_clusters: list[FailureCluster] = field(default_factory=list)
    bottlenecks: list[BottleneckReport] = field(default_factory=list)
    cost_drivers: list[CostDriver] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


def _since_to_iso(since: float) -> str:
    """Convert a Unix timestamp to an ISO-8601 string for SQLite TEXT comparison.

    The delegations table stores ``timestamp`` as ISO-8601 (UTC), so we
    convert the ``since`` Unix epoch float to the same format.
    """
    return datetime.fromtimestamp(since, tz=timezone.utc).isoformat()


class HistoryAnalyzer:
    """Analyzes execution history to identify patterns and bottlenecks."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        from maop.core.backends.db_utils import find_project_root
        self._root = Path(root_dir or find_project_root())

        # Lazy-loaded data sources
        self._timeseries = None
        self._cost_tracker = None
        self._db_path = self._root / "data" / "maop.db"

    def _get_timeseries(self):
        if self._timeseries is None:
            from maop.core.monitoring.timeseries import TimeSeriesStore
            # TimeSeriesStore expects a file path, not a directory.
            self._timeseries = TimeSeriesStore(  # type: ignore
                db_path=self._root / "data" / "timeseries.db"
            )
        return self._timeseries

    def _get_cost_tracker(self):
        if self._cost_tracker is None:
            try:
                from maop.core.monitoring.cost_tracker import get_cost_tracker
                self._cost_tracker = get_cost_tracker()  # type: ignore
            except Exception:
                self._cost_tracker = None
        return self._cost_tracker

    def analyze(self, hours: int = 24) -> AnalysisReport:
        """Analyze execution history for the given period."""
        start = time.time() - hours * 3600

        # Gather data
        loop_stats = self._query_loop_stats(start)
        failure_clusters = self._cluster_failures(start)
        bottlenecks = self._identify_bottlenecks(start)
        cost_drivers = self._analyze_cost_drivers(start)

        # Generate recommendations
        recommendations = self._generate_recommendations(
            loop_stats, failure_clusters, bottlenecks, cost_drivers
        )

        return AnalysisReport(
            period_hours=hours,
            generated_at=time.time(),
            total_loops=loop_stats.get("total", 0),
            success_rate=loop_stats.get("success_rate", 0.0),
            failure_clusters=failure_clusters,
            bottlenecks=bottlenecks,
            cost_drivers=cost_drivers,
            recommendations=recommendations,
        )

    def _query_loop_stats(self, since: float) -> dict[str, Any]:
        """Query loop-level statistics from timeseries."""
        try:
            ts = self._get_timeseries()
            from maop.core.monitoring.timeseries import TimeSeriesQuery

            # Query loop duration
            query = TimeSeriesQuery(
                metric="loop_duration_ms",
                start=since,
                end=time.time(),
                aggregation="avg",
            )
            results = ts.query(query)

            # Query success/failure counts
            total_query = TimeSeriesQuery(
                metric="tasks_total", start=since, end=time.time(), aggregation="sum"
            )
            success_query = TimeSeriesQuery(
                metric="tasks_success", start=since, end=time.time(), aggregation="sum"
            )

            total = ts.query(total_query)
            success = ts.query(success_query)

            total_count = sum(r.get("value", 0) for r in total) if total else 0
            success_count = sum(r.get("value", 0) for r in success) if success else 0
            success_rate = success_count / total_count if total_count > 0 else 0.0

            return {
                "total": len(results) if results else 0,
                "success_rate": success_rate,
                "avg_duration_ms": sum(r.get("value", 0) for r in results) / len(results) if results else 0,
            }
        except Exception as exc:
            logger.warning("[history] Loop stats query failed: %s", exc)
            return {"total": 0, "success_rate": 0.0, "avg_duration_ms": 0}

    def _cluster_failures(self, since: float) -> list[FailureCluster]:
        """Cluster similar failures from delegations table.

        The delegations table stores ``timestamp`` as ISO-8601 text and
        failure detail in the ``stderr`` column (there is no ``error`` column).
        """
        import sqlite3
        clusters: dict[str, FailureCluster] = {}

        try:
            if not self._db_path.exists():
                return []

            since_iso = _since_to_iso(since)
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            # Query failed delegations. Use stderr (not error) — that is the
            # actual schema column in maop.core.data.
            try:
                rows = conn.execute(
                    """SELECT agent, routing_key, exit_code, stderr, timestamp
                       FROM delegations
                       WHERE exit_code != 0 AND timestamp >= ?
                   ORDER BY timestamp DESC LIMIT 500""",
                    (since_iso,),
                ).fetchall()
            finally:
                conn.close()

            for row in rows:
                # Create pattern from error type + agent
                error = row["stderr"] or "unknown"
                error_type = error.split(":")[0][:50]  # First part of error
                pattern = f"{error_type}:agent={row['agent']}"

                if pattern not in clusters:
                    clusters[pattern] = FailureCluster(
                        pattern=pattern,
                        count=0,
                        agents=[],
                        root_cause_hypothesis=self._hypothesize_root_cause(error_type, row["agent"]),
                    )

                cluster = clusters[pattern]
                cluster.count += 1
                if row["agent"] not in cluster.agents:
                    cluster.agents.append(row["agent"])
                if len(cluster.recent_examples) < 3:
                    cluster.recent_examples.append({
                        "agent": row["agent"],
                        "routing_key": row["routing_key"],
                        "exit_code": row["exit_code"],
                        "error": row["stderr"],
                        "timestamp": row["timestamp"],
                    })

            return sorted(clusters.values(), key=lambda c: c.count, reverse=True)[:10]
        except Exception as exc:
            logger.warning("[history] Failure clustering failed: %s", exc)
            return []

    def _hypothesize_root_cause(self, error_type: str, agent: str) -> str:
        """Generate a root cause hypothesis based on error pattern."""
        error_lower = error_type.lower()
        if "timeout" in error_lower:
            return f"Agent {agent} may be overloaded or task too complex; consider increasing timeout or splitting task"
        if "rate" in error_lower and "limit" in error_lower:
            return f"Agent {agent} hitting rate limits; consider reducing call frequency or adding backoff"
        if "auth" in error_lower or "key" in error_lower:
            return f"Authentication issue with agent {agent}; check API key validity"
        if "context" in error_lower or "token" in error_lower:
            return f"Context length exceeded for agent {agent}; consider truncating input or using a model with larger context"
        return f"Recurring error with agent {agent}; investigate error pattern: {error_type}"

    def _identify_bottlenecks(self, since: float) -> list[BottleneckReport]:
        """Identify performance bottlenecks from timeseries data."""
        bottlenecks = []
        try:
            ts = self._get_timeseries()
            from maop.core.monitoring.timeseries import TimeSeriesQuery

            # Check each phase duration
            for phase in ["plan_duration_ms", "exec_duration_ms", "verify_duration_ms", "loop_duration_ms"]:
                query = TimeSeriesQuery(
                    metric=phase, start=since, end=time.time(),
                    aggregation="avg",
                )
                results = ts.query(query)
                if results:
                    avg = sum(r.get("value", 0) for r in results) / len(results)
                    # Simple impact score: normalize by loop duration
                    impact = min(1.0, avg / 10000)  # 10s = max impact
                    bottlenecks.append(BottleneckReport(
                        component=f"phase:{phase.replace('_duration_ms', '')}",
                        avg_duration_ms=avg,
                        p95_duration_ms=avg * 1.5,  # Approximate P95
                        impact_score=impact,
                    ))

            return sorted(bottlenecks, key=lambda b: b.impact_score, reverse=True)[:5]
        except Exception as exc:
            logger.warning("[history] Bottleneck identification failed: %s", exc)
            return []

    def _analyze_cost_drivers(self, since: float) -> list[CostDriver]:
        """Analyze cost drivers from CostTracker.

        CostTracker.summary() returns a ``CostSummary`` pydantic model whose
        ``by_model``/``by_agent`` dicts have shape ``{"tokens", "cost", "calls"}``
        (NOT ``total_cost`` / ``total_tokens`` / ``call_count``).
        """
        drivers = []
        try:
            tracker = self._get_cost_tracker()
            if not tracker:
                return []

            # CostTracker.summary filters by ISO date strings. Convert ``since``.
            start_date = _since_to_iso(since)
            summary = tracker.summary(start_date=start_date)

            # Normalize to a plain dict so we can iterate uniformly.
            by_model = summary.by_model if hasattr(summary, "by_model") else {}
            by_agent = summary.by_agent if hasattr(summary, "by_agent") else {}

            # By model
            for model, stats in by_model.items():
                cost = stats.get("cost", 0.0)
                tokens = stats.get("tokens", 0)
                calls = stats.get("calls", 0)
                drivers.append(CostDriver(
                    dimension="model",
                    dimension_value=model,
                    total_cost=cost,
                    total_tokens=tokens,
                    call_count=calls,
                    avg_cost_per_call=cost / max(1, calls),
                ))

            # By agent
            for agent, stats in by_agent.items():
                cost = stats.get("cost", 0.0)
                tokens = stats.get("tokens", 0)
                calls = stats.get("calls", 0)
                drivers.append(CostDriver(
                    dimension="agent",
                    dimension_value=agent,
                    total_cost=cost,
                    total_tokens=tokens,
                    call_count=calls,
                    avg_cost_per_call=cost / max(1, calls),
                ))

            return sorted(drivers, key=lambda d: d.total_cost, reverse=True)[:10]
        except Exception as exc:
            logger.warning("[history] Cost driver analysis failed: %s", exc)
            return []

    def _generate_recommendations(
        self,
        loop_stats: dict,
        failures: list[FailureCluster],
        bottlenecks: list[BottleneckReport],
        cost_drivers: list[CostDriver],
    ) -> list[str]:
        """Generate actionable recommendations based on analysis."""
        recs = []

        # Success rate recommendations
        if loop_stats["success_rate"] < 0.8:
            recs.append(f"Success rate is {loop_stats['success_rate']:.1%}, below 80% threshold")

        # Failure pattern recommendations
        for cluster in failures[:3]:
            recs.append(f"Address failure pattern '{cluster.pattern}' ({cluster.count} occurrences): {cluster.root_cause_hypothesis}")

        # Bottleneck recommendations
        for b in bottlenecks[:2]:
            if b.impact_score > 0.5:
                recs.append(f"Optimize {b.component}: avg {b.avg_duration_ms:.0f}ms, impact score {b.impact_score:.2f}")

        # Cost recommendations
        for driver in cost_drivers[:2]:
            if driver.total_cost > 1.0:  # $1 threshold
                recs.append(f"Review {driver.dimension}={driver.dimension_value}: ${driver.total_cost:.2f} total, ${driver.avg_cost_per_call:.4f}/call")

        return recs or ["No critical issues identified in the analysis period"]
