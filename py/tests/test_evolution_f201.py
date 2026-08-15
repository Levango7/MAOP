"""F2-01 Agent 自演化闭环 — 测试套件。

覆盖：
  - PerformanceEvaluator: 指标计算 / 对比 / 评分
  - ImprovementSuggester: 规则回退路径（LLM 路径在无 provider 时自动回退）
  - ABTestFramework + SPRT: 序贯检验边界 / 显著性判定
  - AutoDeployer: 提升 / 回滚 / 历史查询
  - PerformanceEvolutionLoop: 闭环串联 / 人工 gate
"""

from __future__ import annotations

import pytest

from maop.core.ab_test import (
    ABTestFramework,
    SPRTConfig,
    SPRTDecision,
    SPRTTest,
)
from maop.core.evolution.auto_deployer import AutoDeployer
from maop.core.evolution.evaluator import (
    MetricDelta,
    PerformanceEvaluator,
    PerformanceMetrics,
)
from maop.core.evolution.suggester import ImprovementSuggester, SuggestionContext
from maop.core.evolution_loop import (
    EvolutionCycleReport,
    PerformanceEvolutionLoop,
)

# ════════════════════════════════════════════════════════════════════
# PerformanceEvaluator
# ════════════════════════════════════════════════════════════════════


class TestPerformanceEvaluator:
    def test_empty_traces(self) -> None:
        m = PerformanceEvaluator().evaluate([])
        assert m.sample_count == 0
        assert m.success_rate == 0.0

    def test_basic_metrics(self) -> None:
        traces = [
            {"success": True, "latency_ms": 100, "cost_usd": 0.001, "tokens": 50},
            {"success": True, "latency_ms": 200, "cost_usd": 0.002, "tokens": 60},
            {"success": False, "latency_ms": 300, "cost_usd": 0.003, "tokens": 70},
        ]
        m = PerformanceEvaluator().evaluate(traces)
        assert m.sample_count == 3
        assert m.success_count == 2
        assert m.failure_count == 1
        assert m.success_rate == pytest.approx(2 / 3, abs=0.01)
        assert m.avg_latency_ms == pytest.approx(200.0)
        assert m.total_cost_usd == pytest.approx(0.006, abs=0.001)
        assert m.total_tokens == 180

    def test_percentiles(self) -> None:
        traces = [{"success": True, "latency_ms": v} for v in range(1, 101)]
        m = PerformanceEvaluator().evaluate(traces)
        assert m.p50_latency_ms == pytest.approx(50.5, abs=1)
        assert m.p95_latency_ms == pytest.approx(95.05, abs=1)
        assert m.p99_latency_ms == pytest.approx(99.01, abs=1)
        assert m.max_latency_ms == 100.0

    def test_duration_ms_fallback(self) -> None:
        traces = [{"success": True, "duration_ms": 500}]
        m = PerformanceEvaluator().evaluate(traces)
        assert m.avg_latency_ms == 500.0

    def test_by_agent_grouping(self) -> None:
        traces = [
            {"success": True, "agent": "a"},
            {"success": True, "agent": "a"},
            {"success": False, "agent": "b"},
        ]
        m = PerformanceEvaluator().evaluate(traces)
        assert m.by_agent["a"]["success_rate"] == 1.0
        assert m.by_agent["b"]["success_rate"] == 0.0
        assert m.by_agent["a"]["count"] == 2

    def test_compare_improvement(self) -> None:
        baseline = [{"success": False}, {"success": False}, {"success": True}, {"success": True}]
        candidate = [{"success": True}] * 4
        delta = PerformanceEvaluator().compare(baseline, candidate)
        assert delta.success_rate_delta > 0
        assert delta.improved is True
        assert delta.regression is False

    def test_compare_regression(self) -> None:
        baseline = [{"success": True, "latency_ms": 100}] * 10
        candidate = [{"success": True, "latency_ms": 500}] * 10
        delta = PerformanceEvaluator().compare(baseline, candidate)
        assert delta.avg_latency_ms_delta > 0
        assert delta.regression is True

    def test_score_in_range(self) -> None:
        m = PerformanceMetrics(success_rate=0.9, avg_latency_ms=1000, avg_cost_usd=0.005)
        score = PerformanceEvaluator().score(m)
        assert 0.0 <= score <= 1.0

    def test_score_monotonic_success_rate(self) -> None:
        ev = PerformanceEvaluator()
        low = ev.score(PerformanceMetrics(success_rate=0.2, avg_latency_ms=1000, avg_cost_usd=0.005))
        high = ev.score(PerformanceMetrics(success_rate=0.95, avg_latency_ms=1000, avg_cost_usd=0.005))
        assert high > low


