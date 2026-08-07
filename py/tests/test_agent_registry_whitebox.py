"""White-box tests for AgentRegistry — registration, query, and persistence.

Exercises register/unregister/list/capability-query/update/persistence paths
against the SQLite-backed implementation in maop.core.agent_registry. Each
test relies on conftest.py's MAOP_DATA_DIR isolation so no real DB is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.agent.lifecycle.agent_registry import AgentRegistry, RegisteredAgent


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def registry(tmp_path: Path) -> AgentRegistry:
    """A fresh AgentRegistry rooted in the isolated tmp dir."""
    return AgentRegistry(root_dir=tmp_path)


def _agent(name: str, **kwargs: object) -> RegisteredAgent:
    """Build a RegisteredAgent with the given name and optional overrides."""
    return RegisteredAgent(name=name, **kwargs)  # type: ignore[arg-type]


# ── 1. Register ─────────────────────────────────────────────────


def test_register_then_get(registry: AgentRegistry) -> None:
    """A registered agent is retrievable by name."""
    registry.register(_agent("agent_a", capabilities=["code"]))
    got = registry.get_agent("agent_a")
    assert got is not None
    assert got.name == "agent_a"


# ── 2. Unregister ───────────────────────────────────────────────


def test_unregister_removes_agent(registry: AgentRegistry) -> None:
    """unregister deletes the agent so subsequent get_agent returns None."""
    registry.register(_agent("agent_a"))
    assert registry.unregister("agent_a") is True
    assert registry.get_agent("agent_a") is None


# ── 3. List all ─────────────────────────────────────────────────


def test_list_agents_returns_all(registry: AgentRegistry) -> None:
    """list_agents() with no filters returns every registered agent."""
    registry.register(_agent("agent_a"))
    registry.register(_agent("agent_b"))
    names = {a.name for a in registry.list_agents()}
    assert names == {"agent_a", "agent_b"}


# ── 4. Query by capability ──────────────────────────────────────


def test_list_by_capability(registry: AgentRegistry) -> None:
    """list_agents(capability=...) returns only agents tagged with that capability."""
    registry.register(_agent("coder", capabilities=["code"]))
    registry.register(_agent("reviewer", capabilities=["review"]))
    result = registry.list_agents(capability="code")
    assert [a.name for a in result] == ["coder"]


# ── 5. Non-existent agent ───────────────────────────────────────


def test_get_nonexistent_returns_none(registry: AgentRegistry) -> None:
    """get_agent on an unknown name returns None (no exception)."""
    assert registry.get_agent("ghost") is None


# ── 6. Update via upsert ────────────────────────────────────────


def test_register_same_name_updates(registry: AgentRegistry) -> None:
    """Re-registering the same name overwrites attributes (INSERT OR REPLACE)."""
    registry.register(_agent("agent_a", timeout_s=60))
    registry.register(_agent("agent_a", timeout_s=120))
    assert registry.get_agent("agent_a").timeout_s == 120


# ── 7. Persistence across instances ─────────────────────────────


def test_persistence_across_registry_instances(tmp_path: Path) -> None:
    """A new AgentRegistry on the same root sees previously registered agents."""
    r1 = AgentRegistry(root_dir=tmp_path)
    r1.register(_agent("agent_a", capabilities=["code"]))
    r2 = AgentRegistry(root_dir=tmp_path)
    got = r2.get_agent("agent_a")
    assert got is not None
    assert got.capabilities == ["code"]