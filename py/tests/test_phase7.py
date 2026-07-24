"""Tests for Phase 7: Dashboard pure Python."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from maop.dashboard import (
    DashboardProvider, DashboardState, AgentStatus,
    create_app, _render_html,
)


# ═══════════════════════════════════════════════════════════════
# DashboardState tests
# ═══════════════════════════════════════════════════════════════

class TestDashboardState:
    """Test dashboard state model."""

    def test_defaults(self):
        state = DashboardState()
        assert state.agents == []
        assert state.total_delegations == 0
        assert state.success_rate == 0.0
        assert state.uptime_s == 0.0

    def test_with_agents(self):
        agents = [
            AgentStatus(name="claude", driver="cli", available=True, breaker_state="closed"),
            AgentStatus(name="codex", driver="cli", available=False, breaker_state="open"),
        ]
        state = DashboardState(agents=agents, total_delegations=10, success_rate=80.0)
        assert len(state.agents) == 2
        assert state.agents[0].name == "claude"
        assert state.agents[1].breaker_state == "open"


# ═══════════════════════════════════════════════════════════════
# DashboardProvider tests
# ═══════════════════════════════════════════════════════════════

class TestDashboardProvider:
    """Test dashboard data provider."""

    def test_get_state_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = DashboardProvider(root_dir=tmp)
            state = provider.get_state()
            assert isinstance(state, DashboardState)
            assert state.uptime_s >= 0  # May be 0 if very fast

    def test_get_state_with_delegations(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Set up maop.db with delegation data (provider reads from SQLite)
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            MAOP_db = data_dir / "maop.db"
            conn = sqlite3.connect(str(MAOP_db))
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS delegations (
                    agent TEXT, task TEXT, exit_code INTEGER,
                    duration_ms INTEGER, timestamp TEXT, error TEXT
                );
                INSERT INTO delegations VALUES
                    ('claude', 'fix bug', 0, 1500, '2026-07-15T10:00:00+00:00', NULL),
                    ('codex', 'refactor', 1, 3000, '2026-07-15T10:05:00+00:00', 'timeout'),
                    ('claude', 'add test', 0, 800, '2026-07-15T10:10:00+00:00', NULL);
            """)
            conn.commit()
            conn.close()

            provider = DashboardProvider(root_dir=tmp)
            state = provider.get_state()
            assert state.total_delegations == 3
            assert state.success_rate == 66.7

    def test_get_state_with_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem_dir = Path(tmp) / "memory" / "entries"
            mem_dir.mkdir(parents=True)
            (mem_dir / "entry1.json").write_text("{}", encoding="utf-8")
            (mem_dir / "entry2.json").write_text("{}", encoding="utf-8")

            provider = DashboardProvider(root_dir=tmp)
            state = provider.get_state()
            assert state.memory_entries == 2

    def test_get_state_with_suggestions(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            suggestions = [{"id": "S000", "type": "slow_agent"}]
            (data_dir / "evolve-suggestions.json").write_text(
                json.dumps(suggestions), encoding="utf-8"
            )

            provider = DashboardProvider(root_dir=tmp)
            state = provider.get_state()
            assert state.evolution_suggestions == 1

    def test_get_agent_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = DashboardProvider(root_dir=tmp)
            detail = provider.get_agent_detail("claude")
            assert detail["name"] == "claude"

    def test_get_recent_delegations(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "logs"
            log_dir.mkdir()
            data = [{"agent": f"a{i}"} for i in range(30)]
            (log_dir / "delegations.json").write_text(json.dumps(data), encoding="utf-8")

            provider = DashboardProvider(root_dir=tmp)
            recent = provider.get_recent_delegations(limit=10)
            assert len(recent) == 10

    def test_get_recent_delegations_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = DashboardProvider(root_dir=tmp)
            recent = provider.get_recent_delegations()
            assert recent == []


# ═══════════════════════════════════════════════════════════════
# HTML rendering tests
# ═══════════════════════════════════════════════════════════════

class TestRenderHtml:
    """Test HTML dashboard rendering."""

    def test_empty_state(self):
        state = DashboardState()
        with pytest.warns(DeprecationWarning, match="_render_html is deprecated"):
            html = _render_html(state)
        assert "MAOP Dashboard" in html
        assert "Delegations" in html

    def test_with_agents(self):
        agents = [
            AgentStatus(name="claude", driver="cli", available=True, breaker_state="closed"),
            AgentStatus(name="codex", driver="cli", available=False, breaker_state="open"),
        ]
        state = DashboardState(agents=agents, total_delegations=42, success_rate=85.5)
        with pytest.warns(DeprecationWarning, match="_render_html is deprecated"):
            html = _render_html(state)
        assert "claude" in html
        assert "codex" in html
        assert "42" in html
        assert "85.5" in html


# ═══════════════════════════════════════════════════════════════
# FastAPI app tests
# ═══════════════════════════════════════════════════════════════

class TestCreateApp:
    """Test FastAPI app creation — verifies deprecation warning is emitted."""

    def test_create_app(self):
        with pytest.warns(DeprecationWarning, match="create_app.*deprecated"):
            app = create_app()
        # May be None if FastAPI not installed, but in our env it should work
        if app is not None:
            assert app.title == "MAOP Dashboard"

    def test_create_app_with_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.warns(DeprecationWarning, match="create_app.*deprecated"):
                app = create_app(root_dir=tmp)
            if app is not None:
                assert app is not None
