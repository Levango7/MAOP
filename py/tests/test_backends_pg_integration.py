"""PostgreSQL integration smoke tests (G5).

These tests verify basic PostgreSQL connectivity and CRUD operations against a
real PG instance. They are marked ``integration`` and excluded from the default
unit-test run via ``-m "not integration"``.

In CI, the ``migrations`` job provisions a ``postgres:16-alpine`` service
container and sets
``DATABASE_URL=postgresql://postgres:test@localhost:5432/maop_test``.

When PostgreSQL is unavailable (e.g. local dev without Docker, or psycopg2 not
installed), every test in this module is skipped so the default suite stays
green — preserving backward compatibility.
"""
from __future__ import annotations

import os

import pytest

# Mark every test in this module as an integration test.
pytestmark = pytest.mark.integration

DEFAULT_DATABASE_URL = "postgresql://postgres:test@localhost:5432/maop_test"


def _get_dsn() -> str:
    """Return the PG connection string from env or the CI default."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def _can_connect() -> bool:
    """Return True iff psycopg2 is importable and a server connection succeeds."""
    try:
        import psycopg2
    except ImportError:
        return False
    try:
        import psycopg2

        with psycopg2.connect(_get_dsn(), connect_timeout=3):
            return True
    except Exception:
        # Any connection error (auth, host unreachable, no server) → skip.
        return False


_HAS_PG = _can_connect()
_skip_if_no_pg = pytest.mark.skipif(
    not _HAS_PG,
    reason="PostgreSQL not available (set DATABASE_URL or run postgres:16-alpine)",
)


# ────────────────────────────────────────────────────────────────────────────
# 1. Connectivity
# ────────────────────────────────────────────────────────────────────────────


@_skip_if_no_pg
def test_pg_connection() -> None:
    """A bare connection to PostgreSQL succeeds and reports a server version."""
    import psycopg2

    with psycopg2.connect(_get_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT version();")
        (version,) = cur.fetchone()
    assert version and "PostgreSQL" in version


# ────────────────────────────────────────────────────────────────────────────
# 2. Create / insert / query round-trip
# ────────────────────────────────────────────────────────────────────────────


@_skip_if_no_pg
def test_pg_create_insert_query() -> None:
    """Create a table, insert rows, and read them back within one transaction.

    Uses a uniquely-named temp table and rolls everything back at the end so the
    shared ``maop_test`` database stays pristine across CI runs.
    """
    import psycopg2

    table = "maop_pg_smoke_test"

    with psycopg2.connect(_get_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
            cur.execute(
                f"CREATE TABLE {table} ("
                "id SERIAL PRIMARY KEY, "
                "name TEXT NOT NULL, "
                "value INTEGER)"
            )
            cur.execute(
                f"INSERT INTO {table} (name, value) VALUES "
                "('alpha', 1), ('beta', 2), ('gamma', 3)"
            )

            cur.execute(f"SELECT name, value FROM {table} ORDER BY value")
            rows = cur.fetchall()

        conn.rollback()  # leave the shared DB pristine

    assert rows == [("alpha", 1), ("beta", 2), ("gamma", 3)]


# ────────────────────────────────────────────────────────────────────────────
# 3. Transaction rollback semantics
# ────────────────────────────────────────────────────────────────────────────


@_skip_if_no_pg
def test_pg_transaction_rollback() -> None:
    """An explicit rollback discards uncommitted DDL+DML."""
    import psycopg2

    table = "maop_pg_smoke_rollback"

    with psycopg2.connect(_get_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
            cur.execute(f"CREATE TABLE {table} (k TEXT PRIMARY KEY, v INTEGER)")
            cur.execute(f"INSERT INTO {table} VALUES ('x', 42)")
        conn.rollback()

        # After rollback the table must not exist.
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
            (exists,) = cur.fetchone()
        assert exists is False