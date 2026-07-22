"""Tests for maop.core.backends_pg.PostgreSQLStorageBackend.

E4 (2026-07-22, Phase E): verifies the PostgreSQL storage backend
behavior without a real PostgreSQL connection.

The psycopg module is mocked entirely, so these tests run even when
psycopg is not installed. Each test creates a fresh mock connection
and verifies the backend delegates SQL correctly to it.
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ── Test-time psycopg stub ────────────────────────────────────
# backends_pg.py does `import psycopg` inside __init__ and _get_conn.
# We inject a fake `psycopg` module into sys.modules so the import
# succeeds without the real package being installed.


class FakePsycopg:
    """Minimal psycopg stub for testing."""

    Connection = MagicMock()


@pytest.fixture
def fake_psycopg():
    """Inject a fake psycopg module into sys.modules."""
    fake = types.ModuleType("psycopg")
    fake.connect = MagicMock(return_value=MagicMock())
    sys.modules["psycopg"] = fake
    yield fake
    sys.modules.pop("psycopg", None)


# ── _build_dsn ────────────────────────────────────────────────


def test_build_dsn_uses_maoP_pg_dsn_when_set():
    """When MAOP_PG_DSN is set, it takes priority over individual vars."""
    from maop.core.backends_pg import _build_dsn
    with patch.dict("os.environ", {
        "MAOP_PG_DSN": "postgresql://user:pass@host:5432/db",
        "MAOP_PG_HOST": "ignored",
    }):
        assert _build_dsn() == "postgresql://user:pass@host:5432/db"


def test_build_dsn_assembles_from_individual_vars():
    """Without MAOP_PG_DSN, the DSN is assembled from individual env vars."""
    from maop.core.backends_pg import _build_dsn
    env = {
        "MAOP_PG_DSN": "",
        "MAOP_PG_HOST": "db.example.com",
        "MAOP_PG_PORT": "6543",
        "MAOP_PG_DATABASE": "mydb",
        "MAOP_PG_USER": "myuser",
        "MAOP_PG_PASSWORD": "secret",
    }
    with patch.dict("os.environ", env, clear=False):
        dsn = _build_dsn()
    assert "db.example.com" in dsn
    assert "6543" in dsn
    assert "mydb" in dsn
    assert "myuser" in dsn
    assert "secret" in dsn


def test_build_dsn_defaults_when_no_env():
    """With no env vars set, defaults are used (localhost:5432/maop)."""
    from maop.core.backends_pg import _build_dsn
    env = {
        "MAOP_PG_DSN": "",
        "MAOP_PG_HOST": "",
        "MAOP_PG_PORT": "",
        "MAOP_PG_DATABASE": "",
        "MAOP_PG_USER": "",
        "MAOP_PG_PASSWORD": "",
    }
    # Save and clear; use patch.dict to restore.
    original = {k: os.environ.get(k, "") for k in env}
    try:
        for k in env:
            os.environ.pop(k, None)
        os.environ["MAOP_PG_DSN"] = ""
        dsn = _build_dsn()
    finally:
        for k, v in original.items():
            if v:
                os.environ[k] = v
    assert "localhost" in dsn
    assert "5432" in dsn
    assert "/maop" in dsn


# ── PostgreSQLStorageBackend ──────────────────────────────────


def test_backend_is_storage_backend_subclass(fake_psycopg):
    """PostgreSQLStorageBackend must subclass StorageBackend."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    from maop.core.backends import StorageBackend
    assert issubclass(PostgreSQLStorageBackend, StorageBackend)


def test_backend_init_creates_connection_and_schema(fake_psycopg):
    """__init__ connects and runs _ensure_schema (CREATE TABLE IF NOT EXISTS)."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    mock_conn = MagicMock()
    fake_psycopg.connect.return_value = mock_conn
    PostgreSQLStorageBackend(dsn="postgresql://test@host/db")
    fake_psycopg.connect.assert_called_once_with(
        "postgresql://test@host/db", autocommit=True,
    )
    # _ensure_schema runs CREATE TABLE for maop_kv and maop_meta.
    cursor = mock_conn.cursor.return_value.__enter__.return_value
    assert cursor.execute.call_count >= 2  # at least 2 CREATE TABLE


def test_backend_init_uses_build_dsn_when_no_dsn_arg(fake_psycopg):
    """When dsn="" is passed, _build_dsn() is used."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    with patch("maop.core.backends_pg._build_dsn", return_value="built-dsn"):
        PostgreSQLStorageBackend(dsn="")
    fake_psycopg.connect.assert_called_once_with("built-dsn", autocommit=True)


