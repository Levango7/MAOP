"""Tests for MAOP.maop_loop — Master orchestrator Plan→Execute→Verify."""

from __future__ import annotations

import pytest


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

        from maop.core.error_schema import MaopResult
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

        from maop.core.error_schema import MaopResult
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
