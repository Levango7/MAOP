"""MAOP Performance Evolution Loop — 性能指标驱动的自演化闭环。

T2 架构债治理：从 ``evolution_loop.py`` 拆分。``PerformanceEvolutionLoop``
（基于性能指标 + AB/SPRT 的闭环）独立成模块；``EvolutionLoop``（基于错误
驱动的闭环）保留在主文件。公开 API 不变（``evolution_loop`` re-export）。
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import time
import uuid as _uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect
from maop.core.evolution.ab_test import ABTestFramework as _ABTestFramework
from maop.core.evolution.ab_test import SPRTConfig as _SPRTConfig
from maop.core.evolution.auto_deployer import AutoDeployer as _AutoDeployer
from maop.core.evolution.evaluator import (
    MetricDelta as _MetricDelta,
)
from maop.core.evolution.evaluator import (
    PerformanceEvaluator as _PerformanceEvaluator,
)
from maop.core.evolution.evaluator import (
    PerformanceMetrics as _PerformanceMetrics,
)
from maop.core.evolution.suggester import (
    ImprovementSuggester as _ImprovementSuggester,
)
from maop.core.evolution.suggester import (
    SuggestionContext as _SuggestionContext,
)

logger = logging.getLogger(__name__)


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

    def _db_connect(self) -> contextlib.AbstractContextManager[sqlite3.Connection]:
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
        return result.model_dump()  # type: ignore[no-any-return]

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
                    logger.exception("[perf-evo] run_forever iteration failed: %s", exc)  # noqa: TRY401
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
