"""Bridge 端点的**按需注册**接线。

背景：`AgentProxy.register()` 在全仓只有定义处和测试调用 —— 注册表**从来是空的**。
于是 `/api/bridge/adapters` 恒返回空表、`/api/bridge/call` 对任何名字都回 404。
那个 404 是"适配器未找到"的正常语义（端点本身写得没问题：鉴权、Pydantic 请求模型、
错误脱敏都在），坏的是从来没人注册。

现在 `bridge_call` 会先按需从 `agents.yaml` 构建并注册同名 agent。本文件钉住这条接线。
"""

from __future__ import annotations

from typing import Any

import pytest

from maop.dashboard.services import agent_service
from maop.delegate.models import AgentConfig


class _FakeBridge:
    """只记录"注册了什么、调用了什么"，不碰真适配器。"""

    def __init__(self, preexisting: set[str] | None = None) -> None:
        self._adapters: dict[str, Any] = {n: object() for n in (preexisting or set())}
        self.registered: list[str] = []
        self.calls: list[tuple[str, str]] = []

    def get(self, name: str) -> Any:
        return self._adapters.get(name)

    def register(self, name: str, adapter: Any) -> None:
        self._adapters[name] = adapter
        self.registered.append(name)

    def list_adapters(self) -> list[str]:
        return list(self._adapters.keys())

    def get_status(self, name: str) -> Any:
        from types import SimpleNamespace
        return SimpleNamespace(model_dump=lambda: {"name": name})

    def call(self, name: str, task: str, **kwargs: Any) -> str:
        if name not in self._adapters:
            raise KeyError(name)
        self.calls.append((name, task))
        return f"ran:{name}"


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch):
    """注入假 bridge，并在用例结束后把全局单例还原（避免污染别的用例）。"""
    fake = _FakeBridge()
    original = agent_service._agent_proxy
    agent_service._set_bridge(fake)
    # 让"从配置解析"这一步可控：默认配置里什么都没有
    monkeypatch.setattr("maop.config.loader.ConfigLoader.load", lambda self: object())
    monkeypatch.setattr("maop.delegate.agent_resolver.AgentResolver.resolve",
                        lambda self, name: None)
    try:
        yield fake
    finally:
        agent_service._set_bridge(original)


def _make_config_resolve_to(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setattr(
        "maop.delegate.agent_resolver.AgentResolver.resolve",
        lambda self, asked: AgentConfig(name=name, cli="echo", driver="cli") if asked == name else None,
    )


class _SentinelAdapter:
    pass


@pytest.fixture
def fake_build(monkeypatch: pytest.MonkeyPatch) -> list[AgentConfig]:
    built: list[AgentConfig] = []

    def _build(cfg: AgentConfig) -> Any:
        built.append(cfg)
        return _SentinelAdapter()

    monkeypatch.setattr("maop.core.agent.adapters.adapter_factory.build_adapter", _build)
    return built


def test_configured_agent_is_registered_on_demand(bridge, monkeypatch, fake_build) -> None:
    """核心：配置里有的 agent，调用时先建再注册，然后真的被调用。"""
    _make_config_resolve_to(monkeypatch, "claude")
    out = agent_service.bridge_call("claude", "hello", {})
    assert bridge.registered == ["claude"], "没有按需注册 —— 端点会一直 404"
    assert bridge.calls == [("claude", "hello")]
    assert out == "ran:claude"
    assert [c.name for c in fake_build] == ["claude"]


def test_unknown_agent_still_404s(bridge) -> None:
    """配置里也没有的名字仍要 KeyError（路由层转 404）—— 不许凭空造适配器。"""
    with pytest.raises(KeyError):
        agent_service.bridge_call("no-such-agent", "t", {})
    assert bridge.registered == []


def test_already_registered_agent_is_not_rebuilt(bridge, monkeypatch, fake_build) -> None:
    """已注册的不重复构建（避免每次调用都 new 一个适配器、丢掉连接状态）。"""
    _set = _FakeBridge(preexisting={"claude"})
    agent_service._set_bridge(_set)
    try:
        _make_config_resolve_to(monkeypatch, "claude")
        agent_service.bridge_call("claude", "hi", {})
        assert _set.registered == []
        assert fake_build == []
    finally:
        agent_service._set_bridge(bridge)


def test_registration_failure_does_not_swallow_the_404(bridge, monkeypatch) -> None:
    """构建适配器抛异常时：记日志、不注册，最终仍是 KeyError（不是 500 里塞假成功）。"""
    _make_config_resolve_to(monkeypatch, "claude")

    def _boom(cfg: AgentConfig) -> Any:
        raise RuntimeError("adapter construction exploded")

    monkeypatch.setattr("maop.core.agent.adapters.adapter_factory.build_adapter", _boom)
    with pytest.raises(KeyError):
        agent_service.bridge_call("claude", "t", {})
    assert bridge.registered == []


def test_adapters_listing_reflects_registered_only(bridge) -> None:
    """`/api/bridge/adapters` 列出的是"已注册"的 —— 按需注册是惰性的，不预建 30+ 适配器。"""
    out = agent_service.bridge_adapters()
    assert out["count"] == 0
    assert out["adapters"] == []
