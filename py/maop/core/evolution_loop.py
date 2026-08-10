"""MAOP Evolution Loop — Closed-loop self-evolution orchestrator.

Ties together three existing subsystems into an automated cycle:

  1. ErrorLedger   → detect error hotspots, generate evolution suggestions
  2. SelfHealEngine → attempt auto-repair before escalating
  3. StrategyEngine → evaluate & apply evolution decisions
  4. ThreeLayerMemory → consolidate knowledge, validate improvements

Loop phases:
  OBSERVE  — gather errors from ErrorLedger + episodic stats
  HEAL     — run SelfHealEngine on detected issues
  SUGGEST  — convert unhealed issues into evolution suggestions
  EVALUATE — StrategyEngine decides which suggestions to apply
  APPLY    — ConfigMutator applies approved mutations
  VALIDATE — compare before/after metrics to confirm improvement
  CONSOLIDATE — extract lessons into Semantic Memory

Usage::

    from maop.core.evolution.evolution_loop import EvolutionLoop

    loop = EvolutionLoop(root_dir="/path/to/MAOP")
    report = loop.run_cycle()
    print(report.summary())
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


from maop.core.evolution.evolution_loop_types import (
    EvolutionSuggestion,
    LoopPhase,
    LoopReport,
    PhaseResult,
)

_EVOLUTION_LOOP_DDL = """
CREATE TABLE IF NOT EXISTS evolution_cycles (
    id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    finished_at REAL DEFAULT 0,
    total_duration_s REAL DEFAULT 0,
    errors_observed INTEGER DEFAULT 0,
    heal_attempts INTEGER DEFAULT 0,
    heal_successes INTEGER DEFAULT 0,
    suggestions_generated INTEGER DEFAULT 0,
    suggestions_applied INTEGER DEFAULT 0,
    validation_improved INTEGER DEFAULT 0,
    consolidated INTEGER DEFAULT 0,
    report_json TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_evo_cycles_started ON evolution_cycles(started_at DESC);
"""


# ── Parallel Implementation Note ──────────────────────────────
# NOTE: EvolutionLoop is one of two parallel self-evolution implementations.
# The other is EvolveEngine in maop/evolve.py.
# Both have production callers:
#   - EvolutionLoop (this class): used by core/three_layer_memory.py (consolidation)
#   - EvolveEngine: used by maop_loop.py (main loop), dashboard/routers/evolve.py
# Future work: consider merging into a single canonical implementation.

class EvolutionLoop:
    """Closed-loop self-evolution orchestrator.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    strategy_name : str
        Strategy for evaluation (conservative/aggressive/balanced/cost_aware).
    auto_consolidate : bool
        Whether to run memory consolidation after each cycle.
    heal_threshold : int
        Min recurrence for an error pattern to trigger heal attempt.
    suggest_threshold : int
        Min recurrence for unhealed errors to become evolution suggestions.
    """

    def __init__(
        self,
        root_dir: str | Path,
        strategy_name: str = "balanced",
        auto_consolidate: bool = True,
        heal_threshold: int = 2,
        suggest_threshold: int = 3,
    ) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("evolution_loop")
        self._strategy_name = strategy_name
        self._auto_consolidate = auto_consolidate
        self._heal_threshold = heal_threshold
        self._suggest_threshold = suggest_threshold
        self._init_db()

    def _init_db(self) -> None:
        with self._db_connect() as conn:
            conn.executescript(_EVOLUTION_LOOP_DDL)

    def _db_connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def run_cycle(self, dry_run: bool = False, auto_rollback: bool = True) -> LoopReport:
        """Execute one complete evolution cycle: OBSERVE→HEAL→SUGGEST→EVALUATE→APPLY→VALIDATE→CONSOLIDATE.

        Parameters
        ----------
        dry_run : bool
            When True, runs all phases but APPLY only logs proposed mutations
            without executing them. Useful for previewing impact.
        auto_rollback : bool
            When True (and not dry_run), if VALIDATE shows no improvement,
            automatically rolls back to the pre-APPLY snapshot.
        """
        report = LoopReport(started_at=time.time(), dry_run=dry_run)
        logger.info("[evo-loop] Starting cycle %s (dry_run=%s)", report.cycle_id, dry_run)

        observe = self._phase_observe()
        report.phases.append(observe)
        report.errors_observed = observe.details.get("hotspot_count", 0)

        if report.errors_observed == 0:
            logger.info("[evo-loop] No errors observed, skipping cycle")
            report.finished_at = time.time()
            report.total_duration_s = round(report.finished_at - report.started_at, 2)
            self._save_report(report)
            return report

        heal = self._phase_heal(observe.details.get("hotspot_patterns", []))
        report.phases.append(heal)
        report.heal_attempts = heal.details.get("attempts", 0)
        report.heal_successes = heal.details.get("successes", 0)

        suggest = self._phase_suggest(observe.details.get("hotspot_patterns", []))
        report.phases.append(suggest)
        report.suggestions_generated = suggest.details.get("count", 0)

        evaluate = self._phase_evaluate(suggest.details.get("suggestions", []))
        report.phases.append(evaluate)

        # Pre-APPLY snapshot: capture file state so we can rollback if needed.
        if not dry_run:
            try:
                from maop.core.reliability.change_tracker import ChangeTracker
                ct = ChangeTracker(root_dir=str(self._root))
                snap_id = ct.snapshot(str(self._root), label=f"pre-apply-{report.cycle_id}")
                report.snapshot_id = snap_id
                logger.info("[evo-loop] Pre-APPLY snapshot: %s", snap_id)
            except Exception as exc:
                logger.warning("[evo-loop] Pre-APPLY snapshot failed (rollback unavailable): %s", exc)

        apply_result = self._phase_apply(evaluate.details.get("approved", []), dry_run=dry_run)
        report.phases.append(apply_result)
        report.suggestions_applied = apply_result.details.get("applied", 0)

        validate = self._phase_validate(report.errors_observed)
        report.phases.append(validate)
        report.validation_improved = validate.details.get("improved", False)

        # Auto-rollback: if not improved and we have a snapshot, undo APPLY.
        if (
            not dry_run
            and auto_rollback
            and not report.validation_improved
            and report.snapshot_id
            and apply_result.details.get("applied", 0) > 0
        ):
            try:
                restored = self.rollback_cycle(report.cycle_id, snapshot_id=report.snapshot_id)
                report.rolled_back = restored > 0
                logger.info(
                    "[evo-loop] Auto-rollback: restored %d files (cycle %s)",
                    restored, report.cycle_id,
                )
            except Exception as exc:
                logger.warning("[evo-loop] Auto-rollback failed: %s", exc)

        if self._auto_consolidate:
            consolidate = self._phase_consolidate()
            report.phases.append(consolidate)
            report.consolidated = consolidate.details.get("consolidated", 0)

        report.finished_at = time.time()
        report.total_duration_s = round(report.finished_at - report.started_at, 2)
        self._save_report(report)
        logger.info("[evo-loop] Cycle %s complete: %s", report.cycle_id, report.summary())
        return report

    def _phase_observe(self) -> PhaseResult:
        start = time.time()
        try:
            from maop.core.reliability.error_ledger import ErrorLedger
            ledger = ErrorLedger(root_dir=str(self._root))
            hotspots = ledger.get_hotspots(top=20)
            unhealed = [h for h in hotspots if h.count >= self._heal_threshold]
            patterns = [h.pattern for h in unhealed]
            return PhaseResult(
                phase=LoopPhase.OBSERVE,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={
                    "hotspot_count": len(unhealed),
                    "hotspot_patterns": patterns,
                    "top_patterns": [{"pattern": h.pattern, "count": h.count} for h in unhealed[:5]],
                },
            )
        except Exception as exc:
            logger.warning("[evo-loop] Observe phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.OBSERVE, success=False, error=str(exc), duration_s=round(time.time() - start, 3))

    def _phase_heal(self, patterns: list[str]) -> PhaseResult:
        start = time.time()
        attempts = 0
        successes = 0
        try:
            from maop.core.reliability.self_heal import SelfHealEngine
            engine = SelfHealEngine(root_dir=str(self._root))
            for pattern in patterns:
                attempts += 1
                report = engine.run_all(trigger_condition=pattern)
                if report.repaired > 0:
                    successes += 1
            return PhaseResult(
                phase=LoopPhase.HEAL,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={"attempts": attempts, "successes": successes},
            )
        except Exception as exc:
            logger.warning("[evo-loop] Heal phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.HEAL, success=False, error=str(exc), duration_s=round(time.time() - start, 3), details={"attempts": attempts, "successes": successes})

    def _phase_suggest(self, patterns: list[str]) -> PhaseResult:
        start = time.time()
        suggestions: list[dict[str, Any]] = []
        try:
            from maop.core.reliability.error_ledger import ErrorLedger
            ledger = ErrorLedger(root_dir=str(self._root))
            promoted = ledger.auto_promote(threshold=self._suggest_threshold)

            for rule in promoted:
                suggestions.append(EvolutionSuggestion(
                    source="error_ledger",
                    category="error",
                    mutation_type="error_pattern_rule",
                    severity="HIGH" if rule.count >= 5 else "MEDIUM",
                    description=f"Recurring pattern '{rule.pattern}' (count={rule.count}) → auto-promoted rule",
                    auto_applicable=True,
                    target_type="system",
                    target_name=rule.pattern,
                    metadata={"pattern": rule.pattern, "count": rule.count, "rule": rule.rule},
                ).model_dump())

            for pattern in patterns:
                if not any(s.get("metadata", {}).get("pattern") == pattern for s in suggestions):
                    errors = ledger.find_by_pattern(pattern)
                    if errors:
                        latest = errors[0]
                        is_routing = "routing" in pattern
                        suggestions.append(EvolutionSuggestion(
                                source="error_ledger",
                                category="routing" if is_routing else "reliability",
                                mutation_type="change_routing" if is_routing else "disable_agent",
                                severity="MEDIUM",
                                description=f"Unhealed error pattern '{pattern}' needs config adjustment",
                                auto_applicable=False,
                                target_type="routing" if is_routing else "agent",
                                target_name=pattern,
                                metadata={"pattern": pattern, "error_type": latest.error_type, "context": latest.context},
                            ).model_dump())

            self._write_suggestions(suggestions)

            return PhaseResult(
                phase=LoopPhase.SUGGEST,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={"count": len(suggestions), "suggestions": suggestions},
            )
        except Exception as exc:
            logger.warning("[evo-loop] Suggest phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.SUGGEST, success=False, error=str(exc), duration_s=round(time.time() - start, 3), details={"count": len(suggestions), "suggestions": suggestions})

    def _phase_evaluate(self, suggestions: list[dict[str, Any]]) -> PhaseResult:
        start = time.time()
        approved: list[dict[str, Any]] = []
        try:
            from maop.core.evolution.evolution_strategies import StrategyEngine
            engine = StrategyEngine(root_dir=str(self._root), strategy_name=self._strategy_name)
            decisions = engine.evaluate(suggestions)
            for decision in decisions:
                if decision.should_apply:
                    approved.append({
                        "suggestion_id": decision.suggestion_id,
                        "type": decision.suggestion_type,
                        "severity": decision.severity,
                        "reason": decision.reason,
                    })
            return PhaseResult(
                phase=LoopPhase.EVALUATE,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={"approved": approved, "total": len(suggestions), "approved_count": len(approved)},
            )
        except Exception as exc:
            logger.warning("[evo-loop] Evaluate phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.EVALUATE, success=False, error=str(exc), duration_s=round(time.time() - start, 3), details={"approved": approved})

    def _phase_apply(self, approved: list[dict[str, Any]], dry_run: bool = False) -> PhaseResult:
        """Apply approved mutations. In dry_run mode, log proposed changes only."""
        start = time.time()
        applied = 0
        proposed: list[dict[str, Any]] = []
        try:
            from maop.core.evolution.evolution_strategies import StrategyEngine
            engine = StrategyEngine(root_dir=str(self._root), strategy_name=self._strategy_name)
            for item in approved:
                sid = item.get("suggestion_id", "")
                if not sid:
                    continue
                if dry_run:
                    proposed.append({
                        "suggestion_id": sid,
                        "type": item.get("type", ""),
                        "severity": item.get("severity", ""),
                        "reason": item.get("reason", ""),
                    })
                    applied += 1
                else:
                    result = engine.apply(sid)
                    if result.get("applied", False):
                        applied += 1
            details: dict[str, Any] = {"applied": applied, "total": len(approved)}
            if dry_run:
                details["proposed"] = proposed
                details["dry_run"] = True
            return PhaseResult(
                phase=LoopPhase.APPLY,
                success=True,
                duration_s=round(time.time() - start, 3),
                details=details,
            )
        except Exception as exc:
            logger.warning("[evo-loop] Apply phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.APPLY, success=False, error=str(exc), duration_s=round(time.time() - start, 3), details={"applied": applied})

    def _phase_validate(self, baseline_errors: int) -> PhaseResult:
        start = time.time()
        try:
            from maop.core.reliability.error_ledger import ErrorLedger
            ledger = ErrorLedger(root_dir=str(self._root))
            current_hotspots = ledger.get_hotspots(top=20)
            current_unhealed = len([h for h in current_hotspots if h.count >= self._heal_threshold])
            improved = current_unhealed < baseline_errors

            if improved:
                logger.info("[evo-loop] Validation: errors decreased %d → %d", baseline_errors, current_unhealed)
            else:
                logger.info("[evo-loop] Validation: no improvement (%d → %d)", baseline_errors, current_unhealed)

            return PhaseResult(
                phase=LoopPhase.VALIDATE,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={"improved": improved, "baseline": baseline_errors, "current": current_unhealed},
            )
        except Exception as exc:
            logger.warning("[evo-loop] Validate phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.VALIDATE, success=False, error=str(exc), duration_s=round(time.time() - start, 3))

    def _phase_consolidate(self) -> PhaseResult:
        start = time.time()
        try:
            from maop.core.memory.three_layer_memory import ThreeLayerMemory
            mem = ThreeLayerMemory(root_dir=str(self._root))
            report = mem.consolidate(min_score=0.6, limit=50)
            return PhaseResult(
                phase=LoopPhase.CONSOLIDATE,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={"candidates": report.candidates, "consolidated": report.consolidated, "errors": report.errors},
            )
        except Exception as exc:
            logger.warning("[evo-loop] Consolidate phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.CONSOLIDATE, success=False, error=str(exc), duration_s=round(time.time() - start, 3))

    def rollback_cycle(self, cycle_id: str, snapshot_id: str = "") -> int:
        """Roll back file changes made during a cycle's APPLY phase.

        Restores files to the state captured by the pre-APPLY snapshot.
        If ``snapshot_id`` is not provided, looks up the cycle's snapshot_id
        from the persisted cycle report.

        Returns the number of files restored.
        """
        snap = snapshot_id
        if not snap:
            # Look up from persisted cycle report.
            with self._db_connect() as conn:
                row = conn.execute(
                    "SELECT report_json FROM evolution_cycles WHERE id=?",
                    (cycle_id,),
                ).fetchone()
            if row is None:
                logger.warning("[evo-loop] rollback_cycle: cycle '%s' not found", cycle_id)
                return 0
            try:
                report = LoopReport.model_validate_json(row[0])
                snap = report.snapshot_id
            except Exception as exc:
                logger.warning("[evo-loop] rollback_cycle: cannot parse cycle report: %s", exc)
                return 0
        if not snap:
            logger.info("[evo-loop] rollback_cycle: no snapshot_id recorded for cycle %s", cycle_id)
            return 0
        try:
            from maop.core.reliability.change_tracker import ChangeTracker
            ct = ChangeTracker(root_dir=str(self._root))
            restored = ct.rollback(str(self._root), to_id=snap)
            logger.info(
                "[evo-loop] rollback_cycle: cycle %s → snapshot %s, %d files restored",
                cycle_id, snap, restored,
            )
            return restored
        except Exception as exc:
            logger.error("[evo-loop] rollback_cycle failed: %s", exc)
            return 0


    # ── 统一数据采集器 ────────────────────────────────────────

    def _collect_delegation_stats(self) -> dict[str, Any]:
        """采集 delegation 历史统计 (来自 EvolveEngine)。"""
        try:
            from maop.evolve import _compute_stats, _load_observability_data_from_db
            db_path = self._root / "data" / "maop.db"
            data = _load_observability_data_from_db(db_path)
            if not data:
                data = _load_observability_data_from_db(self._root / "logs")
            stats = _compute_stats(data)
            return {
                "stats": stats.model_dump(),
                "raw_data": data,
            }
        except Exception as exc:
            logger.debug("[evo-loop] Delegation stats collection failed: %s", exc)
            return {"stats": {}, "raw_data": []}

    def _collect_agent_memory(self, agent_name: str = "") -> dict[str, Any]:
        """采集 agent 记忆数据 (来自 AgentEvolution)。"""
        try:
            from maop.core.agent.memory_ctx.agent_memory import AgentMemory
            mem = AgentMemory(root_dir=str(self._root))
            if agent_name:
                summary = mem.summarize(agent_name)
                return {
                    "agent": agent_name,
                    "summary": summary,
                    "performances": mem.retrieve(agent_name, "performance", limit=100),
                    "error_patterns": mem.retrieve(agent_name, "error_pattern", limit=50),
                    "interactions": mem.retrieve(agent_name, "interaction", limit=100),
                    "preferences": mem.retrieve(agent_name, "preference", limit=20),
                    "lessons": mem.retrieve(agent_name, "lesson", limit=20),
                }
            return {"agent": "", "summary": {}, "performances": [], "error_patterns": [], "interactions": [], "preferences": [], "lessons": []}
        except Exception as exc:
            logger.debug("[evo-loop] Agent memory collection failed: %s", exc)
            return {"agent": agent_name, "summary": {}, "performances": [], "error_patterns": [], "interactions": [], "preferences": [], "lessons": []}

    def _collect_history_analysis(self, hours: int = 24) -> dict[str, Any]:
        """采集历史分析数据 (来自 HistoryAnalyzer)。"""
        try:
            from maop.history_analyzer import HistoryAnalyzer
            analyzer = HistoryAnalyzer(root_dir=self._root)
            report = analyzer.analyze(hours=hours)
            return {
                "failure_clusters": [{"pattern": c.pattern, "count": c.count, "agents": c.agents, "root_cause_hypothesis": c.root_cause_hypothesis} for c in report.failure_clusters],
                "bottlenecks": [{"component": b.component, "avg_duration_ms": b.avg_duration_ms, "impact_score": b.impact_score} for b in report.bottlenecks],
                "cost_drivers": [{"dimension": d.dimension, "dimension_value": d.dimension_value, "total_cost": d.total_cost, "total_tokens": d.total_tokens, "call_count": d.call_count} for d in report.cost_drivers],
                "recommendations": report.recommendations,
            }
        except Exception as exc:
            logger.debug("[evo-loop] History analysis failed: %s", exc)
            return {"failure_clusters": [], "bottlenecks": [], "cost_drivers": [], "recommendations": []}

    def _collect_strategy_learning(self, hours: int = 24) -> dict[str, Any]:
        """采集 agent 策略学习数据 (来自 AgentStrategyLearner)。"""
        try:
            from maop.agent_strategy_learner import AgentStrategyLearner
            learner = AgentStrategyLearner(root_dir=self._root)
            report = learner.learn(hours=hours)
            return {
                "total_combos": report.total_combos,
                "reliable_combos": report.reliable_combos,
                "underperformers": report.underperformers,
                "routing_winners": report.routing_winners,
                "adjustments": [
                    {"agent": a.agent, "routing_key": a.routing_key, "action": a.action,
                     "confidence": a.confidence, "reason": a.reason,
                     "suggested_alternative": a.suggested_alternative,
                     "auto_applicable": a.auto_applicable}
                    for a in report.adjustments
                ],
                "recommendations": report.recommendations,
            }
        except Exception as exc:
            logger.debug("[evo-loop] Strategy learning failed: %s", exc)
            return {"total_combos": 0, "adjustments": [], "routing_winners": {}, "recommendations": []}

    def _collect_cache_evolution(self) -> dict[str, Any]:
        """采集缓存进化数据 (来自 CacheEvolver)。"""
        try:
            from maop.cache_evolver import CacheEvolver
            evolver = CacheEvolver()
            report = evolver.evolve(apply=False)
            return {
                "total_caches": report.total_caches,
                "adjustments": [
                    {"cache_name": a.cache_name, "cache_type": a.cache_type,
                     "parameter": a.parameter, "old_value": a.old_value,
                     "new_value": a.new_value, "reason": a.reason,
                     "auto_applicable": a.auto_applicable}
                    for a in report.adjustments
                ],
                "recommendations": report.recommendations,
            }
        except Exception as exc:
            logger.debug("[evo-loop] Cache evolution failed: %s", exc)
            return {"total_caches": 0, "adjustments": [], "recommendations": []}

    # ── 统一分析器 (10 维度) ──────────────────────────────────

    def _analyze_delegation_stats(self, stats_data: dict[str, Any]) -> list[dict[str, Any]]:
        """从 delegation 统计生成建议 (合并 EvolveEngine 的 4 种规则)。"""
        suggestions: list[dict[str, Any]] = []
        stats = stats_data.get("stats", {})
        raw_data = stats_data.get("raw_data", [])

        # 1. agent 成功率低
        for a in stats.get("by_agent", []):
            if a.get("total", 0) >= 3 and a.get("rate", 100) < 60:
                suggestions.append(EvolutionSuggestion(
                    source="delegation_stats",
                    category="reliability",
                    mutation_type="disable_agent",
                    severity="HIGH",
                    description=f"{a['agent']}: {a['rate']}% success ({a['success']}/{a['total']})",
                    auto_applicable=False,
                    target_type="agent",
                    target_name=a["agent"],
                    mutation_params={"agent": a["agent"], "success_rate": a["rate"]},
                ).model_dump())

        # 2. 路由不匹配 — 修改路由而非禁用 agent
        for ak in stats.get("by_agent_key", []):
            if ak.get("total", 0) >= 3 and ak.get("rate", 100) < 50:
                suggestions.append(EvolutionSuggestion(
                    source="delegation_stats",
                    category="routing",
                    mutation_type="change_routing",
                    severity="HIGH",
                    description=f"{ak['agent']}/{ak['routing_key']}: {ak['rate']}% ({ak['success']}/{ak['total']})",
                    auto_applicable=True,
                    target_type="routing",
                    target_name=ak["routing_key"],
                    mutation_params={"agent": ak["agent"], "routing_key": ak["routing_key"], "success_rate": ak["rate"]},
                ).model_dump())

        # 3. agent 慢 — 增加 timeout 而非减半
        for a in stats.get("by_agent", []):
            if a.get("total", 0) >= 2 and a.get("avg_duration_ms", 0) > 60000:
                suggested_timeout = min(600, int(a["avg_duration_ms"] / 1000 * 1.5))
                suggestions.append(EvolutionSuggestion(
                    source="delegation_stats",
                    category="performance",
                    mutation_type="adjust_timeout",
                    severity="MEDIUM",
                    description=f"{a['agent']}: avg {a['avg_duration_ms']}ms",
                    auto_applicable=True,
                    target_type="agent",
                    target_name=a["agent"],
                    mutation_params={"agent": a["agent"], "suggested_timeout": suggested_timeout},
                ).model_dump())

        # 4. 空路由键
        no_key = [d for d in raw_data if not d.get("routing_key")]
        if no_key:
            suggestions.append(EvolutionSuggestion(
                source="delegation_stats",
                category="routing",
                mutation_type="change_routing",
                severity="LOW",
                description=f"{len(no_key)} delegations with empty routing_key",
                auto_applicable=False,
                target_type="system",
                target_name="empty_routing_key",
            ).model_dump())

        return suggestions

    def _analyze_agent_dimensions(self, mem_data: dict[str, Any]) -> list[dict[str, Any]]:
        """5 维度 agent 进化分析 (合并 AgentEvolution)。"""
        suggestions: list[dict[str, Any]] = []
        agent_name = mem_data.get("agent", "")
        if not agent_name:
            return suggestions

        performances = mem_data.get("performances", [])
        error_patterns = mem_data.get("error_patterns", [])
        interactions = mem_data.get("interactions", [])
        preferences = mem_data.get("preferences", [])


        # 1. 性能维度
        latencies = [p.get("content", {}).get("latency_ms", 0) for p in performances
                     if isinstance(p.get("content", {}).get("latency_ms"), (int, float))]
        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            if avg_latency > 10000:
                suggested_timeout = min(600, int(avg_latency / 1000 * 1.5))
                suggestions.append(EvolutionSuggestion(
                    source="agent_memory",
                    category="performance",
                    mutation_type="adjust_timeout",
                    severity="HIGH",
                    description=f"Average latency {avg_latency:.0f}ms exceeds 10000ms threshold",
                    auto_applicable=False,
                    target_type="agent",
                    target_name=agent_name,
                    mutation_params={"agent": agent_name, "suggested_timeout": suggested_timeout, "avg_latency_ms": round(avg_latency)},
                ).model_dump())

        # 2. 可靠性维度
        total = len(performances)
        failures = sum(1 for p in performances if p.get("content", {}).get("success") is False)
        failure_rate = failures / total if total > 0 else 0
        if failure_rate > 0.3:
            suggestions.append(EvolutionSuggestion(
                source="agent_memory",
                category="reliability",
                mutation_type="adjust_retries",
                severity="HIGH",
                description=f"Failure rate {failure_rate:.1%} exceeds 30% threshold",
                auto_applicable=True,
                target_type="agent",
                target_name=agent_name,
                mutation_params={"agent": agent_name, "suggested_max_retries": 5, "failure_rate": round(failure_rate, 3)},
            ).model_dump())

        # 3. 能力维度
        task_type_counts: dict[str, int] = {}
        for interaction in interactions:
            task_type = interaction.get("content", {}).get("task_type", "")
            if task_type:
                task_type_counts[task_type] = task_type_counts.get(task_type, 0) + 1
        for task_type, count in task_type_counts.items():
            if count >= 3:
                suggestions.append(EvolutionSuggestion(
                    source="agent_memory",
                    category="capability",
                    mutation_type="add_capability",
                    severity="MEDIUM",
                    description=f"Agent frequently used for '{task_type}' ({count} times) but not declared",
                    auto_applicable=True,
                    target_type="agent",
                    target_name=agent_name,
                    mutation_params={"agent": agent_name, "suggested_capability": task_type},
                ).model_dump())

        # 4. 偏好维度
        param_adjustments: dict[str, list] = {}
        for pref in preferences:
            content = pref.get("content", {})
            param = content.get("parameter", "")
            if param:
                param_adjustments.setdefault(param, []).append(content.get("value"))
        for param, values in param_adjustments.items():
            if len(values) >= 5:
                from collections import Counter
                most_common = Counter(str(v) for v in values if v is not None).most_common(1)
                if most_common:
                    suggestions.append(EvolutionSuggestion(
                        source="agent_memory",
                        category="preference",
                        mutation_type="record_preference",
                        severity="LOW",
                        description=f"Parameter '{param}' adjusted {len(values)} times, most common: '{most_common[0][0]}'",
                        auto_applicable=True,
                        target_type="agent",
                        target_name=agent_name,
                        mutation_params={"agent": agent_name, "parameter": param, "suggested_default": most_common[0][0]},
                    ).model_dump())

        # 5. 错误学习维度
        error_freq: dict[str, int] = {}
        for ep in error_patterns:
            error_msg = ep.get("content", {}).get("error", "")[:100]
            if error_msg:
                error_freq[error_msg] = error_freq.get(error_msg, 0) + 1
        for error_msg, freq in error_freq.items():
            if freq >= 3:
                suggestions.append(EvolutionSuggestion(
                    source="agent_memory",
                    category="error",
                    mutation_type="record_lesson",
                    severity="MEDIUM",
                    description=f"Recurring error ({freq} times): '{error_msg[:80]}'",
                    auto_applicable=False,
                    target_type="agent",
                    target_name=agent_name,
                    mutation_params={"agent": agent_name, "error": error_msg, "frequency": freq},
                ).model_dump())

        return suggestions

    def _analyze_history(self, history_data: dict[str, Any]) -> list[dict[str, Any]]:
        """从历史分析生成建议 (成本/失败/瓶颈)。"""
        suggestions: list[dict[str, Any]] = []

        for driver in history_data.get("cost_drivers", []):
            if driver.get("total_cost", 0) > 5.0:
                suggestions.append(EvolutionSuggestion(
                    source="history_analyzer",
                    category="cost",
                    mutation_type="switch_model",
                    severity="MEDIUM",
                    description=f"Model {driver.get('dimension_value', '')} cost ${driver.get('total_cost', 0):.2f}",
                    auto_applicable=False,
                    target_type="system",
                    target_name=driver.get("dimension_value", ""),
                    mutation_params={"model": driver.get("dimension_value", ""), "total_cost": driver.get("total_cost", 0)},
                ).model_dump())

        for cluster in history_data.get("failure_clusters", []):
            if cluster.get("count", 0) >= 3:
                suggestions.append(EvolutionSuggestion(
                    source="history_analyzer",
                    category="error",
                    mutation_type="recurring_failure",
                    severity="HIGH",
                    description=f"Failure pattern '{cluster.get('pattern', '')}' occurred {cluster.get('count', 0)} times",
                    auto_applicable=False,
                    target_type="system",
                    target_name=cluster.get("pattern", ""),
                    mutation_params={"pattern": cluster.get("pattern", ""), "count": cluster.get("count", 0), "root_cause": cluster.get("root_cause_hypothesis", "")},
                ).model_dump())

        for bottleneck in history_data.get("bottlenecks", []):
            if bottleneck.get("avg_duration_ms", 0) > 30000:
                suggestions.append(EvolutionSuggestion(
                    source="history_analyzer",
                    category="bottleneck",
                    mutation_type="adjust_timeout",
                    severity="MEDIUM",
                    description=f"{bottleneck.get('component', '')} avg {bottleneck.get('avg_duration_ms', 0):.0f}ms",
                    auto_applicable=False,
                    target_type="system",
                    target_name=bottleneck.get("component", ""),
                    mutation_params={"component": bottleneck.get("component", ""), "avg_duration_ms": bottleneck.get("avg_duration_ms", 0)},
                ).model_dump())

        return suggestions

    def _analyze_strategy_learning(self, strategy_data: dict[str, Any]) -> list[dict[str, Any]]:
        """从策略学习生成建议。"""
        suggestions: list[dict[str, Any]] = []
        for adj in strategy_data.get("adjustments", []):
            action = adj.get("action", "")
            mutation_map = {
                "disable": "disable_agent",
                "reroute": "change_routing",
                "reduce_timeout": "adjust_timeout",
                "prefer": "change_routing",
                "demote": "change_routing",
            }
            mutation_type = mutation_map.get(action, "record_lesson")
            severity = "HIGH" if action == "disable" else "MEDIUM"
            suggestions.append(EvolutionSuggestion(
                source="strategy_learner",
                category="routing" if action in ("reroute", "prefer", "demote") else "reliability",
                mutation_type=mutation_type,
                severity=severity,
                description=adj.get("reason", ""),
                auto_applicable=adj.get("auto_applicable", False),
                target_type="agent" if action in ("disable", "reduce_timeout") else "routing",
                target_name=adj.get("agent", ""),
                mutation_params={
                    "agent": adj.get("agent", ""),
                    "routing_key": adj.get("routing_key", ""),
                    "suggested_agent": adj.get("suggested_alternative", ""),
                },
            ).model_dump())
        return suggestions

    def _analyze_cache_evolution(self, cache_data: dict[str, Any]) -> list[dict[str, Any]]:
        """从缓存进化生成建议。"""
        suggestions: list[dict[str, Any]] = []
        for adj in cache_data.get("adjustments", []):
            suggestions.append(EvolutionSuggestion(
                source="cache_evolver",
                category="cache",
                mutation_type="adjust_cache",
                severity="LOW",
                description=f"{adj.get('cache_name', '')}: {adj.get('parameter', '')} {adj.get('old_value', 0)}→{adj.get('new_value', 0)} ({adj.get('reason', '')})",
                auto_applicable=adj.get("auto_applicable", False),
                target_type="cache",
                target_name=adj.get("cache_name", ""),
                mutation_params={
                    "cache_name": adj.get("cache_name", ""),
                    "parameter": adj.get("parameter", ""),
                    "new_value": adj.get("new_value", 0),
                },
            ).model_dump())
        return suggestions

    # ── Agent 专属进化 ────────────────────────────────────────

    def evolve_agent(self, agent_name: str, agent_config: Any = None) -> dict[str, Any]:
        """对指定 agent 执行 5 维度进化分析 (AgentEvolution.evolve 的统一替代)。"""
        mem_data = self._collect_agent_memory(agent_name)
        suggestions = self._analyze_agent_dimensions(mem_data)

        # 自动应用安全的建议
        auto_applied = []
        for s in suggestions:
            if s.get("auto_applicable") and not s.get("applied"):
                try:
                    from maop.core.reliability.config_mutator import ConfigMutator
                    mutator = ConfigMutator(root_dir=str(self._root))
                    mut_result = mutator.apply_suggestion(s.get("id", ""))
                    if getattr(mut_result, "applied", False):
                        auto_applied.append(s)
                except Exception as exc:
                    logger.debug("[evo-loop] Auto-apply failed for %s: %s", s.get("id", ""), exc)

        # 记录进化事件到记忆
        try:
            from maop.core.agent.memory_ctx.agent_memory import AgentMemory
            mem = AgentMemory(root_dir=str(self._root))
            mem.record_evolution(
                agent_name=agent_name,
                evolution_type="full_analysis",
                description=f"Generated {len(suggestions)} suggestions, auto-applied {len(auto_applied)}.",
                changes={"suggestions_count": len(suggestions), "auto_applied_count": len(auto_applied)},
                success=True,
            )
        except Exception as exc:
            logger.debug("[evo-loop] Record evolution failed: %s", exc)

        return {
            "agent_name": agent_name,
            "suggestions": suggestions,
            "auto_applied": auto_applied,
            "summary": f"Generated {len(suggestions)} suggestions, auto-applied {len(auto_applied)}.",
        }

    def get_agent_status(self, agent_name: str) -> dict[str, Any]:
        """获取 agent 进化状态 (AgentEvolution.get_status 的统一替代)。"""
        mem_data = self._collect_agent_memory(agent_name)
        summary = mem_data.get("summary", {})
        return {
            "agent_name": agent_name,
            "total_memories": summary.get("total_memories", 0),
            "memory_by_type": summary.get("by_type", {}),
            "evolution_count": summary.get("evolution_count", 0),
            "top_error_patterns": summary.get("top_error_patterns", []),
            "avg_importance": summary.get("avg_importance", 0),
            "ready_for_evolution": summary.get("total_memories", 0) >= 10,
        }

    # ── 统一全量进化 ──────────────────────────────────────────

    def run_full_evolution(self, hours: int = 24, dry_run: bool = False, auto_rollback: bool = True) -> dict[str, Any]:
        """运行全量进化 (合并 EvolveEngine.auto_evolve 的能力)。

        采集所有数据源 → 10 维度分析 → 策略评估 → 安全应用 → 验证
        """
        # 1. 采集所有数据源
        delegation_stats = self._collect_delegation_stats()
        history_data = self._collect_history_analysis(hours=hours)
        strategy_data = self._collect_strategy_learning(hours=hours)
        cache_data = self._collect_cache_evolution()

        # 2. 生成所有建议
        all_suggestions: list[dict[str, Any]] = []
        all_suggestions.extend(self._analyze_delegation_stats(delegation_stats))
        all_suggestions.extend(self._analyze_history(history_data))
        all_suggestions.extend(self._analyze_strategy_learning(strategy_data))
        all_suggestions.extend(self._analyze_cache_evolution(cache_data))

        # 3. 持久化建议
        self._write_suggestions(all_suggestions)

        # 4. 运行标准循环 (含 ErrorLedger + Heal + Strategy + Apply + Validate + Consolidate)
        loop_report = self.run_cycle(dry_run=dry_run, auto_rollback=auto_rollback)

        # 5. 策略评估并应用可自动应用的建议
        auto_applied = 0
        if not dry_run:
            from maop.core.evolution.evolution_strategies import StrategyEngine
            engine = StrategyEngine(root_dir=str(self._root), strategy_name=self._strategy_name)
            decisions = engine.evaluate(all_suggestions)
            for d in decisions:
                if d.should_apply:
                    result = engine.apply(d.suggestion_id)
                    if result.get("applied"):
                        auto_applied += 1

        return {
            "loop_report": loop_report.model_dump(),
            "total_suggestions": len(all_suggestions),
            "auto_applied": auto_applied,
            "delegation_stats": delegation_stats.get("stats", {}),
            "history_analysis": {
                "failure_clusters": len(history_data.get("failure_clusters", [])),
                "bottlenecks": len(history_data.get("bottlenecks", [])),
                "cost_drivers": len(history_data.get("cost_drivers", [])),
            },
            "strategy_learning": {
                "total_combos": strategy_data.get("total_combos", 0),
                "adjustments": len(strategy_data.get("adjustments", [])),
                "reliable_combos": strategy_data.get("reliable_combos", []),
                "underperformers": strategy_data.get("underperformers", []),
                "routing_winners": strategy_data.get("routing_winners", {}),
                "recommendations": strategy_data.get("recommendations", []),
            },
            "cache_evolution": {
                "total_caches": cache_data.get("total_caches", 0),
                "adjustments": len(cache_data.get("adjustments", [])),
                "recommendations": cache_data.get("recommendations", []),
            },
        }

    def _write_suggestions(self, suggestions: list[dict[str, Any]]) -> None:
        """原子写入建议文件，保留已应用状态。"""
        path = self._data_dir / "evolve-suggestions.json"
        existing: list[dict[str, Any]] = []
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, OSError):
                existing = []
        # 保留已应用的建议状态，merge 而非覆盖
        existing_applied = {s.get("id"): s.get("applied", False) for s in existing if s.get("applied")}
        existing_ids = {s.get("id") for s in existing}
        for s in suggestions:
            sid = s.get("id", "")
            if sid not in existing_ids:
                # 保留之前的 applied 状态
                if sid in existing_applied:
                    s["applied"] = True
                existing.append(s)
        # 原子写入
        try:
            from maop.core.reliability.filelock import FileLock
            from maop.core.reliability.safe_writer import safe_write_text
            lock_path = str(path) + ".lock"
            with FileLock(lock_path, timeout_seconds=5):
                safe_write_text(path, json.dumps(existing[-200:], indent=2, ensure_ascii=False), encoding="utf-8")
        except ImportError:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing[-200:], f, indent=2, ensure_ascii=False)

    def _save_report(self, report: LoopReport) -> None:
        with self._db_connect() as conn:
            conn.execute(
                """INSERT INTO evolution_cycles
                   (id, started_at, finished_at, total_duration_s,
                    errors_observed, heal_attempts, heal_successes,
                    suggestions_generated, suggestions_applied,
                    validation_improved, consolidated, report_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (report.cycle_id, report.started_at, report.finished_at,
                 report.total_duration_s, report.errors_observed,
                 report.heal_attempts, report.heal_successes,
                 report.suggestions_generated, report.suggestions_applied,
                 int(report.validation_improved), report.consolidated,
                 report.model_dump_json()),
            )

    def get_cycle_history(self, limit: int = 20) -> list[LoopReport]:
        with self._db_connect() as conn:
            rows = conn.execute(
                "SELECT report_json FROM evolution_cycles ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        reports = []
        for row in rows:
            with contextlib.suppress(Exception):
                reports.append(LoopReport.model_validate_json(row[0]))
        return reports

    def get_stats(self) -> dict[str, Any]:
        with self._db_connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM evolution_cycles").fetchone()[0]
            improved = conn.execute("SELECT COUNT(*) FROM evolution_cycles WHERE validation_improved = 1").fetchone()[0]
            avg_duration = conn.execute("SELECT AVG(total_duration_s) FROM evolution_cycles").fetchone()[0] or 0.0
            total_suggestions = conn.execute("SELECT SUM(suggestions_applied) FROM evolution_cycles").fetchone()[0] or 0
            total_heals = conn.execute("SELECT SUM(heal_successes) FROM evolution_cycles").fetchone()[0] or 0
        return {
            "total_cycles": total,
            "improved_cycles": improved,
            "improvement_rate": round(improved / total, 3) if total > 0 else 0.0,
            "avg_duration_s": round(avg_duration, 2),
            "total_suggestions_applied": total_suggestions,
            "total_heal_successes": total_heals,
        }


# ════════════════════════════════════════════════════════════════════
# F2-01: PerformanceEvolutionLoop — 基于性能指标的闭环调度
# ════════════════════════════════════════════════════════════════════
# 串联 PerformanceEvaluator → ImprovementSuggester → ABTestFramework
#       → AutoDeployer，可配置周期，支持人工 gate 模式。
#
# 与上方 ErrorLedger 驱动的 EvolutionLoop 并存：本类聚焦"性能指标
# 驱动的 AB 验证 + 自动提升/回滚"，前者聚焦"错误热点驱动自愈"。


import uuid as _uuid

from maop.core.ab_test import ABTestFramework as _ABTestFramework, SPRTConfig as _SPRTConfig
from maop.core.evolution.auto_deployer import AutoDeployer as _AutoDeployer
from maop.core.evolution.evaluator import (
    MetricDelta as _MetricDelta,
    PerformanceEvaluator as _PerformanceEvaluator,
    PerformanceMetrics as _PerformanceMetrics,
)
from maop.core.evolution.suggester import (
    ImprovementSuggester as _ImprovementSuggester,
    SuggestionContext as _SuggestionContext,
)


class EvolutionCycleReport(BaseModel):
    """单轮性能演化循环的报告。"""

    cycle_id: str = Field(default_factory=lambda: _uuid.uuid4().hex[:12])
    started_at: float = Field(default_factory=time.time)
    finished_at: float = 0.0
    duration_s: float = 0.0
    experiment: str = ""
    baseline_metrics: _PerformanceMetrics = Field(default_factory=_PerformanceMetrics)
    candidate_metrics: _PerformanceMetrics = Field(default_factory=_PerformanceMetrics)
    delta: _MetricDelta | None = None
    suggestions_count: int = 0
    sprt_decision: str = "continue"
    winner: str = ""
    promoted: bool = False
    rolled_back: bool = False
    pending_approval: bool = False
    detail: str = ""
    error: str = ""

    def summary(self) -> str:
        return (
            f"PerfEvoLoop({self.cycle_id}): "
            f"{self.suggestions_count} suggestions, "
            f"sprt={self.sprt_decision}, winner={self.winner}, "
            f"promoted={self.promoted}, rolled_back={self.rolled_back}, "
            f"pending={self.pending_approval} in {self.duration_s:.1f}s"
        )


_PERF_CYCLE_DDL = """
CREATE TABLE IF NOT EXISTS evolution_perf_cycles (
    id TEXT PRIMARY KEY,
    experiment TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL DEFAULT 0,
    duration_s REAL DEFAULT 0,
    suggestions_count INTEGER DEFAULT 0,
    sprt_decision TEXT DEFAULT 'continue',
    winner TEXT DEFAULT '',
    promoted INTEGER DEFAULT 0,
    rolled_back INTEGER DEFAULT 0,
    pending_approval INTEGER DEFAULT 0,
    detail TEXT DEFAULT '',
    error TEXT DEFAULT '',
    report_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_perf_cycle_exp ON evolution_perf_cycles(experiment, started_at DESC);
"""


class PerformanceEvolutionLoop:
    """性能指标驱动的自演化闭环。

    Parameters
    ----------
    root_dir : str | Path
        MAOP 项目根目录。
    interval_s : float
        自动循环周期（秒）。``run_forever`` 时使用。
    human_gate : bool
        人工 gate 模式：AB 显著后不自动 promote，仅标记 pending_approval。
    enable_llm : bool
        ImprovementSuggester 是否启用 LLM 路径。
    sprt_config : SPRTConfig | None
        AB 实验的 SPRT 参数，None 用默认。

    Usage::

        loop = PerformanceEvolutionLoop(root_dir="/path/to/MAOP")
        report = loop.run_evolution_cycle(
            baseline_traces=traces_a,
            candidate_traces=traces_b,
            experiment="prompt_v2",
        )
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        interval_s: float = 3600.0,
        human_gate: bool = False,
        enable_llm: bool = True,
        sprt_config: _SPRTConfig | None = None,
    ) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._interval_s = interval_s
        self._human_gate = human_gate
        self._sprt_config = sprt_config or _SPRTConfig()
        self._db_path = get_db_path("evolution_perf_loop")
        self._init_db()

        self._evaluator = _PerformanceEvaluator()
        self._suggester = _ImprovementSuggester(root_dir=self._root, enable_llm=enable_llm)
        self._ab_fw = _ABTestFramework(root_dir=str(self._root))
        self._deployer = _AutoDeployer(root_dir=str(self._root))

    def _init_db(self) -> None:
        with self._db_connect() as conn:
            conn.executescript(_PERF_CYCLE_DDL)

    def _db_connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    # ── 单轮循环 ───────────────────────────────────────────────

    def run_evolution_cycle(
        self,
        baseline_traces: list[dict[str, Any]],
        candidate_traces: list[dict[str, Any]],
        *,
        experiment: str,
        agent_name: str = "",
        candidate_config: dict[str, Any] | None = None,
    ) -> EvolutionCycleReport:
        """执行一轮：评估 → 建议 → AB(SPRT) → 部署决策。

        Parameters
        ----------
        baseline_traces, candidate_traces : list[dict]
            control / treatment 的执行 trace 列表。
        experiment : str
            AB 实验名（自动创建若不存在）。
        agent_name : str
            目标 agent 名，传给 Suggester 生成更精准建议。
        candidate_config : dict | None
            treatment 获胜后要持久化的配置，传给 AutoDeployer.promote。
        """
        report = EvolutionCycleReport(experiment=experiment)
        logger.info("[perf-evo] cycle %s start (exp=%s)", report.cycle_id, experiment)
        try:
            # 1. 评估双方指标
            base_metrics = self._evaluator.evaluate(baseline_traces)
            cand_metrics = self._evaluator.evaluate(candidate_traces)
            delta = self._evaluator.compare(baseline_traces, candidate_traces)
            report.baseline_metrics = base_metrics
            report.candidate_metrics = cand_metrics
            report.delta = delta

            # 2. 生成改进建议（基于 candidate 指标 + delta 上下文）
            ctx = _SuggestionContext(
                agent_name=agent_name,
                baseline_metrics=base_metrics,
                delta=delta,
            )
            suggestions = self._suggester.suggest_sync(cand_metrics, ctx)
            report.suggestions_count = len(suggestions)

            # 3. 确保 AB 实验存在
            self._ensure_experiment(experiment)

            # 4. 喂样本给 SPRT（candidate 的每个 success/failure）
            for trace in candidate_traces:
                success = bool(trace.get("success", False))
                self._ab_fw.record(experiment, "treatment", trace.get("entity_id", _uuid.uuid4().hex[:8]), success)
            for trace in baseline_traces:
                success = bool(trace.get("success", False))
                self._ab_fw.record(experiment, "control", trace.get("entity_id", _uuid.uuid4().hex[:8]), success)

            # 5. SPRT 决策
            sprt_result = self._ab_fw.evaluate_sprt(experiment)
            report.sprt_decision = sprt_result.decision.value
            report.winner = sprt_result.winner

            # 6. 部署决策
            if sprt_result.is_significant and sprt_result.winner == "treatment":
                if self._human_gate:
                    report.pending_approval = True
                    report.detail = "AB significant, awaiting human approval (human_gate=True)"
                else:
                    promote_res = self._deployer.promote(
                        experiment, "treatment", config=candidate_config,
                    )
                    report.promoted = promote_res.success
                    report.detail = promote_res.detail
            elif delta.regression:
                rb = self._deployer.rollback_on_regression(experiment, True)
                report.rolled_back = rb.success if rb else False
                report.detail = (rb.detail if rb else "no snapshot to rollback")

            report.finished_at = time.time()
            report.duration_s = round(report.finished_at - report.started_at, 2)
            self._save_report(report)
            logger.info("[perf-evo] cycle %s done: %s", report.cycle_id, report.summary())
            return report
        except Exception as exc:
            report.error = str(exc)
            report.finished_at = time.time()
            report.duration_s = round(report.finished_at - report.started_at, 2)
            logger.exception("[perf-evo] cycle %s failed", report.cycle_id)
            self._save_report(report)
            return report

    def approve_and_promote(
        self,
        experiment: str,
        candidate_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """人工 gate 批准后调用：提升指定实验的 treatment。"""
        result = self._deployer.promote(experiment, "treatment", config=candidate_config)
        return result.model_dump()

    # ── 持续运行 ───────────────────────────────────────────────

    def run_forever(self, trace_collector: Any, *, experiment_prefix: str = "auto") -> Any:
        """按 interval_s 周期持续运行。

        ``trace_collector`` 是无参数可调用对象，返回
        (baseline_traces, candidate_traces, experiment_name)。
        """
        import threading

        def _loop() -> None:
            while True:
                try:
                    base, cand, name = trace_collector()
                    self.run_evolution_cycle(
                        base, cand, experiment=name or f"{experiment_prefix}-{int(time.time())}",
                    )
                except Exception as exc:
                    logger.exception("[perf-evo] run_forever iteration failed: %s", exc)
                time.sleep(self._interval_s)

        t = threading.Thread(target=_loop, daemon=True, name="perf-evo-loop")
        t.start()
        return t

    # ── 查询 ───────────────────────────────────────────────────

    def get_cycle_history(self, experiment: str = "", limit: int = 50) -> list[EvolutionCycleReport]:
        with self._db_connect() as conn:
            if experiment:
                rows = conn.execute(
                    "SELECT report_json FROM evolution_perf_cycles WHERE experiment=? ORDER BY started_at DESC LIMIT ?",
                    (experiment, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT report_json FROM evolution_perf_cycles ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        reports: list[EvolutionCycleReport] = []
        for r in rows:
            with contextlib.suppress(Exception):
                reports.append(EvolutionCycleReport.model_validate_json(r[0]))
        return reports

    def get_pending_approvals(self) -> list[EvolutionCycleReport]:
        """返回所有 pending_approval=True 的循环（人工 gate 待批）。"""
        with self._db_connect() as conn:
            rows = conn.execute(
                "SELECT report_json FROM evolution_perf_cycles WHERE pending_approval=1 ORDER BY started_at DESC",
            ).fetchall()
        out: list[EvolutionCycleReport] = []
        for r in rows:
            with contextlib.suppress(Exception):
                out.append(EvolutionCycleReport.model_validate_json(r[0]))
        return out

    # ── 内部 ───────────────────────────────────────────────────

    def _ensure_experiment(self, experiment: str) -> None:
        """确保 AB 实验存在（已存在则跳过）。"""
        try:
            self._ab_fw.create_experiment(
                name=experiment,
                variants={"control": 50, "treatment": 50},
                sprt_config=self._sprt_config,
            )
        except ValueError:
            # variants sum 不为 100 等参数错误 → 不应发生，记录后跳过
            logger.debug("[perf-evo] create_experiment skipped for %s", experiment)
        except Exception as exc:
            # 实验已存在（UNIQUE 冲突）或其他非致命错误 → 忽略
            logger.debug("[perf-evo] experiment %s already exists or init skipped: %s", experiment, exc)

    def _save_report(self, report: EvolutionCycleReport) -> None:
        with self._db_connect() as conn:
            conn.execute(
                """INSERT INTO evolution_perf_cycles
                   (id, experiment, started_at, finished_at, duration_s,
                    suggestions_count, sprt_decision, winner, promoted, rolled_back,
                    pending_approval, detail, error, report_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (report.cycle_id, report.experiment, report.started_at, report.finished_at,
                 report.duration_s, report.suggestions_count, report.sprt_decision, report.winner,
                 int(report.promoted), int(report.rolled_back), int(report.pending_approval),
                 report.detail, report.error, report.model_dump_json()),
            )
