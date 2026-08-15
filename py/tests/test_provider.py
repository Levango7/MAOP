"""Comprehensive tests for MAOP.dashboard.provider — DashboardProvider & data models."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from maop.dashboard.provider import (
    AgentStatus,
    DashboardProvider,
    DashboardState,
)

# ── Data Model Tests ─────────────────────────────────────────

class TestAgentStatus:
    """Tests for AgentStatus model."""

    def test_defaults(self):
        a = AgentStatus()
        assert a.name == ""
        assert a.driver == ""
        assert a.available is True
        assert a.breaker_state == "closed"
        assert a.breaker_failures == 0
        assert a.last_execution_ms == 0

    def test_custom(self):
        a = AgentStatus(name="claude", driver="cli", available=False,
                        breaker_state="open", breaker_failures=5)
        assert a.name == "claude"
        assert a.available is False
        assert a.breaker_failures == 5


class TestDashboardState:
    """Tests for DashboardState model."""

    def test_defaults(self):
        s = DashboardState()
        assert s.agents == []
        assert s.total_delegations == 0
        assert s.success_rate == 0.0
        assert s.active_tasks == 0
        assert s.memory_entries == 0
        assert s.evolution_suggestions == 0
        assert s.uptime_s == 0.0

    def test_with_agents(self):
        agents = [AgentStatus(name="a"), AgentStatus(name="b")]
        s = DashboardState(agents=agents, total_delegations=10, success_rate=95.5)
        assert len(s.agents) == 2
        assert s.total_delegations == 10


# ── DashboardProvider Tests ──────────────────────────────────

class TestDashboardProviderInit:
    """Tests for DashboardProvider initialization."""

    def test_init_with_root(self, tmp_path):
        dp = DashboardProvider(root_dir=tmp_path)
        assert dp._root == tmp_path

    def test_init_default_root(self):
        dp = DashboardProvider()
        assert dp._root == Path.cwd()

    def test_init_start_time(self, tmp_path):
        dp = DashboardProvider(root_dir=tmp_path)
        assert dp._start_time <= time.time()


class TestGetState:
    """Tests for DashboardProvider.get_state."""

    def test_get_state_empty_root(self, tmp_path):
        dp = DashboardProvider(root_dir=tmp_path)
        state = dp.get_state()
        assert isinstance(state, DashboardState)
        assert state.total_delegations == 0
        assert state.success_rate == 0.0
        assert state.memory_entries == 0
        assert state.evolution_suggestions == 0
        assert state.uptime_s >= 0.0

    def test_get_state_uptime_increases(self, tmp_path):
        dp = DashboardProvider(root_dir=tmp_path)
        state1 = dp.get_state()
        time.sleep(0.1)
        state2 = dp.get_state()
        assert state2.uptime_s >= state1.uptime_s

    def test_get_state_with_memory_entries(self, tmp_path):
        entries_dir = tmp_path / "memory" / "entries"
        entries_dir.mkdir(parents=True)
        (entries_dir / "entry1.json").write_text("{}", encoding="utf-8")
        (entries_dir / "entry2.json").write_text("{}", encoding="utf-8")

        dp = DashboardProvider(root_dir=tmp_path)
        state = dp.get_state()
        assert state.memory_entries == 2

    def test_get_state_with_suggestions(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        suggestions = [{"s": 1}, {"s": 2}, {"s": 3}]
        (data_dir / "evolve-suggestions.json").write_text(
            json.dumps(suggestions), encoding="utf-8"
        )

        dp = DashboardProvider(root_dir=tmp_path)
        state = dp.get_state()
        assert state.evolution_suggestions == 3


class TestCountDelegations:
    """Tests for DashboardProvider._count_delegations."""

    def test_no_db(self, tmp_path):
        dp = DashboardProvider(root_dir=tmp_path)
        assert dp._count_delegations() == 0

    def test_with_db(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "maop.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE delegations (id INTEGER PRIMARY KEY, exit_code INTEGER)")
        conn.execute("INSERT INTO delegations (exit_code) VALUES (0)")
        conn.execute("INSERT INTO delegations (exit_code) VALUES (1)")
        conn.commit()
        conn.close()

        dp = DashboardProvider(root_dir=tmp_path)
        assert dp._count_delegations() == 2

    def test_db_no_delegations_table(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "maop.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE other (id INTEGER)")
        conn.commit()
        conn.close()

        dp = DashboardProvider(root_dir=tmp_path)
        assert dp._count_delegations() == 0


class TestComputeSuccessRate:
    """Tests for DashboardProvider._compute_success_rate."""

    def test_no_db(self, tmp_path):
        dp = DashboardProvider(root_dir=tmp_path)
        assert dp._compute_success_rate() == 0.0

    def test_all_success(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "maop.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE delegations (id INTEGER PRIMARY KEY, exit_code INTEGER)")
        conn.execute("INSERT INTO delegations (exit_code) VALUES (0)")
        conn.execute("INSERT INTO delegations (exit_code) VALUES (0)")
        conn.commit()
        conn.close()

        dp = DashboardProvider(root_dir=tmp_path)
        assert dp._compute_success_rate() == 100.0

    def test_partial_success(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "maop.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE delegations (id INTEGER PRIMARY KEY, exit_code INTEGER)")
        conn.execute("INSERT INTO delegations (exit_code) VALUES (0)")
        conn.execute("INSERT INTO delegations (exit_code) VALUES (1)")
        conn.commit()
        conn.close()

        dp = DashboardProvider(root_dir=tmp_path)
        assert dp._compute_success_rate() == 50.0

    def test_no_delegations(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "maop.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE delegations (id INTEGER PRIMARY KEY, exit_code INTEGER)")
        conn.commit()
        conn.close()

        dp = DashboardProvider(root_dir=tmp_path)
        assert dp._compute_success_rate() == 0.0


class TestCountMemoryEntries:
    """Tests for DashboardProvider._count_memory_entries."""

    def test_no_dir(self, tmp_path):
        dp = DashboardProvider(root_dir=tmp_path)
        assert dp._count_memory_entries() == 0

    def test_with_entries(self, tmp_path):
        entries_dir = tmp_path / "memory" / "entries"
        entries_dir.mkdir(parents=True)
        for i in range(5):
            (entries_dir / f"e{i}.json").write_text("{}", encoding="utf-8")

        dp = DashboardProvider(root_dir=tmp_path)
        assert dp._count_memory_entries() == 5

    def test_non_json_files_excluded(self, tmp_path):
        entries_dir = tmp_path / "memory" / "entries"
        entries_dir.mkdir(parents=True)
        (entries_dir / "e.json").write_text("{}", encoding="utf-8")
        (entries_dir / "e.txt").write_text("x", encoding="utf-8")

        dp = DashboardProvider(root_dir=tmp_path)
        assert dp._count_memory_entries() == 1


class TestCountSuggestions:
    """Tests for DashboardProvider._count_suggestions."""

    def test_no_file(self, tmp_path):
        dp = DashboardProvider(root_dir=tmp_path)
        assert dp._count_suggestions() == 0

    def test_with_list(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "evolve-suggestions.json").write_text(
            json.dumps([1, 2, 3]), encoding="utf-8"
        )
        dp = DashboardProvider(root_dir=tmp_path)
        assert dp._count_suggestions() == 3

    def test_with_non_list(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "evolve-suggestions.json").write_text(
            json.dumps({"key": "val"}), encoding="utf-8"
        )
        dp = DashboardProvider(root_dir=tmp_path)
        assert dp._count_suggestions() == 0

    def test_corrupt_file(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "evolve-suggestions.json").write_text("not json", encoding="utf-8")
        dp = DashboardProvider(root_dir=tmp_path)
        assert dp._count_suggestions() == 0


class TestGetAgentDetail:
    """Tests for DashboardProvider.get_agent_detail."""

    def test_agent_detail_no_config(self, tmp_path):
        dp = DashboardProvider(root_dir=tmp_path)
        detail = dp.get_agent_detail("claude")
        assert detail["name"] == "claude"

    def test_agent_detail_with_config(self, tmp_path):
        # Create config dir with agents.yaml
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        agents_yaml = """
agents:
  claude:
    cli: claude
    driver: cli
    capabilities: [codegen]
    timeout_s: 60
