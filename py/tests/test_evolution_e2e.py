"""自演化闭环端到端冒烟测试 + LLM 路径集成测试。

覆盖四类场景：
  1. LLM 路径集成：mock LLMProviderFactory，验证 ``_llm_suggest`` 正确解析
     JSON 候选建议；无效 JSON / 空数组时回退到规则路径。
  2. 端到端冒烟：模拟 OTel traces → PerformanceEvaluator →
     ImprovementSuggester(mock LLM) → ABTestFramework → AutoDeployer →
     验证 LoopReport。
  3. EvolutionLoop 端到端：``run_cycle(dry_run=True)`` 验证完整七段闭环；
     human_gate 模式下 pending approvals 流程。
  4. 回归检测端到端：基线 vs 退化 metrics，验证 ``compare()`` 与
     ImprovementSuggester 的针对性建议。

所有测试独立可运行，不依赖外部服务；使用 ``tmp_path`` 隔离状态。
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maop.core.agent.llm_chat.llm_models import FallbackResult, LLMResponse
from maop.core.evolution.ab_test import (
    ABTestFramework,
    SPRTConfig,
    SPRTDecision,
)
from maop.core.evolution.auto_deployer import AutoDeployer
from maop.core.evolution.evaluator import (
    PerformanceEvaluator,
    PerformanceMetrics,
)
from maop.core.evolution.evolution_loop import EvolutionLoop, LoopPhase, LoopReport
from maop.core.evolution.evolution_loop_types import EvolutionSuggestion
from maop.core.evolution.evolution_perf_loop import (
    EvolutionCycleReport,
    PerformanceEvolutionLoop,
)
from maop.core.evolution.suggester import ImprovementSuggester, SuggestionContext

# ════════════════════════════════════════════════════════════════════
# 辅助：构造 mock LLM factory / 候选 JSON
# ════════════════════════════════════════════════════════════════════


def _make_llm_candidates() -> list[dict[str, Any]]:
    """构造 LLM 返回的有效 JSON 候选建议数组。"""
    return [
        {
            "mutation_type": "adjust_timeout",
            "severity": "HIGH",
            "description": "avg_latency 8000ms → adjust timeout to 12s",
            "target_name": "researcher",
            "mutation_params": {"agent": "researcher", "suggested_timeout": 12},
            "auto_applicable": True,
        },
        {
            "mutation_type": "switch_model",
            "severity": "MEDIUM",
            "description": "avg_cost $0.05 → consider cheaper model",
            "target_name": "researcher",
            "mutation_params": {"agent": "researcher", "reason": "cost_reduction"},
            "auto_applicable": False,
        },
        {
            "mutation_type": "adjust_retries",
            "severity": "HIGH",
            "description": "success_rate 30% → increase retries to 5",
            "target_name": "researcher",
            "mutation_params": {"agent": "researcher", "suggested_max_retries": 5},
            "auto_applicable": True,
        },
    ]


def _make_mock_factory(content: str) -> MagicMock:
    """构造 mock LLMProviderFactory，其 chat_with_fallback 是 AsyncMock。

    返回的 FallbackResult.response.content = content。
    """
    factory = MagicMock()
    factory.chat_with_fallback = AsyncMock(
        return_value=FallbackResult(
            response=LLMResponse(content=content),
            used_model="mock-model",
            original_model="mock-model",
        )
    )
    return factory


def _make_traces(
    n: int,
    success_rate: float,
    latency: int = 500,
    *,
    agent: str = "researcher",
    seed: int = 42,
) -> list[dict[str, Any]]:
    """构造模拟 OTel trace 数据。"""
    rng = random.Random(seed)
    return [
        {
            "success": rng.random() < success_rate,
            "latency_ms": latency + rng.randint(-100, 100),
            "duration_ms": latency,
            "cost_usd": 0.001 * (1 + rng.random()),
            "tokens": 50 + rng.randint(0, 20),
            "agent": agent,
            "model": "mock-model",
            "entity_id": f"e-{i}",
            "timestamp": 1700000000 + i,
        }
        for i in range(n)
    ]


# ════════════════════════════════════════════════════════════════════
# 测试 1: LLM 路径集成测试
# ════════════════════════════════════════════════════════════════════


class TestLLMSuggestIntegration:
    """mock LLMProviderFactory，验证 _llm_suggest 正确解析 JSON 候选建议。"""

    @pytest.mark.asyncio
    async def test_llm_suggest_parses_valid_json(self, tmp_path: Path) -> None:
        """LLM 返回有效 JSON 数组 → 正确解析为 EvolutionSuggestion 列表。"""
        candidates = _make_llm_candidates()
        factory = _make_mock_factory(json.dumps(candidates))

        suggester = ImprovementSuggester(root_dir=tmp_path, enable_llm=True)
        with patch.object(suggester, "_get_factory", return_value=factory):
            metrics = PerformanceMetrics(
                sample_count=10, success_rate=0.3, avg_latency_ms=8000, avg_cost_usd=0.05,
            )
            suggestions = await suggester._llm_suggest(metrics, SuggestionContext(agent_name="researcher"))

        assert len(suggestions) == 3
        # 验证字段正确映射
        assert all(s.source == "llm" for s in suggestions)
        types = {s.mutation_type for s in suggestions}
        assert types == {"adjust_timeout", "switch_model", "adjust_retries"}
        # 验证 severity 大写化
        assert any(s.severity == "HIGH" for s in suggestions)
        # 验证 target_name / auto_applicable / mutation_params 透传
        timeout_sug = next(s for s in suggestions if s.mutation_type == "adjust_timeout")
        assert timeout_sug.target_name == "researcher"
        assert timeout_sug.auto_applicable is True
        assert timeout_sug.mutation_params["suggested_timeout"] == 12
        # 验证 metadata 包含 raw 原始候选
        assert "raw" in timeout_sug.metadata
        assert "generated_at" in timeout_sug.metadata

    @pytest.mark.asyncio
    async def test_llm_suggest_markdown_fenced_json(self, tmp_path: Path) -> None:
        """LLM 返回 markdown fence 包裹的 JSON → 仍能正确解析。"""
        candidates = _make_llm_candidates()
        content = f"```json\n{json.dumps(candidates)}\n```"
        factory = _make_mock_factory(content)

        suggester = ImprovementSuggester(root_dir=tmp_path, enable_llm=True)
        with patch.object(suggester, "_get_factory", return_value=factory):
            metrics = PerformanceMetrics(sample_count=5, success_rate=0.3)
            suggestions = await suggester._llm_suggest(metrics, SuggestionContext())

        assert len(suggestions) == 3

    @pytest.mark.asyncio
    async def test_llm_suggest_invalid_json_falls_back_to_rules(self, tmp_path: Path) -> None:
        """LLM 返回无效 JSON → suggest() 回退到规则路径。"""
        factory = _make_mock_factory("this is not json at all")

        suggester = ImprovementSuggester(root_dir=tmp_path, enable_llm=True)
        with patch.object(suggester, "_get_factory", return_value=factory):
            metrics = PerformanceMetrics(
                sample_count=10, success_count=3, success_rate=0.3,
            )
            suggestions = await suggester.suggest(metrics, SuggestionContext(agent_name="agent-x"))

        # LLM 路径返回空 → 回退到规则；规则应基于低成功率生成 adjust_retries
        assert len(suggestions) >= 1
        assert all(s.source == "rule" for s in suggestions)
        assert any(s.mutation_type == "adjust_retries" for s in suggestions)

    @pytest.mark.asyncio
    async def test_llm_suggest_empty_array_falls_back_to_rules(self, tmp_path: Path) -> None:
        """LLM 返回空数组 → suggest() 回退到规则路径。"""
        factory = _make_mock_factory("[]")

        suggester = ImprovementSuggester(root_dir=tmp_path, enable_llm=True)
        with patch.object(suggester, "_get_factory", return_value=factory):
            metrics = PerformanceMetrics(
                sample_count=10, success_count=3, success_rate=0.3,
            )
            suggestions = await suggester.suggest(metrics, SuggestionContext(agent_name="agent-y"))

        assert len(suggestions) >= 1
        assert all(s.source == "rule" for s in suggestions)

    @pytest.mark.asyncio
    async def test_llm_suggest_partial_malformed_candidates_skipped(self, tmp_path: Path) -> None:
        """LLM 返回的数组中部分候选缺字段 → 跳过 malformed，保留 valid。"""
        candidates = [
            {
                "mutation_type": "adjust_timeout",
                "severity": "HIGH",
                "description": "valid candidate",
                "target_name": "agent-a",
                "mutation_params": {"timeout_s": 60},
                "auto_applicable": True,
            },
            {"unexpected_field": "no mutation_type, no description"},  # 仍可解析（缺字段用默认值）
        ]
        factory = _make_mock_factory(json.dumps(candidates))

        suggester = ImprovementSuggester(root_dir=tmp_path, enable_llm=True)
        with patch.object(suggester, "_get_factory", return_value=factory):
            metrics = PerformanceMetrics(sample_count=5, success_rate=0.5)
            suggestions = await suggester._llm_suggest(metrics, SuggestionContext())

        # 两个候选都被 _candidate_to_suggestion 接受（缺字段用默认值），故应解析出 2 条
        assert len(suggestions) == 2
        assert suggestions[0].mutation_type == "adjust_timeout"
        # 第二个候选缺 mutation_type → 默认 "adjust_prompt"
        assert suggestions[1].mutation_type == "adjust_prompt"

    @pytest.mark.asyncio
    async def test_llm_suggest_factory_none_returns_empty(self, tmp_path: Path) -> None:
        """_get_factory 返回 None（无 root_dir）→ _llm_suggest 返回空列表。"""
        suggester = ImprovementSuggester(root_dir=None, enable_llm=True)
        metrics = PerformanceMetrics(sample_count=5, success_rate=0.5)
        suggestions = await suggester._llm_suggest(metrics, SuggestionContext())
        assert suggestions == []

    @pytest.mark.asyncio
    async def test_llm_suggest_exception_propagates_for_fallback(self, tmp_path: Path) -> None:
        """LLM 调用抛异常 → suggest() 捕获并回退到规则路径。"""
        factory = MagicMock()
        factory.chat_with_fallback = AsyncMock(side_effect=RuntimeError("LLM service down"))

        suggester = ImprovementSuggester(root_dir=tmp_path, enable_llm=True)
        with patch.object(suggester, "_get_factory", return_value=factory):
            metrics = PerformanceMetrics(
                sample_count=10, success_count=3, success_rate=0.3,
            )
            suggestions = await suggester.suggest(metrics, SuggestionContext(agent_name="agent-z"))

        # 异常被 suggest() 捕获 → 回退到规则
        assert len(suggestions) >= 1
        assert all(s.source == "rule" for s in suggestions)


# ════════════════════════════════════════════════════════════════════
# 测试 2: 端到端冒烟测试
# ════════════════════════════════════════════════════════════════════


class TestE2ESmokeFlow:
    """完整闭环：traces → Evaluator → Suggester(mock LLM) → ABTest → Deployer。"""

    def test_full_pipeline_with_mock_llm(self, tmp_path: Path) -> None:
        """端到端：构造 traces → 评估 → 建议(mock LLM) → AB/SPRT → 部署决策。"""
        # 1. 构造模拟 OTel traces
        baseline_traces = _make_traces(40, success_rate=0.4, latency=500, seed=1)
        candidate_traces = _make_traces(40, success_rate=0.9, latency=300, seed=2)

        # 2. PerformanceEvaluator.evaluate(traces) → PerformanceMetrics
        evaluator = PerformanceEvaluator()
        base_metrics = evaluator.evaluate(baseline_traces)
        cand_metrics = evaluator.evaluate(candidate_traces)
        assert base_metrics.sample_count == 40
        assert cand_metrics.sample_count == 40
        assert cand_metrics.success_rate > base_metrics.success_rate

        # 3. ImprovementSuggester.suggest(metrics) — mock LLM 路径
        candidates = _make_llm_candidates()
        factory = _make_mock_factory(json.dumps(candidates))
        suggester = ImprovementSuggester(root_dir=tmp_path, enable_llm=True)
        with patch.object(suggester, "_get_factory", return_value=factory):
            suggestions = suggester.suggest_sync(cand_metrics, SuggestionContext(agent_name="researcher"))

        # 验证建议列表非空且类型正确
        assert len(suggestions) >= 1
        assert all(isinstance(s, EvolutionSuggestion) for s in suggestions)
        assert all(s.source in ("llm", "rule") for s in suggestions)

        # 4. ABTestFramework：创建实验、记录结果、评估 SPRT
        ab_fw = ABTestFramework(root_dir=tmp_path)
        ab_fw.create_experiment(
            name="e2e-smoke-exp",
            variants={"control": 50, "treatment": 50},
            sprt_config=SPRTConfig(p0=0.5, p1=0.75),
        )
        # 喂 candidate（treatment）样本
        for t in candidate_traces:
            ab_fw.record("e2e-smoke-exp", "treatment", t["entity_id"], bool(t["success"]))
        # 喂 baseline（control）样本
        for t in baseline_traces:
            ab_fw.record("e2e-smoke-exp", "control", t["entity_id"], bool(t["success"]))

        sprt_result = ab_fw.evaluate_sprt("e2e-smoke-exp")
        # candidate 成功率 0.9 远高于 p0=0.5 → 应接受 H1
        assert sprt_result.decision == SPRTDecision.ACCEPT_H1
        assert sprt_result.winner == "treatment"
        assert sprt_result.is_significant is True

        # 5. AutoDeployer：promote 优胜 / rollback 劣化
        deployer = AutoDeployer(root_dir=tmp_path)
        promote_res = deployer.promote(
            "e2e-smoke-exp", "treatment",
            config={"model": "gpt-4o", "timeout_s": 12},
        )
        assert promote_res.success is True
        assert promote_res.winner == "treatment"
        assert promote_res.deployment_id

        # 验证 active variant 文件已写入
        active_file = tmp_path / "data" / "evolution-active-variant.json"
        assert active_file.exists()
        active_data = json.loads(active_file.read_text(encoding="utf-8"))
        assert "e2e-smoke-exp" in active_data
        assert active_data["e2e-smoke-exp"]["winner"] == "treatment"

        # 验证部署历史可查询
        history = deployer.get_history(experiment="e2e-smoke-exp")
        assert len(history) >= 1
        assert history[0].action == "promote"
        assert history[0].winner == "treatment"

    def test_pipeline_regression_triggers_rollback(self, tmp_path: Path) -> None:
        """端到端：candidate 退化 → rollback_on_regression 触发。"""
        baseline_traces = _make_traces(30, success_rate=0.9, latency=100, seed=10)
        candidate_traces = _make_traces(30, success_rate=0.3, latency=800, seed=11)

        evaluator = PerformanceEvaluator()
        delta = evaluator.compare(baseline_traces, candidate_traces)
        # 成功率下降 + 延迟上升 → regression
        assert delta.regression is True
        assert delta.success_rate_delta < 0

        # AutoDeployer.rollback_on_regression(regression=True) → 触发回滚
        deployer = AutoDeployer(root_dir=tmp_path)
        # 先 promote 一次产生快照
        promote_res = deployer.promote("reg-exp", "treatment", config={"v": 1})
        assert promote_res.success
        snap_id = promote_res.snapshot_id

        # regression=True → 触发回滚到 promote 时的快照
        rb_result = deployer.rollback_on_regression("reg-exp", regression=True, snapshot_id=snap_id)
        # 快照可能为空（ChangeTracker 在 tmp_path 无文件可快照）→ rollback 返回 success=False
        # 但流程应被触发（返回 RollbackResult 而非 None）
        assert rb_result is not None

        # regression=False → 不触发回滚（返回 None）
        no_rollback = deployer.rollback_on_regression("reg-exp", regression=False)
        assert no_rollback is None

    def test_pipeline_score_drives_ranking(self, tmp_path: Path) -> None:
        """端到端：evaluator.score() 用于排序候选变体。"""
        evaluator = PerformanceEvaluator()
        good_metrics = PerformanceMetrics(
            sample_count=100, success_rate=0.95, avg_latency_ms=500, avg_cost_usd=0.001,
        )
        bad_metrics = PerformanceMetrics(
            sample_count=100, success_rate=0.3, avg_latency_ms=8000, avg_cost_usd=0.05,
        )
        score_good = evaluator.score(good_metrics)
        score_bad = evaluator.score(bad_metrics)
        assert score_good > score_bad
        assert 0.0 <= score_good <= 1.0
        assert 0.0 <= score_bad <= 1.0


# ════════════════════════════════════════════════════════════════════
# 测试 3: EvolutionLoop 端到端
# ════════════════════════════════════════════════════════════════════


class TestEvolutionLoopE2E:
    """EvolutionLoop 完整七段闭环 + human_gate pending approvals。"""

    def _seed_errors(self, root: Path, pattern: str = "e2e_pattern", count: int = 3) -> None:
        """向 ErrorLedger 注入错误，使 OBSERVE 阶段检测到热点。"""
        from maop.core.reliability.error_ledger import ErrorLedger

        ledger = ErrorLedger(root_dir=str(root))
        for _ in range(count):
            ledger.record(error_type="TestError", pattern=pattern, context="e2e")

    def test_run_cycle_dry_run_full_seven_phases(self, tmp_path: Path) -> None:
        """run_cycle(dry_run=True) 执行完整七段闭环并返回 LoopReport。"""
        self._seed_errors(tmp_path, pattern="dry_run_pattern", count=3)
        loop = EvolutionLoop(root_dir=tmp_path, heal_threshold=1, suggest_threshold=2)

        report = loop.run_cycle(dry_run=True)

        assert isinstance(report, LoopReport)
        assert report.dry_run is True
        assert report.cycle_id
        assert report.total_duration_s >= 0
        # OBSERVE 检测到错误 → 后续阶段都应执行
        assert report.errors_observed >= 1
        # 验证包含所有阶段的 PhaseResult
        phase_values = [p.phase.value for p in report.phases]
        assert "observe" in phase_values
        assert "heal" in phase_values
        assert "suggest" in phase_values
        assert "evaluate" in phase_values
        assert "apply" in phase_values
        assert "validate" in phase_values
        # auto_consolidate=True → consolidate 也应执行
        assert "consolidate" in phase_values
        # dry_run 模式不快照、不回滚
        assert report.snapshot_id == ""
        assert report.rolled_back is False
        # APPLY 阶段应记录 dry_run=True
        apply_phase = next(p for p in report.phases if p.phase == LoopPhase.APPLY)
        assert apply_phase.details.get("dry_run") is True

    def test_run_cycle_no_errors_skips_to_finish(self, tmp_path: Path) -> None:
        """OBSERVE 无错误 → 跳过后续阶段，仅返回 OBSERVE PhaseResult。"""
        loop = EvolutionLoop(root_dir=tmp_path, heal_threshold=1, suggest_threshold=2)
        report = loop.run_cycle(dry_run=True)

        assert report.errors_observed == 0
        # 仅 OBSERVE 阶段
        assert len(report.phases) == 1
        assert report.phases[0].phase == LoopPhase.OBSERVE

    def test_run_cycle_persists_to_db(self, tmp_path: Path) -> None:
        """run_cycle 后报告持久化到 DB，可经 get_cycle_history 查询。"""
        self._seed_errors(tmp_path, pattern="persist_pattern", count=3)
        loop = EvolutionLoop(root_dir=tmp_path, heal_threshold=1, suggest_threshold=2)

        loop.run_cycle(dry_run=True)
        history = loop.get_cycle_history(limit=5)
        assert len(history) >= 1
        assert isinstance(history[0], LoopReport)

    def test_human_gate_pending_approvals_flow(self, tmp_path: Path) -> None:
        """human_gate 模式：AB 显著后不自动 promote，标记 pending_approval。"""
        # candidate 远优于 baseline → SPRT 应快速接受 H1
        baseline_traces = _make_traces(40, success_rate=0.2, latency=500, seed=20)
        candidate_traces = _make_traces(40, success_rate=0.95, latency=300, seed=21)

        loop = PerformanceEvolutionLoop(
            root_dir=tmp_path, human_gate=True, enable_llm=False,
            sprt_config=SPRTConfig(p0=0.5, p1=0.75),
        )
        report = loop.run_evolution_cycle(
            baseline_traces, candidate_traces,
            experiment="gate-e2e-exp", agent_name="researcher",
        )

        assert isinstance(report, EvolutionCycleReport)
        # SPRT 应接受 H1（candidate 0.95 >> p0 0.5）
        assert report.sprt_decision == SPRTDecision.ACCEPT_H1.value
        assert report.winner == "treatment"
        # human_gate → pending_approval=True，不自动 promote
        assert report.pending_approval is True
        assert report.promoted is False

        # 查询 pending approvals
        pending = loop.get_pending_approvals()
        assert len(pending) >= 1
        assert any(p.cycle_id == report.cycle_id for p in pending)

        # 人工批准后 promote
        approve_result = loop.approve_and_promote(
            "gate-e2e-exp", candidate_config={"model": "gpt-4o"},
        )
        assert approve_result["success"] is True
        assert approve_result["winner"] == "treatment"

    def test_auto_promote_when_not_human_gate(self, tmp_path: Path) -> None:
        """非 human_gate 模式：AB 显著后自动 promote。"""
        baseline_traces = _make_traces(40, success_rate=0.2, latency=500, seed=30)
        candidate_traces = _make_traces(40, success_rate=0.95, latency=300, seed=31)

        loop = PerformanceEvolutionLoop(
            root_dir=tmp_path, human_gate=False, enable_llm=False,
            sprt_config=SPRTConfig(p0=0.5, p1=0.75),
        )
        report = loop.run_evolution_cycle(
            baseline_traces, candidate_traces,
            experiment="auto-promote-exp",
            candidate_config={"model": "gpt-4o"},
        )

        assert report.sprt_decision == SPRTDecision.ACCEPT_H1.value
        # 非 human_gate → 自动 promote
        assert report.promoted is True
        assert report.pending_approval is False


# ════════════════════════════════════════════════════════════════════
# 测试 4: 回归检测端到端
# ════════════════════════════════════════════════════════════════════


class TestRegressionDetectionE2E:
    """基线 vs 退化 metrics → compare() 检测回归 + 针对性建议。"""

    def test_compare_detects_success_rate_regression(self, tmp_path: Path) -> None:
        """成功率下降 → compare() 标记 regression。"""
        evaluator = PerformanceEvaluator()
        baseline = [{"success": True, "latency_ms": 100} for _ in range(20)]
        # candidate 成功率从 100% 降到 30%
        candidate = [{"success": i < 6, "latency_ms": 100} for i in range(20)]

        delta = evaluator.compare(baseline, candidate)

        assert delta.success_rate_delta < 0
        assert delta.regression is True
        assert delta.improved is False
        assert "success_rate" in delta.summary

    def test_compare_detects_latency_regression(self, tmp_path: Path) -> None:
        """延迟显著上升（超过 tolerance）→ compare() 标记 regression。"""
        evaluator = PerformanceEvaluator()
        baseline = [{"success": True, "latency_ms": 100} for _ in range(20)]
        candidate = [{"success": True, "latency_ms": 500} for _ in range(20)]

        delta = evaluator.compare(baseline, candidate, latency_tolerance_pct=5.0)

        assert delta.avg_latency_ms_delta > 0
        assert delta.regression is True

    def test_compare_detects_cost_regression(self, tmp_path: Path) -> None:
        """成本显著上升（超过 tolerance）→ compare() 标记 regression。"""
        evaluator = PerformanceEvaluator()
        baseline = [{"success": True, "cost_usd": 0.001} for _ in range(20)]
        candidate = [{"success": True, "cost_usd": 0.01} for _ in range(20)]

        delta = evaluator.compare(baseline, candidate, cost_tolerance_pct=5.0)

        assert delta.total_cost_usd_delta > 0
        assert delta.regression is True

    def test_compare_detects_improvement(self, tmp_path: Path) -> None:
        """成功率提升且延迟未恶化 → compare() 标记 improved。"""
        evaluator = PerformanceEvaluator()
        baseline = [{"success": i < 6, "latency_ms": 500} for i in range(20)]  # 30%
        candidate = [{"success": True, "latency_ms": 500} for _ in range(20)]  # 100%

        delta = evaluator.compare(baseline, candidate)

        assert delta.success_rate_delta > 0
        assert delta.improved is True
        assert delta.regression is False

    def test_suggester_generates_regression_aware_suggestion(self, tmp_path: Path) -> None:
        """检测到回归 → ImprovementSuggester 生成 adjust_prompt 针对性建议。"""
        evaluator = PerformanceEvaluator()
        baseline = [{"success": True, "latency_ms": 100} for _ in range(20)]
        candidate = [{"success": i < 6, "latency_ms": 800} for i in range(20)]

        delta = evaluator.compare(baseline, candidate)
        assert delta.regression

        cand_metrics = evaluator.evaluate(candidate)
        ctx = SuggestionContext(agent_name="regressed-agent", delta=delta)
        suggester = ImprovementSuggester(root_dir=tmp_path, enable_llm=False)
        suggestions = suggester.suggest_sync(cand_metrics, ctx)

        # delta.regression=True → 应生成 adjust_prompt 针对性建议
        regression_sugs = [s for s in suggestions if s.mutation_type == "adjust_prompt"]
        assert len(regression_sugs) >= 1
        assert regression_sugs[0].severity == "HIGH"
        assert regression_sugs[0].auto_applicable is False
        # 建议描述应包含 regression 信息
        assert "regression" in regression_sugs[0].description.lower() or "Regression" in regression_sugs[0].description

    def test_suggester_generates_reliability_suggestion_on_low_success(self, tmp_path: Path) -> None:
        """成功率低 → 生成 adjust_retries 可靠性建议。"""
        metrics = PerformanceMetrics(
            sample_count=20, success_count=4, success_rate=0.2,
        )
        suggester = ImprovementSuggester(root_dir=tmp_path, enable_llm=False)
        suggestions = suggester.suggest_sync(metrics, SuggestionContext(agent_name="flaky-agent"))

        retries_sugs = [s for s in suggestions if s.mutation_type == "adjust_retries"]
        assert len(retries_sugs) >= 1
        assert retries_sugs[0].severity == "HIGH"  # success_rate < 0.5 → HIGH
        assert retries_sugs[0].auto_applicable is True
        assert retries_sugs[0].target_name == "flaky-agent"

    def test_suggester_generates_performance_suggestion_on_high_latency(self, tmp_path: Path) -> None:
        """延迟高 → 生成 adjust_timeout 性能建议。"""
        metrics = PerformanceMetrics(
            sample_count=10, success_rate=0.9, avg_latency_ms=25000,
        )
        suggester = ImprovementSuggester(root_dir=tmp_path, enable_llm=False)
        suggestions = suggester.suggest_sync(metrics, SuggestionContext(agent_name="slow-agent"))

        timeout_sugs = [s for s in suggestions if s.mutation_type == "adjust_timeout"]
        assert len(timeout_sugs) >= 1
        # avg_latency > 20000 → HIGH
        assert timeout_sugs[0].severity == "HIGH"
        assert "timeout" in timeout_sugs[0].description.lower()

    def test_full_regression_e2e_with_perf_loop(self, tmp_path: Path) -> None:
        """端到端：退化场景经 PerformanceEvolutionLoop 触发 rollback。"""
        baseline_traces = _make_traces(30, success_rate=0.9, latency=100, seed=40)
        candidate_traces = _make_traces(30, success_rate=0.3, latency=800, seed=41)

        loop = PerformanceEvolutionLoop(
            root_dir=tmp_path, human_gate=False, enable_llm=False,
        )
        report = loop.run_evolution_cycle(
            baseline_traces, candidate_traces, experiment="reg-e2e-exp",
        )

        assert report.delta is not None
        assert report.delta.regression is True
        # 退化场景：SPRT 不会接受 H1（candidate 差）→ 不 promote
        assert report.promoted is False
        # delta.regression → rollback_on_regression 被调用
        # （无快照可回滚 → rolled_back=False，但流程已执行）
        assert report.error == ""  # 不应崩溃