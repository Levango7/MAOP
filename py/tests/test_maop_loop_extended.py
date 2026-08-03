"""Extended tests for MAOP.maop_loop — feedback loop, guardrail integration, verify states.

G1c (2026-07-22, Phase G): ``MaopLoop._simple_analyze`` is now ``async``
because it delegates to the async ``loop_analyzer.simple_analyze`` (which
itself is async per ADR-013 dual-path). All tests that call
``loop._simple_analyze(...)`` are declared ``async def`` and use ``await``.
pytest-asyncio with ``asyncio_mode = "auto"`` (pyproject.toml) detects
and runs them without explicit ``@pytest.mark.asyncio`` decorators.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maop.maop_loop import LoopConfig, LoopResult, MaopLoop
from maop.maop_verify import VerifyResult


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

            from maop.core.error_schema import new_result
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
            from maop.core.error_schema import new_result
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
            from maop.core.error_schema import new_result
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
        from maop.core.error_schema import MaopResult
        from maop.core.phases import PhaseContext

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
        from maop.core.error_schema import MaopResult
        from maop.core.phases import PhaseContext

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
        from maop.core.error_schema import MaopResult
        from maop.core.phases import PhaseContext

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
