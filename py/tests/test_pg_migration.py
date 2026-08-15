"""Tests for the SQLite → PostgreSQL migration tooling (P1-6).

These tests are mock-only — they do not require a running PostgreSQL
instance. They cover:

* DDL generation: the 001_initial_schema migration emits valid PG syntax
  (BIGSERIAL, JSONB, tsvector, vector) and is idempotent.
* Data copy logic: :mod:`maop.migrations.sqlite_to_pg` transforms rows
  correctly (JSON parsing, column rename, batch boundaries) using mocked
  engines.
* Backend selection: :func:`maop.core.backends.db_utils.get_db_engine`
  honours ``MAOP_DB_BACKEND`` / ``MAOP_DATABASE_URL`` and caches engines.
* CLI wiring: ``maop migrate`` dispatches to the right subcommand.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from typing_extensions import Self

# Ensure the py/ root is on sys.path so `import maop...` works from the
# tests directory regardless of how pytest is invoked.
_PY_ROOT = Path(__file__).resolve().parent.parent
if str(_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(_PY_ROOT))


# ────────────────────────────────────────────────────────────────────────────
# 1. DDL generation (001_initial_schema)
# ────────────────────────────────────────────────────────────────────────────


def _load_initial_schema_module() -> Any:
    """Import the 001_initial_schema migration module."""
    import importlib.util

    here = Path(__file__).resolve()
    mod_path = (
        here.parent.parent
        / "maop"
        / "migrations"
        / "pg"
        / "versions"
        / "001_initial_schema.py"
    )
    spec = importlib.util.spec_from_file_location("maop_migrations_pg_001", mod_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_initial_schema_revision_metadata() -> None:
    """The migration declares the expected revision identifiers."""
    mod = _load_initial_schema_module()
    assert mod.revision == "001_initial_schema"
    assert mod.down_revision is None


def test_initial_schema_uses_bigserial_for_autoincrement() -> None:
    """INTEGER PRIMARY KEY AUTOINCREMENT columns map to BIGSERIAL PRIMARY KEY."""
    mod = _load_initial_schema_module()
    ddl = mod._TABLES_DDL
    # Tables that were INTEGER PRIMARY KEY AUTOINCREMENT in SQLite.
    for table in ("agent_memory", "delegations", "error_log", "metrics",
                  "routing_decisions", "breaker_events", "agent_evolution_history"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl, f"missing {table}"
    # BIGSERIAL must appear (it's the PG translation of AUTOINCREMENT).
    assert "BIGSERIAL PRIMARY KEY" in ddl
    # The original SQLite-ism must NOT appear.
    assert "INTEGER PRIMARY KEY AUTOINCREMENT" not in ddl


def test_initial_schema_uses_jsonb_for_json_columns() -> None:
    """JSON-encoded TEXT columns map to JSONB with sensible defaults."""
    mod = _load_initial_schema_module()
    ddl = mod._TABLES_DDL
    # metadata / payload / tags / roles should be JSONB.
    assert "metadata      JSONB" in ddl or "metadata       JSONB" in ddl or "metadata JSONB" in ddl
    assert "payload       JSONB" in ddl or "payload        JSONB" in ddl or "payload JSONB" in ddl
    assert "roles          JSONB" in ddl
    # The SQLite TEXT default '{}' must not leak through for JSON columns.
    assert "metadata      TEXT" not in ddl


def test_initial_schema_uses_tsvector_for_fts5() -> None:
    """FTS5 virtual tables map to generated tsvector + GIN index."""
    mod = _load_initial_schema_module()
    ddl = mod._TABLES_DDL
    idx = mod._INDEXES_DDL
    # tsvector generated columns on the parent tables.
    assert "tsvector" in ddl
    assert "to_tsvector" in ddl
    assert "GENERATED ALWAYS AS" in ddl
    # GIN index on the tsvector column.
    assert "USING GIN (fts_tsv)" in idx
    # The FTS5 virtual-table syntax must NOT be in the PG DDL.
    assert "USING fts5" not in ddl
    assert "memory_fts" not in ddl.replace("idx_memory_fts_gin", "")
    assert "episodic_memory_fts" not in ddl.replace("idx_episodic_fts_gin", "")


def test_initial_schema_uses_pgvector_for_vec_table() -> None:
    """sqlite-vec virtual table maps to vector(dim) + ivfflat index."""
    mod = _load_initial_schema_module()
    ddl = mod._TABLES_DDL
    idx = mod._INDEXES_DDL
    assert "vector(1536)" in ddl, "pgvector vector type not found"
    assert "ivfflat" in idx, "ivfflat ANN index not found"
    assert "vector_cosine_ops" in idx
    # The sqlite-vec virtual-table name must NOT appear as a CREATE TABLE.
    assert "CREATE TABLE IF NOT EXISTS vec_vectors" not in ddl


def test_initial_schema_is_idempotent() -> None:
    """Every CREATE TABLE/INDEX uses IF NOT EXISTS so re-running is safe."""
    mod = _load_initial_schema_module()
    ddl = mod._TABLES_DDL
    idx = mod._INDEXES_DDL
    # Strip full-line comments so words like "CREATE INDEX" inside a
    # comment don't trip up the idempotency check.
    def _strip_comments(text: str) -> str:
        return "\n".join(
            ln for ln in text.splitlines() if not ln.strip().startswith("--")
        )
    body = _strip_comments(ddl + idx)
    import re

    creates = re.findall(
        r"CREATE\s+(TABLE|INDEX)\s+(IF\s+NOT\s+EXISTS\s+)?\S+", body, re.IGNORECASE,
    )
    for kind, ifne in creates:
        assert ifne, f"CREATE {kind} without IF NOT EXISTS in DDL"


def test_initial_schema_skip_sqlite_internals() -> None:
    """SQLite-internal tables are not created in PG."""
    mod = _load_initial_schema_module()
    ddl = mod._TABLES_DDL
    assert "sqlite_sequence" not in ddl
    # FTS5 shadow tables must not appear.
    for shadow in ("memory_fts_data", "memory_fts_idx", "episodic_memory_fts_config"):
        assert shadow not in ddl


def test_initial_schema_downgrade_drops_in_reverse_order() -> None:
    """The downgrade list contains all tables and is non-empty."""
    mod = _load_initial_schema_module()
    drop_order = mod._DROP_ORDER
    assert len(drop_order) > 30, "drop order should include all MAOP tables"
    # prompt_versions depends on prompt_templates → must be dropped first.
    assert drop_order.index("prompt_versions") < drop_order.index("prompt_templates")
    # No duplicates.
    assert len(drop_order) == len(set(drop_order))


def test_initial_schema_upgrade_requires_pg_dialect() -> None:
    """upgrade() raises if the bind dialect is not PostgreSQL."""
    mod = _load_initial_schema_module()

    # Fake bind that reports a non-PG dialect.
    class FakeDialect:
        name = "sqlite"

    class FakeBind:
        dialect = FakeDialect()

        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            pytest.fail("execute should not be called on non-PG dialect")

    with mock.patch.object(mod.op, "get_bind", return_value=FakeBind()):  # noqa: SIM117
        with pytest.raises(RuntimeError, match="PostgreSQL-only"):
            mod.upgrade()


def test_initial_schema_downgrade_guard_dev_env() -> None:
    """downgrade() proceeds in dev/test environments without the override flag."""
    mod = _load_initial_schema_module()

    # Alembic's op.get_bind() is a proxy that only resolves inside an actual
    # migration context. Patch both get_bind and execute so the downgrade
    # logic (which calls _exec_block → op.get_bind and op.execute) can run
    # in isolation.
    fake_bind = mock.MagicMock(name="bind")
    with mock.patch.dict(os.environ, {"MAOP_ENV": "test"}), \
         mock.patch.object(mod.op, "get_bind", return_value=fake_bind), \
         mock.patch.object(mod.op, "execute") as mock_exec:
        mod.downgrade()
    assert mock_exec.call_count > 0, "downgrade should drop tables in test env"


def test_initial_schema_downgrade_guard_prod_env() -> None:
    """downgrade() refuses in production unless explicitly overridden."""
    mod = _load_initial_schema_module()
    # Strip MAOP_ENV and the override flag to simulate prod.
    env = {k: v for k, v in os.environ.items()
           if k not in ("MAOP_ENV", "MAOP_ALLOW_DESTRUCTIVE_DOWNGRADE")}
    with mock.patch.dict(os.environ, env, clear=True):  # noqa: SIM117
        with pytest.raises(RuntimeError, match="SAFETY"):
            mod.downgrade()


# ────────────────────────────────────────────────────────────────────────────
# 2. Data copy logic (sqlite_to_pg)
# ────────────────────────────────────────────────────────────────────────────


def test_parse_json_handles_dict_and_str() -> None:
    from maop.migrations.sqlite_to_pg import _parse_json

    assert _parse_json(None) is None
    assert _parse_json({"a": 1}) == {"a": 1}
    assert _parse_json([1, 2, 3]) == [1, 2, 3]
    assert _parse_json('{"x": 1}') == {"x": 1}
    assert _parse_json("[1, 2, 3]") == [1, 2, 3]
    # Invalid JSON falls back to the raw string.
    assert _parse_json("not json") == "not json"
    # Bytes are decoded then parsed.
    assert _parse_json(b'{"y": 2}') == {"y": 2}


def test_transform_row_parses_json_columns() -> None:
    from maop.migrations.sqlite_to_pg import _transform_row

    columns = ["id", "metadata", "content"]
    row = ("abc", '{"k": "v"}', "plain text")
    out = _transform_row("messages", columns, row)
    assert out == {"id": "abc", "metadata": {"k": "v"}, "content": "plain text"}


def test_transform_row_leaves_non_json_columns_alone() -> None:
    from maop.migrations.sqlite_to_pg import _transform_row

    # agent_performance has no JSON columns in the map.
    out = _transform_row(
        "agent_performance",
        ["id", "agent", "cost_usd"],
        ("perf1", "coder", 0.5),
    )
    assert out == {"id": "perf1", "agent": "coder", "cost_usd": 0.5}


def test_transform_row_applies_column_renames() -> None:
    """If a column rename is configured, the output key uses the PG name."""
    from maop.migrations import sqlite_to_pg

    with mock.patch.dict(
        sqlite_to_pg._COLUMN_RENAMES,
        {"sessions": {"created_at": "created_ts"}},
    ):
        out = sqlite_to_pg._transform_row(
            "sessions", ["id", "created_at"], ("s1", "2026-01-01"),
        )
    assert out == {"id": "s1", "created_ts": "2026-01-01"}


def test_skip_tables_excludes_fts5_and_vec() -> None:
    from maop.migrations.sqlite_to_pg import _SKIP_TABLES

    assert "sqlite_sequence" in _SKIP_TABLES
    assert "memory_fts" in _SKIP_TABLES
    assert "memory_fts_data" in _SKIP_TABLES
    assert "episodic_memory_fts" in _SKIP_TABLES
    assert "vec_vectors" in _SKIP_TABLES


def test_copy_order_puts_parents_before_children() -> None:
    from maop.migrations.sqlite_to_pg import _COPY_ORDER

    # prompt_versions depends on prompt_templates.
    assert _COPY_ORDER.index("prompt_templates") < _COPY_ORDER.index("prompt_versions")
    # users before sessions (sessions may reference user conceptually).
    assert _COPY_ORDER.index("users") < _COPY_ORDER.index("sessions")
    # sessions before messages.
    assert _COPY_ORDER.index("sessions") < _COPY_ORDER.index("messages")


def test_ordered_tables_appends_extras_sorted() -> None:
    from maop.migrations.sqlite_to_pg import _ordered_tables

    available = ["messages", "users", "zzz_extra", "sessions", "aaa_extra"]
    ordered = _ordered_tables(available)
    # Known tables appear in _COPY_ORDER order.
    assert ordered[:3] == ["users", "sessions", "messages"]
    # Extras are appended sorted alphabetically.
    assert ordered[3:] == ["aaa_extra", "zzz_extra"]


def test_json_columns_covers_known_json_tables() -> None:
    from maop.migrations.sqlite_to_pg import _JSON_COLUMNS

    # Spot-check a few tables that the SQLite schema stores as JSON TEXT.
    assert "metadata" in _JSON_COLUMNS["messages"]
    assert "tags" in _JSON_COLUMNS["sessions"]
    assert "roles" in _JSON_COLUMNS["users"]
    assert "payload" in _JSON_COLUMNS["queue_messages"]
    assert "vector" in _JSON_COLUMNS["vector_entries"]
    assert "capabilities" in _JSON_COLUMNS["registered_agents"]


def _make_fake_engine(table_columns: dict[str, list[str]], table_rows: dict[str, list[tuple]] | None = None) -> mock.MagicMock:
    """Build a fake SQLAlchemy engine with introspection + execution stubs."""
    table_rows = table_rows or {}
    engine = mock.MagicMock(name="engine")

    # inspect(engine).get_table_names() / get_columns(table)
    inspector = mock.MagicMock(name="inspector")
    inspector.get_table_names.return_value = list(table_columns.keys())

    def _get_columns(table: str) -> list[dict[str, str]]:
        return [{"name": c} for c in table_columns[table]]

    inspector.get_columns.side_effect = _get_columns
    inspector.has_table.side_effect = lambda t: t in table_columns

    with mock.patch("maop.migrations.sqlite_to_pg.inspect", return_value=inspector):
        engine._inspector = inspector  # keep a reference for test asserts

    # engine.connect() → context manager yielding a fake connection.
    class FakeConn:
        def execute(self, stmt: Any, params: Any | None = None) -> Any:
            stmt_str = str(stmt)
            if "COUNT(*)" in stmt_str:
                table = stmt_str.split("FROM")[1].strip().strip('"')
                return mock.MagicMock(scalar=mock.MagicMock(return_value=len(table_rows.get(table, []))))
            if "SELECT" in stmt_str and "LIMIT" in stmt_str:
                table = stmt_str.split("FROM")[1].split("LIMIT")[0].strip().strip('"')
                lim = params["lim"] if params else 1000
                off = params["off"] if params else 0
                rows = table_rows.get(table, [])[off:off + lim]
                return mock.MagicMock(fetchall=mock.MagicMock(return_value=rows))
            return mock.MagicMock()

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    class FakeBeginConn(FakeConn):
        """Connection returned by engine.begin() — collects executed statements."""

        def __init__(self) -> None:
            super().__init__()
            self.executed: list[tuple[Any, Any]] = []

        def execute(self, stmt: Any, params: Any | None = None) -> Any:
            self.executed.append((stmt, params))
            return mock.MagicMock()

    fake_begin_conn = FakeBeginConn()

    engine.connect.return_value = FakeConn()
    engine.begin.return_value = fake_begin_conn
    engine._begin_conn = fake_begin_conn  # expose for assertions
    return engine


def test_copy_table_dry_run_counts_rows() -> None:
    """In dry-run mode, copy_table returns the row count without inserting."""
    from maop.migrations.sqlite_to_pg import copy_table

    sqlite_engine = _make_fake_engine(
        {"users": ["username", "roles"]},
        {"users": [("alice", '["admin"]'), ("bob", '["user"]')]},
    )
    pg_engine = _make_fake_engine({"users": ["username", "roles"]})

    with mock.patch("maop.migrations.sqlite_to_pg.inspect") as insp:
        insp.return_value = sqlite_engine._inspector
        # Patch the second call (pg) — inspect is called per engine.
        insp.side_effect = [sqlite_engine._inspector, pg_engine._inspector,
                            sqlite_engine._inspector, pg_engine._inspector]
        n = copy_table(sqlite_engine, pg_engine, "users", dry_run=True, progress=False)
    assert n == 2


def test_copy_table_inserts_transformed_rows() -> None:
    """In live mode, copy_table INSERTs JSON-parsed rows into PG."""
    from maop.migrations.sqlite_to_pg import copy_table

    sqlite_engine = _make_fake_engine(
        {"messages": ["id", "metadata"]},
        {"messages": [("m1", '{"k": 1}'), ("m2", '{"k": 2}')]},
    )
    pg_engine = _make_fake_engine({"messages": ["id", "metadata"]})

    with mock.patch("maop.migrations.sqlite_to_pg.inspect") as insp:
        insp.side_effect = [
            sqlite_engine._inspector, pg_engine._inspector,  # _columns x2
            sqlite_engine._inspector,  # _count_rows
            sqlite_engine._inspector,  # _iter_batches _columns
            pg_engine._inspector,  # _insert_batch _columns
        ]
        n = copy_table(sqlite_engine, pg_engine, "messages", batch_size=10, progress=False)

    assert n == 2
    # The PG begin-connection should have received one execute() with the
    # INSERT statement and a list of 2 re-keyed rows.
    executed = pg_engine._begin_conn.executed
    assert len(executed) >= 1
    stmt, params = executed[-1]
    assert "INSERT INTO" in str(stmt)
    assert isinstance(params, list) and len(params) == 2
    # Each row should have metadata as a dict (parsed from JSON).
    assert params[0]["metadata"] == {"k": 1}
    assert params[1]["metadata"] == {"k": 2}


def test_copy_table_skips_missing_pg_table() -> None:
    """If the PG table doesn't exist, copy_table returns 0 and logs a warning."""
    from maop.migrations.sqlite_to_pg import copy_table

    sqlite_engine = _make_fake_engine({"ghost": ["id"]}, {"ghost": [("a",)]})
    pg_engine = _make_fake_engine({})  # no tables on PG side

    with mock.patch("maop.migrations.sqlite_to_pg.inspect") as insp:
        insp.side_effect = [pg_engine._inspector]  # _pg_has_table
        n = copy_table(sqlite_engine, pg_engine, "ghost", progress=False)
    assert n == 0


