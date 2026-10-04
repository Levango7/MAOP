"""Tests for delegate drivers — CLI, PowerShell, cmd, Python, and wrapper drivers."""
from __future__ import annotations

import shutil
import sys

import pytest

_HAS_POWERSHELL = sys.platform == "win32" or bool(shutil.which("pwsh")) or bool(shutil.which("powershell"))
# cmd.exe driver 是 Windows 专属语义（create_subprocess_exec("cmd", "/c", ...)），
# 非 Windows 平台无 cmd.exe → FileNotFoundError。仅 Windows 上运行。
_IS_WINDOWS = sys.platform == "win32"

from maop.delegate.drivers import (
    _agent_env,
    _run_cli,
    _run_cmd,
    _run_powershell,
    _run_python,
    _run_wrapper,
)
from maop.delegate.models import AgentConfig


def _config(**overrides) -> AgentConfig:
    defaults = {"name": "test-agent", "cli": "echo", "driver": "cli", "model": "test-model"}
    defaults.update(overrides)
    return AgentConfig(**defaults)


class TestRunCli:
    @pytest.mark.asyncio
    async def test_cli_success(self):
        config = _config(cli="python", cli_args="-c \"print('hello')\"")
        result = await _run_cli(config, "test", 10, ".", "t1")
        assert result.exit_code == 0
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_cli_not_found(self):
        config = _config(cli="nonexistent_command_xyz_12345")
        result = await _run_cli(config, "test", 10, ".", "t1")
        assert result.exit_code == -2
        assert "not found" in result.error.lower() or "error" in result.error.lower()

    @pytest.mark.asyncio
    async def test_cli_result_fields(self):
        config = _config(cli="python", cli_args="-c \"print('ok')\"")
        result = await _run_cli(config, "test", 10, ".", "t1")
        assert result.agent == "test-agent"
        assert result.driver == "cli"
        assert result.trace_id == "t1"
        assert result.duration_ms >= 0


class TestRunCmd:
    @pytest.mark.asyncio
    @pytest.mark.skipif(not _IS_WINDOWS, reason="cmd.exe driver is Windows-only")
    async def test_cmd_success(self):
        config = _config(cli="echo", driver="cmd")
        result = await _run_cmd(config, "hello world", 10, ".", "t2")
        assert result.exit_code == 0
        assert result.driver == "cmd"

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _IS_WINDOWS, reason="cmd.exe driver is Windows-only")
    async def test_cmd_not_found(self):
        config = _config(cli="nonexistent_cmd_xyz", driver="cmd")
        result = await _run_cmd(config, "test", 10, ".", "t2")
        assert result.exit_code != 0


class TestRunPowershell:

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _HAS_POWERSHELL, reason="PowerShell not available on this platform")
    async def test_powershell_success(self):
        # CI windows runner 的 PowerShell 可能完全无法启动（实测冷启动 >60s
        # 超时，属于 runner 环境限制而非 driver 缺陷）。预探测 15s：
        # 不可用则 skip，避免每次 CI 空等 60s 后失败。
        import asyncio

        # 与 maop.delegate.drivers._run_powershell 相同的解析逻辑：Windows →
        # powershell.exe；POSIX（ubuntu-latest 预装 PowerShell Core）→ pwsh。
        # 写死 "powershell" 在 ubuntu 上 FileNotFoundError → 测试 ERROR
        # （_HAS_POWERSHELL 因 shutil.which("pwsh") 为 True 不会 skip）。
        ps_bin = "powershell" if sys.platform == "win32" else "pwsh"
        if not shutil.which(ps_bin):
            pytest.skip(f"PowerShell binary '{ps_bin}' not found")
        try:
            probe = await asyncio.create_subprocess_exec(
                ps_bin, "-NoProfile", "-Command", "exit 0",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pytest.skip(f"PowerShell binary '{ps_bin}' not found")
        try:
            await asyncio.wait_for(probe.wait(), timeout=15)
        except asyncio.TimeoutError:
            probe.kill()
            await probe.wait()
            pytest.skip("PowerShell unavailable on this runner (startup >15s)")

        config = _config(cli="Write-Output", driver="powershell", command="Write-Output")
        result = await _run_powershell(config, "hello", 60, ".", "t3")
        assert result.exit_code == 0
        assert result.driver == "powershell"

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _HAS_POWERSHELL, reason="PowerShell not available on this platform")
    async def test_powershell_unsafe_cli_args(self):
        config = _config(
            cli="Write-Output", driver="powershell",
            command="Write-Output",
            cli_args="; rm -rf /",
        )
        result = await _run_powershell(config, "test", 10, ".", "t3")
        assert result.exit_code == -1
        assert "unsafe" in result.error.lower() or "Rejected" in result.error


class TestRunPython:
    @pytest.mark.asyncio
    async def test_python_success(self):
        config = _config(cli="json.tool", driver="python", cli_args="'{\"a\":1}'")
        result = await _run_python(config, "test", 10, ".", "t4")
        assert result.driver == "python"

    @pytest.mark.asyncio
    async def test_python_not_found(self):
        config = _config(cli="nonexistent_module_xyz", driver="python")
        result = await _run_python(config, "test", 10, ".", "t4")
        assert result.exit_code != 0


class TestRunWrapper:
    @pytest.mark.asyncio
    async def test_wrapper_missing_script(self):
        config = _config(driver="wrapper", wrapper="nonexistent_script.ps1")
        result = await _run_wrapper(config, "test", 10, ".", "t5")
        assert result.driver == "wrapper"


