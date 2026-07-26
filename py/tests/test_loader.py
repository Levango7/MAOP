"""Comprehensive tests for MAOP.config.loader — ConfigLoader & Pydantic models."""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.config.loader import (
    AgentDef,
    ConfigLoader,
    GuardsConfig,
    IterativeLoop,
    LoopsConfig,
    MaopConfig,
    RetryConfig,
    RouteEntry,
    RoutingLoop,
    TimeoutConfig,
    WorkflowDef,
    _load_yaml,
    load_config,
)
from maop.core.db_utils import find_project_root

# ── Pydantic Model Tests ─────────────────────────────────────

class TestAgentDef:
    """Tests for AgentDef model."""

    def test_defaults(self):
        a = AgentDef()
        assert a.cli == ""
        assert a.cli_args == ""
        assert a.driver == "cli"
        assert a.capabilities == []
        assert a.model == ""
        assert a.timeout_s == 120
        assert a.description == ""
        assert a.wrapper == ""

    def test_custom(self):
        a = AgentDef(cli="claude", driver="cli", capabilities=["codegen", "chat"],
                     model="gpt-4", timeout_s=60)
        assert a.cli == "claude"
        assert "codegen" in a.capabilities
        assert a.timeout_s == 60

    def test_capabilities_independence(self):
        a1 = AgentDef()
        a2 = AgentDef()
        a1.capabilities.append("x")
        assert a2.capabilities == []


class TestWorkflowDef:
    """Tests for WorkflowDef model."""

    def test_defaults(self):
        w = WorkflowDef()
        assert w.driver == "wrapper"
        assert w.timeout_s == 300

    def test_custom(self):
        w = WorkflowDef(cli="mywf", capabilities=["pipeline"], model="gpt-4")
        assert w.cli == "mywf"
        assert "pipeline" in w.capabilities


class TestRouteEntry:
    """Tests for RouteEntry model."""

    def test_defaults(self):
        r = RouteEntry()
        assert r.primary == ""
        assert r.fallback == ""
        assert r.tertiary == ""

    def test_custom(self):
        r = RouteEntry(primary="claude", fallback="codex", tertiary="gemini")
        assert r.primary == "claude"
        assert r.fallback == "codex"


class TestLoopsConfig:
    """Tests for LoopsConfig model."""

    def test_defaults(self):
        cfg = LoopsConfig()
        assert cfg.iterative.max_attempts == 3
        assert cfg.iterative.backoff_ms == 2000
        assert cfg.iterative.stop_on_success is True
        assert cfg.routing.enabled is True

    def test_custom(self):
        cfg = LoopsConfig(
            iterative=IterativeLoop(max_attempts=5, backoff_ms=1000),
            routing=RoutingLoop(enabled=False),
        )
        assert cfg.iterative.max_attempts == 5
        assert cfg.routing.enabled is False


class TestGuardsConfig:
    """Tests for GuardsConfig model."""

    def test_defaults(self):
        g = GuardsConfig()
        assert g.retry.max_attempts == 3
        assert g.retry.backoff_ms == 2000
        assert g.timeout.default_s == 120

    def test_custom(self):
        g = GuardsConfig(
            retry=RetryConfig(max_attempts=5, backoff_ms=500),
            timeout=TimeoutConfig(default_s=60),
        )
        assert g.retry.max_attempts == 5
        assert g.timeout.default_s == 60


class TestMaopConfig:
    """Tests for MaopConfig model."""

    def test_defaults(self):
        c = MaopConfig()
        assert c.agents == {}
        assert c.workflows == {}
        assert c.routing == {}
        assert isinstance(c.loops, LoopsConfig)
        assert isinstance(c.guards, GuardsConfig)

    def test_with_values(self):
        c = MaopConfig(
            agents={"claude": AgentDef(cli="claude")},
            routing={"codegen": RouteEntry(primary="claude")},
        )
        assert "claude" in c.agents
        assert "codegen" in c.routing


# ── _load_yaml Tests ─────────────────────────────────────────

