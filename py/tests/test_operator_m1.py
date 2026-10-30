"""M-T3.3 Operator M1 —— MaopAgent CR 解析与 agent pinning（mock 全链路）.

M1 交付：``spec.agent`` 真正参与执行路由 ——
- controller 注入 resolver 后解析 MaopAgent CR：存在 → 命令带 ``--agent``
  （道路尽头是 MaopLoop 的 forced_agent，F6a，已有
  ``test_maop_loop::test_run_with_pinned_agent`` 钉住）；未声明 → 按 M0
  行为不带（仅 ``AgentResolved=False`` 条件注记，任务照常执行）；
- CLI 面（``maop run --agent``）由 ``test_cli`` 钉。

边界：maxTurns/turns 会计与配额是后续里程碑（M2/M3）——本文件不承诺。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from maop.operator.controller import OperatorController
from maop.operator.gateway import RunOutcome
from maop.operator.reconcile import plan_launch
from maop.operator.spec import command_for, parse_task_spec

REPO = Path(__file__).resolve().parents[2]
MAOPAGENT_CRD = REPO / "deploy" / "k8s" / "operator" / "crds" / "maopagent.yaml"


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


class FakeResolver:
    """MaopAgent CR 解析 fake：``agent_body=None`` 模拟未声明（404 → None）。"""

    def __init__(self, agent_body: dict | None) -> None:
        self.agent_body = agent_body
        self.calls: list[tuple[str, str]] = []

    async def resolve_agent(self, *, name: str, namespace: str) -> dict | None:
        self.calls.append((name, namespace))
        return self.agent_body


AGENT_CR = {
    "apiVersion": "maop.io/v1alpha1",
    "kind": "MaopAgent",
    "metadata": {"name": "support-bot", "namespace": "maop-system"},
    "spec": {"model": "gpt-4o", "maxTurns": 12, "tools": ["mcp.search"]},
}


# ── 命令构造（--agent pin） ─────────────────────────────────────────


def test_command_for_default_keeps_m0_shape():
    spec = parse_task_spec(_body())
    assert command_for(spec) == ["maop", "run", "检查订单服务健康"]


def test_command_for_pins_agent_when_requested():
    spec = parse_task_spec(_body())
    assert command_for(spec, pin_agent=True) == [
        "maop",
        "run",
        "--agent",
        "support-bot",
        "检查订单服务健康",
    ]


# ── reconcile：AgentResolved 条件 ───────────────────────────────────


def test_plan_launch_agent_resolved_condition():
    found = plan_launch(agent="support-bot", agent_resolved=True)
    cond = found.patch["conditions"]
    assert [c["type"] for c in cond] == ["Launched", "AgentResolved"]
    assert cond[1]["reason"] == "MaopAgentFound"
    assert "pinned" in cond[1]["message"]


def test_plan_launch_agent_not_declared_condition():
    missing = plan_launch(agent="support-bot", agent_resolved=False)
    cond = missing.patch["conditions"]
    assert cond[1]["type"] == "AgentResolved"
    assert cond[1]["reason"] == "MaopAgentNotDeclared"


def test_plan_launch_without_agent_args_keeps_m0_shape():
    t = plan_launch()
    assert [c["type"] for c in t.patch["conditions"]] == ["Launched"]


# ── controller 全链路（mock） ───────────────────────────────────────


async def test_controller_pins_when_agent_declared():
    gateway, runner = FakeGateway(), FakeRunner(RunOutcome(exit_code=0))
    resolver = FakeResolver(AGENT_CR)
    controller = OperatorController(gateway=gateway, runner=runner, resolver=resolver)

    result = await controller.handle_task(name="task-1", namespace="maop-system", body=_body())

    assert result == {"phase": "Succeeded", "launched": True}
    assert runner.commands == [["maop", "run", "--agent", "support-bot", "检查订单服务健康"]]
    assert resolver.calls == [("support-bot", "maop-system")]
    launched_cond = gateway.patches[0][2]["conditions"]
    assert [c["type"] for c in launched_cond] == ["Launched", "AgentResolved"]
    assert launched_cond[1]["reason"] == "MaopAgentFound"


async def test_controller_does_not_pin_when_agent_absent():
    gateway, runner = FakeGateway(), FakeRunner(RunOutcome(exit_code=0))
    resolver = FakeResolver(None)
    controller = OperatorController(gateway=gateway, runner=runner, resolver=resolver)

    result = await controller.handle_task(name="task-1", namespace="maop-system", body=_body())

    assert result == {"phase": "Succeeded", "launched": True}
    assert runner.commands == [["maop", "run", "检查订单服务健康"]]
    launched_cond = gateway.patches[0][2]["conditions"]
    assert launched_cond[1]["reason"] == "MaopAgentNotDeclared"


async def test_controller_without_resolver_keeps_m0_behaviour():
    gateway, runner = FakeGateway(), FakeRunner(RunOutcome(exit_code=0))
    controller = OperatorController(gateway=gateway, runner=runner)

    result = await controller.handle_task(name="task-1", body=_body())

    assert result == {"phase": "Succeeded", "launched": True}
    assert runner.commands == [["maop", "run", "检查订单服务健康"]]
    assert [c["type"] for c in gateway.patches[0][2]["conditions"]] == ["Launched"]


async def test_controller_invalid_spec_skips_resolution():
    """spec 校验失败时不解析 MaopAgent（不发起 k8s 读）。"""
    gateway, runner = FakeGateway(), FakeRunner(RunOutcome(exit_code=0))
    resolver = FakeResolver(AGENT_CR)
    controller = OperatorController(gateway=gateway, runner=runner, resolver=resolver)

    result = await controller.handle_task(name="t", body=_body(agent=""))

    assert result == {"phase": "Failed", "launched": False, "reason": "InvalidSpec"}
    assert resolver.calls == []
    assert runner.commands == []


# ── 机器对齐哨兵：resolver 的 CR 坐标与 CRD YAML 一致 ───────────────


def test_resolver_cr_coordinates_match_maopagent_crd():
    """gateway.resolve_agent 的 group/version/plural 与 CRD YAML 机器对齐（防漂移）。"""
    crd = yaml.safe_load(MAOPAGENT_CRD.read_text(encoding="utf-8"))
    group = crd["spec"]["group"]
    version = crd["spec"]["versions"][0]["name"]
    plural = crd["spec"]["names"]["plural"]

    gateway_src = (REPO / "py" / "maop" / "operator" / "gateway.py").read_text(encoding="utf-8")
    assert f'group="{group}"' in gateway_src
    assert f'version="{version}"' in gateway_src
    assert f'plural="{plural}"' in gateway_src
