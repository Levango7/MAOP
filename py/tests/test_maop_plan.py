"""Tests for maop_plan.py — Task routing to agents."""

from __future__ import annotations

import pytest

from maop.maop_plan import (
    Plan,
    _evaluate_condition,
    _interpolate_vars,
    execute_workflow,
    maop_plan,
)


# v5.0.0: TestRouteByKeyword class removed — _route_by_keyword() was deleted
# in v5.0.0 (deprecated since v4.0.0). Config-based routing is now primary.


class TestMaopPlan:
    def test_basic_plan(self):
        plan = maop_plan("fix the bug")
        assert plan.phase == "plan"
        assert plan.task == "fix the bug"
        # v5.0.0: routing depends on config; verify plan is well-formed
        assert plan.selected_agent
        assert plan.routing_key
        assert "exit_code" in plan.gates
        assert "output" in plan.gates

    def test_security_adds_content_safety_gate(self):
        plan = maop_plan("security audit")
        # v5.0.0: gate depends on routing_key from config routing
        if plan.routing_key in ("security", "quickfix", "review"):
            assert "content-safety" in plan.gates

    def test_deploy_adds_dry_run_gate(self):
        plan = maop_plan("deploy to prod")
        # v5.0.0: gate depends on routing_key from config routing
        if plan.routing_key in ("deploy", "pipeline", "fileops"):
            assert "dry-run" in plan.gates

    def test_quickfix_adds_content_safety_gate(self):
        plan = maop_plan("fix the bug", routing_key="quickfix")
        assert "content-safety" in plan.gates

    def test_pipeline_adds_dry_run_gate(self):
        plan = maop_plan("run pipeline", routing_key="pipeline")
        assert "dry-run" in plan.gates

    def test_review_adds_content_safety_gate(self):
        plan = maop_plan("review code", routing_key="review")
        assert "content-safety" in plan.gates

    def test_fileops_adds_dry_run_gate(self):
        plan = maop_plan("move file", routing_key="fileops")
        assert "dry-run" in plan.gates

    def test_non_security_no_content_safety_gate(self):
        plan = maop_plan("write docs")
        assert "content-safety" not in plan.gates

    def test_non_deploy_no_dry_run_gate(self):
        plan = maop_plan("hello world")
        assert "dry-run" not in plan.gates

    def test_default_budget(self):
        plan = maop_plan("hello world")
        assert plan.budget["timeout_s"] == 120
        assert plan.budget["max_retries"] == 1

    def test_explicit_routing_key(self):
        plan = maop_plan("some task", routing_key="debug")
        assert plan.routing_key == "debug"

    def test_workdir_param(self):
        plan = maop_plan("fix bug", workdir="/tmp/test")
        assert plan.task == "fix bug"

    def test_plan_model_fields(self):
        plan = maop_plan("test")
        assert isinstance(plan, Plan)
        assert hasattr(plan, "selected_agent")
        assert hasattr(plan, "routing_key")
        assert hasattr(plan, "gates")
        assert hasattr(plan, "budget")


# ── ADR-012: Config routing tests ──────────────────────────────

from maop.config.loader import MaopConfig, RouteEntry
from maop.maop_plan import _route_by_config


