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


class TestCorruptionRecoveryScope:
    """sqlite_connect 的"损坏即删除重建"只允许真正损坏的文件（2026-09-26 收紧）。

    sqlite3.OperationalError 是 DatabaseError 的子类，收紧前 "database is locked"
    也会走进删除分支 —— 等于把别的连接正在写的库删掉（对端随后写入已被 unlink 的
    页会 SIGBUS；CI 上表现为 xdist worker node down + 覆盖率门禁假红）。
    """

    def test_classifier_rejects_lock_and_io_errors(self):
        from maop.core.backends.db_utils import _recoverable_corruption

        for msg in (
            "database is locked",
            "database is busy",
            "unable to open database file",
            "disk I/O error",
            "attempt to write a readonly database",
            "no space left on device",
        ):
            exc = sqlite3.OperationalError(msg)
            assert _recoverable_corruption(exc, "whatever.db") is False, msg

    def test_classifier_accepts_genuine_corruption(self):
        from maop.core.backends.db_utils import _recoverable_corruption

        for msg in (
            "file is not a database",
            "database disk image is malformed",
            "unsupported file format",
        ):
            exc = sqlite3.DatabaseError(msg)
            assert _recoverable_corruption(exc, "whatever.db") is True, msg

    def test_locked_database_is_never_deleted(self, tmp_path, monkeypatch):
        from maop.core.backends import db_utils

        db = tmp_path / "live.db"
        seed = sqlite3.connect(str(db))
        seed.execute("CREATE TABLE t (v TEXT)")
        seed.execute("INSERT INTO t VALUES ('keepme')")
        seed.commit()
        seed.close()

        class FakeSqlite:
            DatabaseError = sqlite3.DatabaseError
            OperationalError = sqlite3.OperationalError

            def __init__(self):
                self.calls = 0

            def connect(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise sqlite3.OperationalError("database is locked")
                return sqlite3.connect(*args, **kwargs)

        fake = FakeSqlite()
        monkeypatch.setattr(db_utils, "sqlite3", fake)
        with pytest.raises(sqlite3.OperationalError, match="locked"), sqlite_connect(db):
            pass

        assert fake.calls == 1, "非损坏错误不得走删除重建分支"
        assert db.exists()
        restored = sqlite3.connect(str(db))
        try:
            assert restored.execute("SELECT v FROM t").fetchall() == [("keepme",)]
        finally:
            restored.close()

    def test_genuinely_broken_file_is_still_recreated(self, tmp_path):
        db = tmp_path / "broken.db"
        db.write_bytes(b"this is plainly not a sqlite database file")

        with sqlite_connect(db) as conn:
            conn.execute("CREATE TABLE t (v TEXT)")
            conn.execute("INSERT INTO t VALUES ('fresh')")

        check = sqlite3.connect(str(db))
        try:
            assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert check.execute("SELECT v FROM t").fetchall() == [("fresh",)]
        finally:
            check.close()
