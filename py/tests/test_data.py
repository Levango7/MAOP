"""Tests for MAOP.core.data — SQLite persistence layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.backends.data import MaopDatabase


@pytest.fixture
def db(tmp_path: Path) -> MaopDatabase:
    """Create a temp database and initialize it."""
    db_path = tmp_path / "test.db"
    database = MaopDatabase(db_path=db_path)
    assert database.init() is True
    return database


class TestMaopDatabase:
    def test_init_creates_file(self, tmp_path: Path):
        db_path = tmp_path / "new.db"
        database = MaopDatabase(db_path=db_path)
        assert database.init() is True
        assert db_path.exists()

    def test_insert_and_query_delegation(self, db: MaopDatabase):
        ok = db.insert_delegation(
            agent="claude",
            task="fix bug",
            routing_key="codegen",
            exit_code=0,
            stdout="done",
            duration_ms=1500,
            trace_id="trace-001",
        )
        assert ok is True

        rows = db.get_recent_delegations(limit=10)
        assert len(rows) == 1
        assert rows[0].agent == "claude"
        assert rows[0].task == "fix bug"
        assert rows[0].exit_code == 0

    def test_checkpoint_save_and_load(self, db: MaopDatabase):
        state = {"phase": "exec", "step": 3, "data": [1, 2, 3]}
        ok = db.save_checkpoint("claude", "fix-bug", "exec", state)
        assert ok is True

        loaded = db.get_checkpoint("claude", "fix-bug")
        assert loaded is not None
        assert loaded["phase"] == "exec"
        assert loaded["step"] == 3

    def test_checkpoint_delete(self, db: MaopDatabase):
        db.save_checkpoint("claude", "task1", "plan", {"x": 1})
        assert db.get_checkpoint("claude", "task1") is not None

        ok = db.delete_checkpoint("claude", "task1")
        assert ok is True
        assert db.get_checkpoint("claude", "task1") is None

    def test_log_error(self, db: MaopDatabase):
        ok = db.log_error(
            agent="kimi",
            task="search",
            exit_code=-1,
            error="timeout",
            trace_id="t-002",
            duration_ms=5000,
        )
        assert ok is True

        rows = db._query("SELECT * FROM error_log LIMIT 10")
        assert len(rows) == 1
        assert rows[0]["agent"] == "kimi"
        assert rows[0]["error"] == "timeout"

    def test_record_metric(self, db: MaopDatabase):
        ok = db.record_metric(
            agent="claude",
            metric_name="latency_ms",
            metric_value=123.4,
            tags={"routing_key": "codegen"},
        )
        assert ok is True

        rows = db._query("SELECT * FROM metrics LIMIT 10")
        assert len(rows) == 1
        assert rows[0]["metric_value"] == 123.4

    def test_sync_breaker(self, db: MaopDatabase):
        ok = db.sync_breaker(
            agent="claude",
            state="open",
            failures=3,
            threshold=3,
            last_failure="2026-07-12T10:00:00",
            cooldown_s=60,
        )
        assert ok is True

        rows = db._query("SELECT * FROM circuit_breaker WHERE agent = 'claude'")
        assert len(rows) == 1
        assert rows[0]["state"] == "open"
        assert rows[0]["failures"] == 3

    def test_generic_query(self, db: MaopDatabase):
        """Test generic query/execute methods."""
        db.insert_delegation(agent="test", task="query-test")
        rows = db._query("SELECT COUNT(*) as cnt FROM delegations")
        assert rows[0]["cnt"] >= 1

    def test_multiple_delegations(self, db: MaopDatabase):
        for i in range(5):
            db.insert_delegation(agent=f"agent-{i}", task=f"task-{i}")
        rows = db.get_recent_delegations(limit=3)
        assert len(rows) == 3
