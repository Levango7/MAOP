"""White-box tests for ConfigMutator — apply evolution suggestions to agents.yaml.

Exercises apply_suggestion / backup-restore / pending-list paths against the
file-backed implementation in maop.core.config_mutator. All tests use an
isolated tmp root so no real MAOP config is touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from maop.core.reliability.config_mutator import ConfigMutator, MutationResult


# ── Helpers ──────────────────────────────────────────────────────


def _write_agents_yaml(root: Path, data: dict[str, Any]) -> None:
    """Write agents.yaml under root/config/."""
    cfg_dir = root / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "agents.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8",
    )


def _write_suggestions(root: Path, suggestions: list[dict[str, Any]]) -> None:
    """Write evolve-suggestions.json under root/data/."""
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "evolve-suggestions.json").write_text(
        json.dumps(suggestions, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def _list_pending(root: Path) -> list[dict[str, Any]]:
    """Return unapplied, auto-applicable suggestions (white-box read of JSON)."""
    with open(root / "data" / "evolve-suggestions.json", encoding="utf-8") as f:
        items = json.load(f)
    return [s for s in items if not s.get("applied", False) and s.get("auto_applicable", False)]


def _base_agents() -> dict[str, Any]:
    """Baseline agents.yaml content with two agents and one routing key."""
    return {
        "routing": {"test": {"primary": "agent_a", "fallback": "agent_b"}},
        "agents": {
            "agent_a": {"timeout_s": 60, "enabled": True, "max_retries": 3, "capabilities": ["code"]},
            "agent_b": {"timeout_s": 30, "enabled": True, "max_retries": 2, "capabilities": ["review"]},
        },
    }


# ── 1. Apply valid suggestion ────────────────────────────────────


def test_apply_valid_suggestion_succeeds(tmp_path: Path) -> None:
    """A valid change_routing suggestion applies and reports changes."""
    _write_agents_yaml(tmp_path, _base_agents())
    _write_suggestions(tmp_path, [{
        "id": "S001", "type": "change_routing", "auto_applicable": True, "applied": False,
        "mutation_params": {"routing_key": "test", "suggested_agent": "agent_b"},
    }])
    result = ConfigMutator(root_dir=tmp_path).apply_suggestion("S001")
    assert result.applied is True
    assert result.mutation_type == "change_routing"
    assert len(result.changes) > 0


# ── 2. Invalid suggestion id ────────────────────────────────────


def test_apply_unknown_suggestion_id_returns_not_found(tmp_path: Path) -> None:
    """An unknown suggestion id yields applied=False with a not-found error."""
    _write_agents_yaml(tmp_path, _base_agents())
    _write_suggestions(tmp_path, [])
    result = ConfigMutator(root_dir=tmp_path).apply_suggestion("NOPE")
    assert result.applied is False
    assert "not found" in result.error


# ── 3. Rollback: handler failure restores backup ─────────────────


def test_handler_failure_restores_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a mutation handler raises, agents.yaml is restored from the backup."""
    _write_agents_yaml(tmp_path, _base_agents())
    _write_suggestions(tmp_path, [{
        "id": "S001", "type": "change_routing", "auto_applicable": True, "applied": False,
        "mutation_params": {"routing_key": "test", "suggested_agent": "agent_b"},
    }])
    original = (tmp_path / "config" / "agents.yaml").read_text(encoding="utf-8")

    mutator = ConfigMutator(root_dir=tmp_path)
    monkeypatch.setattr(mutator, "_mutate_routing", lambda _s: (_ for _ in ()).throw(RuntimeError("boom")))
    result = mutator.apply_suggestion("S001")

    assert result.applied is False
    assert "boom" in result.error
    assert (tmp_path / "config" / "agents.yaml").read_text(encoding="utf-8") == original


# ── 4. List pending suggestions ─────────────────────────────────


def test_list_pending_filters_applied_and_non_auto(tmp_path: Path) -> None:
    """_list_pending returns only unapplied, auto-applicable suggestions."""
    _write_suggestions(tmp_path, [
        {"id": "S001", "auto_applicable": True, "applied": False},
        {"id": "S002", "auto_applicable": True, "applied": True},
        {"id": "S003", "auto_applicable": False, "applied": False},
    ])
    assert [s["id"] for s in _list_pending(tmp_path)] == ["S001"]


# ── 5. Non-conflicting sequential application ────────────────────


def test_two_non_conflicting_suggestions_both_apply(tmp_path: Path) -> None:
    """Two suggestions targeting different agents both apply without conflict."""
    _write_agents_yaml(tmp_path, _base_agents())
    _write_suggestions(tmp_path, [
        {"id": "S001", "type": "adjust_timeout", "auto_applicable": True, "applied": False,
         "mutation_params": {"agent": "agent_a", "suggested_timeout": 120}},
        {"id": "S002", "type": "adjust_retries", "auto_applicable": True, "applied": False,
         "mutation_params": {"agent": "agent_b", "suggested_max_retries": 5}},
    ])
    mutator = ConfigMutator(root_dir=tmp_path)
    r1 = mutator.apply_suggestion("S001")
    r2 = mutator.apply_suggestion("S002")
    assert r1.applied and r2.applied
    data = yaml.safe_load((tmp_path / "config" / "agents.yaml").read_text(encoding="utf-8"))
    assert data["agents"]["agent_a"]["timeout_s"] == 120
    assert data["agents"]["agent_b"]["max_retries"] == 5

# --- Merged from test_config_mutator_coverage.py ---

