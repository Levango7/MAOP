"""Tests for Error Ledger, DAG Workflow, and Self-Heal Engine."""

import shutil
import tempfile

import pytest

from maop.core.reliability.error_ledger import ErrorLedger
from maop.core.reliability.self_heal import HealAction, HealRule, HealStatus, SelfHealEngine


@pytest.fixture
def tmp_root():
    tmpdir = tempfile.mkdtemp()
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


# ── Error Ledger ──────────────────────────────────────────────

class TestErrorLedger:
    def test_record_and_find(self, tmp_root):
        ledger = ErrorLedger(root_dir=tmp_root)
        ledger.record(error_type="tool_error", context="git push", pattern="git repo")
        results = ledger.find_by_pattern("git repo")
        assert len(results) >= 1
        assert results[0].pattern == "git repo"

    def test_recurrence_increment(self, tmp_root):
        ledger = ErrorLedger(root_dir=tmp_root)
        ledger.record(error_type="tool_error", pattern="timeout")
        ledger.record(error_type="tool_error", pattern="timeout")
        results = ledger.find_by_pattern("timeout")
        assert results[0].recurrence == 2

    def test_hotspots(self, tmp_root):
        ledger = ErrorLedger(root_dir=tmp_root)
        ledger.record(error_type="a", pattern="timeout")
        ledger.record(error_type="a", pattern="timeout")
        ledger.record(error_type="b", pattern="auth_fail")
        hotspots = ledger.get_hotspots()
        assert len(hotspots) >= 1
        assert hotspots[0].pattern == "timeout"

    def test_auto_promote(self, tmp_root):
        ledger = ErrorLedger(root_dir=tmp_root)
        for _ in range(3):
            ledger.record(error_type="tool_error", pattern="db_locked")
        promoted = ledger.auto_promote(threshold=3)
        assert len(promoted) == 1
        assert promoted[0].pattern == "db_locked"

    def test_auto_promote_below_threshold(self, tmp_root):
        ledger = ErrorLedger(root_dir=tmp_root)
        ledger.record(error_type="tool_error", pattern="minor")
        promoted = ledger.auto_promote(threshold=3)
        assert len(promoted) == 0

    def test_get_promoted_rules(self, tmp_root):
        ledger = ErrorLedger(root_dir=tmp_root)
        for _ in range(3):
            ledger.record(error_type="tool_error", pattern="repeat_err")
        ledger.auto_promote(threshold=3)
        rules = ledger.get_promoted_rules()
        assert len(rules) >= 1


# ── DAG Workflow ──────────────────────────────────────────────

class TestDAGWorkflow:
    def test_topological_sort_linear(self):
        from maop.config.loader import WorkflowStepDef
        from maop.maop_plan import _topological_sort

        steps = [
            WorkflowStepDef(agent="a", task="step0", depends_on=[]),
            WorkflowStepDef(agent="b", task="step1", depends_on=["0"]),
            WorkflowStepDef(agent="c", task="step2", depends_on=["1"]),
        ]
        levels = _topological_sort(steps)
        assert len(levels) == 3
        assert levels[0] == [0]
        assert levels[1] == [1]
        assert levels[2] == [2]

    def test_topological_sort_parallel(self):
        from maop.config.loader import WorkflowStepDef
        from maop.maop_plan import _topological_sort

        steps = [
            WorkflowStepDef(agent="a", task="step0", depends_on=[]),
            WorkflowStepDef(agent="b", task="step1", depends_on=[], parallel=True),
            WorkflowStepDef(agent="c", task="step2", depends_on=["0", "1"]),
        ]
        levels = _topological_sort(steps)
        assert len(levels) == 2
        assert set(levels[0]) == {0, 1}
        assert levels[1] == [2]

    def test_dag_workflow_execution(self):
        from maop.config.loader import MaopConfig, WorkflowDef, WorkflowStepDef
        from maop.maop_plan import execute_workflow

        config = MaopConfig(workflows={
            "dag_wf": WorkflowDef(
                steps=[
                    WorkflowStepDef(agent="a", task="Build", depends_on=[]),
                    WorkflowStepDef(agent="b", task="Test", depends_on=["0"]),
                    WorkflowStepDef(agent="c", task="Deploy", depends_on=["1"]),
                ]
            )
        })
        result = execute_workflow("dag_wf", config=config)
        assert result.steps_completed == 3

    def test_dag_parallel_steps(self):
        from maop.config.loader import MaopConfig, WorkflowDef, WorkflowStepDef
        from maop.maop_plan import execute_workflow

        config = MaopConfig(workflows={
            "par_wf": WorkflowDef(
                steps=[
                    WorkflowStepDef(agent="a", task="Lint", depends_on=[], parallel=True),
                    WorkflowStepDef(agent="b", task="Test", depends_on=[], parallel=True),
                    WorkflowStepDef(agent="c", task="Deploy", depends_on=["0", "1"]),
                ]
            )
        })
        result = execute_workflow("par_wf", config=config)
        assert result.steps_completed == 3


# ── Self-Heal ─────────────────────────────────────────────────

class TestSelfHeal:
    def test_register_rule(self, tmp_root):
        engine = SelfHealEngine(root_dir=tmp_root)
        engine.register(HealRule(
            name="test_rule", condition="test error",
            action=HealAction.CLEAR_CACHE, verify="",
        ))
        report = engine.run_all(trigger_condition="test error")
        assert report.total_rules >= 1

    def test_run_all_healthy(self, tmp_root):
        engine = SelfHealEngine(root_dir=tmp_root)
        report = engine.run_all()
        assert report.checked >= 0

    def test_custom_action(self, tmp_root):
        engine = SelfHealEngine(root_dir=tmp_root)
        called = []
        engine.register_action("custom_fix", lambda: (called.append(True), True)[1])
        engine.register(HealRule(
            name="custom_rule", condition="custom error",
            action=HealAction.CUSTOM, verify="",
        ))
        result = engine.run_rule(HealRule(
            name="custom_rule", condition="custom error",
            action=HealAction.CUSTOM,
        ))
        assert result.status in (HealStatus.REPAIRED, HealStatus.REPAIR_FAILED)

    def test_heal_result_model(self):
        from maop.core.reliability.self_heal import HealResult
        r = HealResult(rule_name="test", status=HealStatus.REPAIRED, message="ok")
        assert r.status == HealStatus.REPAIRED
