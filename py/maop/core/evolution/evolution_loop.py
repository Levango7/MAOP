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
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


from maop.core.evolution.evolution_agent import EvolutionAgentMixin
from maop.core.evolution.evolution_analyzers import EvolutionAnalyzersMixin

# T2: PerformanceEvolutionLoop / EvolutionCycleReport 已拆分至
# maop.core.evolution.evolution_perf_loop，此处 re-export 保持 API。
from maop.core.evolution.evolution_collectors import EvolutionCollectorsMixin
from maop.core.evolution.evolution_loop_types import (  # noqa: F401  # re-export 保持 API（测试经 evolution_loop 引用）
    EvolutionSuggestion,
    LoopPhase,
    LoopReport,
    PhaseResult,
)
from maop.core.evolution.evolution_perf_loop import (  # noqa: F401  # re-export 保持 API
    EvolutionCycleReport,
    PerformanceEvolutionLoop,
)
from maop.core.evolution.evolution_phases import EvolutionPhasesMixin

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

class EvolutionLoop(EvolutionCollectorsMixin, EvolutionAnalyzersMixin, EvolutionAgentMixin, EvolutionPhasesMixin):
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
        *,
        debate_enabled: bool = False,
        debate_dispatcher: Any | None = None,
        debate_participants: list[str] | None = None,
    ) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("evolution_loop")
        self._strategy_name = strategy_name
        self._auto_consolidate = auto_consolidate
        self._heal_threshold = heal_threshold
        self._suggest_threshold = suggest_threshold
        # C-2: DEBATE 阶段插入配置。默认禁用（向后兼容），
        # 启用时才在 SUGGEST 与 EVALUATE 之间插入 _phase_debate()。
        self._debate_enabled = debate_enabled
        self._debate_dispatcher = debate_dispatcher
        self._debate_participants = debate_participants or []
        self._init_db()

    def _init_db(self) -> None:
        with self._db_connect() as conn:
            conn.executescript(_EVOLUTION_LOOP_DDL)

    def _db_connect(self) -> contextlib.AbstractContextManager[sqlite3.Connection]:
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

        # C-2: DEBATE 阶段（默认禁用，启用时才插入）。
        # 对 suggestions 逐条辩论，仅高置信度共识建议进入 EVALUATE。
        # 未配置时透传 suggestions，行为退化为现状（向后兼容）。
        debated_suggestions = suggest.details.get("suggestions", [])
        if self._debate_enabled and self._debate_dispatcher is not None:
            debate = self._phase_debate(suggest.details.get("suggestions", []))
            report.phases.append(debate)
            debated_suggestions = debate.details.get("accepted_suggestions", [])

        evaluate = self._phase_evaluate(debated_suggestions)
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
            # R-5: 辩论轨迹清理（在 CONSOLIDATE 阶段执行，受既有频率约束）。
            # 仅在 DEBATE 启用且 dispatcher 提供 cleanup_traces 方法时调用。
            if self._debate_enabled and self._debate_dispatcher is not None:
                try:
                    cleanup_fn = getattr(
                        self._debate_dispatcher, "cleanup_traces", None,
                    )
                    if callable(cleanup_fn):
                        cleaned = cleanup_fn()
                        consolidate.details["debate_traces_cleaned"] = cleaned
                except Exception as exc:  # pragma: no cover — 清理失败不阻断主流程
                    logger.warning(
                        "[evo-loop] debate trace cleanup failed: %s", exc,
                    )

        report.finished_at = time.time()
        report.total_duration_s = round(report.finished_at - report.started_at, 2)
        self._save_report(report)
        logger.info("[evo-loop] Cycle %s complete: %s", report.cycle_id, report.summary())
        return report








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






    # ── 统一分析器 (10 维度) ──────────────────────────────────






    # ── Agent 专属进化 ────────────────────────────────────────



    # ── 统一全量进化 ──────────────────────────────────────────



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


