"""主动监督器 / 辩论的开关接线（2026-11-10 接线审计的钉子）。

背景
----
这两项此前是「能力建好了，但**只能改 Python 代码**才能打开」：

- Supervisor：全仓无任何 settings/env 开关，唯一开启途径是把
  ``Supervisor(...)`` + ``set_supervisor(...)`` 写进代码。后果：patrol 循环
  永不启动、6 个控制动作永不自动发生、``/api/supervisor/*`` 恒 404。
- DebateDispatcher：只有 ``EvolutionLoop`` 的构造参数，无 env 开关，而且
  全仓**没有一处传它** ⇒ ``/api/debate/*`` 恒 404、演化里的 DEBATE 阶段永不运行。

缺陷本体不是「默认关着」（默认关是对的：一个有破坏性、一个是成本乘数），
而是**没有开关**。所以下面钉的是「开关真的能开、且开的方式分层」，
以及「关着的时候 API 能区分出『未开启』而不是笼统 404」。
"""

from __future__ import annotations

import pytest

from maop.core.scheduling.supervisor import Supervisor
from maop.core.scheduling.supervisor_models import (
    AlertLevel,
    HealthProbe,
    SupervisorAction,
    SupervisorRule,
)


def _rule(action: SupervisorAction, **params) -> SupervisorRule:
    return SupervisorRule(
        rule_id=f"rule.{action.value}",
        name=f"{action.value}-rule",
        action=action,
        alert_level=AlertLevel.WARNING,
        condition={"failure_rate_gt": 0.5},
        action_params=params,
    )


def _probe(agent: str = "a1") -> HealthProbe:
    return HealthProbe(
        agent_id=agent, reachable=False, failure_rate=0.9,
        avg_latency=1.0, timeout_rate=0.0, breaker_open=False,
    )


# ── ① 破坏性动作闸 ──────────────────────────────────────────────


class TestActionsArmedGate:
    @pytest.mark.asyncio
    async def test_alert_always_runs_when_not_armed(self) -> None:
        """ALERT 是安全动作：未 arm 也必须照常发告警。"""
        sup = Supervisor(actions_armed=False)
        calls: list[dict] = []
        sup.warn = _spy(calls)  # type: ignore[method-assign]
        await sup._apply_rule(_rule(SupervisorAction.ALERT), _probe())
        assert len(calls) == 1
        assert calls[0]["reason"] == "alert-rule"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "action", [SupervisorAction.DEGRADE, SupervisorAction.REPLACE,
                   SupervisorAction.TERMINATE, SupervisorAction.UPGRADE],
    )
    async def test_destructive_action_is_suppressed_and_visible(
        self, action: SupervisorAction,
    ) -> None:
        """未 arm 时：**不执行**动作，但必须留下可见的 warn。

        这是本设计最要紧的一条：静默跳过等于"把能力关掉还看不见"，
        warn-only 模式的价值恰恰在于让运营看到「监督器想做什么」。
        """
        sup = Supervisor(actions_armed=False)
        calls: list[dict] = []
        sup.warn = _spy(calls)  # type: ignore[method-assign]
        # 真实现也不该被碰到——用会炸的桩证明"确实没执行"
        for name in ("degrade", "replace", "terminate", "upgrade"):
            setattr(sup, name, _boom(name))

        await sup._apply_rule(
            _rule(action, factor=0.5, replacement="a2", target_version="9.9.9"),
            _probe(),
        )

        assert len(calls) == 1, "未 arm 时应只发一条 warn"
        extra = calls[0].get("extra") or {}
        assert extra.get("suppressed_action") == action.value
        assert extra.get("armed") is False
        assert "MAOP_SUPERVISOR_ACTIONS_ARMED" in (extra.get("hint") or "")
        assert "would " in calls[0]["reason"]

    @pytest.mark.asyncio
    async def test_armed_allows_degrade_to_execute(self) -> None:
        """arm 之后动作真的执行（否则开关就是装饰）。"""
        sup = Supervisor(actions_armed=True)
        seen: list[tuple] = []

        async def _degrade(agent_id, *, factor, reason, triggered_by):
            seen.append((agent_id, factor, triggered_by))

        sup.degrade = _degrade  # type: ignore[method-assign]
        await sup._apply_rule(_rule(SupervisorAction.DEGRADE, factor=0.5), _probe())
        assert seen == [("a1", 0.5, "patrol")]

    def test_armed_defaults_to_false(self) -> None:
        """默认必须**未 arm**：它会真杀 agent，不能靠默认值替人决定。"""
        assert Supervisor()._actions_armed is False


