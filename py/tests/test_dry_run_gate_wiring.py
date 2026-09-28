"""2026-09-28 dry-run gate no-op 修复的接线测试。

历史缺陷：maop_plan 为 deploy/pipeline/fileops 路由追加 "dry-run" gate，
但从不设置 ``plan["dry_run"]=True``，``_gate_dry_run`` 对未声明的 plan
恒 PASS——"部署类路由的安全闸门"实际从未生效（评估报告发现）。

修复语义：
  - 默认（MAOP_DRY_RUN_ENFORCE 未设）：行为与历史一致——gate 在 gates
    列表里但对未声明 dry_run 的 plan 恒 PASS（执行器尚不能自报 dry-run
    信号，直接启用会打断三条路由）。
  - ``MAOP_DRY_RUN_ENFORCE=1``：三条路由的 plan.dry_run=True，gate 真正
    校验执行结果自报的 dry-run 信号。⚠️ 当前执行器不产出该信号，enforce
    即 fail-closed（pipeline/fileops 任务会卡在 verify）。

路由口径（2026-09-28 用 load_config() 实测）：``config/agents.yaml`` 路由表
没有 "deploy" 键——真实任务只会命中 pipeline / fileops；deploy 分支仅能经
``routing_key`` 显式覆盖触达，其断言因此走覆盖路径。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from maop.config.loader import load_config
from maop.maop_plan import maop_plan
from maop.maop_verify import VerifyEngine

# 经 RouteScorer 实测命中对应路由的真实任务
_ROUTE_TASKS = (
    ("run the ci pipeline", "pipeline"),
    ("file operations cleanup", "fileops"),
)


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(autouse=True)
def _reset_route_scorer_singleton():
    """测试隔离：RouteScorer 单例若被 config=None 先初始化，热重载守卫会
    拒绝后续真实 config（同 test_maop_plan.py::TestADR012ConfigRouting 的
    处理）。xdist 动态分配下，别处 ``maop_plan(task)`` 不带 config 的用例
    与本文交错时会使 config 路由失效——不重置则 4/8 用例间歇性失败。"""
    from maop.core.routing.route_scorer import RouteScorer

    RouteScorer.reset()
    yield
    RouteScorer.reset()


def _no_signals_result() -> MagicMock:
    r = MagicMock()
    r.stdout = "applied changes to 3 files"
    r.stderr = ""  # 必须为 str：VerifyResult 分类器对失败结果做 " ".join()
    r.structured_output = {}
    r.exit_code = 0
    return r


class TestPlanDryRunWiring:
    def test_sensitive_routes_declare_gate_but_not_dry_run_by_default(self, monkeypatch, cfg):
        """默认：gate 存在、dry_run=False（历史行为，闸门不改变结果）。"""
        monkeypatch.delenv("MAOP_DRY_RUN_ENFORCE", raising=False)
        plan = maop_plan(task="run the ci pipeline", config=cfg)
        assert plan.routing_key == "pipeline"
        assert "dry-run" in plan.gates
        assert plan.dry_run is False

    def test_enforce_flag_sets_dry_run_on_sensitive_routes(self, monkeypatch, cfg):
        monkeypatch.setenv("MAOP_DRY_RUN_ENFORCE", "1")
        for task, rk in _ROUTE_TASKS:
            plan = maop_plan(task=task, config=cfg)
            assert plan.routing_key == rk, f"{task} should route to {rk}"
            assert "dry-run" in plan.gates
            assert plan.dry_run is True, f"{rk} route should declare dry_run under enforce"

    def test_enforce_flag_covers_deploy_branch_via_override(self, monkeypatch):
        """config 路由表无 deploy 键——其 gate 分支只能经 routing_key 覆盖触达。"""
        monkeypatch.setenv("MAOP_DRY_RUN_ENFORCE", "1")
        plan = maop_plan(task="deploy the service", routing_key="deploy")
        assert plan.routing_key == "deploy"
        assert "dry-run" in plan.gates
        assert plan.dry_run is True

    def test_enforce_flag_does_not_touch_unrelated_routes(self, monkeypatch, cfg):
        monkeypatch.setenv("MAOP_DRY_RUN_ENFORCE", "1")
        plan = maop_plan(task="refactor auth module", config=cfg)
        assert plan.routing_key == "refactor"
        assert plan.dry_run is False
        assert "dry-run" not in plan.gates

    def test_plan_dry_run_flows_through_model_dump(self, monkeypatch, cfg):
        """gate 从 dict 读 plan["dry_run"]——字段必须经 model_dump 存活。"""
        monkeypatch.setenv("MAOP_DRY_RUN_ENFORCE", "1")
        plan = maop_plan(task="run the ci pipeline", config=cfg)
        dumped = plan.model_dump()
        assert dumped["dry_run"] is True


class TestGateBehavior:
    def test_gate_passes_when_plan_does_not_declare(self):
        """未声明 dry_run：gate 恒 PASS（向后兼容合同）。"""
        engine = VerifyEngine()
        vr = engine.verify(plan={"gates": ["dry-run"]}, result=_no_signals_result())
        gate = next(g for g in vr.gates if g.name == "dry-run")
        assert gate.passed is True

    def test_gate_fails_without_signals_when_declared(self):
        """声明 dry_run=True 但结果无 dry-run 信号 → FAIL（修复后可触达）。"""
        engine = VerifyEngine()
        vr = engine.verify(
            plan={"gates": ["dry-run"], "dry_run": True},
            result=_no_signals_result(),
        )
        gate = next(g for g in vr.gates if g.name == "dry-run")
        assert gate.passed is False
        assert "dry-run" in (gate.reason or "")

    def test_gate_passes_with_structured_signal_when_declared(self):
        engine = VerifyEngine()
        r = _no_signals_result()
        r.structured_output = {"dry_run": True}
        vr = engine.verify(
            plan={"gates": ["dry-run"], "dry_run": True}, result=r,
        )
        gate = next(g for g in vr.gates if g.name == "dry-run")
        assert gate.passed is True
