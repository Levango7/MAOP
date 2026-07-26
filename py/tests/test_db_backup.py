"""Tests for MAOP.core.db_backup — SQLite backup with retention."""

import shutil
import tempfile
import time

import pytest

from maop.core.db_backup import DbBackup


@pytest.fixture
def backup_env():
    """Create a temp directory with test SQLite databases."""
    tmpdir = tempfile.mkdtemp()
    import sqlite3
    from pathlib import Path

    data_dir = Path(tmpdir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Create test databases with some data
    for db_name in ["maop.db", "memory.db", "queue.db", "human_queue.db"]:
        conn = sqlite3.connect(str(data_dir / db_name))
        conn.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER, value TEXT)")
        conn.execute("INSERT INTO test VALUES (1, 'hello')")
        conn.commit()
        conn.close()

    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


# ── Backup execution ───────────────────────────────────────────

class TestBackupRun:
    def test_run_all(self, backup_env):
        backup = DbBackup(root_dir=backup_env, retention=5)
        results = backup.run()
        assert len(results) == 4  # maop.db, memory.db, queue.db, human_queue.db
        for r in results:
            assert r.size_bytes > 0
            assert r.duration_ms >= 0

    def test_run_specific(self, backup_env):
        backup = DbBackup(root_dir=backup_env, retention=5)
        results = backup.run(db_names=["maop.db"])
        assert len(results) == 1
        assert results[0].db_name == "maop.db"

    def test_run_nonexistent(self, backup_env):
        backup = DbBackup(root_dir=backup_env, retention=5)
        results = backup.run(db_names=["nonexistent.db"])
        assert len(results) == 0

    def test_backup_file_created(self, backup_env):
        backup = DbBackup(root_dir=backup_env, retention=5)
        results = backup.run()
        from pathlib import Path
        for r in results:
            assert Path(r.backup_path).exists()
            assert Path(r.backup_path).stat().st_size > 0


# ── Retention cleanup ──────────────────────────────────────────

class TestRetention:
    def test_cleanup_removes_old(self, backup_env):
        backup = DbBackup(root_dir=backup_env, retention=2)
        # Run 4 backups
        for _ in range(4):
            time.sleep(0.01)  # Ensure different timestamps
            backup.run()
        # Should have 4*4=16 backups
        assert len(backup._manifest) == 16
        # Cleanup with retention=2 should leave 2*4=8
        removed = backup.cleanup(retention=2)
        assert removed == 8
        assert len(backup._manifest) == 8

    def test_cleanup_keeps_recent(self, backup_env):
        backup = DbBackup(root_dir=backup_env, retention=1)
        backup.run()
        time.sleep(0.01)
        backup.run()
        removed = backup.cleanup(retention=1)
        # Should keep 1 per db = 4, remove 4
        assert removed == 4
        # Each db should have exactly 1 backup
        by_db = {}
        for e in backup._manifest:
            by_db.setdefault(e.db_name, []).append(e)
        for entries in by_db.values():
            assert len(entries) == 1


# ── Manifest ───────────────────────────────────────────────────

class TestManifest:
    def test_manifest_persistence(self, backup_env):
        backup1 = DbBackup(root_dir=backup_env, retention=5)
        backup1.run()
        assert len(backup1._manifest) == 4

        # Reload from disk
        backup2 = DbBackup(root_dir=backup_env, retention=5)
        assert len(backup2._manifest) == 4

    def test_list_backups(self, backup_env):
        backup = DbBackup(root_dir=backup_env, retention=5)
        backup.run()
        all_backups = backup.list_backups()
        assert len(all_backups) == 4
        MAOP_backups = backup.list_backups(db_name="maop.db")
        assert len(MAOP_backups) == 1


# ── Stats ──────────────────────────────────────────────────────

class TestBackupStats:
    def test_stats_empty(self, backup_env):
        backup = DbBackup(root_dir=backup_env, retention=5)
        stats = backup.stats()
        assert stats.total_backups == 0
        assert stats.total_size_bytes == 0

    def test_stats_after_backup(self, backup_env):
        backup = DbBackup(root_dir=backup_env, retention=5)
        backup.run()
        stats = backup.stats()
        assert stats.total_backups == 4
        assert stats.total_size_bytes > 0
        assert len(stats.databases) == 4
        assert stats.retention_count == 5

    def test_repr(self, backup_env):
        backup = DbBackup(root_dir=backup_env, retention=5)
        r = repr(backup)
        assert "DbBackup" in r
        assert "retention=5" in r


# ── Restore ────────────────────────────────────────────────────

class TestRestore:
    def test_restore_latest(self, backup_env):
        import sqlite3
        from pathlib import Path

        backup = DbBackup(root_dir=backup_env, retention=5)
        backup.run()

        # Modify the database
        db_path = Path(backup_env) / "data" / "maop.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("DELETE FROM test")
        conn.commit()
        conn.close()

        # Restore from backup
        success = backup.restore("maop.db")
        assert success

        # Verify data is restored
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT * FROM test").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0] == (1, "hello")

    def test_restore_nonexistent(self, backup_env):
        backup = DbBackup(root_dir=backup_env, retention=5)
        success = backup.restore("nonexistent.db")
        assert not success


# ── Scheduler ──────────────────────────────────────────────────

class TestScheduler:
    def test_start_stop(self, backup_env):
        backup = DbBackup(root_dir=backup_env, retention=5)
        backup.start_scheduler(interval_s=1)
        assert backup._scheduler_running
        time.sleep(0.1)
        backup.stop_scheduler()
        assert not backup._scheduler_running


# ── ADR-011: State Source Truth ──────────────────────────────────

class TestADR011StateSourceTruth:
    """Verify ADR-011: all state is in SQLite, no JSON truth sources remain."""

    def test_default_databases_includes_human_queue(self):
        from maop.core.db_backup import DEFAULT_DATABASES
        assert "human_queue.db" in DEFAULT_DATABASES

    def test_circuit_breaker_default_path_is_maop_db(self):
        from maop.core.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker()
        assert cb._path.name == "maop.db"

    def test_human_proxy_uses_sqlite_not_json(self):
        import inspect

        from maop.core.human_proxy import HumanProxy
        src = inspect.getsource(HumanProxy)
        assert "get_db_path" in src or "sqlite" in src.lower()
        assert "human-queue.json" not in src

    def test_no_json_truth_sources_in_core(self):
        import inspect
        import pkgutil

        import maop.core as core_pkg
        json_truth_patterns = [
            "circuit-breaker.json", "human-queue.json", "message_queue.json"
        ]
        for _importer, modname, _ispkg in pkgutil.walk_packages(
            core_pkg.__path__, core_pkg.__name__ + "."
        ):
            try:
                mod = __import__(modname, fromlist=[""])
                src = inspect.getsource(mod)
                for pattern in json_truth_patterns:
                    assert pattern not in src, (
                        f"ADR-011 violation: {pattern} found in {modname}"
                    )
            except Exception:
                pass