class TestAgentConfig:
    def test_defaults(self):
        config = AgentConfig(name="test")
        assert config.cli == ""
        assert config.driver == "cli"
        assert config.timeout_s == 180
        assert config.model is None
        assert config.capabilities == []
        assert config.env == {}

    def test_with_values(self):
        config = AgentConfig(
            name="my-agent", cli="claude", driver="cli",
            cli_args="-p '{task}'", timeout_s=60, model="gpt-4",
        )
        assert config.name == "my-agent"
        assert config.cli_args == "-p '{task}'"


# ── AgentConfig.env 接线（2026-10-04）──────────────────────────────────
#
# 背景：``AgentConfig.env`` 在 models.py 里声明、可以写在 agents.yaml，但当时
# **全库无一处读取** —— 配了不生效，子进程静默继承服务器全量环境。这类"字段
# 存在≠能力存在"只有让真实子进程把值打出来才守得住（mock create_subprocess_exec
# 会对"漏传 env"失明：它只看调用参数，不看子进程实际拿到什么）。

_ENV_PROBE_KEY = "MAOP_TEST_AGENT_ENV_PROBE"


def _as_posix(path) -> str:
    """给 driver 传路径时统一用正斜杠。

    Windows 上 ``cli_args`` 会被 ``shlex.split(..., posix=True)`` 切分，反斜杠
    会被当转义吃掉（这是既存缺陷，已单独上报，不在本改动范围内）；正斜杠在两
    平台都被接受，故测试用它，避免把另一个缺陷混进来。
    """
    return str(path).replace("\\", "/")


def _write_env_probe(tmp_path) -> object:
    """写一个把指定环境变量打到 stdout 的子进程脚本。"""
    probe = tmp_path / "env_probe.py"
    probe.write_text(
        "import os, sys\n"
        f"sys.stdout.write(os.environ.get({_ENV_PROBE_KEY!r}, 'MISSING'))\n",
        encoding="utf-8",
    )
    return probe


class TestAgentEnvWiring:
    """``AgentConfig.env`` 必须真的传进子进程。"""

    @pytest.mark.asyncio
    async def test_configured_env_reaches_the_child_process(self, tmp_path):
        """真实子进程断言：配了 env 就一定要看得到。

        把 ``env=_agent_env(config)`` 从 ``_run_cli`` 拿掉这条即红（实测过）。
        """
        config = _config(
            cli=sys.executable,
            cli_args=_as_posix(_write_env_probe(tmp_path)),
            env={_ENV_PROBE_KEY: "wired-ok"},
        )
        result = await _run_cli(config, "ignored", 30, ".", "t-env-on")
        assert result.exit_code == 0, f"stderr={result.stderr!r}"
        assert result.stdout == "wired-ok"

    @pytest.mark.asyncio
    async def test_env_is_absent_when_not_configured(self, tmp_path, monkeypatch):
        """对照组：没配 env 时子进程看不到该变量。

        没有这条，上面那条可能因为"变量本来就在宿主环境里"而假绿。
        """
        monkeypatch.delenv(_ENV_PROBE_KEY, raising=False)
        config = _config(
            cli=sys.executable,
            cli_args=_as_posix(_write_env_probe(tmp_path)),
        )
        result = await _run_cli(config, "ignored", 30, ".", "t-env-off")
        assert result.exit_code == 0, f"stderr={result.stderr!r}"
        assert result.stdout == "MISSING"

    def test_agent_env_is_none_when_nothing_configured(self):
        """空 env → ``None``（= 不传 env，走默认继承）。

        这是向后兼容契约：只有 agents.yaml 明写了 env 才改变行为。若改成无脑
        ``{**os.environ}``，"配了才生效"的语义与默认继承路径就一起没了。
        """
        assert _agent_env(_config()) is None
        assert _agent_env(_config(env={})) is None

    def test_agent_env_merges_and_agent_side_wins(self, monkeypatch):
        """合并语义：以服务器环境为底，agent 侧同名键覆盖。"""
        monkeypatch.setenv("MAOP_TEST_BASE_KEY", "server-value")
        merged = _agent_env(_config(env={
            "MAOP_TEST_BASE_KEY": "agent-value",
            "MAOP_TEST_EXTRA_KEY": "extra",
        }))
        assert merged is not None
        assert merged["MAOP_TEST_BASE_KEY"] == "agent-value"
        assert merged["MAOP_TEST_EXTRA_KEY"] == "extra"
        assert "PATH" in merged or "Path" in merged, "基础继承面不能被砍掉"

    def test_every_subprocess_call_passes_agent_env(self):
        """结构守卫：drivers.py 每一处 ``create_subprocess_exec`` 都要带上 env。

        行为测试只能覆盖被真正跑过的 driver；新加第六个 driver 时忘了 env=，
        只有这条会红。用 AST 读真实源码（不 mock —— mock 对"漏传"这一维失明）。
        """
        import ast
        import pathlib

        src_path = pathlib.Path(__file__).resolve().parents[1] / "maop" / "delegate" / "drivers.py"
        tree = ast.parse(src_path.read_text(encoding="utf-8"))

        def _callee_name(func: ast.AST) -> str:
            if isinstance(func, ast.Attribute):
                return func.attr
            if isinstance(func, ast.Name):
                return func.id
            return ""

        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _callee_name(node.func) == "create_subprocess_exec"
        ]
        # 先证明选择器没写空：否则下面"没有缺 env 的调用"会因为一个都没匹配到而假绿。
        assert calls, (
            "在 drivers.py 里没匹配到 create_subprocess_exec —— 选择器失效，"
            "这条守卫会静默变成空断言"
        )
        missing = [
            node.lineno for node in calls
            if not any(kw.arg == "env" for kw in node.keywords)
        ]
        assert not missing, (
            f"drivers.py 第 {missing} 行的 create_subprocess_exec 没传 env= —— "
            "AgentConfig.env 在这些 driver 上会重新变成死字段"
        )
