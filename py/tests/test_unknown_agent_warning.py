"""``--agent`` 不在册告警回归（2026-11-07 质量排查·静默换人）.

背景：``maop run --agent claude`` 而 claude 不在 agents.yaml 在册时，首轮
dispatch 失败并沿 failover 链兜到别的 agent 执行——白耗 turn 且「请求的
agent 与实际执行的 agent 不一致」而无任何提示（实测 turns=4 的根因之一）。
入口告警是本修复的交付物；failover 机制本身保留。
"""

from __future__ import annotations

from pathlib import Path

from maop.cli_run import unknown_agent_warning


def _write_config(tmp_path: Path, agent_names: list[str]) -> Path:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    lines = ["agents:"]
    for name in agent_names:
        lines += [
            f"  {name}:",
            "    cli: echo",
            "    driver: cli",
        ]
    (home / "config" / "agents.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return home


def test_unknown_agent_gets_warning_with_known_names(tmp_path: Path) -> None:
    home = _write_config(tmp_path, ["codex", "copilot"])
    warning = unknown_agent_warning("claude", home)
    assert warning is not None
    assert "--agent 'claude'" in warning
    assert "codex" in warning and "copilot" in warning


def test_known_agent_no_warning(tmp_path: Path) -> None:
    home = _write_config(tmp_path, ["codex", "copilot"])
    assert unknown_agent_warning("codex", home) is None


def test_empty_agent_no_warning(tmp_path: Path) -> None:
    home = _write_config(tmp_path, ["codex"])
    assert unknown_agent_warning("", home) is None


def test_missing_config_no_warning(tmp_path: Path) -> None:
    """配置不可读：告警尽力而为，绝不阻断（返回 None）。"""
    assert unknown_agent_warning("claude", tmp_path / "nonexistent") is None


def test_config_without_agents_no_warning(tmp_path: Path) -> None:
    home = tmp_path / "empty-home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    assert unknown_agent_warning("claude", home) is None
