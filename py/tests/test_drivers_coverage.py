"""Coverage tests for maop.delegate.drivers — error/edge-case branches.

Covers empty-cli, invalid-args, timeout, FileNotFoundError, and generic
exception paths for each driver (cli, wrapper, powershell, cmd, python).
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maop.delegate.drivers import (
    DRIVERS,
    _run_cli,
    _run_cmd,
    _run_powershell,
    _run_python,
    _run_wrapper,
)
from maop.delegate.models import AgentConfig


def _config(**kw):
    return AgentConfig(name="test-agent", **kw)


def _mock_proc(*, stdout=b"ok", stderr=b"", returncode=0, communicate_raises=None):
    """Create a mock subprocess proc."""
    proc = MagicMock()
    proc.returncode = returncode
    if communicate_raises:
        proc.communicate = AsyncMock(side_effect=communicate_raises)
    else:
        proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


# ── CLI driver ────────────────────────────────────────────────

class TestRunCliEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_cli_returns_error(self):
        config = _config(cli="", driver="cli")
        result = await _run_cli(config, "task", 10, "", "trace")
        assert result.exit_code == -1
        assert "empty cli" in result.error

    @pytest.mark.asyncio
    async def test_whitespace_cli_returns_error(self):
        config = _config(cli="   ", driver="cli")
        result = await _run_cli(config, "task", 10, "", "trace")
        assert result.exit_code == -1

    @pytest.mark.asyncio
    async def test_invalid_cli_args_template(self):
        """Unmatched quotes in cli_args should return error, not crash."""
        config = _config(cli="echo", cli_args="-p '{task}'", driver="cli")
        # Use a template with bad quotes
        config.cli_args = "--msg 'unmatched"
        result = await _run_cli(config, "task", 10, "", "trace")
        assert result.exit_code == -1
        assert "Invalid cli_args" in result.error

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        config = _config(cli="nonexistent_cmd_xyz", cli_args="{task}", driver="cli")
        result = await _run_cli(config, "task", 10, "", "trace")
        # Should get -2 (command not found) or -5 (driver exception)
        assert result.exit_code in (-2, -5)

    @pytest.mark.asyncio
    async def test_subprocess_success(self):
        config = _config(cli="echo", cli_args="{task}", driver="cli")
        proc = _mock_proc(stdout=b"hello world", returncode=0)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await _run_cli(config, "hello world", 10, "", "trace")
        assert result.exit_code == 0
        assert "hello world" in result.stdout

    @pytest.mark.asyncio
    async def test_timeout(self):
        config = _config(cli="echo", cli_args="{task}", driver="cli")
        proc = _mock_proc()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
             patch("asyncio.wait_for", AsyncMock(side_effect=asyncio.TimeoutError())):
            result = await _run_cli(config, "task", 1, "", "trace")
        assert result.exit_code == -1
        assert "TIMEOUT" in result.error

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        config = _config(cli="echo", cli_args="{task}", driver="cli")
        with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await _run_cli(config, "task", 10, "", "trace")
        assert result.exit_code == -2
        assert "Execution error" in result.error


# ── Wrapper driver ────────────────────────────────────────────

class TestRunWrapperEdgeCases:
    @pytest.mark.asyncio
    async def test_wrapper_success_json_output(self):
        config = _config(wrapper="fake.ps1", driver="wrapper")
        json_output = json.dumps({"ok": True, "exit_code": 0, "stdout": "result"}).encode()
        proc = _mock_proc(stdout=json_output, returncode=0)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await _run_wrapper(config, "task", 10, "", "trace")
        assert result.exit_code == 0
        assert result.stdout == "result"

    @pytest.mark.asyncio
    async def test_wrapper_success_raw_output(self):
        config = _config(wrapper="fake.ps1", driver="wrapper")
        proc = _mock_proc(stdout=b"raw output", returncode=0)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await _run_wrapper(config, "task", 10, "", "trace")
        assert result.exit_code == 0
        assert "raw output" in result.stdout

    @pytest.mark.asyncio
    async def test_wrapper_timeout(self):
        config = _config(wrapper="fake.ps1", driver="wrapper")
        proc = _mock_proc()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
             patch("asyncio.wait_for", AsyncMock(side_effect=asyncio.TimeoutError())):
            result = await _run_wrapper(config, "task", 1, "", "trace")
        assert result.exit_code == -1
        assert "TIMEOUT" in result.error

    @pytest.mark.asyncio
    async def test_wrapper_file_not_found(self):
        config = _config(wrapper="fake.ps1", driver="wrapper")
        with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError("no powershell"))):
            result = await _run_wrapper(config, "task", 10, "", "trace")
        assert result.exit_code == -2
        assert "PowerShell not found" in result.error

    @pytest.mark.asyncio
    async def test_wrapper_generic_exception(self):
        config = _config(wrapper="fake.ps1", driver="wrapper")
        with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await _run_wrapper(config, "task", 10, "", "trace")
        assert result.exit_code == -2
        assert "Execution error" in result.error


# ── PowerShell driver ────────────────────────────────────────

class TestRunPowershellEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_command_returns_error(self):
        config = _config(command="", cli="", driver="powershell")
        result = await _run_powershell(config, "task", 10, "", "trace")
        assert result.exit_code == -1
        assert "empty" in result.error

    @pytest.mark.asyncio
    async def test_unsafe_cli_args_rejected(self):
        config = _config(command="agent", cli_args="--arg; rm -rf", driver="powershell")
        result = await _run_powershell(config, "task", 10, "", "trace")
        assert result.exit_code == -1
        assert "unsafe" in result.error

    @pytest.mark.asyncio
    async def test_powershell_success(self):
        config = _config(command="echo", driver="powershell")
        proc = _mock_proc(stdout=b"ps output", returncode=0)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await _run_powershell(config, "task", 10, "", "trace")
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_powershell_timeout(self):
        config = _config(command="echo", driver="powershell")
        proc = _mock_proc()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
             patch("asyncio.wait_for", AsyncMock(side_effect=asyncio.TimeoutError())):
            result = await _run_powershell(config, "task", 1, "", "trace")
        assert result.exit_code == -1

    @pytest.mark.asyncio
    async def test_powershell_file_not_found(self):
        config = _config(command="echo", driver="powershell")
        with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError("no ps"))):
            result = await _run_powershell(config, "task", 10, "", "trace")
        assert result.exit_code == -2

    @pytest.mark.asyncio
    async def test_powershell_generic_exception(self):
        config = _config(command="echo", driver="powershell")
        with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await _run_powershell(config, "task", 10, "", "trace")
        assert result.exit_code == -2

    @pytest.mark.asyncio
    async def test_powershell_with_cli_args_template(self):
        config = _config(command="agent", cli_args="-Task {task}", driver="powershell")
        proc = _mock_proc(stdout=b"ok", returncode=0)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await _run_powershell(config, "do something", 10, "", "trace")
        assert result.exit_code == 0


# ── CMD driver ────────────────────────────────────────────────

class TestRunCmdEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_cli_returns_error(self):
        config = _config(cli="", driver="cmd")
        result = await _run_cmd(config, "task", 10, "", "trace")
        assert result.exit_code == -1
        assert "empty cli" in result.error

    @pytest.mark.asyncio
    async def test_cmd_success(self):
        config = _config(cli="echo", driver="cmd")
        proc = _mock_proc(stdout=b"cmd output", returncode=0)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await _run_cmd(config, "task", 10, "", "trace")
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_cmd_timeout(self):
        config = _config(cli="echo", driver="cmd")
        proc = _mock_proc()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
             patch("asyncio.wait_for", AsyncMock(side_effect=asyncio.TimeoutError())):
            result = await _run_cmd(config, "task", 1, "", "trace")
        assert result.exit_code == -1

    @pytest.mark.asyncio
    async def test_cmd_file_not_found(self):
        config = _config(cli="echo", driver="cmd")
        with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError("no cmd"))):
            result = await _run_cmd(config, "task", 10, "", "trace")
        assert result.exit_code == -2

    @pytest.mark.asyncio
    async def test_cmd_generic_exception(self):
        config = _config(cli="echo", driver="cmd")
        with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await _run_cmd(config, "task", 10, "", "trace")
        assert result.exit_code == -2


# ── Python driver ─────────────────────────────────────────────

class TestRunPythonEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_cli_returns_error(self):
        config = _config(cli="", driver="python")
        result = await _run_python(config, "task", 10, "", "trace")
        assert result.exit_code == -1
        assert "empty cli" in result.error

    @pytest.mark.asyncio
    async def test_python_success(self):
        config = _config(cli="json.tool", cli_args='{"a":1}', driver="python")
        proc = _mock_proc(stdout=b'{"a": 1}', returncode=0)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await _run_python(config, '{"a":1}', 10, "", "trace")
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_python_timeout(self):
        config = _config(cli="json.tool", driver="python")
        proc = _mock_proc()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
             patch("asyncio.wait_for", AsyncMock(side_effect=asyncio.TimeoutError())):
            result = await _run_python(config, "task", 1, "", "trace")
        assert result.exit_code == -1

    @pytest.mark.asyncio
    async def test_python_file_not_found(self):
        config = _config(cli="nonexistent_module_xyz", driver="python")
        with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError("no module"))):
            result = await _run_python(config, "task", 10, "", "trace")
        assert result.exit_code == -2

    @pytest.mark.asyncio
    async def test_python_generic_exception(self):
        config = _config(cli="json.tool", driver="python")
        with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await _run_python(config, "task", 10, "", "trace")
        assert result.exit_code == -2


# ── DRIVERS table ─────────────────────────────────────────────

class TestDriversTable:
    def test_all_drivers_registered(self):
        assert set(DRIVERS.keys()) == {"cli", "wrapper", "powershell", "cmd", "python"}

    def test_drivers_are_coroutines(self):
        import inspect
        for name, fn in DRIVERS.items():
            assert inspect.iscoroutinefunction(fn), f"{name} should be async"