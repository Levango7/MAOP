"""Tests for MAOP.delegate.dispatcher — Agent dispatch with driver registry."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from maop.core.circuit_breaker import CircuitBreaker
from maop.core.error_schema import new_result
from maop.delegate.dispatcher import (
    AgentConfig,
    Dispatcher,
    _escape_for_cmd,
    _escape_for_ps_command,
)


class TestSecurityEscaping:
    def test_cmd_escape_special_chars(self):
        result = _escape_for_cmd("hello & world | test")
        assert "^&" in result
        assert "^|" in result

    def test_cmd_escape_caret(self):
        result = _escape_for_cmd("foo^bar")
        assert "^^" in result

    def test_ps_command_escape(self):
        result = _escape_for_ps_command("it's a test")
        assert result.startswith("'")
        assert result.endswith("'")
        assert "''" in result  # single quote escaped


class TestAgentConfig:
    def test_defaults(self):
        cfg = AgentConfig(name="claude")
        assert cfg.driver == "cli"
        assert cfg.timeout_s == 180
        assert cfg.capabilities == []
        # F2a (2026-07-22, Phase F): provider field defaults to empty.
        assert cfg.provider == ""

    def test_full_config(self):
        cfg = AgentConfig(
            name="kimi", cli="kimi-cli", driver="wrapper",
            cli_args="--model moonshot", timeout_s=300,
            model="moonshot-v1", wrapper="kimi-wrapper.ps1",
            provider="moonshot",  # F2a (Phase F): LLM provider name
        )
        assert cfg.driver == "wrapper"
        assert cfg.timeout_s == 300
        assert cfg.provider == "moonshot"


class TestDispatcher:
    def test_dispatch_unknown_agent(self):
        """Dispatching to an unconfigured agent returns error result."""
        dispatcher = Dispatcher()
        result = asyncio.run(
            dispatcher.dispatch(agent="nonexistent", task="test")
        )
        assert not result.result.is_success()
        assert "not found" in (result.result.error or "")
        assert result.driver_used == ""

    def test_dispatch_with_config(self):
        """Dispatcher resolves agent from config."""
        # Create a mock config with agents
        mock_agent = MagicMock()
        mock_agent.name = "claude"
        mock_agent.cli = "echo"
        mock_agent.driver = "cli"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = "claude-3"
        mock_agent.wrapper = ""
        mock_agent.command = ""
        # F2c (2026-07-22, Phase F): explicitly set provider so
        # dispatcher._resolve_agent receives a str, not a MagicMock
        # auto-attribute (would fail Pydantic validation).
        mock_agent.provider = ""

        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        dispatcher = Dispatcher(MAOP_config=mock_config)

        # The CLI driver will try to run "echo" — mock _DRIVERS dict directly
        from maop.delegate import dispatcher as disp_mod
        original_cli = disp_mod._DRIVERS["cli"]

        async def mock_cli(config, prompt, timeout, workdir, trace_id, *, streamer=None):
            return new_result(
                agent="claude", task="test", exit_code=0, stdout="hello",
                driver="cli", model="claude-3",
            )

        disp_mod._DRIVERS["cli"] = mock_cli
        try:
            result = asyncio.run(
                dispatcher.dispatch(agent="claude", task="test")
            )
            assert result.result.is_success()
            assert result.driver_used == "cli"
        finally:
            disp_mod._DRIVERS["cli"] = original_cli

    def test_circuit_breaker_blocks(self, tmp_path):
        """When circuit breaker is open, dispatch returns error."""
        mock_agent = MagicMock()
        mock_agent.name = "failing-agent"
        mock_agent.cli = "fail"
        mock_agent.driver = "cli"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = None
        mock_agent.wrapper = ""
        mock_agent.command = ""
        mock_agent.provider = ""  # F2c (Phase F): see test_dispatch_with_config

        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        # Isolate breaker state in a per-test tmp DB so repeated runs do not
        # accumulate failures in the production maop.db (which would leave
        # "failing-agent" permanently OPEN and break other tests).
        breaker = CircuitBreaker(tmp_path / "test_breaker.db")
        # Trip the breaker: record failures >= threshold (3)
        for _ in range(3):
            breaker.record_failure("failing-agent")

        dispatcher = Dispatcher(MAOP_config=mock_config, breaker=breaker)
        result = asyncio.run(
            dispatcher.dispatch(agent="failing-agent", task="test")
        )
        assert result.breaker_tripped
        assert "Circuit breaker OPEN" in (result.result.error or "")

    def test_dispatch_unknown_driver(self, tmp_path):
        """Agent with unknown driver returns error."""
        mock_agent = MagicMock()
        mock_agent.name = "bad-driver"
        mock_agent.cli = "echo"
        mock_agent.driver = "nonexistent"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = None
        mock_agent.wrapper = ""
        mock_agent.command = ""
        mock_agent.provider = ""  # F2c (Phase F): see test_dispatch_with_config

        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        # Isolate breaker state in a per-test tmp DB. Without this, each test
        # run calls arecord_failure("bad-driver") (dispatcher.py:935) into the
        # shared production maop.db; after 3 runs the breaker trips OPEN and
        # the OPEN check (dispatcher.py:874) short-circuits before the
        # "Unknown driver" path, breaking the assertion.
        breaker = CircuitBreaker(tmp_path / "test_breaker.db")
        dispatcher = Dispatcher(MAOP_config=mock_config, breaker=breaker)
        result = asyncio.run(
            dispatcher.dispatch(agent="bad-driver", task="test")
        )
        assert not result.result.is_success()
        assert "Unknown driver" in (result.result.error or "")

    def test_wildcard_agent_match(self):
        """Dispatcher falls back to wildcard matching."""
        mock_agent = MagicMock()
        mock_agent.name = "codex*"
        mock_agent.cli = "echo"
        mock_agent.driver = "cli"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = None
        mock_agent.wrapper = ""
        mock_agent.command = ""
        mock_agent.provider = ""  # F2c (Phase F): see test_dispatch_with_config

        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        dispatcher = Dispatcher(MAOP_config=mock_config)

        # Patch _DRIVERS dict to avoid actually running a subprocess
        from maop.delegate import dispatcher as disp_mod
        original_cli = disp_mod._DRIVERS["cli"]

        async def mock_cli(config, prompt, timeout, workdir, trace_id, *, streamer=None):
            return new_result(
                agent=config.name, task=prompt, exit_code=0, stdout="ok", driver="cli",
            )

        disp_mod._DRIVERS["cli"] = mock_cli
        try:
            result = asyncio.run(
                dispatcher.dispatch(agent="codex-mini", task="test")
            )
            assert result.result.is_success()
            assert result.driver_used == "cli"
        finally:
            disp_mod._DRIVERS["cli"] = original_cli
