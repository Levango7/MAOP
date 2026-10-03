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
from maop.core.evolution.evolution_loop_types import (  # re-export 保持 API（测试经 evolution_loop 引用）
    EvolutionSuggestion,
    LoopPhase,  # noqa: F401
    LoopReport,
    PhaseResult,  # noqa: F401
)
from maop.core.evolution.evolution_perf_loop import (  # re-export 保持 API
    EvolutionCycleReport,  # noqa: F401
    PerformanceEvolutionLoop,  # noqa: F401
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
        # T3.1-e：把历史读取能力注入 mixin —— EVALUATE 阶段据此把上一轮
        # 人工批准的建议回流放行（PhasesMixin 被多个宿主复用，不能假设
        # 宿主都有 get_cycle_history）。
        self._cycle_history_reader = self.get_cycle_history
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
        # T3.1-e：本轮 cycle_id 供 EVALUATE 回流时排除自身（历史按 started_at 排序，
        # 同秒开跑时可能把自己排到"上一轮"位置）
        self._current_cycle_id = report.cycle_id

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

        # AC-04: Capture pending_approval from evaluate phase for human gate
        pending_approval_ids = [item.get("suggestion_id", "") for item in evaluate.details.get("pending_approval", [])]
        report.pending_approval = pending_approval_ids
        if pending_approval_ids:
            report.approval_state = "pending"
            logger.info("[evo-loop] %d suggestions pending human approval", len(pending_approval_ids))

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

        # Only apply approved suggestions; pending_approval ones are held for human gate
        apply_result = self._phase_apply(evaluate.details.get("approved", []), dry_run=dry_run)
        report.phases.append(apply_result)
        report.suggestions_applied = apply_result.details.get("applied", 0)

        validate = self._phase_validate(report.errors_observed)
        report.phases.append(validate)
        report.validation_improved = validate.details.get("improved", False)

        # v5.2.0: A/B SPRT 决策联动回滚（AC-04）。
        # 从 VALIDATE 阶段提取 ab_recommendation：
        # - "promote"：treatment 显著胜出，跳过回滚（即使错误未减少）。
        # - "rollback"：treatment 未胜出，强制回滚。
        # - "continue" / None：数据不足或无 A/B 实验，退化为传统逻辑（未改善则回滚）。
        ab_recommendation = validate.details.get("ab_recommendation")
        should_rollback = False
        if ab_recommendation == "promote":
            # A/B 判定 treatment 胜出，跳过回滚。
            logger.info("[evo-loop] AB/SPRT promote → skip rollback")
            should_rollback = False
        elif ab_recommendation == "rollback":
            # A/B 判定 treatment 未胜出，强制回滚。
            should_rollback = True
            logger.info("[evo-loop] AB/SPRT rollback → force rollback")
        else:
            # 传统路径：无 A/B 决策或 CONTINUE，未改善则回滚。
            should_rollback = not report.validation_improved

        # Auto-rollback: 执行回滚（需有快照且实际应用了变更）。
        if (
            not dry_run
            and auto_rollback
            and should_rollback
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

    def get_report(self, cycle_id: str) -> LoopReport | None:
        """按 cycle_id 读取单条循环报告；不存在返回 None。

        T3.0: 审批 API 的公开读取入口（此前 service 调了不存在的
        ``_load_report`` 直接 AttributeError）。旧行 JSON 缺新字段时由
        pydantic 默认值兜底。
        """
        with self._db_connect() as conn:
            row = conn.execute(
                "SELECT report_json FROM evolution_cycles WHERE id = ?",
                (cycle_id,),
            ).fetchone()
        if row is None:
            return None
        with contextlib.suppress(Exception):
            return LoopReport.model_validate_json(row[0])
        return None

    def update_report(self, report: LoopReport) -> bool:
        """更新已落库的循环报告（审批决策持久化）。

        T3.0: ``_save_report`` 是纯 INSERT——对已存在的主键再 INSERT 会撞
        IntegrityError，审批路径必须走 UPDATE。cycle_id 不存在时返回 False
        （调用方按 404 语义处理），不抛异常。

        Returns:
            True 当至少更新了一行。
        """
        with self._db_connect() as conn:
            cur = conn.execute(
                "UPDATE evolution_cycles SET report_json = ? WHERE id = ?",
                (report.model_dump_json(), report.cycle_id),
            )
            return cur.rowcount > 0

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

    # AC-05 / spec §16: 自动回滚 SLA 验证辅助
    # T3.1: 此前注释写着"仅供测试使用（生产无入口）"——现在接了真实入口
    # （`maop evolution inject-degradation` CLI + dashboard 演练端点），
    # 运维可以在预发真实演练"劣化注入 → 自动回滚 <5min"的验收链路。
    def build_degradation_suggestion(self) -> EvolutionSuggestion:
        """构造一条必然导致 VALIDATE 失败的劣化建议（AC-05 演练用）。

        mutation_type="adjust_timeout" + timeout_s=-1 会被 VALIDATE 阶段拒绝，
        触发 auto_rollback=True 分支，5 分钟内回滚。

        Public wrapper over :meth:`_build_degradation_test_suggestion`
        （保留私有名向后兼容既有测试）。
        """
        return self._build_degradation_test_suggestion()

    def inject_degradation_suggestion(self) -> EvolutionSuggestion:
        """把劣化建议**落盘**到建议队列，使其能被下一轮 run_cycle 真实消费。

        这是 T3.1 补上的关键一环：``_write_suggestions`` 是 merge 语义
        （evolution_agent.py:153-172 保留已存在条目），因此外部注入的建议
        不会被下一轮 SUGGEST 覆盖掉。会进入 EVALUATE → （Balanced 策略下
        HIGH 级自动放行）→ APPLY 真改 agents.yaml → VALIDATE 判定失败 →
        真 ChangeTracker 回滚。

        与只打印对象的区别：以前这条路径只存在于测试代码里（"生产无入口"），
        运维无法演练；现在 ``maop evolution inject-degradation`` 落盘后紧跟
        ``maop evolution trigger`` 即完成一次真实演练。
        """
        suggestion = self.build_degradation_suggestion()
        payload = suggestion.model_dump()
        # model_dump 会带上向后兼容别名，保持与 SUGGEST 阶段产出的形状一致，
        # 否则 ConfigMutator 读 mutation_type 时取不到值
        payload.setdefault("type", payload.get("mutation_type", ""))
        payload.setdefault("suggestion_type", payload.get("category", ""))
        payload["auto_applicable"] = True  # 演练条目显式可自动应用
        payload["source"] = "degradation_drill"

        path = self._root / "data" / "evolve-suggestions.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: list[dict[str, Any]] = []
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = []
        existing_ids = {s.get("id") for s in existing}
        if suggestion.id not in existing_ids:
            existing.append(payload)
            path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        return suggestion

    def _build_degradation_test_suggestion(self) -> EvolutionSuggestion:
        """构造必然导致 VALIDATE 失败的测试建议（仅 AC-05 验收用）。

        mutation_type="adjust_timeout" + timeout_s=-1 会被 VALIDATE 阶段拒绝，
        触发 auto_rollback=True 分支，5 分钟内回滚。
        """
        from maop.core.evolution.evolution_loop_types import EvolutionSuggestion
        return EvolutionSuggestion(
            category="performance",
            mutation_type="adjust_timeout",
            severity="HIGH",
            target_name="__ac05_test__",
            mutation_params={"timeout_s": -1},
            metadata={"_ac05_test": True},
        )


# ════════════════════════════════════════════════════════════════════
# F2-01: PerformanceEvolutionLoop — 基于性能指标的闭环调度
# ════════════════════════════════════════════════════════════════════
# 串联 PerformanceEvaluator → ImprovementSuggester → ABTestFramework
#       → AutoDeployer，可配置周期，支持人工 gate 模式。
#
# 与上方 ErrorLedger 驱动的 EvolutionLoop 并存：本类聚焦"性能指标
# 驱动的 AB 验证 + 自动提升/回滚"，前者聚焦"错误热点驱动自愈"。


