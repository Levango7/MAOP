"""M-T3.3 M3.3 —— operator 用量会计回写（turns / tokensUsed / tokensSource）.

M3.3 交付：M3.1 的 ``[USAGE] turns=N tokens=M source=S`` 协议行闭合到 CRD
——controller 解析并写入 ``status.turns`` / ``status.tokensUsed`` /
``status.tokensSource``；旧版只有 ``turns=`` 的行保持兼容（缺段不写字段）。
``tokensSource`` 区分 measured/estimated（CLI 驱动不产 token 时不冒充）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from maop.operator.controller import OperatorController, _parse_usage
from maop.operator.gateway import RunOutcome
from maop.operator.reconcile import plan_finish

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


# ── 协议行解析 ─────────────────────────────────────────────────────


def test_parse_usage_full_line():
    line = "[TRACE] abc\n[USAGE] turns=2 tokens=150 source=measured\n"
    assert _parse_usage(line) == (2, 150, "measured")


def test_parse_usage_legacy_turns_only():
    # 旧版 maop run 只打 turns=（M2 时代）——前向兼容
    assert _parse_usage("[USAGE] turns=3\n") == (3, None, None)


def test_parse_usage_missing_line():
    assert _parse_usage("some output without usage") == (None, None, None)


# ── 终态补丁 ───────────────────────────────────────────────────────


def test_plan_finish_records_usage_fields():
    t = plan_finish(exit_code=0, turns=2, tokens_used=150, tokens_source="measured")
    assert t.patch["turns"] == 2
    assert t.patch["tokensUsed"] == 150
    assert t.patch["tokensSource"] == "measured"


def test_plan_finish_omits_usage_when_absent():
    t = plan_finish(exit_code=0)
    assert "turns" not in t.patch
    assert "tokensUsed" not in t.patch
    assert "tokensSource" not in t.patch


def test_plan_finish_tokens_estimated_source():
    t = plan_finish(exit_code=1, turns=1, tokens_used=0, tokens_source="estimated")
    assert t.patch["tokensUsed"] == 0  # 0 是合法观测值，与"缺段"区分
    assert t.patch["tokensSource"] == "estimated"


# ── controller 端到端（mock） ───────────────────────────────────────


async def test_controller_writes_measured_usage_to_status():
    gateway = FakeGateway()
    runner = FakeRunner(RunOutcome(
        exit_code=0,
        stdout_tail="[TRACE] abc\n[USAGE] turns=2 tokens=150 source=measured\n",
    ))
    controller = OperatorController(gateway=gateway, runner=runner)

    result = await controller.handle_task(
        name="task-1", namespace="maop-system", body=_body()
    )

    assert result == {"phase": "Succeeded", "launched": True}
    finish = gateway.patches[1][2]
    assert (finish["turns"], finish["tokensUsed"], finish["tokensSource"]) == (
        2, 150, "measured",
    )


async def test_controller_legacy_line_writes_turns_only():
    gateway = FakeGateway()
    runner = FakeRunner(RunOutcome(exit_code=1, stdout_tail="[USAGE] turns=1\n"))
    controller = OperatorController(gateway=gateway, runner=runner)

    result = await controller.handle_task(name="task-1", body=_body())

    assert result["phase"] == "Failed"
    finish = gateway.patches[1][2]
    assert finish["turns"] == 1
    assert "tokensUsed" not in finish and "tokensSource" not in finish


# ── 机器对齐哨兵：status 用量字段与 CRD YAML 一致 ──────────────────


def test_usage_fields_match_maoptask_crd():
    crd = yaml.safe_load(MAOPTASK_CRD.read_text(encoding="utf-8"))
    status_props = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"][
        "properties"
    ]["status"]["properties"]
    assert status_props["tokensUsed"]["type"] == "integer"
    assert status_props["tokensSource"]["enum"] == ["measured", "estimated"]
    assert status_props["turns"]["type"] == "integer"