def test_migrate_filters_to_requested_tables() -> None:
    """migrate(tables=[...]) only copies the requested tables."""
    from maop.migrations.sqlite_to_pg import migrate

    sqlite_engine = _make_fake_engine(
        {"users": ["username"], "sessions": ["id"], "messages": ["id"]},
        {"users": [("a",)], "sessions": [("s1",)], "messages": [("m1",)]},
    )
    pg_engine = _make_fake_engine(
        {"users": ["username"], "sessions": ["id"], "messages": ["id"]},
    )

    with mock.patch("maop.migrations.sqlite_to_pg.get_sqlite_engine", return_value=sqlite_engine), \
         mock.patch("maop.migrations.sqlite_to_pg.get_pg_engine", return_value=pg_engine), \
         mock.patch("maop.migrations.sqlite_to_pg.inspect") as insp:
        insp.return_value = sqlite_engine._inspector
        insp.side_effect = None
        # Make inspect() return the right inspector based on the engine arg.
        def _insp_factory(engine: Any) -> Any:
            return engine._inspector
        insp.side_effect = _insp_factory
        results = migrate(tables=["users"], dry_run=True, progress=False)

    assert set(results.keys()) == {"users"}
    assert results["users"] == 1


# ────────────────────────────────────────────────────────────────────────────
# 3. Backend selection (db_utils.get_db_engine)
# ────────────────────────────────────────────────────────────────────────────


