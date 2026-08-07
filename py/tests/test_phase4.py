"""Tests for Phase 4: MAOP-loop, MAOP-plan, MAOP-execute, MAOP-verify, engine."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from maop.core.reliability.error_schema import new_result
from maop.core.security.permission import PermissionCheck
from maop.engine import (
    Engine,
    StepStatus,
    StepType,
    WorkflowStep,
    _resolve_template,
    _topological_sort,
)
from maop.maop_execute import Delegate, maop_execute
from maop.maop_loop import LoopConfig, LoopResult, MaopLoop
from maop.maop_plan import Plan, _route_by_keyword, maop_plan
from maop.maop_verify import GateResult, VerifyEngine

# ═══════════════════════════════════════════════════════════════
# maop_plan tests
# ═══════════════════════════════════════════════════════════════

class TestPlanRouting:
    """Test keyword-based routing."""

    def test_code_keywords(self):
        rk, agent = _route_by_keyword("refactor the main module")
        assert rk == "code"
        assert agent == "codex"

    def test_test_keywords(self):
        rk, agent = _route_by_keyword("write unit tests for auth")
        assert rk == "test"
        assert agent == "codex"

    def test_debug_keywords(self):
        rk, agent = _route_by_keyword("fix the TypeError exception")
        assert rk == "debug"
        assert agent == "codex"

    def test_docs_keywords(self):
        rk, agent = _route_by_keyword("document the API endpoints")
        assert rk == "docs"
        assert agent == "claude"

    def test_design_keywords(self):
        rk, agent = _route_by_keyword("design the architecture for microservices")
        assert rk == "design"
        assert agent == "claude"

    def test_security_keywords(self):
        rk, agent = _route_by_keyword("security audit for authentication")
        assert rk == "security"
        assert agent == "codex"

    def test_deploy_keywords(self):
        rk, agent = _route_by_keyword("deploy to production")
        assert rk == "deploy"
        assert agent == "codex"

    def test_default_chat(self):
        rk, agent = _route_by_keyword("hello how are you")
        assert rk == "chat"
        assert agent == "claude"

    def test_maop_plan_returns_plan(self):
        plan = maop_plan(task="fix the bug in parser")
        assert isinstance(plan, Plan)
        assert plan.selected_agent == "codex"
        assert plan.routing_key == "debug"

    def test_maop_plan_with_routing_key_override(self):
        plan = maop_plan(task="anything", routing_key="deploy")
        assert plan.routing_key == "deploy"

    def test_maop_plan_security_gates(self):
        plan = maop_plan(task="security audit for login")
        assert "content-safety" in plan.gates

    def test_maop_plan_deploy_gates(self):
        plan = maop_plan(task="deploy the service")
        assert "dry-run" in plan.gates


# ═══════════════════════════════════════════════════════════════
# maop_verify tests
# ═══════════════════════════════════════════════════════════════

class TestVerify:
    """Test verification engine."""

    def test_exit_code_pass(self):
        result = new_result(agent="a", task="t", exit_code=0, stdout="ok")
        engine = VerifyEngine()
        vr = engine.verify(plan={"gates": ["exit_code"]}, result=result)
        assert vr.passed

    def test_exit_code_fail(self):
        result = new_result(agent="a", task="t", exit_code=1, stdout="err")
        engine = VerifyEngine()
        vr = engine.verify(plan={"gates": ["exit_code"]}, result=result)
        assert not vr.passed

    def test_output_pass(self):
        result = new_result(agent="a", task="t", exit_code=0, stdout="hello world")
        engine = VerifyEngine()
        vr = engine.verify(plan={"gates": ["output"]}, result=result)
        assert vr.passed

    def test_output_fail_empty(self):
        result = new_result(agent="a", task="t", exit_code=0, stdout="")
        engine = VerifyEngine()
        vr = engine.verify(plan={"gates": ["output"]}, result=result)
        assert not vr.passed

    def test_content_safety_pass(self):
        result = new_result(agent="a", task="t", exit_code=0, stdout="normal output")
        engine = VerifyEngine()
        vr = engine.verify(plan={"gates": ["content-safety"]}, result=result)
        assert vr.passed

    def test_content_safety_detects_secret(self):
        result = new_result(
            agent="a", task="t", exit_code=0,
            stdout='api_key = "sk-abcdefghijklmnopqrstuvwxyz123456"',
        )
        engine = VerifyEngine()
        vr = engine.verify(plan={"gates": ["content-safety"]}, result=result)
        assert not vr.passed

    def test_syntax_check_pass(self):
        result = new_result(agent="a", task="t", exit_code=0, stdout="def foo(): pass")
        engine = VerifyEngine()
        vr = engine.verify(plan={"gates": ["syntax-check"]}, result=result)
        assert vr.passed

    def test_syntax_check_detects_error(self):
        result = new_result(
            agent="a", task="t", exit_code=0,
            stdout="SyntaxError: invalid syntax",
        )
        engine = VerifyEngine()
        vr = engine.verify(plan={"gates": ["syntax-check"]}, result=result)
        assert not vr.passed

    def test_unknown_gate(self):
        result = new_result(agent="a", task="t", exit_code=0, stdout="ok")
        engine = VerifyEngine()
        vr = engine.verify(plan={"gates": ["nonexistent-gate"]}, result=result)
        assert not vr.passed  # Unknown gate fails

    def test_multiple_gates(self):
        result = new_result(agent="a", task="t", exit_code=0, stdout="ok output")
        engine = VerifyEngine()
        vr = engine.verify(plan={"gates": ["exit_code", "output"]}, result=result)
        assert vr.passed
        assert len(vr.gates) == 2

    def test_verify_none_result(self):
        engine = VerifyEngine()
        vr = engine.verify(plan={"gates": ["exit_code"]}, result=None)
        assert not vr.passed

    def test_custom_gate(self):
        def my_gate(plan, result):
            return GateResult(name="my-gate", passed=True, reason="ok")

        engine = VerifyEngine(custom_gates={"my-gate": my_gate})
        result = new_result(agent="a", task="t", exit_code=0, stdout="ok")
        vr = engine.verify(plan={"gates": ["my-gate"]}, result=result)
        assert vr.passed

    def test_feedback_on_failure(self):
        result = new_result(agent="a", task="t", exit_code=1, stdout="")
        engine = VerifyEngine()
        vr = engine.verify(plan={"gates": ["exit_code", "output"]}, result=result)
        assert not vr.passed
        assert vr.feedback  # Non-empty feedback


# ═══════════════════════════════════════════════════════════════
# engine (DAG) tests
# ═══════════════════════════════════════════════════════════════

class TestTopologicalSort:
    """Test topological sort for DAG layers."""

    def test_linear_chain(self):
        steps = [
            WorkflowStep(id="s1"),
            WorkflowStep(id="s2", depends_on=["s1"]),
            WorkflowStep(id="s3", depends_on=["s2"]),
        ]
        layers = _topological_sort(steps)
        assert len(layers) == 3
        assert layers[0][0].id == "s1"
        assert layers[1][0].id == "s2"
        assert layers[2][0].id == "s3"

    def test_parallel_steps(self):
        steps = [
            WorkflowStep(id="s1"),
            WorkflowStep(id="s2"),
            WorkflowStep(id="s3", depends_on=["s1", "s2"]),
        ]
        layers = _topological_sort(steps)
        assert len(layers) == 2
        assert len(layers[0]) == 2  # s1 and s2 parallel
        assert layers[1][0].id == "s3"

    def test_diamond(self):
        steps = [
            WorkflowStep(id="s1"),
            WorkflowStep(id="s2", depends_on=["s1"]),
            WorkflowStep(id="s3", depends_on=["s1"]),
            WorkflowStep(id="s4", depends_on=["s2", "s3"]),
        ]
        layers = _topological_sort(steps)
        assert len(layers) == 3
        assert layers[0][0].id == "s1"
        assert len(layers[1]) == 2  # s2 and s3 parallel
        assert layers[2][0].id == "s4"


class TestResolveTemplate:
    """Test template variable resolution."""

    def test_simple(self):
        assert _resolve_template("Hello {{ name }}", {"name": "World"}) == "Hello World"

    def test_multiple(self):
        result = _resolve_template("{{ a }} and {{ b }}", {"a": "X", "b": "Y"})
        assert result == "X and Y"

    def test_no_match(self):
        assert _resolve_template("No vars", {"x": "y"}) == "No vars"

    def test_empty_template(self):
        assert _resolve_template("", {"x": "y"}) == ""


class TestEngine:
    """Test unified engine execution."""

    def test_single_agent_step(self):
        steps = [WorkflowStep(id="s1", type=StepType.AGENT, agent="claude", task="hello")]
        engine = Engine()
        result = asyncio.run(engine.run(steps, context={"task": "hello"}))
        assert result.success
        assert len(result.steps) == 1
        assert result.steps[0].status == StepStatus.SUCCESS

    def test_terminal_step(self):
        steps = [WorkflowStep(id="done", type=StepType.TERMINAL)]
        engine = Engine()
        result = asyncio.run(engine.run(steps, context={"key": "value"}))
        assert result.success
        assert result.steps[0].status == StepStatus.SUCCESS

    def test_verify_step_upstream_ok(self):
        steps = [
            WorkflowStep(id="s1", type=StepType.AGENT, agent="claude", task="do work"),
            WorkflowStep(id="v1", type=StepType.VERIFY, depends_on=["s1"]),
        ]
        engine = Engine()
        result = asyncio.run(engine.run(steps))
        assert result.success

    def test_three_step_pipeline(self):
        steps = [
            WorkflowStep(id="agent", type=StepType.AGENT, agent="claude", task="code"),
            WorkflowStep(id="verify", type=StepType.VERIFY, depends_on=["agent"]),
            WorkflowStep(id="done", type=StepType.TERMINAL, depends_on=["verify"]),
        ]
        engine = Engine()
        result = asyncio.run(engine.run(steps))
        assert result.success
        assert len(result.steps) == 3

    def test_abort_on_failure(self):
        steps = [
            WorkflowStep(id="s1", type=StepType.VERIFY, agent="fail", task="fail",
                         on_failure="abort"),
            WorkflowStep(id="s2", type=StepType.AGENT, agent="ok", task="ok",
                         depends_on=["s1"]),
        ]
        # VERIFY step with no successful upstream → fails
        engine = Engine()
        asyncio.run(engine.run(steps))
        # s1 is VERIFY with no deps, but no upstream results → depends_on empty
        # Actually VERIFY with empty depends_on passes. Let's use a different approach.
        # Use AGENT step but make it fail via custom executor
        steps2 = [
            WorkflowStep(id="s1", type=StepType.AGENT, agent="fail", task="fail",
                         on_failure="abort"),
            WorkflowStep(id="s2", type=StepType.AGENT, agent="ok", task="ok",
                         depends_on=["s1"]),
        ]

        async def fail_executor(step, **kw):
            if step.id == "s1":
                raise RuntimeError("deliberate failure")
            return new_result(agent="ok", task="ok", exit_code=0, stdout="ok")

        engine2 = Engine(step_executor=fail_executor)
        result2 = asyncio.run(engine2.run(steps2))
        assert result2.steps[0].status == StepStatus.FAILED
        assert result2.steps[1].status == StepStatus.SKIPPED

    def test_parallel_execution(self):
        steps = [
            WorkflowStep(id="s1", type=StepType.AGENT, agent="a1", task="t1"),
            WorkflowStep(id="s2", type=StepType.AGENT, agent="a2", task="t2"),
            WorkflowStep(id="done", type=StepType.TERMINAL, depends_on=["s1", "s2"]),
        ]
        engine = Engine()
        result = asyncio.run(engine.run(steps))
        assert result.success
        assert len(result.steps) == 3


# ═══════════════════════════════════════════════════════════════
# maop_loop tests
# ═══════════════════════════════════════════════════════════════

class TestLoopConfig:
    """Test LoopConfig defaults."""

    def test_defaults(self):
        cfg = LoopConfig()
        assert cfg.max_retries == 1
        assert cfg.default_timeout_s == 120
        assert cfg.feedback_max_cycles == 2
        assert cfg.skip_verify is False


class TestLoopResult:
    """Test LoopResult model."""

    def test_default(self):
        r = LoopResult(task="test")
        assert r.success is False
        assert r.feedback_cycles == 0
        assert r.total_duration_ms == 0


class TestMaopLoop:
    """Test MaopLoop orchestrator (with mocked subsystems)."""

    def test_build_fallback_chain_default(self):
        loop = MaopLoop.__new__(MaopLoop)
        loop._config = None
        chain = loop._build_fallback_chain("claude", "chat")
        assert chain == ["claude"]

    def test_build_fallback_chain_with_config(self):
        from maop.config.loader import AgentDef, MaopConfig, RouteEntry

        config = MaopConfig(
            agents={"claude": AgentDef(), "codex": AgentDef()},
            workflows={},
            routing={
                "code": RouteEntry(primary="codex", fallback="claude", tertiary=""),
            },
        )
        loop = MaopLoop.__new__(MaopLoop)
        loop._config = config
        chain = loop._build_fallback_chain("codex", "code")
        assert chain[0] == "codex"
        assert "claude" in chain

    def test_loop_config_model(self):
        cfg = LoopConfig(
            max_retries=3,
            default_timeout_s=300,
            feedback_max_cycles=5,
            skip_verify=True,
        )
        assert cfg.max_retries == 3
        assert cfg.default_timeout_s == 300
        assert cfg.feedback_max_cycles == 5
        assert cfg.skip_verify is True


# ═══════════════════════════════════════════════════════════════
# maop_execute tests
# ═══════════════════════════════════════════════════════════════

class TestMaopExecute:
    """Test maop_execute function."""

    def test_delegate_model(self):
        d = Delegate(agent="claude", task="hello")
        assert d.agent == "claude"
        assert d.timeout_seconds == 120

    def test_execute_with_mocked_dispatcher(self):
        """Test execute with a mocked dispatcher that returns success."""
        mock_dispatcher = MagicMock()
        mock_pm = MagicMock()
        mock_pm.check.return_value = PermissionCheck(allowed=True, decision="allow")
        mock_dispatch_result = MagicMock()
        mock_result = new_result(agent="claude", task="test", exit_code=0, stdout="ok")
        mock_dispatch_result.result = mock_result
        mock_dispatcher.dispatch = AsyncMock(return_value=mock_dispatch_result)

        result = asyncio.run(maop_execute(
            agent="claude", task="test",
            dispatcher=mock_dispatcher,
            permission_manager=mock_pm,
        ))
        assert result.is_success()
        assert result.stdout == "ok"

    def test_execute_guardrail_block(self):
        """Test that guardrail blocks execution."""
        mock_pm = MagicMock()
        mock_pm.check.return_value = PermissionCheck(allowed=True, decision="allow")
        mock_guardrail = MagicMock()
        mock_check = MagicMock()
        mock_check.passed = False
        mock_check.reason = "Content blocked"
        mock_guardrail.check = MagicMock(return_value=mock_check)

        result = asyncio.run(maop_execute(
            agent="claude", task="dangerous content",
            guardrail=mock_guardrail,
            permission_manager=mock_pm,
        ))
        assert not result.is_success()
        assert "Guardrail blocked" in (result.error or "")
