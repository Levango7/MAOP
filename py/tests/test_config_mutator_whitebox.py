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

from maop.core.config_mutator import ConfigMutator


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