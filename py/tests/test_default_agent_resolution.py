"""routing 兜底 agent 解析回归（2026-11-07 质量排查第一轮）.

动机：兜底此前恒定 ``codex``——用户环境无 codex 时首轮 dispatch 打到一个
不存在的 agent，白耗 turn 再靠 failover 兜回（实测 ``turns=4`` 部分归因）。
新语义：env 显式值优先 → 在册 codex 优先（保持历史行为）→ 首个 enabled 且
cli 非空的 agent → 字面 codex（如实报错，不静默换人）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from maop.maop_plan import resolve_default_agent


def _spec(cli: str = "claude", enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(cli=cli, enabled=enabled)


def _cfg(**agents: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(agents=dict(agents))


def test_env_value_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAOP_DEFAULT_AGENT", "my-agent")
    assert resolve_default_agent(_cfg(codex=_spec())) == "my-agent"


def test_codex_preferred_when_enabled_in_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAOP_DEFAULT_AGENT", raising=False)
    cfg = _cfg(aaa=_spec(), codex=_spec())
    assert resolve_default_agent(cfg) == "codex"


def test_first_usable_agent_when_no_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 codex（典型用户环境）：取首个 enabled 且 cli 非空的 agent。"""
    monkeypatch.delenv("MAOP_DEFAULT_AGENT", raising=False)
    cfg = _cfg(mycli=_spec(cli="mycli"))
    assert resolve_default_agent(cfg) == "mycli"


def test_disabled_or_emptied_agents_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAOP_DEFAULT_AGENT", raising=False)
    cfg = _cfg(
        broken=_spec(cli="", enabled=True),
        off=_spec(cli="x", enabled=False),
        good=_spec(cli="good"),
    )
    assert resolve_default_agent(cfg) == "good"


def test_literal_fallback_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """无配置时保持字面 codex（让「无可用 agent」在派发处如实报错）。"""
    monkeypatch.delenv("MAOP_DEFAULT_AGENT", raising=False)
    assert resolve_default_agent(None) == "codex"
    assert resolve_default_agent(_cfg()) == "codex"