# ════════════════════════════════════════════════════════════════════
# ImprovementSuggester
# ════════════════════════════════════════════════════════════════════


class TestImprovementSuggester:
    def test_rule_based_low_success(self, tmp_path) -> None:
        metrics = PerformanceMetrics(sample_count=10, success_count=3, success_rate=0.3)
        suggester = ImprovementSuggester(root_dir=tmp_path, enable_llm=False)
        suggestions = suggester.suggest_sync(metrics, SuggestionContext(agent_name="agent-x"))
        types = [s.mutation_type for s in suggestions]
        assert "adjust_retries" in types
        assert any(s.target_name == "agent-x" for s in suggestions)

    def test_rule_based_high_latency(self, tmp_path) -> None:
        metrics = PerformanceMetrics(sample_count=5, success_rate=1.0, avg_latency_ms=8000)
        suggester = ImprovementSuggester(root_dir=tmp_path, enable_llm=False)
        suggestions = suggester.suggest_sync(metrics)
        types = [s.mutation_type for s in suggestions]
        assert "adjust_timeout" in types

    def test_rule_based_high_cost(self, tmp_path) -> None:
        metrics = PerformanceMetrics(sample_count=5, success_rate=1.0, avg_cost_usd=0.05)
        suggester = ImprovementSuggester(root_dir=tmp_path, enable_llm=False)
        suggestions = suggester.suggest_sync(metrics)
        types = [s.mutation_type for s in suggestions]
        assert "switch_model" in types

    def test_rule_based_no_suggestions_when_healthy(self, tmp_path) -> None:
        metrics = PerformanceMetrics(
            sample_count=10, success_rate=0.95, avg_latency_ms=500, avg_cost_usd=0.001,
        )
        suggester = ImprovementSuggester(root_dir=tmp_path, enable_llm=False)
        suggestions = suggester.suggest_sync(metrics)
        assert suggestions == []

    def test_llm_disabled_falls_back_to_rules(self, tmp_path) -> None:
        metrics = PerformanceMetrics(sample_count=5, success_rate=0.3)
        suggester = ImprovementSuggester(root_dir=tmp_path, enable_llm=False)
        suggestions = suggester.suggest_sync(metrics)
        # 应该有规则生成的建议
        assert len(suggestions) >= 1
        assert all(s.source == "rule" for s in suggestions)

    def test_regression_delta_triggers_suggestion(self, tmp_path) -> None:
        metrics = PerformanceMetrics(sample_count=10, success_rate=0.5)
        delta = MetricDelta(regression=True, summary="regression detected")
        ctx = SuggestionContext(agent_name="a", delta=delta)
        suggester = ImprovementSuggester(root_dir=tmp_path, enable_llm=False)
        suggestions = suggester.suggest_sync(metrics, ctx)
        assert any(s.mutation_type == "adjust_prompt" for s in suggestions)


# ════════════════════════════════════════════════════════════════════
# SPRT + ABTestFramework
# ════════════════════════════════════════════════════════════════════


class TestSPRT:
    def test_config_validation(self) -> None:
        with pytest.raises(ValueError):
            SPRTConfig(p0=0.7, p1=0.5)  # p1 must be > p0
        with pytest.raises(ValueError):
            SPRTConfig(alpha=0.0)
        with pytest.raises(ValueError):
            SPRTConfig(beta=1.5)

    def test_initial_state(self) -> None:
        test = SPRTTest(SPRTConfig(), treatment_name="treatment")
        assert test.state.decision == SPRTDecision.CONTINUE
        assert test.state.samples == 0
        assert not test.state.is_stopped

    def test_accept_h1_on_strong_improvement(self) -> None:
        # p1=0.7, 大量成功样本应快速接受 H1
        cfg = SPRTConfig(p0=0.5, p1=0.7, alpha=0.05, beta=0.2)
        test = SPRTTest(cfg, treatment_name="treatment")
        for _ in range(500):
            if test.state.is_stopped:
                break
            test.update(True)
        assert test.state.decision == SPRTDecision.ACCEPT_H1
        assert test.state.winner == "treatment"

    def test_accept_h0_on_no_improvement(self) -> None:
        # p0=0.5, 样本成功率约 0.5 → 应接受 H0
        cfg = SPRTConfig(p0=0.5, p1=0.7, alpha=0.05, beta=0.2)
        test = SPRTTest(cfg, treatment_name="treatment")
        for i in range(2000):
            if test.state.is_stopped:
                break
            test.update(i % 2 == 0)  # 交替 success/failure → ~0.5
        assert test.state.decision == SPRTDecision.ACCEPT_H0

    def test_llr_monotonic_with_successes(self) -> None:
        cfg = SPRTConfig(p0=0.5, p1=0.7)
        test = SPRTTest(cfg)
        llr_before = test.state.llr
        test.update(True)
        assert test.state.llr > llr_before
        llr_mid = test.state.llr
        test.update(False)
        assert test.state.llr < llr_mid

    def test_reset(self) -> None:
        test = SPRTTest(SPRTConfig())
        test.update(True)
        test.update(True)
        assert test.state.samples == 2
        test.reset()
        assert test.state.samples == 0
        assert test.state.llr == 0.0

    def test_update_batch(self) -> None:
        test = SPRTTest(SPRTConfig())
        test.update_batch(successes=10, failures=2)
        assert test.state.samples == 12
        assert test.state.successes == 10

    def test_stopped_state_ignores_further_updates(self) -> None:
        cfg = SPRTConfig(p0=0.5, p1=0.7)
        test = SPRTTest(cfg)
        for _ in range(500):
            if test.state.is_stopped:
                break
            test.update(True)
        assert test.state.is_stopped
        samples_at_stop = test.state.samples
        test.update(True)
        assert test.state.samples == samples_at_stop  # 不再增长


