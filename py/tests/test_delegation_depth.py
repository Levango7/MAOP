"""Cross-process delegation-depth guard tests."""

from __future__ import annotations

import pytest

from maop.core.agent.adapters.cli_adapter import CLIAdapter, CLIAdapterConfig
from maop.core.agent.delegation import depth
from maop.delegate.drivers import _agent_env
from maop.delegate.models import AgentConfig


def test_current_depth_reads_env(monkeypatch):
    monkeypatch.setenv(depth.DEPTH_ENV, "2")
    assert depth.current_depth() == 2


def test_current_depth_ignores_garbage(monkeypatch):
    monkeypatch.setenv(depth.DEPTH_ENV, "not-a-number")
    assert depth.current_depth() == 0


def test_next_depth_env_increments_and_preserves_base(monkeypatch):
    monkeypatch.setenv(depth.DEPTH_ENV, "2")
    env = depth.next_depth_env({"FOO": "bar"})
    assert env["FOO"] == "bar"
    assert env[depth.DEPTH_ENV] == "3"


def test_agent_env_injects_depth_even_without_config_env(monkeypatch):
    monkeypatch.setenv(depth.DEPTH_ENV, "1")
    env = _agent_env(AgentConfig(name="x"))
    assert env[depth.DEPTH_ENV] == "2"


def test_adapter_env_cannot_block_depth(monkeypatch):
    monkeypatch.setenv(depth.DEPTH_ENV, "0")
    adapter = CLIAdapter(
        CLIAdapterConfig(command="python", blocked_env=[depth.DEPTH_ENV])
    )
    env = adapter._build_env()
    assert env[depth.DEPTH_ENV] == "1"


def test_maop_run_refuses_at_cap_inherited_from_env(monkeypatch, capsys):
    from maop import cli

    monkeypatch.setenv(depth.DEPTH_ENV, str(depth.MAX_SELF_DELEGATION_DEPTH))
    with pytest.raises(SystemExit) as exc:
        cli.cmd_run("task")
    assert exc.value.code == 1
    assert "hard cap" in capsys.readouterr().err