# ── ② 辩论：容器工厂受开关门控 ──────────────────────────────────


class TestDebateContainerFactory:
    def test_disabled_raises_so_router_can_say_why(self, monkeypatch) -> None:
        """关着时显式报错（而非返回哑对象）⇒ 路由把 None 映射成 404。

        若返回一个"哑 dispatcher"，403/404 就会消失、用户看到的是一个
        永远不产出结论的辩论——比 404 坏得多。
        """
        from maop.core.reliability.services import ServiceContainer

        monkeypatch.setattr(
            "maop.config.settings.get_settings",
            lambda: _Settings(debate_enabled=False),
        )
        container = ServiceContainer(root_dir=".")
        assert container.get("debate_dispatcher", raise_on_failure=False) is None

    def test_disabled_raises_when_failure_required(self, monkeypatch) -> None:
        from maop.core.reliability.services import ServiceContainer

        monkeypatch.setattr(
            "maop.config.settings.get_settings",
            lambda: _Settings(debate_enabled=False),
        )
        container = ServiceContainer(root_dir=".")
        with pytest.raises(RuntimeError, match="MAOP_DEBATE_ENABLED"):
            container.get("debate_dispatcher")

    def test_factory_is_registered(self) -> None:
        """工厂必须**在册**——缺陷本体就是"取了但没注册"。"""
        from maop.core.reliability.services import ServiceContainer

        container = ServiceContainer(root_dir=".")
        assert "debate_dispatcher" in container._factories


# ── ③ 演化循环跟随开关（默认关 ⇒ 零行为变化）────────────────────


class TestEvolutionLoopFollowsSwitch:
    def test_default_off_keeps_debate_disabled(self, monkeypatch, tmp_path) -> None:
        """开关关着时：显式传 False 与不传，行为一致（都不跑 debate）。"""
        from maop.core.evolution.evolution_loop import EvolutionLoop

        monkeypatch.setattr(
            "maop.config.settings.get_settings",
            lambda: _Settings(debate_enabled=False),
        )
        loop = EvolutionLoop(root_dir=tmp_path)
        assert loop._debate_enabled is False
        assert loop._debate_dispatcher is None

    def test_explicit_param_wins_over_settings(self, monkeypatch, tmp_path) -> None:
        """显式传参优先于开关（调用方仍能强制关掉）。"""
        from maop.core.evolution.evolution_loop import EvolutionLoop

        monkeypatch.setattr(
            "maop.config.settings.get_settings",
            lambda: _Settings(debate_enabled=True),
        )
        loop = EvolutionLoop(root_dir=tmp_path, debate_enabled=False)
        assert loop._debate_enabled is False


# ── 辅助 ────────────────────────────────────────────────────────


class _Settings:
    """最小 settings 替身（只带本组测试关心的字段）。"""

    def __init__(self, *, debate_enabled: bool = False,
                 supervisor_enabled: bool = False,
                 supervisor_actions_armed: bool = False) -> None:
        self.debate_enabled = debate_enabled
        self.supervisor_enabled = supervisor_enabled
        self.supervisor_actions_armed = supervisor_actions_armed


def _spy(sink: list[dict]):
    async def _warn(agent_id, reason, *, level=None, extra=None):
        sink.append({"agent_id": agent_id, "reason": reason, "extra": extra})
    return _warn


def _boom(name: str):
    async def _f(*a, **kw):
        raise AssertionError(f"{name} 不该被调用（未 arm 时不得执行破坏性动作）")
    return _f
