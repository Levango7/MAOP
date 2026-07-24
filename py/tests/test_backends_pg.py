"""Tests for maop.core.backends_pg.PostgreSQLStorageBackend.

Verifies the PostgreSQL storage backend behavior without a real PostgreSQL
connection. Both psycopg and psycopg_pool are mocked entirely, so these
tests run even when neither package is installed.

The fake_psycopg fixture injects stub ``psycopg`` and ``psycopg_pool``
modules into sys.modules. Each test creates a fresh mock pool and verifies
the backend delegates SQL correctly through ``pool.connection()`` cursors.
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ── Test-time psycopg + psycopg_pool stubs ───────────────────
# backends_pg.py does `import psycopg` and `from psycopg_pool import
# ConnectionPool` inside __init__. We inject fake modules into sys.modules
# so the imports succeed without the real packages being installed.


@pytest.fixture
def fake_psycopg():
    """Inject fake psycopg + psycopg_pool modules into sys.modules.

    Yields the fake psycopg module. The mock pool instance is attached as
    ``fake._pool_mock`` and the fake psycopg_pool module as
    ``fake._pool_mod`` so tests can assert on ConnectionPool construction
    and pool behavior.
    """
    fake = types.ModuleType("psycopg")
    fake.connect = MagicMock(return_value=MagicMock())
    sys.modules["psycopg"] = fake

    fake_pool_mod = types.ModuleType("psycopg_pool")
    mock_pool = MagicMock()
    fake_pool_mod.ConnectionPool = MagicMock(return_value=mock_pool)
    sys.modules["psycopg_pool"] = fake_pool_mod

    # Attach for easy test access.
    fake._pool_mod = fake_pool_mod
    fake._pool_mock = mock_pool

    yield fake

    sys.modules.pop("psycopg", None)
    sys.modules.pop("psycopg_pool", None)


def _pool_cursor(backend):
    """Return the mock cursor used by the backend's pool."""
    conn = backend._pool.connection.return_value.__enter__.return_value
    return conn.cursor.return_value.__enter__.return_value


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


def test_backend_init_creates_pool_and_schema(fake_psycopg):
    """__init__ creates a ConnectionPool and runs _ensure_schema (CREATE TABLE)."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="postgresql://test@host/db")
    fake_psycopg._pool_mod.ConnectionPool.assert_called_once()
    call_kwargs = fake_psycopg._pool_mod.ConnectionPool.call_args.kwargs
    assert call_kwargs["conninfo"] == "postgresql://test@host/db"
    assert call_kwargs["min_size"] == 1
    assert call_kwargs["max_size"] == 10
    assert call_kwargs["kwargs"] == {"autocommit": True}
    # _ensure_schema runs CREATE TABLE for maop_kv and maop_meta.
    cursor = _pool_cursor(backend)
    assert cursor.execute.call_count >= 2  # at least 2 CREATE TABLE


def test_backend_init_uses_build_dsn_when_no_dsn_arg(fake_psycopg):
    """When dsn="" is passed, _build_dsn() is used as the pool conninfo."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    with patch("maop.core.backends_pg._build_dsn", return_value="built-dsn"):
        PostgreSQLStorageBackend(dsn="")
    fake_psycopg._pool_mod.ConnectionPool.assert_called_once()
    call_kwargs = fake_psycopg._pool_mod.ConnectionPool.call_args.kwargs
    assert call_kwargs["conninfo"] == "built-dsn"


# ── execute / fetchone / fetchall ─────────────────────────────


def test_execute_delegates_to_cursor(fake_psycopg):
    """execute(sql, params) calls cursor.execute with the SQL and params."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = _pool_cursor(backend)
    backend.execute("INSERT INTO t VALUES (%s)", ("value",))
    cursor.execute.assert_called_with("INSERT INTO t VALUES (%s)", ("value",))


def test_execute_with_no_params_passes_empty_tuple(fake_psycopg):
    """execute(sql) with no params passes an empty tuple."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = _pool_cursor(backend)
    backend.execute("DELETE FROM t")
    cursor.execute.assert_called_with("DELETE FROM t", ())


def test_fetchone_returns_dict_or_none(fake_psycopg):
    """fetchone() returns a dict (column→value) or None."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = _pool_cursor(backend)
    cursor.description = [("id",), ("name",)]
    cursor.fetchone.return_value = (1, "alice")
    result = backend.fetchone("SELECT id, name FROM t WHERE id=%s", (1,))
    assert result == {"id": 1, "name": "alice"}


def test_fetchone_returns_none_when_no_row(fake_psycopg):
    """fetchone() returns None when the query yields no rows."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = _pool_cursor(backend)
    cursor.description = [("id",)]
    cursor.fetchone.return_value = None
    assert backend.fetchone("SELECT 1 WHERE false") is None


def test_fetchall_returns_list_of_dicts(fake_psycopg):
    """fetchall() returns a list of dicts."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = _pool_cursor(backend)
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
    cursor = _pool_cursor(backend)
    cursor.description = [("id",)]
    cursor.fetchall.return_value = []
    assert backend.fetchall("SELECT 1 WHERE false") == []


# ── commit / rollback (no-ops with pool + autocommit) ─────────


def test_commit_is_noop_with_pool(fake_psycopg):
    """commit() is a no-op with pool + autocommit (each execute auto-commits)."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    # Should not raise and should not touch the pool.
    backend._pool.commit = MagicMock()
    backend.commit()
    backend._pool.commit.assert_not_called()


