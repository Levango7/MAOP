"""Tests for MAOP.config — YAML config loading."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from maop.config.loader import ConfigLoader, MaopConfig


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Create a minimal config directory with agents.yaml and rules.yaml."""
    cfg = tmp_path / "config"
    cfg.mkdir()

    agents_yaml = {
        "agents": {
            "claude": {
                "cli": "claude",
                "driver": "cli",
                "capabilities": ["codegen", "review"],
                "model": "step-3.7-flash",
                "timeout_s": 120,
                "description": "Claude Code",
            },
            "kimi": {
                "cli": "kimi",
                "driver": "powershell",
                "capabilities": ["search"],
                "model": "step-3.7-flash",
                "timeout_s": 120,
                "description": "Kimi CLI",
            },
        },
        "routing": {
            "codegen": {"primary": "claude", "fallback": "kimi", "tertiary": ""},
            "search": {"primary": "kimi", "fallback": "claude", "tertiary": ""},
        },
        "loops": {
            "iterative": {"max_attempts": 3, "backoff_ms": 2000, "stop_on_success": True},
            "routing": {"enabled": True},
        },
    }
    (cfg / "agents.yaml").write_text(
        yaml.dump(agents_yaml, allow_unicode=True), encoding="utf-8"
    )

    rules_yaml = {
        "guards": {
            "retry": {"max_attempts": 3, "backoff_ms": 2000},
            "timeout": {"default_s": 120},
        }
    }
    (cfg / "rules.yaml").write_text(
        yaml.dump(rules_yaml, allow_unicode=True), encoding="utf-8"
    )

    return tmp_path


class TestConfigLoader:
    def test_load_basic(self, config_dir: Path):
        cfg = ConfigLoader(project_root=config_dir).load()
        assert isinstance(cfg, MaopConfig)
        assert "claude" in cfg.agents
        assert "kimi" in cfg.agents
        assert cfg.agents["claude"].driver == "cli"
        assert cfg.agents["kimi"].driver == "powershell"

    def test_routing(self, config_dir: Path):
        cfg = ConfigLoader(project_root=config_dir).load()
        assert "codegen" in cfg.routing
        assert cfg.routing["codegen"].primary == "claude"
        assert cfg.routing["codegen"].fallback == "kimi"

    def test_loops(self, config_dir: Path):
        cfg = ConfigLoader(project_root=config_dir).load()
        assert cfg.loops.iterative.max_attempts == 3
        assert cfg.loops.iterative.backoff_ms == 2000
        assert cfg.loops.routing.enabled is True

    def test_guards(self, config_dir: Path):
        cfg = ConfigLoader(project_root=config_dir).load()
        assert cfg.guards.retry.max_attempts == 3
        assert cfg.guards.timeout.default_s == 120

    def test_reload(self, config_dir: Path):
        loader = ConfigLoader(project_root=config_dir)
        cfg1 = loader.load()
        cfg2 = loader.reload()
        assert cfg1.agents.keys() == cfg2.agents.keys()

    def test_missing_config_dir(self, tmp_path: Path):
        """Should return empty config when files don't exist."""
        empty = tmp_path / "empty"
        empty.mkdir()
        (empty / "config").mkdir()
        cfg = ConfigLoader(project_root=empty).load()
        assert cfg.agents == {}
        assert cfg.routing == {}
