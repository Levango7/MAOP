"""Tests for MAOP.core.migration - DB migration tool."""

from __future__ import annotations

import sqlite3

import pytest

from maop.core.backends.migration import Migration, MigrationManager


class TestMigration:
    def test_create(self):
        m = Migration(version=1, name="create_agents", up_sql="CREATE TABLE agents (id INTEGER)")
        assert m.version == 1
        assert m.name == "create_agents"

    def test_with_down(self):
        m = Migration(version=1, name="test", up_sql="CREATE TABLE t (id)", down_sql="DROP TABLE t")
        assert m.down_sql == "DROP TABLE t"


class TestMigrationManager:
    @pytest.fixture
    def db_path(self, tmp_path):
        return tmp_path / "test.db"

    @pytest.fixture
    def migrations_dir(self, tmp_path):
        mdir = tmp_path / "migrations"
        mdir.mkdir()
        # Create migration files
        (mdir / "001_create_agents.sql").write_text(
            "CREATE TABLE agents (id INTEGER PRIMARY KEY, name TEXT);"
        )
        (mdir / "002_add_memory_index.sql").write_text(
            "CREATE TABLE memory_index (id INTEGER PRIMARY KEY, key TEXT, value TEXT);\n"
            "CREATE INDEX idx_key ON memory_index(key);\n"
            "-- DOWN:\n"
            "DROP INDEX idx_key;\n"
            "DROP TABLE memory_index;"
        )
        return mdir

    def test_creates_migration_table(self, db_path):
        MigrationManager(db_path)
        conn = sqlite3.connect(str(db_path))
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = [t[0] for t in tables]
        assert "_migrations" in table_names
        conn.close()

    def test_current_version_initial(self, db_path):
        mgr = MigrationManager(db_path)
        assert mgr.current_version() == 0

    def test_apply_migration(self, db_path):
        mgr = MigrationManager(db_path)
        m = Migration(version=1, name="test", up_sql="CREATE TABLE test (id INTEGER)")
        record = mgr.apply(m)
        assert record.version == 1
        assert mgr.current_version() == 1

    def test_rollback(self, db_path):
        mgr = MigrationManager(db_path)
        m = Migration(
            version=1, name="test",
            up_sql="CREATE TABLE test (id INTEGER)",
            down_sql="DROP TABLE test",
        )
        mgr.apply(m)
        assert mgr.current_version() == 1
        mgr.rollback(m)
        assert mgr.current_version() == 0

    def test_rollback_no_down_sql(self, db_path):
        mgr = MigrationManager(db_path)
        m = Migration(version=1, name="test", up_sql="CREATE TABLE test (id INTEGER)")
        mgr.apply(m)
        with pytest.raises(ValueError, match="irreversible"):
            mgr.rollback(m)

    def test_discover_migrations(self, db_path, migrations_dir):
        mgr = MigrationManager(db_path, migrations_dir)
        discovered = mgr._discover_migrations()
        assert len(discovered) == 2
        assert discovered[0].version == 1
        assert discovered[0].name == "create_agents"
        assert discovered[1].version == 2
        assert discovered[1].name == "add_memory_index"
        assert discovered[1].down_sql  # Has DOWN section

    def test_pending_migrations(self, db_path, migrations_dir):
        mgr = MigrationManager(db_path, migrations_dir)
        pending = mgr.pending_migrations()
        assert len(pending) == 2

    def test_upgrade_all(self, db_path, migrations_dir):
        mgr = MigrationManager(db_path, migrations_dir)
        results = mgr.upgrade()
        assert len(results) == 2
        assert mgr.current_version() == 2

    def test_upgrade_to_target(self, db_path, migrations_dir):
        mgr = MigrationManager(db_path, migrations_dir)
        results = mgr.upgrade(target_version=1)
        assert len(results) == 1
        assert mgr.current_version() == 1
        # Second upgrade should only apply v2
        results2 = mgr.upgrade()
        assert len(results2) == 1
        assert mgr.current_version() == 2

    def test_upgrade_no_pending(self, db_path, migrations_dir):
        mgr = MigrationManager(db_path, migrations_dir)
        mgr.upgrade()
        results = mgr.upgrade()
        assert len(results) == 0

    def test_downgrade(self, db_path, migrations_dir):
        mgr = MigrationManager(db_path, migrations_dir)
        mgr.upgrade()
        assert mgr.current_version() == 2
        rolled = mgr.downgrade(steps=1)
        assert len(rolled) == 1
        assert mgr.current_version() == 1

    def test_applied_versions(self, db_path, migrations_dir):
        mgr = MigrationManager(db_path, migrations_dir)
        mgr.upgrade()
        applied = mgr.applied_versions()
        assert len(applied) == 2
        assert applied[0].version == 1
        assert applied[1].version == 2

    def test_status(self, db_path, migrations_dir):
        mgr = MigrationManager(db_path, migrations_dir)
        mgr.upgrade()
        status = mgr.status()
        assert status["current_version"] == 2
        assert status["applied_count"] == 2
        assert status["pending_count"] == 0
