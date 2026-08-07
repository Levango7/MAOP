"""Tests for MAOP.maop_loop — Master orchestrator Plan→Execute→Verify."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from maop.maop_loop import LoopConfig, LoopResult, MaopLoop
from maop.maop_verify import VerifyResult


class TestLoopConfig:
    def test_default_values(self):
        from maop.maop_loop import LoopConfig
        lc = LoopConfig()
        assert lc.default_timeout_s == 120
        assert lc.max_retries == 1
        assert lc.enable_log_rotation is True
        assert lc.enable_semantic_analyze is True
        assert lc.enable_parallel is True
        assert lc.max_workers == 4
        assert lc.enable_load_balancer is True
        assert lc.enable_result_cache is True
        assert lc.result_cache_size == 128
        assert lc.enable_evolve is True
        assert lc.enable_dream is True

    def test_custom_values(self):
        from maop.maop_loop import LoopConfig
        lc = LoopConfig(max_workers=8, max_retries=5, default_timeout_s=300)
        assert lc.max_workers == 8
        assert lc.max_retries == 5
        assert lc.default_timeout_s == 300


class TestLoopResult:
    def test_default_values(self):
        from maop.maop_loop import LoopResult
        r = LoopResult(task="test task")
        assert r.task == "test task"
        assert r.trace_id == ""
        assert r.selected_agent == ""
        assert r.routing_key == ""
        assert r.feedback_cycles == 0
        assert r.total_duration_ms == 0
        assert r.success is False
        assert r.parallel_executed is False
        assert r.block_reason == ""

    def test_with_values(self):
        from maop.maop_loop import LoopResult
        r = LoopResult(
            task="deploy",
            trace_id="abc-123",
            selected_agent="claude",
            routing_key="chat",
            success=True,
            total_duration_ms=5000,
        )
        assert r.trace_id == "abc-123"
        assert r.selected_agent == "claude"
        assert r.success is True
        assert r.total_duration_ms == 5000


class TestRequirementAnalysis:
    def test_default_values(self):
        from maop.maop_loop import RequirementAnalysis
        ra = RequirementAnalysis(task="test")
        assert ra.objectives == []
        assert ra.boundaries == []
        assert ra.acceptance_criteria == []
        assert ra.assumptions == []
        assert ra.clarified_task == ""


class TestMaopLoopInit:
    """Test MaopLoop initialization with various configs."""

    def test_init_with_root_dir(self, tmp_path):
        from maop.maop_loop import MaopLoop
        # Create minimal config structure
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "agents.yaml").write_text("agents: {}\n")
        (config_dir / "models.yaml").write_text("models: {}\n")
        (tmp_path / "data").mkdir()

        loop = MaopLoop(root_dir=str(tmp_path))
        assert loop._root == tmp_path
        assert loop._loop_config is not None
        assert loop._breaker is not None
        assert loop._dispatcher is not None
        assert loop._guardrail is not None
        assert loop._verify_engine is not None
        assert loop._memory is not None

    def test_init_with_custom_loop_config(self, tmp_path):
        from maop.maop_loop import LoopConfig, MaopLoop
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "agents.yaml").write_text("agents: {}\n")
        (config_dir / "models.yaml").write_text("models: {}\n")
        (tmp_path / "data").mkdir()

        lc = LoopConfig(max_workers=2, enable_evolve=False)
        loop = MaopLoop(root_dir=str(tmp_path), loop_config=lc)
        assert loop._loop_config.max_workers == 2
        assert loop._loop_config.enable_evolve is False

    def test_init_disables_optional_subsystems(self, tmp_path):
        from maop.maop_loop import LoopConfig, MaopLoop
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "agents.yaml").write_text("agents: {}\n")
        (config_dir / "models.yaml").write_text("models: {}\n")
        (tmp_path / "data").mkdir()

        lc = LoopConfig(
            enable_parallel=False,
            enable_load_balancer=False,
            enable_result_cache=False,
            enable_metrics=False,
            enable_timeseries=False,
        )
        loop = MaopLoop(root_dir=str(tmp_path), loop_config=lc)
        assert loop._worker_pool is None
        assert loop._load_balancer is None
        assert loop._result_cache is None
        assert loop._metrics is None
        assert loop._timeseries is None


class TestMaopLoopRun:
    """Test MaopLoop.run() basic flow."""

    @pytest.mark.asyncio
    async def test_run_returns_loop_result(self, tmp_path):
        from maop.maop_loop import LoopConfig, LoopResult, MaopLoop
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "agents.yaml").write_text("agents: {}\n")
        (config_dir / "models.yaml").write_text("models: {}\n")
        (tmp_path / "data").mkdir()

        lc = LoopConfig(
            enable_semantic_analyze=False,
            enable_parallel=False,
            enable_load_balancer=False,
            enable_result_cache=False,
            enable_metrics=False,
            enable_timeseries=False,
            enable_evolve=False,
            enable_dream=False,
            enable_cache_guard=False,
            # Speed up test: fail fast on empty config (no real agent available)
            iterative_max_attempts=1,
            iterative_backoff_ms=0,
            retry_backoff_ms=0,
            default_timeout_s=5,
        )
        loop = MaopLoop(root_dir=str(tmp_path), loop_config=lc)

        # run() will fail at execution (no real agent), but should return LoopResult
        result = await loop.run(task="test task", workdir=str(tmp_path))
        assert isinstance(result, LoopResult)
        assert result.task == "test task"
        assert result.trace_id  # should have a generated trace_id
        assert result.total_duration_ms >= 0

    @pytest.mark.asyncio
    async def test_run_generates_trace_id(self, tmp_path):
        from maop.maop_loop import LoopConfig, MaopLoop
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "agents.yaml").write_text("agents: {}\n")
        (config_dir / "models.yaml").write_text("models: {}\n")
        (tmp_path / "data").mkdir()

        lc = LoopConfig(
            enable_semantic_analyze=False,
            enable_parallel=False,
            enable_load_balancer=False,
            enable_result_cache=False,
            enable_metrics=False,
            enable_timeseries=False,
            enable_evolve=False,
            enable_dream=False,
            enable_cache_guard=False,
            # Speed up test: fail fast on empty config (no real agent available)
            iterative_max_attempts=1,
            iterative_backoff_ms=0,
            retry_backoff_ms=0,
            default_timeout_s=5,
        )
        loop = MaopLoop(root_dir=str(tmp_path), loop_config=lc)

        r1 = await loop.run(task="task1", workdir=str(tmp_path))
        r2 = await loop.run(task="task2", workdir=str(tmp_path))
        assert r1.trace_id != r2.trace_id
        assert len(r1.trace_id) > 0
        assert len(r2.trace_id) > 0


class TestSimpleAnalyze:
    async def test_simple_analyze(self, tmp_path):
        # G1c (2026-07-22, Phase G): _simple_analyze is now async because it
        # delegates to the async loop_analyzer.simple_analyze (ADR-013 dual-path).
        from maop.maop_loop import MaopLoop
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "agents.yaml").write_text("agents: {}\n")
        (config_dir / "models.yaml").write_text("models: {}\n")
        (tmp_path / "data").mkdir()

        loop = MaopLoop(root_dir=str(tmp_path))
        result = await loop._simple_analyze("Fix a bug in the parser")
        assert result is not None
        assert hasattr(result, "objectives")
        assert len(result.objectives) > 0


class TestBudgetReconciliation:
    """Test that budget reconciliation uses real pricing from registry (P2-6)."""

    @pytest.mark.asyncio
    async def test_budget_uses_registry_pricing(self, tmp_path):
        """When model is in registry, provider and estimated_cost must be populated."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from maop.core.reliability.error_schema import MaopResult
        from maop.maop_loop import LoopConfig, MaopLoop

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "agents.yaml").write_text("agents: {}\n")
        (config_dir / "models.yaml").write_text("models: {}\n")
        (tmp_path / "data").mkdir()

        lc = LoopConfig(
            enable_semantic_analyze=False, enable_parallel=False,
            enable_load_balancer=False, enable_result_cache=False,
            enable_metrics=False, enable_timeseries=False,
            enable_evolve=False, enable_dream=False, enable_cache_guard=False,
        )
        loop = MaopLoop(root_dir=str(tmp_path), loop_config=lc)

        # Mock execution to return a result with a model
        mock_result = MaopResult(
            agent="claude", task="x" * 400,
            stdout="y" * 200, model="claude-3-sonnet",
            exit_code=0,
        )

        with patch.object(loop, '_execute_with_strategy', new_callable=AsyncMock, return_value=mock_result), \
             patch('maop.model.budget.BudgetGuard') as MockBG, \
             patch('maop.model.registry.ModelRegistry') as MockReg:

            mock_model_def = MagicMock()
            mock_model_def.provider = "anthropic"
            mock_model_def.cost_per_1k_input = 0.003
            mock_model_def.cost_per_1k_output = 0.015
            mock_registry = MagicMock()
            mock_registry.get_model.return_value = mock_model_def
            MockReg.return_value = mock_registry

            mock_bg = MagicMock()
            mock_bg.record_actual_cost.return_value = {}
            MockBG.return_value = mock_bg

            await loop.run(task="test task", workdir=str(tmp_path))

            assert mock_bg.record_actual_cost.called
            kwargs = mock_bg.record_actual_cost.call_args.kwargs
            assert kwargs['provider'] == "anthropic"
            assert kwargs['model'] == "claude-3-sonnet"
            assert kwargs['estimated_cost'] > 0.0
            # est_tokens_in = 400//4 = 100, est_tokens_out = 200//4 = 50
            # cost = 0.003*100/1000 + 0.015*50/1000 = 0.0003 + 0.00075 = 0.00105
            assert kwargs['estimated_cost'] == pytest.approx(0.00105)
            assert kwargs['actual_tokens_in'] == 100
            assert kwargs['actual_tokens_out'] == 50

    @pytest.mark.asyncio
    async def test_budget_fallback_when_model_not_in_registry(self, tmp_path):
        """When model is not in registry, provider="" and estimated_cost=0.0."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from maop.core.reliability.error_schema import MaopResult
        from maop.maop_loop import LoopConfig, MaopLoop

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "agents.yaml").write_text("agents: {}\n")
        (config_dir / "models.yaml").write_text("models: {}\n")
        (tmp_path / "data").mkdir()

        lc = LoopConfig(
            enable_semantic_analyze=False, enable_parallel=False,
            enable_load_balancer=False, enable_result_cache=False,
            enable_metrics=False, enable_timeseries=False,
            enable_evolve=False, enable_dream=False, enable_cache_guard=False,
        )
        loop = MaopLoop(root_dir=str(tmp_path), loop_config=lc)

        mock_result = MaopResult(
            agent="claude", task="x" * 400,
            stdout="y" * 200, model="unknown-model",
            exit_code=0,
        )

        with patch.object(loop, '_execute_with_strategy', new_callable=AsyncMock, return_value=mock_result), \
             patch('maop.model.budget.BudgetGuard') as MockBG, \
             patch('maop.model.registry.ModelRegistry') as MockReg:

            mock_registry = MagicMock()
            mock_registry.get_model.return_value = None  # model not found
            MockReg.return_value = mock_registry

            mock_bg = MagicMock()
            mock_bg.record_actual_cost.return_value = {}
            MockBG.return_value = mock_bg

            await loop.run(task="test task", workdir=str(tmp_path))

            assert mock_bg.record_actual_cost.called
            kwargs = mock_bg.record_actual_cost.call_args.kwargs
            assert kwargs['provider'] == ""
            assert kwargs['estimated_cost'] == 0.0


# ── Coverage tests (merged from test_maop_loop_coverage3.py) ──

# ── Init branches ───────────────────────────────────────────────────


class TestMaopLoopInitCoverage:
    def test_init_default_root(self, tmp_path):
        """Cover default root_dir branch (100-101)."""
        from maop.maop_loop import MaopLoop
        # When root_dir is None, find_project_root() is called
        loop = MaopLoop()
        assert loop is not None

    def test_init_config_loader_failure(self, tmp_path):
        """Cover ConfigLoader failure branch (109-111)."""
        from maop.maop_loop import MaopLoop
        with patch("maop.maop_loop.ConfigLoader") as MockLoader:
            MockLoader.return_value.load.side_effect = RuntimeError("cfg boom")
            loop = MaopLoop(root_dir=str(tmp_path))
        assert loop._config is None

    def test_init_with_explicit_config(self, tmp_path):
        """Cover branch where config is explicitly provided."""
        from maop.maop_loop import MaopLoop
        from maop.config.loader import MaopConfig
        cfg = MaopConfig()
        loop = MaopLoop(root_dir=str(tmp_path), config=cfg)
        assert loop._config is cfg

    def test_init_bridge_event_bus_failure(self, tmp_path):
        """Cover bridge_event_bus failure branch (135-137)."""
        from maop.maop_loop import MaopLoop
        loop = MaopLoop(root_dir=str(tmp_path))
        # If hook_mgr exists, bridge_event_bus may fail
        if loop._hook_mgr:
            with patch.object(loop._hook_mgr, "bridge_event_bus", side_effect=RuntimeError("bridge boom")):
                # Re-init to trigger the branch
                loop2 = MaopLoop(root_dir=str(tmp_path))
                assert loop2 is not None


# ── llm_factory property ────────────────────────────────────────────


class TestLlmFactory:
    def test_llm_factory_init_failure(self, tmp_path):
        """Cover LLMProviderFactory init failure (222-223)."""
        from maop.maop_loop import MaopLoop
        loop = MaopLoop(root_dir=str(tmp_path))
        with patch("maop.core.agent.llm_chat.llm_provider.LLMProviderFactory", side_effect=RuntimeError("llm boom")):
            factory = loop.llm_factory
        assert factory is None

    def test_llm_factory_cached(self, tmp_path):
        """Cover cached return on second access."""
        from maop.maop_loop import MaopLoop
        loop = MaopLoop(root_dir=str(tmp_path))
        # First access may succeed or fail; second access should return cached
        f1 = loop.llm_factory
        f2 = loop.llm_factory
        assert f1 is f2


# ─– _record_metric ─────────────────────────────────────────────────


class TestRecordMetric:
    def test_record_metric_no_timeseries(self, tmp_path):
        from maop.maop_loop import MaopLoop
        loop = MaopLoop(root_dir=str(tmp_path))
        # Should not raise even if timeseries is None
        loop._record_metric("test", 1.0)

    def test_record_metric_with_exception(self, tmp_path):
        from maop.maop_loop import MaopLoop
        loop = MaopLoop(root_dir=str(tmp_path))
        # Mock timeseries to raise
        loop._timeseries = MagicMock()
        loop._timeseries.record.side_effect = RuntimeError("ts boom")
        loop._record_metric("test", 1.0)  # should not raise


# ─– _inject_memory ─────────────────────────────────────────────────


class TestInjectMemory:
    def test_inject_no_memory(self, tmp_path):
        from maop.maop_loop import MaopLoop, PhaseContext
        loop = MaopLoop(root_dir=str(tmp_path))
        loop._memory = None
        ctx = PhaseContext(task="test", original_task="test", trace_id="t1")
        loop._inject_memory(ctx, "")  # should not raise

    def test_inject_with_memory_context(self, tmp_path):
        from maop.maop_loop import MaopLoop, PhaseContext
        loop = MaopLoop(root_dir=str(tmp_path))
        mock_mem = MagicMock()
        mock_mem.inject.return_value = "some context"
        mock_mem.trace.return_value = None
        loop._memory = mock_mem
        ctx = PhaseContext(task="test", original_task="test", trace_id="t1")
        loop._inject_memory(ctx, "")
        assert "some context" in ctx.task

    def test_inject_memory_search_fallback(self, tmp_path):
        """Cover branch where inject returns empty, search provides results."""
        from maop.maop_loop import MaopLoop, PhaseContext
        loop = MaopLoop(root_dir=str(tmp_path))
        mock_mem = MagicMock()
        mock_mem.inject.return_value = ""
        mock_result = MagicMock(agent="a", task="t", snippet="snippet text")
        mock_mem.search.return_value = [mock_result]
        mock_mem.trace.return_value = None
        loop._memory = mock_mem
        ctx = PhaseContext(task="test", original_task="test", trace_id="t1")
        loop._inject_memory(ctx, "")
        assert "Memory Context" in ctx.task

    def test_inject_memory_exception(self, tmp_path):
        """Cover memory injection exception (327-328)."""
        from maop.maop_loop import MaopLoop, PhaseContext
        loop = MaopLoop(root_dir=str(tmp_path))
        mock_mem = MagicMock()
        mock_mem.inject.side_effect = RuntimeError("inject boom")
        loop._memory = mock_mem
        ctx = PhaseContext(task="test", original_task="test", trace_id="t1")
        loop._inject_memory(ctx, "")  # should not raise

    def test_inject_trace_exception(self, tmp_path):
        """Cover trace recording exception (335-336)."""
        from maop.maop_loop import MaopLoop, PhaseContext
        loop = MaopLoop(root_dir=str(tmp_path))
        mock_mem = MagicMock()
        mock_mem.inject.return_value = ""
        mock_mem.trace.side_effect = RuntimeError("trace boom")
        loop._memory = mock_mem
        ctx = PhaseContext(task="test", original_task="test", trace_id="t1")
        loop._inject_memory(ctx, "")  # should not raise


# ─– run() with mocked phases ───────────────────────────────────────


class TestRunMocked:
    def _setup_loop(self, tmp_path):
        """Create a loop with _build_loop_result mocked to avoid validation errors."""
        from maop.maop_loop import MaopLoop
        loop = MaopLoop(root_dir=str(tmp_path))
        loop._inject_memory = MagicMock()
        # Mock _build_loop_result to return a simple result
        loop._build_loop_result = MagicMock(return_value=MagicMock())
        return loop

    @pytest.mark.asyncio
    async def test_run_with_pinned_agent(self, tmp_path):
        """Cover agent pinning branch (278-280)."""
        from maop.maop_loop import PhaseResult
        loop = self._setup_loop(tmp_path)
        skip_result = PhaseResult(skip_remaining=True)
        loop._phase_analyze = AsyncMock(return_value=skip_result)
        result = await loop.run("test task", agent="claude")
        assert result is not None

    @pytest.mark.asyncio
    async def test_run_skip_after_analyze(self, tmp_path):
        """Cover skip_remaining after analyze (289)."""
        from maop.maop_loop import PhaseResult
        loop = self._setup_loop(tmp_path)
        skip_result = PhaseResult(skip_remaining=True)
        loop._phase_analyze = AsyncMock(return_value=skip_result)
        result = await loop.run("test")
        assert result is not None

    @pytest.mark.asyncio
    async def test_run_skip_after_plan(self, tmp_path):
        """Cover skip_remaining after plan (293)."""
        from maop.maop_loop import PhaseResult
        loop = self._setup_loop(tmp_path)
        cont = PhaseResult(skip_remaining=False)
        skip = PhaseResult(skip_remaining=True)
        loop._phase_analyze = AsyncMock(return_value=cont)
        loop._phase_plan = AsyncMock(return_value=skip)
        result = await loop.run("test")
        assert result is not None

    @pytest.mark.asyncio
    async def test_run_skip_after_execute(self, tmp_path):
        """Cover skip_remaining after execute (297)."""
        from maop.maop_loop import PhaseResult
        loop = self._setup_loop(tmp_path)
        cont = PhaseResult(skip_remaining=False)
        skip = PhaseResult(skip_remaining=True)
        loop._phase_analyze = AsyncMock(return_value=cont)
        loop._phase_plan = AsyncMock(return_value=cont)
        loop._phase_execute = AsyncMock(return_value=skip)
        result = await loop.run("test")
        assert result is not None

    @pytest.mark.asyncio
    async def test_run_skip_after_verify(self, tmp_path):
        """Cover skip_remaining after verify (301)."""
        from maop.maop_loop import PhaseResult
        loop = self._setup_loop(tmp_path)
        cont = PhaseResult(skip_remaining=False)
        skip = PhaseResult(skip_remaining=True)
        loop._phase_analyze = AsyncMock(return_value=cont)
        loop._phase_plan = AsyncMock(return_value=cont)
        loop._phase_execute = AsyncMock(return_value=cont)
        loop._phase_verify = AsyncMock(return_value=skip)
        result = await loop.run("test")
        assert result is not None

    @pytest.mark.asyncio
    async def test_run_skip_after_feedback(self, tmp_path):
        """Cover skip_remaining after feedback (305)."""
        from maop.maop_loop import PhaseResult
        loop = self._setup_loop(tmp_path)
        cont = PhaseResult(skip_remaining=False)
        skip = PhaseResult(skip_remaining=True)
        loop._phase_analyze = AsyncMock(return_value=cont)
        loop._phase_plan = AsyncMock(return_value=cont)
        loop._phase_execute = AsyncMock(return_value=cont)
        loop._phase_verify = AsyncMock(return_value=cont)
        loop._phase_feedback = AsyncMock(return_value=skip)
        result = await loop.run("test")
        assert result is not None

    @pytest.mark.asyncio
    async def test_run_full_cycle(self, tmp_path):
        """Cover full cycle with all phases completing."""
        from maop.maop_loop import PhaseResult
        loop = self._setup_loop(tmp_path)
        cont = PhaseResult(skip_remaining=False)
        loop._phase_analyze = AsyncMock(return_value=cont)
        loop._phase_plan = AsyncMock(return_value=cont)
        loop._phase_execute = AsyncMock(return_value=cont)
        loop._phase_verify = AsyncMock(return_value=cont)
        loop._phase_feedback = AsyncMock(return_value=cont)
        loop._phase_evolve = AsyncMock()
        result = await loop.run("test")
        assert result is not None




# ── Extended tests (merged from test_maop_loop_extended.py) ──

def _make_loop(tmp_path: Path, **overrides) -> MaopLoop:
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "agents.yaml").write_text("agents: {}\n")
    (config_dir / "models.yaml").write_text("models: {}\n")
    (tmp_path / "data").mkdir(exist_ok=True)
    lc = LoopConfig(
        enable_semantic_analyze=False,
        enable_parallel=False,
        enable_load_balancer=False,
        enable_result_cache=False,
        enable_metrics=False,
        enable_timeseries=False,
        enable_evolve=False,
        enable_dream=False,
        enable_cache_guard=False,
        **overrides,
    )
    return MaopLoop(root_dir=str(tmp_path), loop_config=lc)


class TestSimpleAnalyzeExtended:
    async def test_multiline_objectives(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = await loop._simple_analyze("目标: Fix bug\n目标: Add tests")
        assert len(result.objectives) == 2

    async def test_chinese_sections(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = await loop._simple_analyze("边界: No external APIs\n风险: Data loss")
        assert len(result.boundaries) == 1
        assert len(result.risks) == 1

    async def test_continuation_lines(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = await loop._simple_analyze("objective: Main goal\n  sub-point A\n  sub-point B")
        assert len(result.objectives) >= 2

    async def test_default_objectives_when_empty(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = await loop._simple_analyze("just a plain task")
        assert len(result.objectives) == 1
        assert result.objectives[0] == "just a plain task"

    async def test_default_acceptance_criteria(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = await loop._simple_analyze("simple task")
        assert len(result.acceptance_criteria) == 1
        assert "Task completes" in result.acceptance_criteria[0]


# ── t18: simple_analyze semantic analysis tests ─────────────

class TestSimpleAnalyzeSemantic:
    """t18 (2026-07-21) — simple_analyze now performs semantic analysis:
    detects action verbs (bilingual), tech-stack keywords (bilingual),
    and estimates complexity (simple / moderate / complex).
    """

    async def test_detects_english_action_verbs(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = await loop._simple_analyze(
            "Implement a new API endpoint, then add tests, and fix the bug."
        )
        assert "implement" in result.action_verbs
        assert "add" in result.action_verbs
        assert "fix" in result.action_verbs
        assert "test" in result.action_verbs

    async def test_detects_chinese_action_verbs(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = await loop._simple_analyze("实现一个新接口，然后添加测试，并修复 bug。")
        assert "实现" in result.action_verbs
        assert "添加" in result.action_verbs
        assert "修复" in result.action_verbs

    async def test_detects_tech_stack_english(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = await loop._simple_analyze(
            "Build a REST API with a postgres database backend, "
            "add JWT auth and a WebSocket channel."
        )
        assert "api" in result.tech_stack
        assert "rest" in result.tech_stack
        assert "postgres" in result.tech_stack
        assert "jwt" in result.tech_stack
        assert "websocket" in result.tech_stack

    async def test_detects_tech_stack_chinese(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = await loop._simple_analyze("实现接口和数据库，配置认证")
        assert "接口" in result.tech_stack
        assert "数据库" in result.tech_stack
        assert "认证" in result.tech_stack
        assert "配置" in result.tech_stack

    async def test_complexity_simple_for_short_task(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = await loop._simple_analyze("fix typo")
        assert result.complexity == "simple"

    async def test_complexity_moderate_for_medium_task(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = await loop._simple_analyze(
            "Implement the API and add database integration, then deploy to docker. "
            "After that, write tests and configure CI."
        )
        assert result.complexity in ("moderate", "complex")

    async def test_complexity_complex_for_long_multistep_task(self, tmp_path):
        loop = _make_loop(tmp_path)
        long_task = (
            "Implement a REST API with a postgres database backend, "
            "then add JWT auth, a WebSocket channel, and a redis cache. "
            "After that, migrate the existing sqlite data, integrate the "
            "graphql endpoint, refactor the frontend, update the CLI, "
            "then deploy to kubernetes and write comprehensive tests, "
            "document the new config schema, and audit the security."
        )
        result = await loop._simple_analyze(long_task)
        assert result.complexity == "complex"

    async def test_semantic_fields_default_empty_when_no_matches(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = await loop._simple_analyze("just a plain task with no keywords")
        assert result.action_verbs == []
        assert result.tech_stack == []
        assert result.complexity == "simple"

    async def test_existing_fields_unchanged_with_semantics(self, tmp_path):
        """Sanity: existing parsing behavior unaffected by semantic additions."""
        loop = _make_loop(tmp_path)
        result = await loop._simple_analyze("目标: Implement API\n边界: No external calls")
        assert len(result.objectives) == 1
        assert "Implement API" in result.objectives[0]
        assert len(result.boundaries) == 1
        # semantics should also be populated
        assert "implement" in result.action_verbs
        assert "api" in result.tech_stack

    async def test_word_boundary_avoids_false_positive_substrings(self, tmp_path):
        """English verbs/tech keywords matched with word boundaries so
        substrings like 'fix' inside 'suffix' do not trigger a match."""
        loop = _make_loop(tmp_path)
        result = await loop._simple_analyze("The suffix of the word is interesting.")
        assert "fix" not in result.action_verbs


class TestFeedbackLoop:
    @pytest.mark.asyncio
    async def test_blocked_state_stops_loop(self, tmp_path):
        loop = _make_loop(tmp_path)

        mock_verify = VerifyResult(passed=False, summary="Blocked")
        mock_verify.state = "blocked"
        mock_verify.block_reason = "User input needed"

        with patch.object(loop, '_plan', new_callable=AsyncMock) as mock_plan, \
             patch.object(loop, '_execute_with_strategy', new_callable=AsyncMock) as mock_exec, \
             patch.object(loop, '_verify', new_callable=AsyncMock, return_value=mock_verify):
            mock_plan.return_value = {"selected_agent": "claude", "routing_key": "chat", "budget": {"timeout_s": 30}}

            from maop.core.reliability.error_schema import new_result
            mock_exec.return_value = new_result(agent="claude", task="test", exit_code=0, stdout="ok")

            result = await loop.run(task="test task", workdir=str(tmp_path))
            assert result.block_reason == "User input needed"
            assert result.feedback_cycles == 0

    @pytest.mark.asyncio
    async def test_failed_state_stops_loop(self, tmp_path):
        loop = _make_loop(tmp_path, feedback_max_cycles=3)

        mock_verify = VerifyResult(passed=False, summary="Structural failure")
        mock_verify.state = "failed"

        with patch.object(loop, '_plan', new_callable=AsyncMock) as mock_plan, \
             patch.object(loop, '_execute_with_strategy', new_callable=AsyncMock) as mock_exec, \
             patch.object(loop, '_verify', new_callable=AsyncMock, return_value=mock_verify):
            mock_plan.return_value = {"selected_agent": "claude", "routing_key": "chat", "budget": {"timeout_s": 30}}
            from maop.core.reliability.error_schema import new_result
            mock_exec.return_value = new_result(agent="claude", task="test", exit_code=1, error="fail")

            result = await loop.run(task="test task", workdir=str(tmp_path))
            assert result.feedback_cycles == 0
            assert result.success is False

    @pytest.mark.asyncio
    async def test_working_state_retries(self, tmp_path):
        loop = _make_loop(tmp_path, feedback_max_cycles=2)

        verify_working = VerifyResult(passed=False, summary="Needs retry")
        verify_working.state = "working"

        verify_done = VerifyResult(passed=True, summary="OK")
        verify_done.state = "done"

        with patch.object(loop, '_plan', new_callable=AsyncMock) as mock_plan, \
             patch.object(loop, '_execute_with_retry', new_callable=AsyncMock) as mock_exec, \
             patch.object(loop, '_verify', new_callable=AsyncMock, side_effect=[verify_working, verify_done]):
            mock_plan.return_value = {"selected_agent": "claude", "routing_key": "chat", "budget": {"timeout_s": 30}}
            from maop.core.reliability.error_schema import new_result
            mock_exec.return_value = new_result(agent="claude", task="test", exit_code=0, stdout="ok")

            result = await loop.run(task="test task", workdir=str(tmp_path))
            assert result.feedback_cycles == 1


class TestFallbackChain:
    def test_build_fallback_chain_no_config(self, tmp_path):
        loop = _make_loop(tmp_path)
        loop._config = None
        chain = loop._build_fallback_chain("claude", "chat")
        assert chain == ["claude"]

    def test_build_fallback_chain_with_routing(self, tmp_path):
        loop = _make_loop(tmp_path)
        mock_route = MagicMock()
        mock_route.primary = "claude"
        mock_route.fallback = "kimi"
        mock_route.tertiary = "codex"
        mock_config = MagicMock()
        mock_config.routing = {"chat": mock_route}
        loop._config = mock_config

        chain = loop._build_fallback_chain("claude", "chat")
        assert "claude" in chain
        assert "kimi" in chain
        assert "codex" in chain


class TestVerifyPhase:
    @pytest.mark.asyncio
    async def test_skip_verify_returns_none(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = await loop._verify({}, None, str(tmp_path), skip=True, trace_id="test")
        assert result is None

    @pytest.mark.asyncio
    async def test_verify_exception_returns_errored(self, tmp_path):
        loop = _make_loop(tmp_path)
        with patch.object(loop._verify_engine, 'verify', side_effect=RuntimeError("boom")):
            result = await loop._verify({}, None, str(tmp_path), skip=False, trace_id="test")
            assert result is not None
            assert result.passed is False
            assert result.errored is True
            assert "boom" in result.summary


class TestBuildLoopResultVerifyErrored:
    """C3 residual fix: when verify engine errors, _build_loop_result must NOT
    count it as a task failure (errored=True → verify_ok=True)."""

    def test_verify_errored_does_not_fail_task(self, tmp_path):
        from maop.core.reliability.error_schema import MaopResult
        from maop.core.agent.evolution.phases import PhaseContext

        loop = _make_loop(tmp_path)
        ctx = PhaseContext(
            original_task="test-task",
            plan_result={},
            execution_result=MaopResult(agent="a", task="t", exit_code=0),
            verify_result=VerifyResult(passed=False, errored=True, summary="engine error"),
            trace_id="test",
        )
        result = loop._build_loop_result(ctx, start=__import__("time").monotonic())
        assert result.success is True

    def test_verify_real_failure_fails_task(self, tmp_path):
        from maop.core.reliability.error_schema import MaopResult
        from maop.core.agent.evolution.phases import PhaseContext

        loop = _make_loop(tmp_path)
        ctx = PhaseContext(
            original_task="test-task",
            plan_result={},
            execution_result=MaopResult(agent="a", task="t", exit_code=0),
            verify_result=VerifyResult(passed=False, errored=False, summary="real failure"),
            trace_id="test",
        )
        result = loop._build_loop_result(ctx, start=__import__("time").monotonic())
        assert result.success is False

    def test_no_verify_does_not_fail_task(self, tmp_path):
        from maop.core.reliability.error_schema import MaopResult
        from maop.core.agent.evolution.phases import PhaseContext

        loop = _make_loop(tmp_path)
        ctx = PhaseContext(
            original_task="test-task",
            plan_result={},
            execution_result=MaopResult(agent="a", task="t", exit_code=0),
            verify_result=None,
            trace_id="test",
        )
        result = loop._build_loop_result(ctx, start=__import__("time").monotonic())
        assert result.success is True


class TestLoopResultFields:
    def test_parallel_executed_default(self):
        r = LoopResult(task="t")
        assert r.parallel_executed is False

    def test_block_reason_default(self):
        r = LoopResult(task="t")
        assert r.block_reason == ""

    def test_analysis_default(self):
        r = LoopResult(task="t")
        assert r.analysis == {}
