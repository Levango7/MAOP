"""Extended tests for MAOP.delegate.dispatcher — guardrail integration, driver registry, model resolution."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch


from maop.core.error_schema import new_result
from maop.delegate.dispatcher import (
    DispatchResult, Dispatcher,
    _escape_for_cmd, _escape_for_ps_command, _DRIVERS,
)


class TestDriverRegistry:
    def test_all_expected_drivers_registered(self):
        assert "cli" in _DRIVERS
        assert "wrapper" in _DRIVERS
        assert "powershell" in _DRIVERS
        assert "cmd" in _DRIVERS
        assert "python" in _DRIVERS

    def test_driver_count(self):
        assert len(_DRIVERS) == 5


class TestGuardrailIntegration:
    def test_guardrail_blocks_dispatch(self):
        mock_agent = MagicMock()
        mock_agent.name = "claude"
        mock_agent.cli = "echo"
        mock_agent.driver = "cli"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = None
        mock_agent.wrapper = ""
        mock_agent.command = ""
        mock_agent.provider = ""  # F2c (Phase F): explicit str to avoid MagicMock
        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        dispatcher = Dispatcher(MAOP_config=mock_config)

        with patch("maop.core.guardrail.Guardrail") as MockGuardrail:
            mock_gr = MagicMock()
            mock_check = MagicMock()
            mock_check.passed = False
            mock_violation = MagicMock()
            mock_violation.action = "block"
            mock_violation.message = "sensitive content detected"
            mock_check.violations = [mock_violation]
            mock_gr.check.return_value = mock_check
            MockGuardrail.return_value = mock_gr

            result = asyncio.run(
                dispatcher.dispatch(agent="claude", task="sk-abc1234567890123456789012")
            )
            assert result.result.exit_code == -4
            assert "Guardrail BLOCKED" in (result.result.error or "")

    def test_guardrail_fail_closed_on_exception(self):
        mock_agent = MagicMock()
        mock_agent.name = "claude"
        mock_agent.cli = "echo"
        mock_agent.driver = "cli"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = None
        mock_agent.wrapper = ""
        mock_agent.command = ""
        mock_agent.provider = ""  # F2c (Phase F): explicit str to avoid MagicMock
        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        dispatcher = Dispatcher(MAOP_config=mock_config)

        with patch("maop.core.guardrail.Guardrail", side_effect=RuntimeError("guardrail crashed")):
            result = asyncio.run(
                dispatcher.dispatch(agent="claude", task="test")
            )
            assert result.result.exit_code == -4
            assert "fail-closed" in (result.result.error or "")


class TestModelResolution:
    def test_model_selector_injects_model(self):
        mock_agent = MagicMock()
        mock_agent.name = "claude"
        mock_agent.cli = "echo"
        mock_agent.driver = "cli"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = "original-model"
        mock_agent.wrapper = ""
        mock_agent.command = ""
        mock_agent.provider = ""  # F2c (Phase F): explicit str to avoid MagicMock
        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        mock_selector = MagicMock()
        mock_em = MagicMock()
        mock_em.model_name = "resolved-model"
        mock_selector.select_for_routing_key.return_value = mock_em

        dispatcher = Dispatcher(MAOP_config=mock_config, model_selector=mock_selector)

        from maop.delegate import dispatcher as disp_mod
        original_cli = disp_mod._DRIVERS["cli"]

        async def mock_cli(config, prompt, timeout, workdir, trace_id, *, streamer=None):
            assert config.model == "resolved-model"
            return new_result(agent="claude", task="test", exit_code=0, stdout="ok", driver="cli")

        disp_mod._DRIVERS["cli"] = mock_cli
        try:
            result = asyncio.run(
                dispatcher.dispatch(agent="claude", task="test", routing_key="chat")
            )
            assert result.result.is_success()
            assert result.model_resolved is True
        finally:
            disp_mod._DRIVERS["cli"] = original_cli

    def test_model_selector_failure_keeps_original(self):
        mock_agent = MagicMock()
        mock_agent.name = "claude"
        mock_agent.cli = "echo"
        mock_agent.driver = "cli"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = "fallback-model"
        mock_agent.wrapper = ""
        mock_agent.command = ""
        mock_agent.provider = ""  # F2c (Phase F): explicit str to avoid MagicMock
        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        mock_selector = MagicMock()
        mock_selector.select_for_routing_key.side_effect = RuntimeError("model unavailable")

        dispatcher = Dispatcher(MAOP_config=mock_config, model_selector=mock_selector)

        from maop.delegate import dispatcher as disp_mod
        original_cli = disp_mod._DRIVERS["cli"]

        async def mock_cli(config, prompt, timeout, workdir, trace_id, *, streamer=None):
            assert config.model == "fallback-model"
            return new_result(agent="claude", task="test", exit_code=0, stdout="ok", driver="cli")

        disp_mod._DRIVERS["cli"] = mock_cli
        try:
            result = asyncio.run(
                dispatcher.dispatch(agent="claude", task="test", routing_key="chat")
            )
            assert result.model_resolved is False
        finally:
            disp_mod._DRIVERS["cli"] = original_cli


class TestEscapeForCmd:
    def test_empty_string(self):
        assert _escape_for_cmd("") == ""

    def test_no_special_chars(self):
        assert _escape_for_cmd("hello world") == "hello world"

    def test_all_special_chars(self):
        result = _escape_for_cmd("& | < > ^ ( )")
        assert "^&" in result
        assert "^|" in result
        assert "^<" in result
        assert "^>" in result
        assert "^^" in result
        assert "^(" in result
        assert "^)" in result

    def test_newline_escaped(self):
        result = _escape_for_cmd("line1\nline2")
        assert "^\n" in result


class TestEscapeForPsCommand:
    def test_empty_string(self):
        result = _escape_for_ps_command("")
        assert result == "''"

    def test_single_quote_escaped(self):
        result = _escape_for_ps_command("it's")
        assert "''" in result

    def test_null_bytes_stripped(self):
        result = _escape_for_ps_command("test\x00injection")
        assert "\x00" not in result

    def test_dollar_not_expanded(self):
        result = _escape_for_ps_command("$env:PATH")
        assert result.startswith("'")
        assert result.endswith("'")
        assert "$env" in result


class TestDispatchResult:
    def test_default_values(self):
        r = DispatchResult(result=new_result(agent="a", task="t", exit_code=0))
        assert r.driver_used == ""
        assert r.breaker_tripped is False
        assert r.model_resolved is True


class TestSubagentResolution:
    """Test Dispatcher._resolve_agent() with parent/child format."""

    def _make_config_with_subagents(self):
        from maop.config.loader import AgentDef, SubagentDef, MaopConfig
        parent = AgentDef(
            cli="mavis",
            cli_args="{task}",
            driver="cli",
            capabilities=["codegen", "chat", "review"],
            model="minimax-m2.7",
            timeout_s=120,
            subagents={
                "coder": SubagentDef(
                    cli_args="agent start coder --prompt {task}",
                    capabilities=["codegen", "refactor"],
                    description="Mavis sub-agent: Coder",
                ),
                "verifier": SubagentDef(
                    cli_args="agent start verifier --prompt {task}",
                    capabilities=["review", "verify"],
                    description="Mavis sub-agent: Verifier",
                ),
            },
        )
        return MaopConfig(agents={"mavis": parent})

    def test_resolve_subagent_parent_child(self):
        cfg = self._make_config_with_subagents()
        dispatcher = Dispatcher(MAOP_config=cfg)
        resolved = dispatcher._resolve_agent("mavis/verifier")
        assert resolved is not None
        assert resolved.name == "mavis/verifier"
        assert resolved.cli == "mavis"
        assert resolved.cli_args == "agent start verifier --prompt {task}"
        assert resolved.driver == "cli"
        assert resolved.model == "minimax-m2.7"
        assert "verify" in resolved.capabilities

    def test_resolve_subagent_coder(self):
        cfg = self._make_config_with_subagents()
        dispatcher = Dispatcher(MAOP_config=cfg)
        resolved = dispatcher._resolve_agent("mavis/coder")
        assert resolved is not None
        assert resolved.name == "mavis/coder"
        assert resolved.cli_args == "agent start coder --prompt {task}"
        assert "codegen" in resolved.capabilities

    def test_resolve_subagent_unknown_child(self):
        cfg = self._make_config_with_subagents()
        dispatcher = Dispatcher(MAOP_config=cfg)
        resolved = dispatcher._resolve_agent("mavis/nonexistent")
        assert resolved is None

    def test_resolve_subagent_unknown_parent(self):
        cfg = self._make_config_with_subagents()
        dispatcher = Dispatcher(MAOP_config=cfg)
        resolved = dispatcher._resolve_agent("unknown/child")
        assert resolved is None

    def test_resolve_subagent_caching(self):
        cfg = self._make_config_with_subagents()
        dispatcher = Dispatcher(MAOP_config=cfg)
        first = dispatcher._resolve_agent("mavis/verifier")
        second = dispatcher._resolve_agent("mavis/verifier")
        assert first is not None
        assert second is not None
        assert first.name == second.name
        assert first.cli == second.cli

    def test_resolve_parent_agent_still_works(self):
        cfg = self._make_config_with_subagents()
        dispatcher = Dispatcher(MAOP_config=cfg)
        resolved = dispatcher._resolve_agent("mavis")
        assert resolved is not None
        assert resolved.name == "mavis"
        assert resolved.cli_args == "{task}"

    def test_dispatch_subagent_integration(self):
        cfg = self._make_config_with_subagents()
        dispatcher = Dispatcher(MAOP_config=cfg)

        from maop.delegate import dispatcher as disp_mod
        original_cli = disp_mod._DRIVERS["cli"]

        async def mock_cli(config, prompt, timeout, workdir, trace_id, *, streamer=None):
            assert config.name == "mavis/verifier"
            assert config.cli == "mavis"
            assert "verifier" in config.cli_args
            return new_result(
                agent=config.name, task=prompt, exit_code=0,
                stdout="verified", driver="cli",
            )

        disp_mod._DRIVERS["cli"] = mock_cli
        try:
            result = asyncio.run(
                dispatcher.dispatch(agent="mavis/verifier", task="check this code")
            )
            assert result.result.is_success()
            assert result.driver_used == "cli"
        finally:
            disp_mod._DRIVERS["cli"] = original_cli


class TestConfigLoaderSubagents:
    """Test ConfigLoader correctly parses subagents from YAML."""

    def test_load_config_parses_subagents(self, tmp_path):
        from maop.config.loader import ConfigLoader
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        agents_yaml = config_dir / "agents.yaml"
        agents_yaml.write_text("""