class TestLoadYaml:
    """Tests for _load_yaml helper."""

    def test_load_valid_yaml(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("key: value\nlist:\n  - a\n  - b", encoding="utf-8")
        result = _load_yaml(f)
        assert result == {"key": "value", "list": ["a", "b"]}

    def test_load_nonexistent(self, tmp_path):
        f = tmp_path / "nonexistent.yaml"
        assert _load_yaml(f) is None

    def test_load_empty_file(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("", encoding="utf-8")
        result = _load_yaml(f)
        assert result == {}

    def test_load_invalid_yaml(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text(":\n  - [invalid", encoding="utf-8")
        result = _load_yaml(f)
        assert result is None


# ── ConfigLoader Tests ───────────────────────────────────────

@pytest.fixture
def agents_yaml_content() -> str:
    return """
agents:
  claude:
    cli: claude
    driver: cli
    capabilities: [codegen, chat]
    model: claude-sonnet
    timeout_s: 60
  codex:
    cli: codex
    driver: cli
    capabilities: [codegen]
    timeout_s: 120

workflows:
  full-pipeline:
    cli: MAOP-run
    driver: wrapper
    capabilities: [pipeline]
    timeout_s: 600

routing:
  codegen:
    primary: claude
    fallback: codex
  chat:
    primary: claude

loops:
  iterative:
    max_attempts: 5
    backoff_ms: 1000
  routing:
    enabled: false
"""


@pytest.fixture
def rules_yaml_content() -> str:
    return """
guards:
  retry:
    max_attempts: 3
    backoff_ms: 2000
  timeout:
    default_s: 90
"""


@pytest.fixture
def project_root(tmp_path, agents_yaml_content, rules_yaml_content) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "agents.yaml").write_text(agents_yaml_content, encoding="utf-8")
    (config_dir / "rules.yaml").write_text(rules_yaml_content, encoding="utf-8")
    return tmp_path


class TestConfigLoader:
    """Tests for ConfigLoader."""

    def test_init_with_root(self, project_root):
        loader = ConfigLoader(project_root=project_root)
        assert loader._root == project_root
        assert loader._config_dir == project_root / "config"

    def test_init_default_root(self):
        loader = ConfigLoader()
        assert isinstance(loader._root, Path)

    def test_load_agents(self, project_root):
        loader = ConfigLoader(project_root=project_root)
        config = loader.load()
        assert "claude" in config.agents
        assert "codex" in config.agents
        assert config.agents["claude"].cli == "claude"
        assert config.agents["claude"].timeout_s == 60

    def test_load_workflows(self, project_root):
        loader = ConfigLoader(project_root=project_root)
        config = loader.load()
        assert "full-pipeline" in config.workflows
        assert config.workflows["full-pipeline"].driver == "wrapper"

    def test_load_routing(self, project_root):
        loader = ConfigLoader(project_root=project_root)
        config = loader.load()
        assert "codegen" in config.routing
        assert config.routing["codegen"].primary == "claude"
        assert config.routing["codegen"].fallback == "codex"

    def test_load_loops(self, project_root):
        loader = ConfigLoader(project_root=project_root)
        config = loader.load()
        assert config.loops.iterative.max_attempts == 5
        assert config.loops.iterative.backoff_ms == 1000
        assert config.loops.routing.enabled is False

    def test_load_guards(self, project_root):
        loader = ConfigLoader(project_root=project_root)
        config = loader.load()
        assert config.guards.retry.max_attempts == 3
        assert config.guards.timeout.default_s == 90

    def test_load_missing_files(self, tmp_path):
        loader = ConfigLoader(project_root=tmp_path)
        config = loader.load()
        assert config.agents == {}
        assert config.workflows == {}
        assert config.routing == {}
        assert isinstance(config.loops, LoopsConfig)
        assert isinstance(config.guards, GuardsConfig)

    def test_load_partial_files(self, tmp_path, agents_yaml_content):
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "agents.yaml").write_text(agents_yaml_content, encoding="utf-8")
        # No rules.yaml

        loader = ConfigLoader(project_root=tmp_path)
        config = loader.load()
        assert "claude" in config.agents
        # Guards should have defaults
        assert config.guards.retry.max_attempts == 3

    def test_reload(self, project_root):
        loader = ConfigLoader(project_root=project_root)
        config1 = loader.load()
        config2 = loader.reload()
        assert config2.agents.keys() == config1.agents.keys()

    def test_reload_picks_up_changes(self, project_root):
        loader = ConfigLoader(project_root=project_root)
        config1 = loader.load()
        assert "claude" in config1.agents

        # Modify agents.yaml
        agents_yaml = """
agents:
  newagent:
    cli: newagent
    driver: cli
"""
        (project_root / "config" / "agents.yaml").write_text(agents_yaml, encoding="utf-8")
        config2 = loader.reload()
        assert "newagent" in config2.agents
        assert "claude" not in config2.agents


class TestLoadConfigFunction:
    """Tests for load_config convenience function."""

    def test_load_config(self, project_root):
        config = load_config(project_root=project_root)
        assert "claude" in config.agents

    def test_load_config_no_root(self):
        config = load_config()
        assert isinstance(config, MaopConfig)


class TestFindProjectRoot:
    """Tests for find_project_root helper."""

    def test_returns_path(self):
        root = find_project_root()
        assert isinstance(root, Path)