"""
        (config_dir / "agents.yaml").write_text(agents_yaml, encoding="utf-8")

        dp = DashboardProvider(root_dir=tmp_path)
        detail = dp.get_agent_detail("claude")
        assert detail["name"] == "claude"
        # May or may not have driver depending on whether circuit_breaker loads
        # but name should always be present


class TestGetRecentDelegations:
    """Tests for DashboardProvider.get_recent_delegations."""

    def test_no_file(self, tmp_path):
        dp = DashboardProvider(root_dir=tmp_path)
        assert dp.get_recent_delegations() == []

    def test_with_list(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(parents=True)
        delegations = [{"id": 1}, {"id": 2}, {"id": 3}]
        (logs_dir / "delegations.json").write_text(
            json.dumps(delegations), encoding="utf-8"
        )
        dp = DashboardProvider(root_dir=tmp_path)
        result = dp.get_recent_delegations()
        assert len(result) == 3

    def test_with_limit(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(parents=True)
        delegations = [{"id": i} for i in range(10)]
        (logs_dir / "delegations.json").write_text(
            json.dumps(delegations), encoding="utf-8"
        )
        dp = DashboardProvider(root_dir=tmp_path)
        result = dp.get_recent_delegations(limit=3)
        assert len(result) == 3

    def test_with_single_dict(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "delegations.json").write_text(
            json.dumps({"id": 1}), encoding="utf-8"
        )
        dp = DashboardProvider(root_dir=tmp_path)
        result = dp.get_recent_delegations()
        assert len(result) == 1

    def test_corrupt_file(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "delegations.json").write_text("not json", encoding="utf-8")
        dp = DashboardProvider(root_dir=tmp_path)
        assert dp.get_recent_delegations() == []


# v5.0.0: TestRenderHtml class removed — _render_html() was deleted in v5.0.0
# (deprecated since v4.0.0). Vue 3 SPA is the sole frontend.


class TestAsyncCountDelegations:
    """Tests for DashboardProvider._async_count_delegations."""

    @pytest.mark.asyncio
    async def test_no_db(self, tmp_path):
        dp = DashboardProvider(root_dir=tmp_path)
        assert await dp._async_count_delegations() == 0

    @pytest.mark.asyncio
    async def test_with_db(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "maop.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE delegations (id INTEGER PRIMARY KEY, exit_code INTEGER)")
        conn.execute("INSERT INTO delegations (exit_code) VALUES (0)")
        conn.execute("INSERT INTO delegations (exit_code) VALUES (1)")
        conn.commit()
        conn.close()

        dp = DashboardProvider(root_dir=tmp_path)
        assert await dp._async_count_delegations() == 2


class TestAsyncComputeSuccessRate:
    """Tests for DashboardProvider._async_compute_success_rate."""

    @pytest.mark.asyncio
    async def test_no_db(self, tmp_path):
        dp = DashboardProvider(root_dir=tmp_path)
        assert await dp._async_compute_success_rate() == 0.0

    @pytest.mark.asyncio
    async def test_all_success(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "maop.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE delegations (id INTEGER PRIMARY KEY, exit_code INTEGER)")
        conn.execute("INSERT INTO delegations (exit_code) VALUES (0)")
        conn.execute("INSERT INTO delegations (exit_code) VALUES (0)")
        conn.commit()
        conn.close()

        dp = DashboardProvider(root_dir=tmp_path)
        assert await dp._async_compute_success_rate() == 100.0

    @pytest.mark.asyncio
    async def test_partial_success(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "maop.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE delegations (id INTEGER PRIMARY KEY, exit_code INTEGER)")
        conn.execute("INSERT INTO delegations (exit_code) VALUES (0)")
        conn.execute("INSERT INTO delegations (exit_code) VALUES (1)")
        conn.commit()
        conn.close()

        dp = DashboardProvider(root_dir=tmp_path)
        assert await dp._async_compute_success_rate() == 50.0


class TestAsyncGetState:
    """Tests for DashboardProvider.async_get_state."""

    @pytest.mark.asyncio
    async def test_empty_root(self, tmp_path):
        dp = DashboardProvider(root_dir=tmp_path)
        state = await dp.async_get_state()
        assert isinstance(state, DashboardState)
        assert state.total_delegations == 0
        assert state.success_rate == 0.0

    @pytest.mark.asyncio
    async def test_with_delegations(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "maop.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE delegations (
                agent TEXT, task TEXT, exit_code INTEGER,
                duration_ms INTEGER, timestamp TEXT, error TEXT
            );
            INSERT INTO delegations VALUES
                ('claude', 'fix bug', 0, 1500, '2026-07-15T10:00:00', NULL),
                ('codex', 'refactor', 1, 3000, '2026-07-15T10:05:00', 'timeout'),
                ('claude', 'add test', 0, 800, '2026-07-15T10:10:00', NULL);
        """)
        conn.commit()
        conn.close()

        dp = DashboardProvider(root_dir=tmp_path)
        state = await dp.async_get_state()
        assert state.total_delegations == 3
        assert state.success_rate == 66.7


# v5.0.0: TestCreateApp class removed — create_app() was deleted in v5.0.0
# (deprecated since v4.0.0). Use maop.dashboard.server:app instead.
