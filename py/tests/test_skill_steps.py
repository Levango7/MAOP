"""Tests for Skill steps, execution, and extended SkillMeta."""

import json
import shutil
import tempfile

import pytest

from maop.core.evolution.skill_version import (
    SkillExecutionResult,
    SkillMeta,
    SkillStep,
    SkillStepResult,
    SkillVersionManager,
)


@pytest.fixture
def skill_env():
    tmpdir = tempfile.mkdtemp()
    mgr = SkillVersionManager(root_dir=tmpdir)
    yield mgr
    shutil.rmtree(tmpdir, ignore_errors=True)


class TestSkillStep:
    def test_defaults(self):
        step = SkillStep(name="scan")
        assert step.action == ""
        assert step.timeout_s == 120
        assert step.always_run is False

    def test_with_params(self):
        step = SkillStep(
            name="security_scan",
            description="Scan for security issues",
            action="terminal",
            params={"command": "ruff check {target}"},
            timeout_s=60,
        )
        assert step.params["command"] == "ruff check {target}"


class TestSkillMetaExtended:
    def test_steps_field(self):
        meta = SkillMeta(
            name="code-review",
            steps=[
                SkillStep(name="1_security", action="search_files", params={"pattern": "(eval|exec)"}),
                SkillStep(name="2_quality", action="terminal", params={"command": "ruff check {target}"}),
                SkillStep(name="3_structure", action="prompt", prompt="Analyze code structure"),
            ],
            pitfalls=["Python 2/3 compat not needed", "ruff may not be installed"],
            preferred_model="deepseek-chat",
            fallback_model="glm-5.2-free",
        )
        assert len(meta.steps) == 3
        assert len(meta.pitfalls) == 2
        assert meta.preferred_model == "deepseek-chat"

    def test_meta_serialization(self):
        meta = SkillMeta(
            name="test-skill",
            steps=[SkillStep(name="step1", action="prompt", prompt="Hello")],
            pitfalls=["Watch out!"],
        )
        data = json.loads(meta.model_dump_json())
        assert len(data["steps"]) == 1
        assert data["pitfalls"][0] == "Watch out!"


class TestSkillExecution:
    def test_execute_nonexistent_skill(self, skill_env):
        result = skill_env.execute_skill("nonexistent")
        assert result.total_steps == 0

    def test_execute_skill_with_steps(self, skill_env):
        skill_env.save_skill(
            "test-skill",
            content="# Test Skill",
            metadata={
                "steps": [
                    {"name": "step1", "action": "prompt", "prompt": "Analyze this"},
                    {"name": "step2", "action": "search_files", "params": {"pattern": "eval"}},
                ],
                "pitfalls": ["Test pitfall"],
                "preferred_model": "deepseek-chat",
            },
        )
        result = skill_env.execute_skill("test-skill")
        assert result.total_steps == 2
        assert result.completed_steps == 2
        assert result.failed_steps == 0

    def test_execute_with_custom_executor(self, skill_env):
        def my_executor(step, ctx):
            return SkillStepResult(step_name=step.name, success=True, output=f"Custom: {step.name}")

        skill_env.save_skill(
            "custom-exec",
            content="# Custom",
            metadata={"steps": [{"name": "a", "action": "prompt", "prompt": "test"}]},
        )
        result = skill_env.execute_skill("custom-exec", step_executor=my_executor)
        assert result.completed_steps == 1
        assert "Custom: a" in result.step_results[0].output

    def test_execute_step_failure_stops(self, skill_env):
        def failing_executor(step, ctx):
            if step.name == "step2":
                return SkillStepResult(step_name=step.name, success=False, error="Failed")
            return SkillStepResult(step_name=step.name, success=True, output="OK")

        skill_env.save_skill(
            "failing-skill",
            content="# Fail",
            metadata={"steps": [
                {"name": "step1", "action": "prompt"},
                {"name": "step2", "action": "prompt"},
                {"name": "step3", "action": "prompt"},
            ]},
        )
        result = skill_env.execute_skill("failing-skill", step_executor=failing_executor)
        assert result.completed_steps == 1
        assert result.failed_steps == 1
        assert result.total_steps == 3

    def test_execute_always_run_step(self, skill_env):
        call_order = []

        def executor(step, ctx):
            call_order.append(step.name)
            if step.name == "step1":
                return SkillStepResult(step_name=step.name, success=False, error="Fail")
            return SkillStepResult(step_name=step.name, success=True, output="OK")

        skill_env.save_skill(
            "always-run-skill",
            content="# Always",
            metadata={"steps": [
                {"name": "step1", "action": "prompt"},
                {"name": "cleanup", "action": "prompt", "always_run": True},
            ]},
        )
        skill_env.execute_skill("always-run-skill", step_executor=executor)
        assert "cleanup" in call_order

    def test_execute_conditional_skip(self, skill_env):
        skill_env.save_skill(
            "cond-skill",
            content="# Conditional",
            metadata={"steps": [
                {"name": "always", "action": "prompt"},
                {"name": "optional", "action": "prompt", "condition": "!skip_optional"},
            ]},
        )
        result = skill_env.execute_skill("cond-skill", context={"skip_optional": True})
        assert result.skipped_steps == 1
        assert result.completed_steps == 1


class TestSkillStepResult:
    def test_defaults(self):
        r = SkillStepResult(step_name="test")
        assert r.success is True
        assert r.output == ""

    def test_with_error(self):
        r = SkillStepResult(step_name="test", success=False, error="boom")
        assert r.error == "boom"


class TestSkillExecutionResult:
    def test_defaults(self):
        r = SkillExecutionResult(skill_name="test")
        assert r.total_steps == 0


class TestHotReload:
    def test_hot_reload(self, skill_env):
        skill_env.save_skill("reload-test", content="v1", metadata={"tags": ["test"]})
        count = skill_env.hot_reload()
        assert count >= 1

    def test_hot_reload_empty(self, skill_env):
        count = skill_env.hot_reload()
        assert count >= 0


class TestSkillMatch:
    def test_match_by_name(self, skill_env):
        skill_env.save_skill("code-review", content="Review code", metadata={"tags": ["review", "code"]})
        skill_env.save_skill("deploy", content="Deploy app", metadata={"tags": ["deploy", "production"]})
        matches = skill_env.match("review code", top_k=5)
        assert len(matches) >= 1
        assert matches[0][0].name == "code-review"

    def test_match_by_description(self, skill_env):
        skill_env.save_skill("security-scan", content="Scan for vulnerabilities",
                             metadata={"description": "Security vulnerability scanning"})
        matches = skill_env.match("security vulnerability")
        assert len(matches) >= 1

    def test_match_empty_intent(self, skill_env):
        skill_env.save_skill("test", content="test")
        matches = skill_env.match("")
        assert len(matches) >= 1

    def test_match_no_results(self, skill_env):
        skill_env.save_skill("deploy", content="Deploy", metadata={"tags": ["deploy"]})
        matches = skill_env.match("quantum physics")
        assert all(s < 0.3 for _, s in matches)