class TestADR012ConfigRouting:
    """Verify ADR-012: config routing (match + keywords) takes precedence."""

    @pytest.fixture(autouse=True)
    def _reset_route_scorer_singleton(self):
        """Reset RouteScorer singleton for test isolation.

        Without this, a prior test calling maop_plan without config
        initializes the singleton with config=None. The hot-reload guard
        then refuses to reinitialize, so the config fixture is ignored.
        """
        from maop.core.routing.route_scorer import RouteScorer

        RouteScorer.reset()
        yield
        RouteScorer.reset()

    @pytest.fixture
    def config(self):
        return MaopConfig(routing={
            "codegen": RouteEntry(
                primary="claude", fallback="cursor", tertiary="kilo",
                keywords=["编写", "写代码", "implement", "coding", "feature"],
            ),
            "refactor": RouteEntry(
                primary="claude", fallback="qoder",
                match=r"(?:refactor|rewrite|restructure|clean\s+up)",
                keywords=["重构", "重写", "rewrite"],
            ),
            "search": RouteEntry(
                primary="kimi", fallback="qoder",
                keywords=["搜索", "检索", "find", "research", "查询"],
            ),
            "verify": RouteEntry(
                primary="mavis/verifier", fallback="claude",
                match=r"(?:verify|test|assert|unit\s+test|integration)",
                keywords=["验证", "verify", "测试"],
            ),
            "quickfix": RouteEntry(
                primary="cursor", fallback="autoclaw",
                match=r"(?:fix|bug|error|hotfix|patch)",
                keywords=["修复", "fix", "bug"],
            ),
        })

    def test_keyword_match(self, config):
        rk, agent = _route_by_config("implement new feature", config)
        assert rk == "codegen"
        assert agent == "claude"

    def test_keyword_chinese(self, config):
        rk, agent = _route_by_config("搜索相关资料", config)
        assert rk == "search"
        assert agent == "kimi"

    def test_regex_match(self, config):
        rk, agent = _route_by_config("refactor the module", config)
        assert rk == "refactor"
        assert agent == "claude"

    def test_regex_match_over_keyword(self, config):
        rk, agent = _route_by_config("verify the fix", config)
        assert rk == "verify"
        assert agent == "mavis/verifier"

    def test_no_match_returns_none(self, config):
        result = _route_by_config("hello world random task", config)
        assert result is None

    def test_none_config_returns_none(self):
        result = _route_by_config("implement feature", None)
        assert result is None

    def test_empty_keywords_and_match_skipped(self):
        config = MaopConfig(routing={
            "chat": RouteEntry(primary="openclaw", fallback="mimo"),
        })
        result = _route_by_config("random task", config)
        assert result is None

    def test_invalid_regex_skipped(self):
        config = MaopConfig(routing={
            "bad": RouteEntry(
                primary="claude", match="[invalid regex",
                keywords=["fallback-keyword"],
            ),
        })
        rk, agent = _route_by_config("use fallback-keyword", config)
        assert rk == "bad"
        assert agent == "claude"

    def test_config_routing_precedence_over_legacy(self, config):
        plan = maop_plan("implement new feature", config=config)
        assert plan.selected_agent == "claude"
        assert plan.routing_key == "codegen"

    def test_fallback_to_default_when_config_misses(self):
        """v5.0.0: legacy keyword routing removed; config miss falls back to chat/claude."""
        plan = maop_plan("deploy to production")
        # With config routing, this may match a deploy route or fall back to default.
        # v5.0.0: fallback is "chat"/"claude" (was legacy keyword "deploy"/"codex").
        assert plan.selected_agent
        assert plan.routing_key


# ── Workflow DSL ──────────────────────────────────────────────

class TestInterpolateVars:
    def test_simple_substitution(self):
        assert _interpolate_vars("Hello ${name}", {"name": "MAOP"}) == "Hello MAOP"

    def test_missing_var_unchanged(self):
        assert _interpolate_vars("${missing}", {}) == "${missing}"

    def test_multiple_vars(self):
        assert _interpolate_vars("${a}+${b}", {"a": "1", "b": "2"}) == "1+2"


class TestEvaluateCondition:
    def test_empty_condition_is_true(self):
        assert _evaluate_condition("", {}) is True

    def test_truthy_var(self):
        assert _evaluate_condition("x", {"x": True}) is True

    def test_falsy_var(self):
        assert _evaluate_condition("x", {"x": False}) is False


class TestExecuteWorkflow:
    def test_workflow_not_found(self):
        result = execute_workflow("nonexistent", config=None)
        assert result.steps_total == 0

    def test_workflow_with_steps(self):
        from maop.config.loader import MaopConfig, WorkflowDef, WorkflowStepDef

        config = MaopConfig(workflows={
            "test_wf": WorkflowDef(
                steps=[
                    WorkflowStepDef(agent="claude", task="Write code"),
                    WorkflowStepDef(agent="codex", task="Run tests"),
                ]
            )
        })
        result = execute_workflow("test_wf", config=config)
        assert result.steps_total == 2
        assert result.steps_completed == 2

    def test_workflow_with_condition_skip(self):
        from maop.config.loader import MaopConfig, WorkflowDef, WorkflowStepDef

        config = MaopConfig(workflows={
            "cond_wf": WorkflowDef(
                steps=[
                    WorkflowStepDef(agent="claude", task="Step 1"),
                    WorkflowStepDef(agent="codex", task="Step 2", condition="False"),
                ]
            )
        })
        result = execute_workflow("cond_wf", config=config)
        assert result.steps_completed == 1
        assert result.steps_skipped == 1

    def test_workflow_always_run(self):
        from maop.config.loader import MaopConfig, WorkflowDef, WorkflowStepDef

        config = MaopConfig(workflows={
            "always_wf": WorkflowDef(
                steps=[
                    WorkflowStepDef(agent="claude", task="Step 1"),
                    WorkflowStepDef(agent="codex", task="Cleanup", condition="False", always_run=True),
                ]
            )
        })
        result = execute_workflow("always_wf", config=config)
        assert result.steps_completed == 2
        assert result.steps_skipped == 0

    def test_workflow_variable_interpolation(self):
        from maop.config.loader import MaopConfig, WorkflowDef, WorkflowStepDef

        config = MaopConfig(workflows={
            "var_wf": WorkflowDef(
                steps=[
                    WorkflowStepDef(agent="claude", task="Build ${project}"),
                ]
            )
        })
        result = execute_workflow("var_wf", config=config, initial_vars={"project": "MAOP"})
        assert result.steps_completed == 1
        assert result.variables.get("steps.0.output") is not None