class TestABTestFramework:
    def test_create_and_record(self, tmp_path) -> None:
        fw = ABTestFramework(root_dir=tmp_path)
        fw.create_experiment(name="exp1", variants={"control": 50, "treatment": 50})
        v = fw.assign("exp1", "user-1")
        assert v in ("control", "treatment")
        state = fw.record("exp1", "treatment", "user-1", success=True)
        assert state.samples >= 1

    def test_evaluate_sprt_significant(self, tmp_path) -> None:
        fw = ABTestFramework(root_dir=tmp_path)
        fw.create_experiment(
            name="exp2", variants={"control": 50, "treatment": 50},
            sprt_config=SPRTConfig(p0=0.4, p1=0.7),
        )
        # treatment 大量成功
        for i in range(200):
            fw.record("exp2", "treatment", f"u-{i}", success=True)
        # control 一些样本
        for i in range(50):
            fw.record("exp2", "control", f"c-{i}", success=(i % 2 == 0))
        result = fw.evaluate_sprt("exp2")
        assert result.is_significant
        assert result.winner == "treatment"

    def test_evaluate_sprt_continue(self, tmp_path) -> None:
        fw = ABTestFramework(root_dir=tmp_path)
        fw.create_experiment(name="exp3", variants={"control": 50, "treatment": 50})
        fw.record("exp3", "treatment", "u-1", success=True)
        result = fw.evaluate_sprt("exp3")
        # 单样本不应立即显著
        assert result.decision == SPRTDecision.CONTINUE or not result.is_significant

    def test_list_experiments(self, tmp_path) -> None:
        fw = ABTestFramework(root_dir=tmp_path)
        fw.create_experiment(name="a", variants={"control": 50, "treatment": 50})
        fw.create_experiment(name="b", variants={"control": 50, "treatment": 50})
        exps = fw.list_experiments()
        assert "a" in exps
        assert "b" in exps


# ════════════════════════════════════════════════════════════════════
# AutoDeployer
# ════════════════════════════════════════════════════════════════════


class TestAutoDeployer:
    def test_promote(self, tmp_path) -> None:
        deployer = AutoDeployer(root_dir=tmp_path)
        result = deployer.promote("exp1", "treatment", config={"model": "gpt-4o"})
        assert result.success
        assert result.winner == "treatment"
        assert result.deployment_id

    def test_promote_disabled(self, tmp_path) -> None:
        deployer = AutoDeployer(root_dir=tmp_path, enable_promote=False)
        result = deployer.promote("exp1", "treatment")
        assert not result.success

    def test_promote_empty_winner(self, tmp_path) -> None:
        deployer = AutoDeployer(root_dir=tmp_path)
        result = deployer.promote("exp1", "")
        assert not result.success

    def test_rollback_no_snapshot(self, tmp_path) -> None:
        deployer = AutoDeployer(root_dir=tmp_path)
        result = deployer.rollback("exp1")
        assert not result.success  # 无快照可回滚

    def test_rollback_disabled(self, tmp_path) -> None:
        deployer = AutoDeployer(root_dir=tmp_path, enable_rollback=False)
        result = deployer.rollback("exp1", snapshot_id="snap-1")
        assert not result.success

    def test_history(self, tmp_path) -> None:
        deployer = AutoDeployer(root_dir=tmp_path)
        deployer.promote("exp1", "treatment")
        deployer.promote("exp2", "treatment")
        history = deployer.get_history()
        assert len(history) == 2
        assert all(h.action == "promote" for h in history)

    def test_history_filtered_by_experiment(self, tmp_path) -> None:
        deployer = AutoDeployer(root_dir=tmp_path)
        deployer.promote("exp1", "treatment")
        deployer.promote("exp2", "treatment")
        history = deployer.get_history(experiment="exp1")
        assert len(history) == 1
        assert history[0].experiment == "exp1"

    def test_rollback_on_regression_no_op_when_no_regression(self, tmp_path) -> None:
        deployer = AutoDeployer(root_dir=tmp_path)
        result = deployer.rollback_on_regression("exp1", regression=False)
        assert result is None


