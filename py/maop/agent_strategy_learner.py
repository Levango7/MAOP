"""MAOP Agent Strategy Learner — Phase β.3a

Learns optimal agent-selection strategies from execution history and
generates actionable adjustments.  Builds on ``AgentPerformanceTracker``
(which already scores agents by success_rate * cost_efficiency) by
adding:

  - Per-routing-key winner analysis (which agent is best for each key)
  - Underperformer detection (agent+key combos that consistently fail)
  - Automatic generation of routing adjustments
  - Safe auto-application of high-confidence adjustments

Outputs ``AgentStrategyAdjustment`` suggestions that can be consumed by
``EvolveEngine.auto_evolve()`` and persisted alongside existing
``Suggestion`` records.

Usage::

    from maop.agent_strategy_learner import AgentStrategyLearner

    learner = AgentStrategyLearner(root_dir="/path/to/MAOP")
    report = learner.learn()
    for adj in report.adjustments:
        print(adj.agent, adj.routing_key, adj.action, adj.reason)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maop.core.agent.lifecycle.agent_performance import AgentPerformanceTracker

logger = logging.getLogger(__name__)


# ── Data Models ───────────────────────────────────────────────────

@dataclass
class RoutingKeyPerformance:
    """Aggregated performance for one agent on one routing key."""
    agent: str = ""
    routing_key: str = ""
    total: int = 0
    success: int = 0
    failure: int = 0
    success_rate: float = 0.0
    avg_cost_usd: float = 0.0
    avg_latency_ms: float = 0.0

    @property
    def is_reliable(self) -> bool:
        """Reliable = enough samples and decent success rate."""
        return self.total >= 5 and self.success_rate >= 0.8


@dataclass
class AgentStrategyAdjustment:
    """A proposed change to agent routing strategy.

    action values:
      - "prefer"      : boost this agent for this routing_key
      - "demote"      : lower priority of this agent for this routing_key
      - "disable"     : disable this agent entirely (severe underperformance)
      - "reroute"     : switch routing_key to a better agent
      - "reduce_timeout" : lower timeout_s for a slow agent
    """
    agent: str = ""
    routing_key: str = ""
    action: str = ""
    confidence: float = 0.0
    reason: str = ""
    suggested_alternative: str = ""
    auto_applicable: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class StrategyLearnReport:
    """Result of a learning run."""
    total_combos: int = 0
    reliable_combos: int = 0
    underperformers: int = 0
    adjustments: list[AgentStrategyAdjustment] = field(default_factory=list)
    routing_winners: dict[str, str] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)


# ── Learner ───────────────────────────────────────────────────────

class AgentStrategyLearner:
    """Learn agent-selection strategies from performance history.

    Reads from ``agent_performance`` table (populated by
    ``AgentPerformanceTracker.record()``) and generates routing
    adjustments that can be auto-applied or surfaced for human review.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root (where ``data/`` lives).
    min_samples : int
        Minimum samples for a combo to be considered reliable.
    underperformer_threshold : float
        Success rate below this triggers demote/disable action.
    disable_threshold : float
        Success rate below this with enough samples triggers disable.
    """

    def __init__(
        self,
        root_dir: str | Path,
        min_samples: int = 5,
        underperformer_threshold: float = 0.5,
        disable_threshold: float = 0.2,
    ) -> None:
        self._root = Path(root_dir)
        self._tracker = AgentPerformanceTracker(root_dir=self._root)
        self._min_samples = max(1, min_samples)
        self._underperformer_threshold = float(underperformer_threshold)
        self._disable_threshold = float(disable_threshold)

    def learn(self, hours: int = 168) -> StrategyLearnReport:
        """Analyze history and produce strategy adjustments.

        Parameters
        ----------
        hours : int
            Lookback window in hours (default 7 days).
        """
        combos = self._load_combos(hours=hours)
        winners = self._identify_winners(combos)
        adjustments = self._generate_adjustments(combos, winners)

        reliable = sum(1 for c in combos if c.is_reliable)
        underperf = sum(
            1 for c in combos
            if c.total >= self._min_samples
            and c.success_rate < self._underperformer_threshold
        )

        recommendations = self._build_recommendations(
            combos, winners, adjustments
        )

        return StrategyLearnReport(
            total_combos=len(combos),
            reliable_combos=reliable,
            underperformers=underperf,
            adjustments=adjustments,
            routing_winners=winners,
            recommendations=recommendations,
        )

    def _load_combos(self, hours: int) -> list[RoutingKeyPerformance]:
        """Load agent+routing_key performance aggregates from DB."""
        since = time.time() - hours * 3600
        try:
            with self._tracker._db_connect() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        agent,
                        routing_key,
                        COUNT(*) AS total,
                        SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) AS success,
                        SUM(CASE WHEN outcome = 'failure' THEN 1 ELSE 0 END) AS failure,
                        AVG(CASE WHEN cost_usd > 0 THEN cost_usd END) AS avg_cost,
                        AVG(CASE WHEN latency_ms > 0 THEN latency_ms END) AS avg_latency
                    FROM agent_performance
                    WHERE created_at >= ?
                    GROUP BY agent, routing_key
                    HAVING total >= 1
                    """,
                    (since,),
                ).fetchall()
        except Exception as exc:
            logger.warning("[strategy_learner] Failed to load combos: %s", exc)
            return []

        combos: list[RoutingKeyPerformance] = []
        for r in rows:
            total = int(r[2] or 0)
            success = int(r[3] or 0)
            failure = int(r[4] or 0)
            combos.append(RoutingKeyPerformance(
                agent=r[0] or "unknown",
                routing_key=r[1] or "",
                total=total,
                success=success,
                failure=failure,
                success_rate=round(success / total, 3) if total > 0 else 0.0,
                avg_cost_usd=round(float(r[5] or 0.0), 6),
                avg_latency_ms=round(float(r[6] or 0.0), 1),
            ))
        return combos

    def _identify_winners(
        self, combos: list[RoutingKeyPerformance]
    ) -> dict[str, str]:
        """For each routing_key, find the best-performing agent.

        Returns a mapping ``routing_key -> best_agent``.
        Only considers agents with enough samples.
        """
        by_key: dict[str, list[RoutingKeyPerformance]] = {}
        for c in combos:
            if c.total < self._min_samples:
                continue
            by_key.setdefault(c.routing_key, []).append(c)

        winners: dict[str, str] = {}
        for key, group in by_key.items():
            # Score: success_rate (primary) - cost_normalized (secondary)
            max_cost = max((g.avg_cost_usd for g in group), default=0.0) or 1.0
            best = max(
                group,
                key=lambda g: (
                    g.success_rate * 0.8
                    + (1.0 - min(g.avg_cost_usd / max_cost, 1.0)) * 0.2
                ),
            )
            if best.success_rate >= self._underperformer_threshold:
                winners[key] = best.agent
        return winners

    def _generate_adjustments(
        self,
        combos: list[RoutingKeyPerformance],
        winners: dict[str, str],
    ) -> list[AgentStrategyAdjustment]:
        """Generate strategy adjustments from the analysis."""
        adjustments: list[AgentStrategyAdjustment] = []

        for c in combos:
            if c.total < self._min_samples:
                continue

            # Case 1: Severe underperformer → disable
            if c.success_rate < self._disable_threshold and c.total >= 10:
                alt = winners.get(c.routing_key, "")
                adjustments.append(AgentStrategyAdjustment(
                    agent=c.agent,
                    routing_key=c.routing_key,
                    action="disable",
                    confidence=min(1.0, c.total / 20.0),
                    reason=(
                        f"{c.agent}/{c.routing_key or '*'}: "
                        f"{c.success_rate:.0%} success ({c.success}/{c.total})"
                    ),
                    suggested_alternative=alt,
                    auto_applicable=False,  # disabling is high-impact, needs human
                ))
                continue

            # Case 2: Underperformer with better alternative → reroute
            if c.success_rate < self._underperformer_threshold:
                alt = winners.get(c.routing_key, "")
                if alt and alt != c.agent:
                    adjustments.append(AgentStrategyAdjustment(
                        agent=c.agent,
                        routing_key=c.routing_key,
                        action="reroute",
                        confidence=min(0.9, c.total / 15.0),
                        reason=(
                            f"{c.agent} success {c.success_rate:.0%} on '{c.routing_key}', "
                            f"but {alt} performs better"
                        ),
                        suggested_alternative=alt,
                        auto_applicable=True,  # safe: rerouting to proven agent
                    ))
                else:
                    adjustments.append(AgentStrategyAdjustment(
                        agent=c.agent,
                        routing_key=c.routing_key,
                        action="demote",
                        confidence=min(0.8, c.total / 15.0),
                        reason=(
                            f"{c.agent}/{c.routing_key or '*'}: "
                            f"{c.success_rate:.0%} success (below threshold)"
                        ),
                        auto_applicable=False,
                    ))
                continue

            # Case 3: Slow but reliable → reduce timeout
            if c.success_rate >= 0.8 and c.avg_latency_ms > 60000:
                adjustments.append(AgentStrategyAdjustment(
                    agent=c.agent,
                    routing_key=c.routing_key,
                    action="reduce_timeout",
                    confidence=0.6,
                    reason=(
                        f"{c.agent} reliable but slow "
                        f"(avg {c.avg_latency_ms:.0f}ms)"
                    ),
                    auto_applicable=True,
                ))

            # Case 4: Reliable winner → prefer
            if (
                c.success_rate >= 0.9
                and c.routing_key
                and winners.get(c.routing_key) == c.agent
            ):
                adjustments.append(AgentStrategyAdjustment(
                    agent=c.agent,
                    routing_key=c.routing_key,
                    action="prefer",
                    confidence=min(1.0, c.total / 20.0),
                    reason=(
                        f"{c.agent} is the best performer for '{c.routing_key}' "
                        f"({c.success_rate:.0%}, n={c.total})"
                    ),
                    auto_applicable=False,  # informational, no change needed
                ))

        # Sort by confidence descending
        adjustments.sort(key=lambda a: a.confidence, reverse=True)
        return adjustments

    def _build_recommendations(
        self,
        combos: list[RoutingKeyPerformance],
        winners: dict[str, str],
        adjustments: list[AgentStrategyAdjustment],
    ) -> list[str]:
        """Human-readable recommendations for the evolve report."""
        recs: list[str] = []

        if not combos:
            recs.append("Insufficient performance data for strategy learning.")
            return recs

        disable_count = sum(1 for a in adjustments if a.action == "disable")
        reroute_count = sum(1 for a in adjustments if a.action == "reroute")
        if disable_count:
            recs.append(
                f"{disable_count} agent(s) severely underperforming — "
                f"review and consider disabling."
            )
        if reroute_count:
            recs.append(
                f"{reroute_count} routing_key(s) have a better agent available — "
                f"auto-reroute enabled."
            )

        # Top performer highlight
        reliable = [c for c in combos if c.is_reliable]
        if reliable:
            top = max(reliable, key=lambda c: c.success_rate)
            recs.append(
                f"Top performer: {top.agent} on '{top.routing_key or '*'}' "
                f"({top.success_rate:.0%} success, n={top.total})."
            )

        # Coverage gap
        keys_with_data = {c.routing_key for c in combos if c.total >= self._min_samples}
        keys_with_winners = set(winners.keys())
        gap = keys_with_data - keys_with_winners
        if gap:
            recs.append(
                f"{len(gap)} routing_key(s) lack a reliable agent — "
                f"consider adding new agents or improving prompts."
            )

        return recs

    def apply_adjustment(self, adj: AgentStrategyAdjustment) -> bool:
        """Apply a single safe adjustment to agents.yaml.

        Only auto-applicable adjustments (``auto_applicable=True``) are
        applied; others return False and require manual application.
        """
        if not adj.auto_applicable:
            return False

        try:
            import yaml
        except ImportError:
            logger.warning("[strategy_learner] PyYAML not installed")
            return False

        agents_yaml = self._root / "config" / "agents.yaml"
        if not agents_yaml.exists():
            return False

        try:
            with open(agents_yaml, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning("[strategy_learner] Failed to read agents.yaml: %s", exc)
            return False

        agents = data.get("agents", {})
        changed = False

        if adj.action == "reduce_timeout":
            cfg = agents.get(adj.agent)
            if isinstance(cfg, dict):
                current = cfg.get("timeout_s", 120)
                cfg["timeout_s"] = max(30, int(current) // 2)
                changed = True

        # "reroute" is handled at the routing layer, not agents.yaml —
        # we log it but don't modify config here.  The routing table
        # (routes.json) is the canonical place for routing overrides.
        if adj.action == "reroute" and adj.routing_key:
            try:
                routes_file = self._root / "data" / "routes.json"
                routes: dict[str, Any] = {}
                if routes_file.exists():
                    import json
                    routes = json.loads(routes_file.read_text(encoding="utf-8"))
                routes[adj.routing_key] = adj.suggested_alternative
                routes_file.parent.mkdir(parents=True, exist_ok=True)
                routes_file.write_text(
                    json.dumps(routes, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                changed = True
            except Exception as exc:
                logger.warning(
                    "[strategy_learner] Failed to update routes.json: %s", exc
                )

        if changed:
            import shutil

            from maop.core.reliability.filelock import FileLock
            from maop.core.reliability.safe_writer import safe_write_text
            _lock = str(agents_yaml) + ".lock"
            with FileLock(_lock, timeout_seconds=10):
                shutil.copy2(agents_yaml, str(agents_yaml) + ".bak")
                _content = yaml.dump(data, default_flow_style=False, allow_unicode=True)
                safe_write_text(agents_yaml, _content, encoding="utf-8")
            logger.info(
                "[strategy_learner] Applied %s for %s/%s",
                adj.action, adj.agent, adj.routing_key or "*",
            )

        return changed