def test_get_db_engine_defaults_to_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without any env vars, get_db_engine() returns a SQLite engine."""
    from maop.core.backends import db_utils

    monkeypatch.delenv("MAOP_DB_BACKEND", raising=False)
    monkeypatch.delenv("MAOP_DATABASE_URL", raising=False)
    monkeypatch.delenv("MAOP_DB_URL", raising=False)
    db_utils.reset_db_engine_cache()

    engine = db_utils.get_db_engine(cache=False)
    assert engine.dialect.name == "sqlite"


def test_get_db_engine_selects_pg_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAOP_DB_BACKEND=postgresql produces a PG dialect engine."""
    from maop.core.backends import db_utils

    monkeypatch.setenv("MAOP_DB_BACKEND", "postgresql")
    monkeypatch.setenv("MAOP_DATABASE_URL", "postgresql+psycopg2://x:y@localhost:5432/maop")
    db_utils.reset_db_engine_cache()

    # Mock create_engine so the test doesn't require psycopg2 to be installed.
    # We assert the URL/dialect that would be used, not a real connection.
    captured: dict[str, Any] = {}

    def fake_create_engine(url: str, **kwargs: Any) -> mock.MagicMock:
        captured["url"] = url
        captured["kwargs"] = kwargs
        engine = mock.MagicMock(name="pg_engine")
        engine.url = mock.MagicMock(__str__=lambda self: url)
        # Simulate PG dialect.
        engine.dialect = mock.MagicMock(name="pg_dialect")
        engine.dialect.name = "postgresql"
        return engine

    with mock.patch("sqlalchemy.create_engine", side_effect=fake_create_engine):
        engine = db_utils.get_db_engine(cache=False)
    assert engine.dialect.name == "postgresql"
    assert "postgresql+psycopg2" in captured["url"]


