"""M-T3.3 M3.2 —— 配额门（QuotaExceeded 相位接线，检查器注入式）+ 跨仓桥接线.

社区版只提供**机制**：拉起前询问注入的 ``QuotaChecker``、拒绝则终态
``QuotaExceeded``（CRD enum 预留相位）且不拉起子进程、终态后回报用量。
不注入配额门的路径逐字节保持 M0 行为；真实配额策略（多租户聚合）属
MAOS 企业版配额域——其 operator 侧适配器（``OperatorQuotaChecker``，
MAOS PR #27）经 ``handlers._enterprise_quota_checker`` 在单例构造时
try-import 接线（本文件末两例；个人版 import 失败 → None）。
"""

from __future__ import annotations

import sys
import types

from maop.operator import handlers
from maop.operator.controller import OperatorController
from maop.operator.gateway import RunOutcome


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


class FakeQuotaChecker:
    """记录型配额门：``decision`` 决定放行与否。"""

    def __init__(self, decision: tuple[bool, str] = (True, "")) -> None:
        self.decision = decision
        self.checks: list[dict] = []
        self.usages: list[dict] = []

    async def check_quota(self, *, tenant: str, max_turns: int, prompt: str):
        self.checks.append({
            "tenant": tenant, "max_turns": max_turns, "prompt": prompt,
        })
        return self.decision

    async def record_usage(self, *, tenant: str, turns: int, tokens_used: int) -> None:
        self.usages.append({
            "tenant": tenant, "turns": turns, "tokens_used": tokens_used,
        })


# ── 拒绝路径（不拉起） —────────────────────────────────────────────


async def test_quota_denied_patches_quota_exceeded_without_launch():
    gateway, runner = FakeGateway(), FakeRunner(RunOutcome(exit_code=0))
    checker = FakeQuotaChecker((False, "daily turns quota exceeded"))
    controller = OperatorController(
        gateway=gateway, runner=runner, quota_checker=checker
    )

    result = await controller.handle_task(
        name="task-1", namespace="maop-system", body=_body(tenant="t-42")
    )

    assert result == {
        "phase": "QuotaExceeded", "launched": False,
        "reason": "daily turns quota exceeded",
    }
    assert runner.commands == []  # 未拉起
    terminal = gateway.patches[0][2]
    assert terminal["phase"] == "QuotaExceeded"
    cond = terminal["conditions"][0]
    assert cond["reason"] == "QuotaExceeded"
    assert "t-42" in cond["message"]
    assert checker.checks == [
        {"tenant": "t-42", "max_turns": 5, "prompt": "检查订单服务健康"}
    ]


async def test_quota_denied_is_terminal_noop_on_replay():
    """QuotaExceeded 是终态：事件重放时幂等 no-op。"""
    gateway = FakeGateway()
    controller = OperatorController(
        gateway=gateway,
        runner=FakeRunner(RunOutcome(exit_code=0)),
        quota_checker=FakeQuotaChecker((False, "deny")),
    )
    result = await controller.handle_task(
        name="task-1", body=_body(tenant="t-1"), status_phase="QuotaExceeded"
    )
    assert result == {"phase": "QuotaExceeded", "launched": False}
    assert gateway.patches == []


# ── 放行路径（含用量回报） ────────────────────────────────────────


async def test_quota_allowed_runs_and_records_usage():
    gateway, runner = FakeGateway(), FakeRunner(RunOutcome(
        exit_code=0, stdout_tail="[USAGE] turns=1 tokens=150 source=measured\n",
    ))
    checker = FakeQuotaChecker((True, ""))
    controller = OperatorController(
        gateway=gateway, runner=runner, quota_checker=checker
    )

    result = await controller.handle_task(
        name="task-1", namespace="maop-system", body=_body(tenant="t-7")
    )

    assert result == {"phase": "Succeeded", "launched": True}
    assert runner.commands  # 已拉起
    finish = gateway.patches[-1][2]
    assert finish["turns"] == 1
    assert finish["tokensUsed"] == 150
    assert checker.usages == [{"tenant": "t-7", "turns": 1, "tokens_used": 150}]


async def test_quota_no_checker_keeps_m0_behavior():
    """未注入配额门：不拒绝、不回报（M0 行为逐字节不变）。"""
    gateway, runner = FakeGateway(), FakeRunner(RunOutcome(
        exit_code=0, stdout_tail="[USAGE] turns=1 tokens=150 source=measured\n",
    ))
    controller = OperatorController(gateway=gateway, runner=runner)

    result = await controller.handle_task(
        name="task-1", namespace="maop-system", body=_body(tenant="t-9")
    )

    assert result == {"phase": "Succeeded", "launched": True}
    assert runner.commands


# ── 跨仓桥接线（handlers._enterprise_quota_checker） ────────────────


def test_enterprise_checker_wired_when_enterprise_available(monkeypatch):
    """企业包在 sys.modules → 单例构造注入适配器（MAOS PR #27 桥）。"""
    recorded: dict = {}

    class _FakeQM:
        def __init__(self, db_path):
            recorded["db_path"] = db_path

    class _FakeChecker:
        def __init__(self, qm):
            recorded["qm"] = qm
            self.qm = qm

    fake_quota = types.ModuleType("maop.enterprise.quota")
    fake_quota.QuotaManager = _FakeQM
    fake_adapter = types.ModuleType("maop.enterprise.quota_operator")
    fake_adapter.OperatorQuotaChecker = _FakeChecker
    monkeypatch.setitem(sys.modules, "maop.enterprise.quota", fake_quota)
    monkeypatch.setitem(sys.modules, "maop.enterprise.quota_operator", fake_adapter)

    checker = handlers._enterprise_quota_checker()

    assert isinstance(checker, _FakeChecker)
    assert checker.qm is recorded["qm"]  # 适配器持有构造时传入的 QuotaManager
    assert str(recorded["db_path"]).endswith("maop.db")


def test_enterprise_checker_none_when_personal_edition(monkeypatch):
    """个人版：maop.enterprise 不可 import → None（配额门不生效，行为同基线）。"""
    monkeypatch.setitem(sys.modules, "maop.enterprise.quota_operator", None)
    monkeypatch.setitem(sys.modules, "maop.enterprise.quota", None)

    assert handlers._enterprise_quota_checker() is None
