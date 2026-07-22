"""Tests for MAOP.dashboard.data_bridge — Pure Python data bridge."""

import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from maop.dashboard.data_bridge import DataBridge


@pytest.fixture
def bridge_env():
    """Create a temp directory with test SQLite databases."""
    tmpdir = tempfile.mkdtemp()
    from pathlib import Path

    data_dir = Path(tmpdir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Use dynamic timestamps (recent) so time-windowed queries include them
    now = datetime.now(timezone.utc)
    def ts(offset_min):
        return (now - timedelta(minutes=offset_min)).isoformat()

    # Create maop.db with test data
    MAOP_db = data_dir / "maop.db"
    conn = sqlite3.connect(str(MAOP_db))
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS delegations (
            agent TEXT, task TEXT, exit_code INTEGER,
            duration_ms INTEGER, timestamp TEXT, error TEXT
        );
        CREATE TABLE IF NOT EXISTS circuit_breaker_state (
            agent TEXT PRIMARY KEY, state TEXT, failures INTEGER, threshold INTEGER
        );
        CREATE TABLE IF NOT EXISTS failover_chains (
            name TEXT PRIMARY KEY, agents TEXT, current_index INTEGER
        );
        CREATE TABLE IF NOT EXISTS error_log (
            agent TEXT, error TEXT, timestamp TEXT
        );
        INSERT INTO delegations VALUES
            ('claude', 'fix bug', 0, 1500, '{ts(30)}', NULL),
            ('claude', 'add test', 0, 800, '{ts(25)}', NULL),
            ('codex', 'refactor', 1, 3000, '{ts(20)}', 'timeout'),
            ('claude', 'update doc', 0, 200, '{ts(15)}', NULL);
        INSERT INTO circuit_breaker_state VALUES
            ('claude', 'closed', 0, 5),
            ('codex', 'open', 5, 5);
        INSERT INTO failover_chains VALUES
            ('primary', 'claude,codex,gpt', 0);
    """)
    conn.commit()
    conn.close()

    # Create memory.db with test data
    mem_db = data_dir / "memory.db"
    conn = sqlite3.connect(str(mem_db))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory_entries (
            id TEXT PRIMARY KEY, agent TEXT, task TEXT, content TEXT,
            tags TEXT, topic TEXT, trace_id TEXT, session_id TEXT,
            exit_code INTEGER, duration_ms INTEGER, timestamp TEXT
        );
        CREATE TABLE IF NOT EXISTS memory_traces (
            trace_id TEXT PRIMARY KEY, parent_trace_id TEXT,
            session_id TEXT, task TEXT, agents TEXT,
            created TEXT, last_active TEXT, status TEXT
        );
        CREATE TABLE IF NOT EXISTS memory_trajectory (
            id TEXT PRIMARY KEY, trace_id TEXT, agent TEXT, task TEXT,
            tool_name TEXT, tool_input TEXT, tool_output TEXT,
            duration_ms INTEGER, exit_code INTEGER, timestamp TEXT
        );
        INSERT INTO memory_entries VALUES
            ('m1', 'claude', 'fix bug', 'fixed', '', 'general', '', '', 0, 0, '{ts(30)}'),
            ('m2', 'codex', 'refactor', 'refactored', '', 'codegen', '', '', 0, 0, '{ts(25)}');
    """)
    conn.commit()
    conn.close()

    # Create queue.db
    queue_db = data_dir / "queue.db"
    conn = sqlite3.connect(str(queue_db))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS queue_messages (
            id TEXT PRIMARY KEY, topic TEXT, payload TEXT,
            status TEXT, priority INTEGER, max_retries INTEGER,
            ack_timeout_s REAL, retries INTEGER, created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS queue_dead_letters (
            id TEXT PRIMARY KEY, topic TEXT, payload TEXT,
            error TEXT, retries INTEGER, created_at TEXT,
            moved_at TEXT
        );
        INSERT INTO queue_messages VALUES
            ('q1', 'tasks', 'work1', 'pending', 5, 3, 30.0, 0, '{ts(30)}', '{ts(30)}'),
            ('q2', 'tasks', 'work2', 'processing', 5, 3, 30.0, 0, '{ts(29)}', '{ts(29)}');
        INSERT INTO queue_dead_letters VALUES
            ('d1', 'tasks', 'failed_work', 'max retries', 3, '{ts(60)}', '{ts(55)}');
    """)
    conn.commit()
    conn.close()

    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


# ── Report ─────────────────────────────────────────────────────

class TestReport:
    @pytest.mark.asyncio
    async def test_report(self, bridge_env):
        bridge = DataBridge(root_dir=bridge_env)
        result = await bridge.report(hours=48)
        assert result["total_delegations"] == 4
        assert result["success_rate"] == 75.0  # 3/4 * 100
        assert "claude" in result["by_agent"]
        assert "codex" in result["by_agent"]

    @pytest.mark.asyncio
    async def test_report_structure(self, bridge_env):
        bridge = DataBridge(root_dir=bridge_env)
        result = await bridge.report(hours=48)
        # Verify structure even with data
        assert "hours" in result
        assert "total_delegations" in result
        assert "success_rate" in result
        assert "by_agent" in result


# ── Agent Stats ────────────────────────────────────────────────

class TestAgentStats:
    @pytest.mark.asyncio
    async def test_agent_stats(self, bridge_env):
        bridge = DataBridge(root_dir=bridge_env)
        result = await bridge.agent_stats()
        assert len(result) >= 2
        # Find claude
        claude = next(a for a in result if a["agent"] == "claude")
        assert claude["total_delegations"] == 3
        assert claude["circuit_breaker"] == "closed"
        # success_rate should be 0-100 percentage (3/3 * 100 = 100.0)
        assert claude["success_rate"] == 100.0
        # Find codex
        codex = next(a for a in result if a["agent"] == "codex")
        assert codex["circuit_breaker"] == "open"
        # codex: 0/1 * 100 = 0.0
        assert codex["success_rate"] == 0.0


# ── Live ───────────────────────────────────────────────────────

class TestLive:
    @pytest.mark.asyncio
    async def test_live(self, bridge_env):
        bridge = DataBridge(root_dir=bridge_env)
        result = await bridge.live()
        assert "recent_delegations" in result
        assert "open_circuit_breakers" in result
        assert "timestamp" in result
        # Codex breaker is open
        assert len(result["open_circuit_breakers"]) >= 1


# ── Failures ───────────────────────────────────────────────────

class TestFailures:
    @pytest.mark.asyncio
    async def test_failures(self, bridge_env):
        bridge = DataBridge(root_dir=bridge_env)
        result = await bridge.failures()
        assert len(result) >= 1
        assert result[0]["exit_code"] != 0


# ── Timeseries ─────────────────────────────────────────────────

class TestTimeseries:
    @pytest.mark.asyncio
    async def test_timeseries(self, bridge_env):
        bridge = DataBridge(root_dir=bridge_env)
        result = await bridge.timeseries(hours=168)
        assert isinstance(result, list)
        # Should have at least 1 hour bucket
        if result:
            assert "hour" in result[0]
            assert "total" in result[0]


# ── Chain ──────────────────────────────────────────────────────

class TestChain:
    @pytest.mark.asyncio
    async def test_chain(self, bridge_env):
        bridge = DataBridge(root_dir=bridge_env)
        result = await bridge.chain()
        assert isinstance(result, list)
        if result:
            assert "name" in result[0]


# ── Memory Stats ───────────────────────────────────────────────

class TestMemoryStats:
    @pytest.mark.asyncio
    async def test_memory_stats(self, bridge_env):
        bridge = DataBridge(root_dir=bridge_env)
        result = await bridge.memory_stats()
        assert result["total_entries"] == 2
        assert "by_agent" in result
        assert "by_topic" in result


# ── Queue Stats ────────────────────────────────────────────────

class TestQueueStats:
    @pytest.mark.asyncio
    async def test_queue_stats(self, bridge_env):
        bridge = DataBridge(root_dir=bridge_env)
        result = await bridge.queue_stats()
        assert result["pending"] == 1
        assert result["processing"] == 1
        assert result["dead_letters"] == 1


# ── Guardrail Report ───────────────────────────────────────────

class TestGuardrailReport:
    @pytest.mark.asyncio
    async def test_guardrail_report(self, bridge_env):
        bridge = DataBridge(root_dir=bridge_env)
        result = await bridge.guardrail_report()
        assert "total_rules" in result
        assert "status" in result


# ── Bridge Stats ───────────────────────────────────────────────

class TestBridgeStats:
    @pytest.mark.asyncio
    async def test_stats_tracking(self, bridge_env):
        bridge = DataBridge(root_dir=bridge_env)
        await bridge.report()
        await bridge.live()
        stats = bridge.stats()
        assert stats.queries >= 2
        assert stats.total_latency_ms >= 0

    def test_repr(self, bridge_env):
        bridge = DataBridge(root_dir=bridge_env)
        r = repr(bridge)
        assert "DataBridge" in r