def test_get_db_engine_normalises_pg_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    """'postgres' and 'pg' are normalised to 'postgresql'."""
    from maop.core.backends import db_utils

    monkeypatch.setenv("MAOP_DATABASE_URL", "postgresql+psycopg2://x:y@localhost:5432/maop")
    db_utils.reset_db_engine_cache()

    def fake_create_engine(url: str, **kwargs: Any) -> mock.MagicMock:
        engine = mock.MagicMock(name="pg_engine")
        engine.url = mock.MagicMock(__str__=lambda self: url)
        engine.dialect = mock.MagicMock(name="pg_dialect")
        engine.dialect.name = "postgresql"
        return engine

    with mock.patch("sqlalchemy.create_engine", side_effect=fake_create_engine):
        for alias in ("postgres", "pg", "postgresql"):
            engine = db_utils.get_db_engine(backend=alias, cache=False)
            assert engine.dialect.name == "postgresql"


def test_get_db_engine_rejects_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown backend name raises ValueError."""
    from maop.core.backends import db_utils

    monkeypatch.delenv("MAOP_DB_BACKEND", raising=False)
    with pytest.raises(ValueError, match="Unsupported"):
        db_utils.get_db_engine(backend="mysql")


def test_get_db_engine_caches_by_backend_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated calls with the same (backend, url) return the same engine."""
    from maop.core.backends import db_utils

    monkeypatch.delenv("MAOP_DB_BACKEND", raising=False)
    monkeypatch.delenv("MAOP_DATABASE_URL", raising=False)
    monkeypatch.delenv("MAOP_DB_URL", raising=False)
    db_utils.reset_db_engine_cache()

    e1 = db_utils.get_db_engine(backend="sqlite", cache=True)
    e2 = db_utils.get_db_engine(backend="sqlite", cache=True)
    assert e1 is e2

    # cache=False bypasses the cache.
    e3 = db_utils.get_db_engine(backend="sqlite", cache=False)
    assert e3 is not e1


