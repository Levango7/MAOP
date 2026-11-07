"""``maop run`` 派发前自检（CLI 可用性）回归（2026-11-08）.

动机（首公里·CLI 未就绪）：``agents.yaml`` 注册约 30 个外部 CLI，用户机器上
通常只装了几个。被 ``--agent`` 指定的那个若没装，现状是走完 plan → dispatch
→ ``FileNotFoundError`` → ``exit_code=-2``：先白等一轮编排，再拿到一句
``Command not found``。``cli_availability_error`` 把判断提到派发之前。

本文件只测判定函数本身；``cmd_run`` 的接线（打印 + ``sys.exit(1)``）由
``tests/test_cli.py`` 的既有运行路径覆盖。
"""

from __future__ import annotations

import sys
from pathlib import Path

from maop.cli_run import cli_availability_error


def _write_config(tmp_path: Path, entries: dict[str, dict[str, str]]) -> Path:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    lines = ["agents:"]
    for name, fields in entries.items():
        lines.append(f"  {name}:")
        for key, value in fields.items():
            lines.append(f"    {key}: {value}")
    (home / "config" / "agents.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return home


def test_missing_cli_blocks_before_dispatch(tmp_path: Path) -> None:
    home = _write_config(
        tmp_path,
        {"ghost": {"cli": "maop-definitely-not-a-real-cli-xyz", "driver": "cli"}},
    )
    error = cli_availability_error("ghost", home)
    assert error is not None
    assert "[BLOCK]" in error
    assert "maop-definitely-not-a-real-cli-xyz" in error
    assert "ghost" in error


def test_available_cli_passes(tmp_path: Path) -> None:
    """用解释器自身的绝对路径当 cli：``shutil.which`` 对绝对路径直接判存在。"""
    home = _write_config(
        tmp_path,
        {"local": {"cli": f'"{sys.executable}"', "driver": "cli"}},
    )
    assert cli_availability_error("local", home) is None


def test_unknown_agent_is_not_this_function_business(tmp_path: Path) -> None:
    """不在册 ⇒ 返回 None（由 ``unknown_agent_warning`` 负责）。"""
    home = _write_config(tmp_path, {"codex": {"cli": "maop-nope-xyz", "driver": "cli"}})
    assert cli_availability_error("claude", home) is None


def test_non_cli_driver_is_skipped(tmp_path: Path) -> None:
    home = _write_config(
        tmp_path,
        {"desk": {"cli": "maop-nope-xyz", "driver": "desktop_app"}},
    )
    assert cli_availability_error("desk", home) is None


def test_empty_cli_is_skipped(tmp_path: Path) -> None:
    home = _write_config(tmp_path, {"blank": {"cli": '""', "driver": "cli"}})
    assert cli_availability_error("blank", home) is None


def test_python_interpreter_is_always_considered_available(tmp_path: Path) -> None:
    """``drivers.py`` 会把 python/python3 换成 ``sys.executable``，不必探 PATH。"""
    home = _write_config(tmp_path, {"py": {"cli": "python", "driver": "cli"}})
    assert cli_availability_error("py", home) is None


def test_missing_config_is_skipped(tmp_path: Path) -> None:
    """配置不可读：自检尽力而为，不阻断（与 unknown_agent_warning 同策略）。"""
    assert cli_availability_error("ghost", tmp_path / "nonexistent") is None


def test_empty_agent_is_skipped(tmp_path: Path) -> None:
    home = _write_config(tmp_path, {"ghost": {"cli": "maop-nope-xyz", "driver": "cli"}})
    assert cli_availability_error("", home) is None
