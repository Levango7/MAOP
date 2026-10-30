"""M-T3.3 Operator M2 —— turn 预算透传与 status.turns 会计（mock 全链路）.

M2 交付：``spec.maxTurns`` 真正生效并可见 ——
- ``command_for(apply_turn_budget=True)`` → ``maop run --max-turns <n>``；
- controller 解析子进程 stdout 的 ``[USAGE] turns=N`` 协议行，终态补丁写
  ``status.turns``（缺行则不写——与未打该行的旧版 ``maop run`` 前后兼容）；
- CLI/loop 侧的预算截断与计数由 ``test_turn_budget`` / ``test_cli`` 钉。

边界：tokensUsed 与配额/多租户是 M3。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from maop.operator.controller import OperatorController
from maop.operator.gateway import RunOutcome
from maop.operator.reconcile import plan_finish
from maop.operator.spec import command_for, parse_task_spec

REPO = Path(__file__).resolve().parents[2]
MAOPTASK_CRD = REPO / "deploy" / "k8s" / "operator" / "crds" / "maoptask.yaml"


def _body(**spec_overrides) -> dict:
    spec = {"agent": "support-bot", "prompt": "检查订单服务健康", "maxTurns": 5}
    spec.update(spec_overrides)
    return {"metadata": {"name": "task-1", "namespace": "maop-system"}, "spec": spec}


class FakeGateway:
    def __init__(self) -> None:
        self.patches: list[tuple[str, str, dict]] = []

    async def patch_status(self, *, name: str, namespace: str, patch: dict) -> None:
        self.patches.append((name, namespace, patch))


class FakeRunner:
    def __init__(self, outcome: RunOutcome) -> None:
        self.outcome = outcome
        self.commands: list[list[str]] = []

    async def run(self, command: list[str], *, timeout_s: float | None = None) -> RunOutcome:
        self.commands.append(list(command))
        return self.outcome


# ── 命令构造（--max-turns 透传） ─────────────────────────────────────


def test_command_for_default_keeps_previous_shape():
    spec = parse_task_spec(_body())
    assert command_for(spec) == ["maop", "run", "检查订单服务健康"]


def test_command_for_applies_turn_budget():
    spec = parse_task_spec(_body())
    assert command_for(spec, apply_turn_budget=True) == [
        "maop",
        "run",
        "--max-turns",
        "5",
        "检查订单服务健康",
    ]


def test_command_for_combined_flags_order():
    spec = parse_task_spec(_body())
    assert command_for(spec, pin_agent=True, apply_turn_budget=True) == [
        "maop",
        "run",
        "--agent",
        "support-bot",
        "--max-turns",
        "5",
        "检查订单服务健康",
    ]


# ── reconcile：status.turns 写入 ────────────────────────────────────


def test_plan_finish_records_turns():
    ok = plan_finish(exit_code=0, turns=7)
    assert ok.patch["turns"] == 7
    assert ok.patch["phase"] == "Succeeded"


def test_plan_finish_omits_turns_when_absent():
    ok = plan_finish(exit_code=0)
    assert "turns" not in ok.patch


def test_plan_finish_turns_on_failure_paths():
    assert plan_finish(exit_code=1, turns=3).patch["turns"] == 3
    assert plan_finish(timed_out=True, turns=2).patch["turns"] == 2
    assert plan_finish(cancelled=True, turns=1).patch["turns"] == 1


# ── controller 全链路（mock） ───────────────────────────────────────


async def test_controller_passes_budget_and_writes_turns():
    gateway = FakeGateway()
    runner = FakeRunner(RunOutcome(exit_code=0, stdout_tail="[TRACE] abc\n[USAGE] turns=3\n"))
    controller = OperatorController(gateway=gateway, runner=runner)

    result = await controller.handle_task(name="task-1", namespace="maop-system", body=_body())

    assert result == {"phase": "Succeeded", "launched": True}
    assert runner.commands == [["maop", "run", "--max-turns", "5", "检查订单服务健康"]]
    assert gateway.patches[1][2]["turns"] == 3


async def test_controller_without_usage_line_omits_turns():
    gateway = FakeGateway()
    runner = FakeRunner(RunOutcome(exit_code=1, stdout_tail="some output without usage"))
    controller = OperatorController(gateway=gateway, runner=runner)

    result = await controller.handle_task(name="task-1", body=_body())

    assert result["phase"] == "Failed"
    assert "turns" not in gateway.patches[1][2]


# ── 机器对齐哨兵：status.turns / spec.maxTurns 与 CRD YAML 一致 ─────


def test_turn_fields_match_maoptask_crd():
    """``status.turns`` 与 ``spec.maxTurns`` 的 CRD 形状机器对齐（防漂移）。"""
    crd = yaml.safe_load(MAOPTASK_CRD.read_text(encoding="utf-8"))
    schema = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
    status_props = schema["properties"]["status"]["properties"]
    assert status_props["turns"]["type"] == "integer"
    spec_props = schema["properties"]["spec"]["properties"]
    assert spec_props["maxTurns"]["minimum"] == 1