class TestApplySuggestionErrors:
    def test_suggestion_not_found(self, tmp_path):
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S999")
        assert result.applied is False
        assert "not found" in result.error

    def test_suggestion_not_auto_applicable(self, tmp_path):
        _write_suggestions(tmp_path, [{"id": "S1", "auto_applicable": False, "type": "change_routing"}])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is False
        assert "not auto-applicable" in result.error

    def test_suggestion_already_applied(self, tmp_path):
        _write_suggestions(tmp_path, [{"id": "S1", "auto_applicable": True, "applied": True, "type": "change_routing"}])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is False
        assert "already applied" in result.error

    def test_unknown_mutation_type(self, tmp_path):
        _write_suggestions(tmp_path, [{"id": "S1", "auto_applicable": True, "type": "unknown_type"}])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is False
        assert "Unknown mutation type" in result.error


class TestMutateRouting:
    def test_change_primary_agent(self, tmp_path):
        _write_agents_yaml(tmp_path, {"routing": {"codegen": {"primary": "claude", "fallback": "gpt"}}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "change_routing",
            "mutation_params": {"routing_key": "codegen", "suggested_agent": "gemini"},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True
        assert any("primary" in c for c in result.changes)

    def test_demote_current_agent(self, tmp_path):
        _write_agents_yaml(tmp_path, {"routing": {"codegen": {"primary": "claude", "fallback": "gpt"}}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "change_routing",
            "mutation_params": {"routing_key": "codegen", "agent": "claude"},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True

    def test_new_routing_key(self, tmp_path):
        _write_agents_yaml(tmp_path, {"routing": {}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "change_routing",
            "mutation_params": {"routing_key": "new_key", "suggested_agent": "agent1"},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True

    def test_no_routing_key_returns_empty(self, tmp_path):
        _write_agents_yaml(tmp_path, {"routing": {}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "change_routing",
            "mutation_params": {},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True
        assert result.changes == []


class TestMutateTimeout:
    def test_increase_timeout(self, tmp_path):
        _write_agents_yaml(tmp_path, {"agents": {"claude": {"timeout_s": 60}}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "adjust_timeout",
            "mutation_params": {"agent": "claude", "suggested_timeout": 120},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True
        assert any("timeout_s" in c for c in result.changes)

    def test_auto_increase_50_percent(self, tmp_path):
        _write_agents_yaml(tmp_path, {"agents": {"claude": {"timeout_s": 60}}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "adjust_timeout",
            "mutation_params": {"agent": "claude"},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True

    def test_agent_not_found(self, tmp_path):
        _write_agents_yaml(tmp_path, {"agents": {}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "adjust_timeout",
            "mutation_params": {"agent": "nonexistent", "suggested_timeout": 100},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True
        assert result.changes == []


class TestMutateDisableAgent:
    def test_disable_agent(self, tmp_path):
        _write_agents_yaml(tmp_path, {"agents": {"claude": {"enabled": True}}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "disable_agent",
            "mutation_params": {"agent": "claude"},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True
        assert any("enabled" in c for c in result.changes)

    def test_already_disabled(self, tmp_path):
        _write_agents_yaml(tmp_path, {"agents": {"claude": {"enabled": False}}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "disable_agent",
            "mutation_params": {"agent": "claude"},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True
        assert result.changes == []


class TestMutateEmptyRouting:
    def test_assign_agent(self, tmp_path):
        _write_agents_yaml(tmp_path, {"routing": {}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "change_routing_empty",
            "mutation_params": {"routing_key": "empty_key", "suggested_agent": "agent1"},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True
        assert any("empty" in c for c in result.changes)

    def test_no_key_or_agent(self, tmp_path):
        _write_agents_yaml(tmp_path, {"routing": {}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "change_routing_empty",
            "mutation_params": {},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True
        assert result.changes == []


class TestMutateAddCapability:
    def test_add_new_capability(self, tmp_path):
        _write_agents_yaml(tmp_path, {"agents": {"claude": {"capabilities": ["code"]}}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "add_capability",
            "mutation_params": {"agent": "claude", "suggested_capability": "review"},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True
        assert any("capabilities" in c for c in result.changes)

    def test_capability_already_exists(self, tmp_path):
        _write_agents_yaml(tmp_path, {"agents": {"claude": {"capabilities": ["code"]}}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "add_capability",
            "mutation_params": {"agent": "claude", "suggested_capability": "code"},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True
        assert result.changes == []

    def test_no_agent_or_capability(self, tmp_path):
        _write_agents_yaml(tmp_path, {"agents": {}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "add_capability",
            "mutation_params": {},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True
        assert result.changes == []


class TestMutateAdjustRettries:
    def test_adjust_retries(self, tmp_path):
        _write_agents_yaml(tmp_path, {"agents": {"claude": {"max_retries": 3}}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "adjust_retries",
            "mutation_params": {"agent": "claude", "suggested_max_retries": 5},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True
        assert any("max_retries" in c for c in result.changes)

    def test_no_agent(self, tmp_path):
        _write_agents_yaml(tmp_path, {"agents": {}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "adjust_retries",
            "mutation_params": {},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True
        assert result.changes == []


class TestMutateSwitchModel:
    def test_switch_model_no_changes(self, tmp_path):
        _write_agents_yaml(tmp_path, {"agents": {}})
        _write_suggestions(tmp_path, [{
            "id": "S1", "auto_applicable": True, "type": "switch_model",
            "mutation_params": {"model": "gpt-4", "total_cost": 10.5},
        }])
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S1")
        assert result.applied is True
        assert result.changes == []


class TestMutationResultModel:
    def test_defaults(self):
        r = MutationResult()
        assert r.applied is False
        assert r.changes == []
        assert r.error == ""