# ════════════════════════════════════════════════════════════════════
# PerformanceEvolutionLoop
# ════════════════════════════════════════════════════════════════════


class TestPerformanceEvolutionLoop:
    def _make_traces(self, n: int, success_rate: float, latency: int = 500) -> list[dict]:
        import random

        rng = random.Random(42)
        return [
            {
                "success": rng.random() < success_rate,
                "latency_ms": latency + rng.randint(-100, 100),
                "cost_usd": 0.001,
                "tokens": 50,
                "entity_id": f"e-{i}",
            }
            for i in range(n)
        ]

    def test_cycle_basic(self, tmp_path) -> None:
        loop = PerformanceEvolutionLoop(root_dir=tmp_path, enable_llm=False)
        baseline = self._make_traces(30, 0.4)
        candidate = self._make_traces(30, 0.8)
        report = loop.run_evolution_cycle(
            baseline, candidate, experiment="test-exp", agent_name="agent-a",
        )
        assert isinstance(report, EvolutionCycleReport)
        assert report.experiment == "test-exp"
        assert report.baseline_metrics.sample_count == 30
        assert report.candidate_metrics.sample_count == 30
        assert report.duration_s >= 0

    def test_cycle_with_regression(self, tmp_path) -> None:
        loop = PerformanceEvolutionLoop(root_dir=tmp_path, enable_llm=False)
        baseline = self._make_traces(20, 0.9, latency=100)
        candidate = self._make_traces(20, 0.3, latency=800)
        report = loop.run_evolution_cycle(baseline, candidate, experiment="reg-exp")
        assert report.delta is not None
        # 成功率下降 → regression
        assert report.delta.regression

    def test_human_gate_pending(self, tmp_path) -> None:
        loop = PerformanceEvolutionLoop(root_dir=tmp_path, human_gate=True, enable_llm=False)
        # candidate 远优于 baseline → SPRT 应显著
        baseline = self._make_traces(40, 0.3)
        candidate = self._make_traces(40, 0.95)
        report = loop.run_evolution_cycle(baseline, candidate, experiment="gate-exp")
        # 若 SPRT 显著，human_gate 应阻止 promote 并标记 pending
        if report.sprt_decision == "accept_h1":
            assert report.pending_approval
            assert not report.promoted

    def test_cycle_history(self, tmp_path) -> None:
        loop = PerformanceEvolutionLoop(root_dir=tmp_path, enable_llm=False)
        baseline = self._make_traces(10, 0.5)
        candidate = self._make_traces(10, 0.6)
        loop.run_evolution_cycle(baseline, candidate, experiment="hist-exp")
        loop.run_evolution_cycle(baseline, candidate, experiment="hist-exp")
        history = loop.get_cycle_history(experiment="hist-exp")
        assert len(history) == 2
        assert all(h.experiment == "hist-exp" for h in history)

    def test_cycle_history_all(self, tmp_path) -> None:
        loop = PerformanceEvolutionLoop(root_dir=tmp_path, enable_llm=False)
        baseline = self._make_traces(5, 0.5)
        candidate = self._make_traces(5, 0.6)
        loop.run_evolution_cycle(baseline, candidate, experiment="a")
        loop.run_evolution_cycle(baseline, candidate, experiment="b")
        history = loop.get_cycle_history()
        assert len(history) == 2

    def test_approve_and_promote(self, tmp_path) -> None:
        loop = PerformanceEvolutionLoop(root_dir=tmp_path, human_gate=True, enable_llm=False)
        result = loop.approve_and_promote("exp-x", candidate_config={"model": "gpt-4o"})
        assert result["success"] is True
        assert result["winner"] == "treatment"

    def test_pending_approvals(self, tmp_path) -> None:
        loop = PerformanceEvolutionLoop(root_dir=tmp_path, human_gate=True, enable_llm=False)
        # 无 pending 时返回空
        pending = loop.get_pending_approvals()
        assert isinstance(pending, list)

    def test_cycle_error_handling(self, tmp_path) -> None:
        loop = PerformanceEvolutionLoop(root_dir=tmp_path, enable_llm=False)
        # 空 traces 不应崩溃
        report = loop.run_evolution_cycle([], [], experiment="empty-exp")
        assert report.error == ""  # 应优雅处理
        assert report.baseline_metrics.sample_count == 0