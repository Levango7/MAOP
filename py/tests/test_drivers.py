"""Tests for delegate drivers — CLI, PowerShell, cmd, Python, and wrapper drivers."""
from __future__ import annotations

import pytest

from maop.delegate.drivers import (
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
    async def test_cmd_success(self):
        config = _config(cli="echo", driver="cmd")
        result = await _run_cmd(config, "hello world", 10, ".", "t2")
        assert result.exit_code == 0
        assert result.driver == "cmd"

    @pytest.mark.asyncio
    async def test_cmd_not_found(self):
        config = _config(cli="nonexistent_cmd_xyz", driver="cmd")
        result = await _run_cmd(config, "test", 10, ".", "t2")
        assert result.exit_code != 0


class TestRunPowershell:
    @pytest.mark.asyncio
    async def test_powershell_success(self):
        config = _config(cli="Write-Output", driver="powershell", command="Write-Output")
        result = await _run_powershell(config, "hello", 10, ".", "t3")
        assert result.exit_code == 0
        assert result.driver == "powershell"

    @pytest.mark.asyncio
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