def test_get_conn_reuses_existing_connection(fake_psycopg):
    """_get_conn returns the existing connection if not closed."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    mock_conn = MagicMock()
    mock_conn.closed = False
    fake_psycopg.connect.return_value = mock_conn
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    # First _get_conn (during __init__) already used connect once.
    initial_call_count = fake_psycopg.connect.call_count
    conn1 = backend._get_conn()
    conn2 = backend._get_conn()
    assert conn1 is conn2
    # No additional connect() calls.
    assert fake_psycopg.connect.call_count == initial_call_count


def test_get_conn_reconnects_when_closed(fake_psycopg):
    """_get_conn opens a new connection when the existing one is closed."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    mock_conn1 = MagicMock()
    mock_conn1.closed = False
    mock_conn2 = MagicMock()
    mock_conn2.closed = False
    fake_psycopg.connect.side_effect = [mock_conn1, mock_conn2]
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    # Simulate the first connection being closed.
    mock_conn1.closed = True
    new_conn = backend._get_conn()
    assert new_conn is mock_conn2
    assert fake_psycopg.connect.call_count == 2


# ── execute / fetchone / fetchall ─────────────────────────────


def test_execute_delegates_to_cursor(fake_psycopg):
    """execute(sql, params) calls cursor.execute with the SQL and params."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = backend._conn.cursor.return_value.__enter__.return_value
    backend.execute("INSERT INTO t VALUES (%s)", ("value",))
    cursor.execute.assert_called_with("INSERT INTO t VALUES (%s)", ("value",))


def test_execute_with_no_params_passes_empty_tuple(fake_psycopg):
    """execute(sql) with no params passes an empty tuple."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = backend._conn.cursor.return_value.__enter__.return_value
    backend.execute("DELETE FROM t")
    cursor.execute.assert_called_with("DELETE FROM t", ())


def test_fetchone_returns_dict_or_none(fake_psycopg):
    """fetchone() returns a dict (column→value) or None."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = backend._conn.cursor.return_value.__enter__.return_value
    cursor.description = [("id",), ("name",)]
    cursor.fetchone.return_value = (1, "alice")
    result = backend.fetchone("SELECT id, name FROM t WHERE id=%s", (1,))
    assert result == {"id": 1, "name": "alice"}


def test_fetchone_returns_none_when_no_row(fake_psycopg):
    """fetchone() returns None when the query yields no rows."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = backend._conn.cursor.return_value.__enter__.return_value
    cursor.description = [("id",)]
    cursor.fetchone.return_value = None
    assert backend.fetchone("SELECT 1 WHERE false") is None


def test_fetchall_returns_list_of_dicts(fake_psycopg):
    """fetchall() returns a list of dicts."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = backend._conn.cursor.return_value.__enter__.return_value
    cursor.description = [("id",), ("name",)]
    cursor.fetchall.return_value = [(1, "a"), (2, "b"), (3, "c")]
    rows = backend.fetchall("SELECT id, name FROM t")
    assert len(rows) == 3
    assert rows[0] == {"id": 1, "name": "a"}
    assert rows[2] == {"id": 3, "name": "c"}


def test_fetchall_returns_empty_list_when_no_rows(fake_psycopg):
    """fetchall() returns [] when the query yields no rows."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = backend._conn.cursor.return_value.__enter__.return_value
    cursor.description = [("id",)]
    cursor.fetchall.return_value = []
    assert backend.fetchall("SELECT 1 WHERE false") == []


# ── commit / rollback / close ─────────────────────────────────


