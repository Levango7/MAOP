"""派发安全门（`core/security/dispatch_gate.py`）与它的**接线**测试。

背景：这套检查原先只在 `maop/maop_execute.py::_check_permission_and_hooks` 里，而
`maop_execute()` 全仓零生产调用方 ⇒ 三个真实入口（CLI run / dashboard DAG / chat 回退）
全都绕过权限判断与 `pre_dispatch` 钩子。现在门接在唯一漏斗 `Dispatcher.dispatch()` 上。

本文件有两层，缺一不可：

* **策略层**：开关语义（默认关 = 不改变行为）、deny/ask/异常一律 fail-closed、钩子否决。
* **接线层**：`Dispatcher.dispatch` 真的会调用它，且在门拒绝时**根本不进入
  `_dispatch_impl`**（= 不落任何 driver / 子进程）。只测策略层的话，把 dispatch 里那几行
  删掉照样全绿 —— 本仓栽过同形的坑（函数对但没人调用）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from maop.core.reliability.error_schema import new_result
from maop.core.security.dispatch_gate import (
    PERMISSION_DENIED_EXIT_CODE,
    check_dispatch_gate,
)
from maop.delegate.dispatcher import Dispatcher, DispatchResult


class _FakePM:
    """替身：只回答一次 check()，用于钉判定分支。"""

    def __init__(self, decision: str, reason: str = "", matched_rule: str = "r1") -> None:
        self._check = SimpleNamespace(
            decision=decision, reason=reason, matched_rule=matched_rule,
            allowed=(decision == "allow"),
        )

    def check(self, agent: str, action: str = "*"):
        return self._check


def _set_enforce(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    """切换 `MAOP_PERMISSION_ENFORCE`（真实读取点：settings 单例）。"""
    monkeypatch.setattr(
        "maop.config.settings.get_settings",
        lambda: SimpleNamespace(permission_enforce=on),
    )


def _gate(**kw):
    params = {"agent": "a", "task": "t", "routing_key": "codegen", "trace_id": "tr-1"}
    params.update(kw)
    return asyncio.run(check_dispatch_gate(**params))


# ── 策略层 ───────────────────────────────────────────────────────────


def test_gate_is_inert_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认（未设 MAOP_PERMISSION_ENFORCE）门挂载但不改变行为。

    这条是"不把产品打死"的保证：`PermissionManager` 的无匹配规则默认是 `ask`
    （= 挂起待批准 ⇒ 拒绝），若默认就启用，开箱即用（个人版、零规则）的**所有**
    派发都会被拒。实测依据见 `core/security/permission.py` 的默认返回。
    """
    _set_enforce(monkeypatch, False)
    # 即便存在一条 deny 规则，关闭时也必须放行（门不动任何行为）
    assert _gate(permission_manager=_FakePM("deny")) is None


def test_gate_denies_on_deny_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enforce(monkeypatch, True)
    blocked = _gate(permission_manager=_FakePM("deny", reason="nope"))
    assert blocked is not None
    assert blocked.exit_code == PERMISSION_DENIED_EXIT_CODE
    assert "Permission denied" in (blocked.error or "") and "nope" in (blocked.error or "")