agents:
  mavis:
    cli: mavis
    cli_args: '{task}'
    driver: cli
    model: minimax-m2.7
    timeout_s: 120
    subagents:
      coder:
        cli_args: agent start coder --prompt {task}
        capabilities:
          - codegen
          - refactor
        description: 'Mavis sub-agent: Coder'
      verifier:
        cli_args: agent start verifier --prompt {task}
        capabilities:
          - review
          - verify
        description: 'Mavis sub-agent: Verifier'
routing:
  verify:
    primary: mavis/verifier
    fallback: claude
""", encoding="utf-8")
        rules_yaml = config_dir / "rules.yaml"
        rules_yaml.write_text("guards:\n  retry:\n    max_attempts: 3\n", encoding="utf-8")

        loader = ConfigLoader(project_root=tmp_path)
        cfg = loader.load()

        assert "mavis" in cfg.agents
        mavis = cfg.agents["mavis"]
        assert len(mavis.subagents) == 2
        assert "coder" in mavis.subagents
        assert "verifier" in mavis.subagents
        assert mavis.subagents["coder"].cli_args == "agent start coder --prompt {task}"
        assert "codegen" in mavis.subagents["coder"].capabilities
        assert mavis.subagents["verifier"].cli_args == "agent start verifier --prompt {task}"

        assert "verify" in cfg.routing
        assert cfg.routing["verify"].primary == "mavis/verifier"

    def test_load_config_agent_without_subagents(self, tmp_path):
        from maop.config.loader import ConfigLoader
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        agents_yaml = config_dir / "agents.yaml"
        agents_yaml.write_text("""
agents:
  claude:
    cli: claude
    cli_args: "-p '{task}'"
    driver: cli
    model: yi-large
    timeout_s: 120
""", encoding="utf-8")
        rules_yaml = config_dir / "rules.yaml"
        rules_yaml.write_text("guards:\n  retry:\n    max_attempts: 3\n", encoding="utf-8")

        loader = ConfigLoader(project_root=tmp_path)
        cfg = loader.load()

        assert "claude" in cfg.agents
        assert cfg.agents["claude"].subagents == {}