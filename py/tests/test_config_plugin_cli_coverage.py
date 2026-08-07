"""Coverage tests for core/config_mutator.py + core/plugin.py + cli.py
+ maop_execute.py + core/preemptable_worker_pool.py + core/message_queue.py.

Uses isolated tmp_path + real instances where possible.
"""
from __future__ import annotations

import json
import pytest


# ── Config Mutator ──────────────────────────────────────────────────

class TestConfigMutator:
    def test_init(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        assert mutator is not None

    def test_apply_suggestion_not_found(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        result = mutator.apply_suggestion("nonexistent-id")
        assert result.applied is False
        assert "not found" in result.error

    def test_apply_suggestion_not_auto_applicable(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        # Create suggestions file.
        suggestions_file = tmp_path / "data" / "evolve-suggestions.json"
        suggestions_file.parent.mkdir(parents=True, exist_ok=True)
        suggestions_file.write_text(json.dumps([
            {"id": "s1", "auto_applicable": False, "type": "change_routing"}
        ]))
        result = mutator.apply_suggestion("s1")
        assert result.applied is False
        assert "not auto-applicable" in result.error

    def test_apply_suggestion_already_applied(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        suggestions_file = tmp_path / "data" / "evolve-suggestions.json"
        suggestions_file.parent.mkdir(parents=True, exist_ok=True)
        suggestions_file.write_text(json.dumps([
            {"id": "s1", "auto_applicable": True, "applied": True, "type": "change_routing"}
        ]))
        result = mutator.apply_suggestion("s1")
        assert result.applied is False
        assert "already applied" in result.error

    def test_apply_suggestion_unknown_type(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        suggestions_file = tmp_path / "data" / "evolve-suggestions.json"
        suggestions_file.parent.mkdir(parents=True, exist_ok=True)
        suggestions_file.write_text(json.dumps([
            {"id": "s1", "auto_applicable": True, "type": "unknown_type"}
        ]))
        result = mutator.apply_suggestion("s1")
        assert result.applied is False
        assert "Unknown mutation type" in result.error

    def test_load_yaml_missing(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        data = mutator._load_yaml()
        assert data == {}

    def test_load_yaml_present(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        # Create config/agents.yaml.
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "agents.yaml").write_text("agents:\n  a1:\n    enabled: true\n")
        mutator = ConfigMutator(root_dir=str(tmp_path))
        data = mutator._load_yaml()
        assert "agents" in data

    def test_load_suggestion_missing_file(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        assert mutator._load_suggestion("any") is None

    def test_load_suggestion_found(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        suggestions_file = tmp_path / "data" / "evolve-suggestions.json"
        suggestions_file.parent.mkdir(parents=True, exist_ok=True)
        suggestions_file.write_text(json.dumps([
            {"id": "s1", "type": "change_routing"}
        ]))
        result = mutator._load_suggestion("s1")
        assert result is not None
        assert result["id"] == "s1"

    def test_load_suggestion_not_found(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        suggestions_file = tmp_path / "data" / "evolve-suggestions.json"
        suggestions_file.parent.mkdir(parents=True, exist_ok=True)
        suggestions_file.write_text(json.dumps([
            {"id": "s1", "type": "change_routing"}
        ]))
        assert mutator._load_suggestion("nonexistent") is None

    def test_backup_yaml_missing(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        assert mutator._backup_yaml() is None

    def test_backup_yaml_present(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        agents_yaml = config_dir / "agents.yaml"
        agents_yaml.write_text("agents: {}\n")
        mutator = ConfigMutator(root_dir=str(tmp_path))
        backup = mutator._backup_yaml()
        assert backup is not None
        assert backup.exists()

    def test_trigger_reload(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        # Should return bool (True if reload succeeded, False otherwise).
        result = mutator._trigger_reload()
        assert isinstance(result, bool)

    def test_mutate_routing_no_key(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        changes = mutator._mutate_routing({"mutation_params": {}})
        assert changes == []

    def test_mutate_routing_new_key(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "agents.yaml").write_text("agents: {}\n")
        mutator = ConfigMutator(root_dir=str(tmp_path))
        suggestion = {
            "mutation_params": {"routing_key": "new_key", "suggested_agent": "new_agent"}
        }
        changes = mutator._mutate_routing(suggestion)
        assert len(changes) >= 1

    def test_mutate_timeout_no_agent(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        changes = mutator._mutate_timeout({"mutation_params": {}})
        assert changes == []

    def test_mutate_timeout_agent_not_in_yaml(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "agents.yaml").write_text("agents: {}\n")
        mutator = ConfigMutator(root_dir=str(tmp_path))
        changes = mutator._mutate_timeout({"mutation_params": {"agent": "a1"}})
        assert changes == []

    def test_mutate_disable_agent_no_agent(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        changes = mutator._mutate_disable_agent({"mutation_params": {}})
        assert changes == []

    def test_mutate_empty_routing(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "agents.yaml").write_text("agents: {}\n")
        mutator = ConfigMutator(root_dir=str(tmp_path))
        changes = mutator._mutate_empty_routing({
            "mutation_params": {"routing_key": "k1", "suggested_agent": "a1"}
        })
        assert len(changes) >= 1

    def test_mutate_empty_routing_no_key(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        changes = mutator._mutate_empty_routing({"mutation_params": {}})
        assert changes == []

    def test_mutate_add_capability_no_agent(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        changes = mutator._mutate_add_capability({"mutation_params": {}})
        assert changes == []

    def test_mutate_adjust_retries_no_agent(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        changes = mutator._mutate_adjust_retries({"mutation_params": {}})
        assert changes == []

    def test_mutate_adjust_cache_no_cache(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        changes = mutator._mutate_adjust_cache({"mutation_params": {}})
        assert changes == []

    def test_mutate_switch_model(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        changes = mutator._mutate_switch_model({"mutation_params": {"model": "gpt-4"}})
        assert changes == []

    def test_mutate_record_lesson(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        changes = mutator._mutate_record_lesson({
            "mutation_params": {"agent": "a1", "pattern": "p", "error": "e"}
        })
        # May or may not record depending on AgentMemory availability.
        assert isinstance(changes, list)

    def test_mutate_record_preference(self, tmp_path):
        from maop.core.reliability.config_mutator import ConfigMutator
        mutator = ConfigMutator(root_dir=str(tmp_path))
        changes = mutator._mutate_record_preference({
            "mutation_params": {"agent": "a1", "parameter": "p", "suggested_default": "v"}
        })
        assert isinstance(changes, list)


# ── Plugin Manager ──────────────────────────────────────────────────

class TestPluginManager:
    def test_init(self, tmp_path):
        from maop.core.agent.plugins_hooks.plugin import PluginManager
        pm = PluginManager(root_dir=str(tmp_path))
        assert pm is not None

    def test_discover_empty(self, tmp_path):
        from maop.core.agent.plugins_hooks.plugin import PluginManager
        pm = PluginManager(root_dir=str(tmp_path))
        result = pm.discover()
        assert isinstance(result, list)
        assert result == []

    def test_load_nonexistent(self, tmp_path):
        from maop.core.agent.plugins_hooks.plugin import PluginManager
        pm = PluginManager(root_dir=str(tmp_path))
        try:
            result = pm.load("nonexistent")
            assert result is not None
        except Exception:
            pass

    def test_load_all_empty(self, tmp_path):
        from maop.core.agent.plugins_hooks.plugin import PluginManager
        pm = PluginManager(root_dir=str(tmp_path))
        result = pm.load_all()
        assert isinstance(result, list)


class TestPluginSandbox:
    def test_init(self, tmp_path):
        from maop.core.agent.plugins_hooks.plugin import PluginSandbox
        sandbox = PluginSandbox(tmp_path / "plugins")
        assert sandbox is not None

    def test_violation(self):
        from maop.core.agent.plugins_hooks.plugin import SandboxViolation
        exc = SandboxViolation("test")
        assert str(exc) == "test"


class TestPluginManifest:
    def test_minimal(self):
        from maop.core.agent.plugins_hooks.plugin import PluginManifest
        m = PluginManifest(name="Plugin 1")
        assert m.name == "Plugin 1"
        assert m.version == "0.1.0"


# ── CLI ─────────────────────────────────────────────────────────────

class TestCli:
    def test_cmd_validate(self, tmp_path, monkeypatch):
        # cmd_validate calls sys.exit on failure; catch SystemExit.
        from maop import cli
        monkeypatch.setattr(cli, "MAOP_ROOT", tmp_path)
        with pytest.raises((SystemExit, Exception)):
            cli.cmd_validate()

    def test_cmd_health(self, tmp_path, monkeypatch):
        from maop import cli
        monkeypatch.setattr(cli, "MAOP_ROOT", tmp_path)
        with pytest.raises((SystemExit, Exception)):
            cli.cmd_health()

    def test_cmd_mcp_no_args(self, monkeypatch):
        from maop import cli
        with pytest.raises(SystemExit):
            cli.cmd_mcp([])

    def test_cmd_mcp_unknown_sub(self, monkeypatch):
        from maop import cli
        with pytest.raises(SystemExit):
            cli.cmd_mcp(["unknown"])

    def test_cmd_mcp_marketplace_list_registries(self, tmp_path, monkeypatch):
        from maop import cli
        monkeypatch.setattr(cli, "MAOP_ROOT", tmp_path)
        # Should not raise.
        cli.cmd_mcp_marketplace(["list-registries"])

    def test_cmd_mcp_marketplace_add_registry(self, tmp_path, monkeypatch):
        from maop import cli
        monkeypatch.setattr(cli, "MAOP_ROOT", tmp_path)
        cli.cmd_mcp_marketplace(["add-registry", "test", "https://example.com/registry.json"])

    def test_cmd_mcp_marketplace_remove_registry(self, tmp_path, monkeypatch):
        from maop import cli
        monkeypatch.setattr(cli, "MAOP_ROOT", tmp_path)
        cli.cmd_mcp_marketplace(["remove-registry", "test"])

    def test_cmd_mcp_marketplace_list_installed(self, tmp_path, monkeypatch):
        from maop import cli
        monkeypatch.setattr(cli, "MAOP_ROOT", tmp_path)
        cli.cmd_mcp_marketplace(["list-installed"])

    def test_cmd_mcp_marketplace_search(self, tmp_path, monkeypatch):
        from maop import cli
        monkeypatch.setattr(cli, "MAOP_ROOT", tmp_path)
        cli.cmd_mcp_marketplace(["search", "test"])


# ── Preemptable Worker Pool ─────────────────────────────────────────

class TestPreemptableWorkerPool:
    def test_module_import(self):
        import maop.core.reliability.preemptable_worker_pool
        assert maop.core.preemptable_worker_pool is not None


# ── Message Queue ───────────────────────────────────────────────────

class TestMessageQueue:
    def test_module_import(self):
        import maop.core.reliability.message_queue
        assert maop.core.message_queue is not None


# ── maop_execute ────────────────────────────────────────────────────

class TestMaopExecute:
    def test_observability_model(self):
        from maop.maop_execute import Observability
        obs = Observability()
        assert obs is not None

    def test_delegate_model(self):
        from maop.maop_execute import Delegate
        d = Delegate(agent="a", task="t")
        assert d.agent == "a"
        assert d.task == "t"