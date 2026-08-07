"""Unit tests for MAOP.core.db_utils module."""

from __future__ import annotations

import sqlite3

import pytest

from maop.core.backends.db_utils import sqlite_connect, validate_identifier


class TestValidateIdentifier:
    def test_valid_simple(self):
        assert validate_identifier("foo") == "foo"

    def test_valid_with_underscore(self):
        assert validate_identifier("_bar") == "_bar"

    def test_valid_alphanumeric(self):
        assert validate_identifier("tbl_123") == "tbl_123"

    def test_invalid_starts_with_digit(self):
        with pytest.raises(ValueError, match="Invalid SQL"):
            validate_identifier("1abc")

    def test_invalid_special_chars(self):
        with pytest.raises(ValueError, match="Invalid SQL"):
            validate_identifier("drop table")

    def test_invalid_empty(self):
        with pytest.raises(ValueError, match="Invalid SQL"):
            validate_identifier("")

    def test_invalid_sql_injection(self):
        with pytest.raises(ValueError, match="Invalid SQL"):
            validate_identifier("users; DROP TABLE--")

    def test_custom_context(self):
        with pytest.raises(ValueError, match="column"):
            validate_identifier("1bad", context="column")


class TestSqliteConnect:
    def test_basic_connection(self, tmp_path):
        db = tmp_path / "test.db"
        with sqlite_connect(db) as conn:
            conn.execute("CREATE TABLE t (id INTEGER)")
            conn.execute("INSERT INTO t VALUES (1)")
        with sqlite_connect(db) as conn:
            rows = conn.execute("SELECT * FROM t").fetchall()
            assert len(rows) == 1

    def test_wal_mode(self, tmp_path):
        db = tmp_path / "test.db"
        with sqlite_connect(db, wal=True) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"

    def test_wal_disabled(self, tmp_path):
        db = tmp_path / "test.db"
        with sqlite_connect(db, wal=False) as conn:
            conn.execute("CREATE TABLE t (id INTEGER)")

    def test_foreign_keys(self, tmp_path):
        db = tmp_path / "test.db"
        with sqlite_connect(db, foreign_keys=True) as conn:
            fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert fk == 1

    def test_foreign_keys_disabled(self, tmp_path):
        db = tmp_path / "test.db"
        with sqlite_connect(db, foreign_keys=False) as conn:
            conn.execute("CREATE TABLE t (id INTEGER)")

    def test_rollback_on_error(self, tmp_path):
        db = tmp_path / "test.db"
        with sqlite_connect(db) as conn:
            conn.execute("CREATE TABLE t (id INTEGER UNIQUE)")
            conn.execute("INSERT INTO t VALUES (1)")
        with pytest.raises(sqlite3.IntegrityError), sqlite_connect(db) as conn:
            conn.execute("INSERT INTO t VALUES (1)")
        with sqlite_connect(db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            assert count == 1

    def test_row_factory(self, tmp_path):
        db = tmp_path / "test.db"
        with sqlite_connect(db) as conn:
            conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
            conn.execute("INSERT INTO t VALUES (1, 'alice')")
            row = conn.execute("SELECT * FROM t").fetchone()
            assert row["name"] == "alice"

    def test_no_row_factory(self, tmp_path):
        db = tmp_path / "test.db"
        with sqlite_connect(db, row_factory=None) as conn:
            conn.execute("CREATE TABLE t (id INTEGER)")
            conn.execute("INSERT INTO t VALUES (1)")
            row = conn.execute("SELECT * FROM t").fetchone()
            assert isinstance(row, tuple)

    def test_connection_closed_after_context(self, tmp_path):
        db = tmp_path / "test.db"
        with sqlite_connect(db) as conn:
            conn.execute("CREATE TABLE t (id INTEGER)")
        with pytest.raises(Exception):  # noqa: B017
            conn.execute("SELECT 1")
