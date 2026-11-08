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
    registered = registry.get_agent("agent_a")
    assert registered is not None
    assert registered.timeout_s == 120


# ── 7. Persistence across instances ─────────────────────────────


def test_persistence_across_registry_instances(tmp_path: Path) -> None:
    """A new AgentRegistry on the same root sees previously registered agents."""
    r1 = AgentRegistry(root_dir=tmp_path)
    r1.register(_agent("agent_a", capabilities=["code"]))
    r2 = AgentRegistry(root_dir=tmp_path)
    got = r2.get_agent("agent_a")
    assert got is not None
    assert got.capabilities == ["code"]


# ── 8. category 透传（批 1：Agents 分类 taxonomy）─────────────────


def test_register_from_config_persists_category(tmp_path: Path) -> None:
    """register_from_config 把 yaml 的 category 键透传并落库，重启后仍可读。"""
    yaml_path = tmp_path / "agents.yaml"
    yaml_path.write_text(
        "agents:\n"
        "  alpha:\n"
        "    capabilities: [codegen]\n"
        "    cli: alpha\n"
        "    category: general\n"
        "  beta:\n"
        "    capabilities: [deploy]\n"
        "    cli: beta\n"
        "    category: platform\n",
        encoding="utf-8",
    )
    reg = AgentRegistry(root_dir=tmp_path)
    assert reg.register_from_config(yaml_path) == 2
    by_name = {a.name: a.category for a in reg.list_agents()}
    assert by_name == {"alpha": "general", "beta": "platform"}
    reg2 = AgentRegistry(root_dir=tmp_path)
    by_name2 = {a.name: a.category for a in reg2.list_agents()}
    assert by_name2 == {"alpha": "general", "beta": "platform"}


def test_category_defaults_to_empty(registry: AgentRegistry) -> None:
    """未带 category 的 agent（如 scanner 注册）category 为空串，前端可缺省归类。"""
    registry.register(_agent("legacy", capabilities=["chat"]))
    got = registry.get_agent("legacy")
    assert got is not None
    assert got.category == ""


def test_migrate_schema_adds_category_column(registry: AgentRegistry) -> None:
    """旧库缺 category 列时，_init_db 的增量迁移自动补齐且不丢数据。"""
    import sqlite3

    db_path = registry._db_path
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO registered_agents (name) VALUES ('old_agent')")
        conn.execute("ALTER TABLE registered_agents DROP COLUMN category")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(registered_agents)")}
    assert "category" not in cols  # 已复刻旧库形态

    reg2 = AgentRegistry(root_dir=registry._root)
    agents = reg2.list_agents()
    assert [a.name for a in agents] == ["old_agent"]
    assert agents[0].category == ""
    with sqlite3.connect(db_path) as conn2:
        cols2 = {r[1] for r in conn2.execute("PRAGMA table_info(registered_agents)")}
    assert "category" in cols2  # 迁移已补列