def test_commit_calls_connection_commit(fake_psycopg):
    """commit() calls connection.commit()."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    mock_conn = MagicMock()
    mock_conn.closed = False
    fake_psycopg.connect.return_value = mock_conn
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    # _ensure_schema opened a cursor; reset mock for clean assertion.
    mock_conn.commit.reset_mock()
    backend.commit()
    mock_conn.commit.assert_called_once()


def test_commit_noop_when_connection_closed(fake_psycopg):
    """commit() is a no-op when the connection is closed."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    mock_conn = MagicMock()
    mock_conn.closed = False
    fake_psycopg.connect.return_value = mock_conn
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    mock_conn.closed = True
    mock_conn.commit.reset_mock()
    backend.commit()  # should not raise
    mock_conn.commit.assert_not_called()


def test_rollback_calls_connection_rollback(fake_psycopg):
    """rollback() calls connection.rollback()."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    mock_conn = MagicMock()
    mock_conn.closed = False
    fake_psycopg.connect.return_value = mock_conn
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    mock_conn.rollback.reset_mock()
    backend.rollback()
    mock_conn.rollback.assert_called_once()


def test_rollback_noop_when_connection_closed(fake_psycopg):
    """rollback() is a no-op when the connection is closed."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    mock_conn = MagicMock()
    mock_conn.closed = False
    fake_psycopg.connect.return_value = mock_conn
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    mock_conn.closed = True
    mock_conn.rollback.reset_mock()
    backend.rollback()  # should not raise
    mock_conn.rollback.assert_not_called()


def test_close_closes_and_clears_connection(fake_psycopg):
    """close() closes the connection and sets _conn to None."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    mock_conn = MagicMock()
    mock_conn.closed = False
    fake_psycopg.connect.return_value = mock_conn
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    backend.close()
    mock_conn.close.assert_called_once()
    assert backend._conn is None


def test_close_noop_when_already_closed(fake_psycopg):
    """close() is a no-op when _conn.closed is True (no .close() call, _conn untouched)."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    mock_conn = MagicMock()
    mock_conn.closed = False
    fake_psycopg.connect.return_value = mock_conn
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    mock_conn.close.reset_mock()
    # Simulate the connection being already closed by the server.
    mock_conn.closed = True
    backend.close()  # should not raise, should not call .close()
    mock_conn.close.assert_not_called()
    # Note: close() only sets _conn = None inside the `if not closed` branch,
    # so when the conn is already closed, _conn is left as-is (not None).
    # This is the documented behavior.


# ── table_exists ──────────────────────────────────────────────


def test_table_exists_returns_true_when_found(fake_psycopg):
    """table_exists() returns True when the table is found."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = backend._conn.cursor.return_value.__enter__.return_value
    cursor.description = [("?column?",)]
    cursor.fetchone.return_value = (1,)
    assert backend.table_exists("maop_kv") is True
    cursor.execute.assert_called_with(
        "SELECT 1 FROM information_schema.tables WHERE table_name=%s LIMIT 1",
        ("maop_kv",),
    )


def test_table_exists_returns_false_when_not_found(fake_psycopg):
    """table_exists() returns False when the table is not found."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = backend._conn.cursor.return_value.__enter__.return_value
    cursor.description = [("?column?",)]
    cursor.fetchone.return_value = None
    assert backend.table_exists("nonexistent_table") is False


# ── _ensure_schema ────────────────────────────────────────────


def test_ensure_schema_creates_kv_and_meta_tables(fake_psycopg):
    """_ensure_schema() runs CREATE TABLE for maop_kv and maop_meta."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = backend._conn.cursor.return_value.__enter__.return_value
    # __init__ already called _ensure_schema once; inspect its calls.
    execute_calls = [str(c) for c in cursor.execute.call_args_list]
    # At least 2 CREATE TABLE statements.
    create_count = sum(1 for c in execute_calls if "CREATE TABLE" in c.upper())
    assert create_count >= 2


def test_ensure_schema_uses_if_not_exists(fake_psycopg):
    """_ensure_schema() uses CREATE TABLE IF NOT EXISTS (idempotent)."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = backend._conn.cursor.return_value.__enter__.return_value
    execute_calls = [str(c) for c in cursor.execute.call_args_list]
    create_calls = [c for c in execute_calls if "CREATE TABLE" in c.upper()]
    assert all("IF NOT EXISTS" in c.upper() for c in create_calls)