def test_rollback_is_noop_with_pool(fake_psycopg):
    """rollback() is a no-op with pool + autocommit."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    backend._pool.rollback = MagicMock()
    backend.rollback()
    backend._pool.rollback.assert_not_called()


# ── close ─────────────────────────────────────────────────────


def test_close_closes_pool_and_clears_reference(fake_psycopg):
    """close() closes the pool and sets _pool to None."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    pool_ref = backend._pool
    backend.close()
    pool_ref.close.assert_called_once()
    assert backend._pool is None


def test_close_noop_when_already_closed(fake_psycopg):
    """close() is a no-op when _pool is already None."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    pool_ref = backend._pool
    backend.close()
    pool_ref.close.reset_mock()
    backend.close()  # second call should be a no-op
    pool_ref.close.assert_not_called()
    assert backend._pool is None


# ── table_exists ──────────────────────────────────────────────


def test_table_exists_returns_true_when_found(fake_psycopg):
    """table_exists() returns True when the table is found."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = _pool_cursor(backend)
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
    cursor = _pool_cursor(backend)
    cursor.description = [("?column?",)]
    cursor.fetchone.return_value = None
    assert backend.table_exists("nonexistent_table") is False


# ── _ensure_schema ────────────────────────────────────────────


def test_ensure_schema_creates_kv_and_meta_tables(fake_psycopg):
    """_ensure_schema() runs CREATE TABLE for maop_kv and maop_meta."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = _pool_cursor(backend)
    # __init__ already called _ensure_schema once; inspect its calls.
    execute_calls = [str(c) for c in cursor.execute.call_args_list]
    # At least 2 CREATE TABLE statements.
    create_count = sum(1 for c in execute_calls if "CREATE TABLE" in c.upper())
    assert create_count >= 2


def test_ensure_schema_uses_if_not_exists(fake_psycopg):
    """_ensure_schema() uses CREATE TABLE IF NOT EXISTS (idempotent)."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    cursor = _pool_cursor(backend)
    execute_calls = [str(c) for c in cursor.execute.call_args_list]
    create_calls = [c for c in execute_calls if "CREATE TABLE" in c.upper()]
    assert all("IF NOT EXISTS" in c.upper() for c in create_calls)


# ── Pool-specific behavior (3.1.6) ────────────────────────────


def test_pool_initialization(fake_psycopg):
    """ConnectionPool is created with correct min/max size and autocommit kwargs."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    assert backend._pool is not None
    assert backend._pool is fake_psycopg._pool_mock
    fake_psycopg._pool_mod.ConnectionPool.assert_called_once_with(
        conninfo="test-dsn",
        min_size=1,
        max_size=10,
        kwargs={"autocommit": True},
    )


def test_pool_connection_acquired(fake_psycopg):
    """execute() acquires a connection from the pool via pool.connection()."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    # Reset mock to clear calls from __init__/_ensure_schema.
    backend._pool.connection.reset_mock()
    backend.execute("SELECT 1")
    backend._pool.connection.assert_called_once()


def test_pool_closed_on_close(fake_psycopg):
    """close() closes the connection pool (not just a single connection)."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    pool_ref = backend._pool
    backend.close()
    pool_ref.close.assert_called_once()
    assert backend._pool is None


def test_pool_reused_across_calls(fake_psycopg):
    """Multiple execute/fetchone/fetchall calls reuse the same pool instance."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    backend = PostgreSQLStorageBackend(dsn="test-dsn")
    # __init__ already called ConnectionPool once.
    initial_call_count = fake_psycopg._pool_mod.ConnectionPool.call_count
    pool_ref = backend._pool
    backend.execute("SELECT 1")
    backend.execute("SELECT 2")
    backend.fetchone("SELECT 3")
    backend.fetchall("SELECT 4")
    # No additional ConnectionPool constructor calls.
    assert fake_psycopg._pool_mod.ConnectionPool.call_count == initial_call_count
    # Same pool instance throughout.
    assert backend._pool is pool_ref


def test_pg_backend_with_dsn_env(fake_psycopg):
    """MAOP_PG_DSN environment variable takes priority for DSN construction."""
    from maop.core.backends_pg import PostgreSQLStorageBackend
    env_dsn = "postgresql://envuser:envpass@envhost:5432/envdb"
    with patch.dict("os.environ", {"MAOP_PG_DSN": env_dsn}):
        backend = PostgreSQLStorageBackend(dsn="")
    call_kwargs = fake_psycopg._pool_mod.ConnectionPool.call_args.kwargs
    assert call_kwargs["conninfo"] == env_dsn


def test_pg_backend_without_psycopg_degrades():
    """When psycopg/psycopg_pool is not installed, ImportError propagates so the
    caller (backends.py get_storage_backend / pg_persist._get_pg_backend) can
    catch it and degrade to SQLite or in-memory storage."""
    # Save and remove from sys.modules so import is re-attempted.
    saved_pg = sys.modules.pop("psycopg", None)
    saved_pool = sys.modules.pop("psycopg_pool", None)

    import builtins
    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name in ("psycopg", "psycopg_pool"):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    try:
        builtins.__import__ = failing_import
        from maop.core.backends_pg import PostgreSQLStorageBackend
        with pytest.raises(ImportError):
            PostgreSQLStorageBackend(dsn="test-dsn")
    finally:
        builtins.__import__ = real_import
        if saved_pg is not None:
            sys.modules["psycopg"] = saved_pg
        if saved_pool is not None:
            sys.modules["psycopg_pool"] = saved_pool