def test_gate_asks_human_and_denies_until_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ask` 不是放行：建人工审批请求，并按拒绝返回（fail-closed）。"""
    _set_enforce(monkeypatch, True)
    created: list[dict] = []

    import maop.core.agent.delegation.human_proxy as hp_mod

    class _HP:
        def __init__(self, **_kw) -> None: ...
        def request(self, **kw):
            created.append(kw)
            return "req-123"

    monkeypatch.setattr(hp_mod, "HumanProxy", _HP)
    blocked = _gate(permission_manager=_FakePM("ask"))
    assert blocked is not None and blocked.exit_code == PERMISSION_DENIED_EXIT_CODE
    assert "pending human approval" in (blocked.error or "")
    assert created, "ask 必须真的建了人工审批请求，否则人工永远看不到"


def test_gate_fails_closed_when_permission_check_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """安全检查崩溃绝不能等于放行。"""
    _set_enforce(monkeypatch, True)

    class _BoomPM:
        def check(self, agent: str, action: str = "*"):
            raise RuntimeError("rule table corrupted")

    blocked = _gate(permission_manager=_BoomPM())
    assert blocked is not None and blocked.exit_code == PERMISSION_DENIED_EXIT_CODE
    assert "fail" in (blocked.error or "").lower()


def test_gate_allows_when_rule_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    """正对照：allow 时放行 —— 否则上面的拒绝用例可能因为"总是拒绝"而假绿。"""
    _set_enforce(monkeypatch, True)
    assert _gate(permission_manager=_FakePM("allow")) is None


def test_gate_is_fail_closed_when_no_rule_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实 PermissionManager：无匹配规则 = ask = 拒绝。

    这条同时是**启用前的说明书**：置 `MAOP_PERMISSION_ENFORCE=1` 之前必须先给要用的
    agent 加 allow 规则，否则第一批派发会全被拒。
    """
    _set_enforce(monkeypatch, True)
    from maop.core.security.permission import PermissionManager

    pm = PermissionManager(root_dir=".")
    blocked = _gate(agent="never-configured-agent", permission_manager=pm)
    assert blocked is not None and blocked.exit_code == PERMISSION_DENIED_EXIT_CODE
    assert "pending human approval" in (blocked.error or "")


def test_gate_allows_after_an_allow_rule_is_added(monkeypatch: pytest.MonkeyPatch) -> None:
    """补上 allow 规则后同一 agent 放行 —— 证明上一条不是"永远拒绝"。"""
    _set_enforce(monkeypatch, True)
    from maop.core.security.permission import PermissionManager

    pm = PermissionManager(root_dir=".")
    pm.add_rule(agent="configured-agent", action="*", decision="allow", reason="seed")
    assert _gate(agent="configured-agent", permission_manager=pm) is None


def test_pre_dispatch_hook_can_veto(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enforce(monkeypatch, True)
    import maop.core.agent.plugins_hooks.hook_manager as hm_mod

    async def _trigger(_event, _payload):
        return [SimpleNamespace(decision="deny", hook_id="hook-x", error="forbidden by policy")]

    monkeypatch.setattr(hm_mod, "get_hook_manager", lambda: SimpleNamespace(trigger=_trigger))
    blocked = _gate(permission_manager=_FakePM("allow"))
    assert blocked is not None and blocked.exit_code == PERMISSION_DENIED_EXIT_CODE
    assert "hook-x" in (blocked.error or "")


# ── 接线层：门必须在唯一漏斗上，且在 driver 之前 ──────────────────────


def _spy_impl(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """把 `_dispatch_impl` 换成哨兵：只记录"有没有被走到"。"""
    reached: list[tuple] = []

    async def _impl(self, agent, task, **kw):
        reached.append((agent, task))
        return DispatchResult(
            result=new_result(agent=agent, task=task, exit_code=0, stdout="ran"),
        )

    monkeypatch.setattr(Dispatcher, "_dispatch_impl", _impl)
    return reached


def test_dispatch_denies_before_reaching_the_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """核心接线证明：门拒绝时**不进入** `_dispatch_impl`（于是不落任何 driver）。"""
    _set_enforce(monkeypatch, True)
    reached = _spy_impl(monkeypatch)
    from maop.core.security.permission import PermissionManager

    PermissionManager(root_dir=".").add_rule(
        agent="blocked-agent", action="*", decision="deny", reason="unit-test deny",
    )

    res = asyncio.run(Dispatcher().dispatch(agent="blocked-agent", task="t"))
    assert res.result.exit_code == PERMISSION_DENIED_EXIT_CODE, res.result
    assert "Permission denied" in (res.result.error or "")
    assert reached == [], "门拒绝了却仍然走到了 driver —— 接线没生效"


def test_dispatch_proceeds_when_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """正对照：allow 时必须真的走到 `_dispatch_impl`。

    没有这条，`test_dispatch_denies_before_reaching_the_driver` 在"dispatch 里根本没接
    门、而是被别的原因短路"时也可能绿。
    """
    _set_enforce(monkeypatch, True)
    reached = _spy_impl(monkeypatch)
    from maop.core.security.permission import PermissionManager

    PermissionManager(root_dir=".").add_rule(
        agent="ok-agent", action="*", decision="allow", reason="unit-test allow",
    )

    res = asyncio.run(Dispatcher().dispatch(agent="ok-agent", task="t"))
    assert res.result.exit_code == 0, res.result
    assert reached == [("ok-agent", "t")], "allow 了却没到 driver"


def test_dispatch_unchanged_when_gate_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认关 + 存在 deny 规则 ⇒ 行为与历史一致（仍会走到 driver）。"""
    _set_enforce(monkeypatch, False)
    reached = _spy_impl(monkeypatch)
    from maop.core.security.permission import PermissionManager

    PermissionManager(root_dir=".").add_rule(
        agent="whatever-agent", action="*", decision="deny", reason="should be ignored",
    )

    res = asyncio.run(Dispatcher().dispatch(agent="whatever-agent", task="t"))
    assert res.result.exit_code == 0, res.result
    assert reached == [("whatever-agent", "t")]


def test_denied_dispatch_is_not_recorded_as_agent_performance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """被拒的派发没到过 driver，不该污染自适应路由的性能统计。"""
    _set_enforce(monkeypatch, True)
    _spy_impl(monkeypatch)
    recorded: list[str] = []
    monkeypatch.setattr(
        Dispatcher, "_record_agent_performance",
        lambda self, agent, routing_key, result: recorded.append(agent),
    )
    from maop.core.security.permission import PermissionManager

    PermissionManager(root_dir=".").add_rule(
        agent="blocked-agent", action="*", decision="deny", reason="unit-test deny",
    )
    asyncio.run(Dispatcher().dispatch(agent="blocked-agent", task="t"))
    assert recorded == [], "被拒的派发被记进了 agent 性能统计"