def test_get_db_backend_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from maop.core.backends import db_utils

    monkeypatch.setenv("MAOP_DB_BACKEND", "postgresql")
    assert db_utils.get_db_backend() == "postgresql"

    monkeypatch.setenv("MAOP_DB_BACKEND", "sqlite")
    assert db_utils.get_db_backend() == "sqlite"


def test_get_db_engine_url_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit url arg > MAOP_DATABASE_URL > MAOP_DB_URL > default."""
    from maop.core.backends import db_utils

    monkeypatch.setenv("MAOP_DATABASE_URL", "postgresql+psycopg2://from-env/maop")
    monkeypatch.setenv("MAOP_DB_URL", "postgresql+psycopg2://from-legacy/maop")
    db_utils.reset_db_engine_cache()

    captured: list[str] = []

    def fake_create_engine(url: str, **kwargs: Any) -> mock.MagicMock:
        captured.append(url)
        engine = mock.MagicMock(name="pg_engine")
        engine.url = mock.MagicMock(__str__=lambda self: url)
        engine.dialect = mock.MagicMock(name="pg_dialect")
        engine.dialect.name = "postgresql"
        return engine

    with mock.patch("sqlalchemy.create_engine", side_effect=fake_create_engine):
        # Explicit arg wins.
        engine = db_utils.get_db_engine(
            backend="postgresql", url="postgresql+psycopg2://explicit/maop", cache=False,
        )
        assert "explicit" in str(engine.url)

        # MAOP_DATABASE_URL wins over MAOP_DB_URL.
        engine = db_utils.get_db_engine(backend="postgresql", cache=False)
        assert "from-env" in str(engine.url)


# ────────────────────────────────────────────────────────────────────────────
# 4. CLI wiring (maop.cli migrate)
# ────────────────────────────────────────────────────────────────────────────


def test_cli_migrate_no_args_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    """`maop migrate` with no subcommand prints usage and exits 1."""
    from maop.cli import cmd_migrate

    with pytest.raises(SystemExit) as exc:
        cmd_migrate([])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "pg-init" in captured.err
    assert "sqlite-to-pg" in captured.err
    assert "status" in captured.err


def test_cli_migrate_unknown_subcommand_exits(capsys: pytest.CaptureFixture[str]) -> None:
    from maop.cli import cmd_migrate

    with pytest.raises(SystemExit) as exc:
        cmd_migrate(["bogus"])
    assert exc.value.code == 1


def test_cli_migrate_pg_init_invokes_alembic(monkeypatch: pytest.MonkeyPatch) -> None:
    """pg-init shells out to `alembic upgrade head` against the PG ini."""
    from maop import cli

    called: list[list[str]] = []

    def fake_call(cmd: list[str], *_args: Any, **_kwargs: Any) -> int:
        called.append(cmd)
        return 0

    # subprocess is imported lazily inside _run_alembic, so patch the
    # stdlib module's call attribute directly.
    import subprocess
    monkeypatch.setattr(subprocess, "call", fake_call)
    monkeypatch.delenv("MAOP_DATABASE_URL", raising=False)
    monkeypatch.delenv("MAOP_DB_URL", raising=False)

    cli.cmd_migrate_pg_init()

    assert len(called) == 1
    assert called[0][0] == "alembic"
    assert "upgrade" in called[0]
    assert "head" in called[0]
    # The PG alembic.ini path must be passed via -c.
    assert any("migrations" in str(c) and "pg" in str(c) for c in called[0])


def test_cli_migrate_sqlite_to_pg_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """sqlite-to-pg calls maop.migrations.sqlite_to_pg.migrate with parsed args."""
    from maop import cli

    captured: dict[str, Any] = {}

    def fake_migrate(**kwargs: Any) -> dict[str, int]:
        captured.update(kwargs)
        return {"users": 5}

    monkeypatch.setattr("maop.migrations.sqlite_to_pg.migrate", fake_migrate)
    cli.cmd_migrate_sqlite_to_pg(["--dry-run", "--tables", "users,sessions"])

    assert captured["dry_run"] is True
    assert captured["tables"] == ["users", "sessions"]


def test_cli_main_dispatches_migrate(monkeypatch: pytest.MonkeyPatch) -> None:
    """`maop migrate ...` is dispatched before the flat top-level parser."""
    from maop import cli

    seen: list[str] = []

    def fake_cmd_migrate(args: Sequence[str]) -> None:
        seen.extend(args)

    monkeypatch.setattr(cli, "cmd_migrate", fake_cmd_migrate)
    monkeypatch.setattr(sys, "argv", ["maop", "migrate", "status"])

    cli.main()
    assert seen == ["status"]


# ────────────────────────────────────────────────────────────────────────────
# 5. Alembic env.py configuration
# ────────────────────────────────────────────────────────────────────────────


def test_pg_alembic_ini_exists() -> None:
    """The PG alembic.ini file exists at the documented location."""
    here = Path(__file__).resolve()
    ini = here.parent.parent / "maop" / "migrations" / "pg" / "alembic.ini"
    assert ini.exists()
    text = ini.read_text(encoding="utf-8")
    assert "script_location = py/maop/migrations/pg" in text
    assert "postgresql" in text


def test_pg_env_py_uses_maop_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """env.py prefers MAOP_DATABASE_URL over MAOP_DB_URL over the default."""
    env_path = _PY_ROOT / "maop" / "migrations" / "pg" / "env.py"
    assert env_path.exists()
    src = env_path.read_text(encoding="utf-8")
    assert "MAOP_DATABASE_URL" in src
    assert "MAOP_DB_URL" in src
    # The default must be a PG URL, not SQLite.
    assert "postgresql+psycopg2://localhost:5432/maop" in src


def test_pg_env_py_enables_extensions() -> None:
    """env.py attempts to enable pgvector and pg_trgm extensions."""
    env_path = _PY_ROOT / "maop" / "migrations" / "pg" / "env.py"
    src = env_path.read_text(encoding="utf-8")
    assert "CREATE EXTENSION" in src
    assert "vector" in src
    assert "pg_trgm" in src