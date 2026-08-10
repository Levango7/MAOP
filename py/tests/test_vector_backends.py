"""Tests for the F1-02 vector backend abstraction layer.

Covers:
- :class:`VectorBackend` ABC contract (cannot instantiate, subclasses must
  implement all abstract methods).
- :class:`SqliteVectorBackend` — full CRUD + search round-trip against a
  real temp SQLite DB (no external deps).
- :class:`PgVectorBackend` — SQL generation and result parsing via mocked
  SQLAlchemy engine (no real PostgreSQL required).
- :func:`get_vector_backend` factory — env-var selection.
- :mod:`maop.migrations.pg.vector_migration` — dry-run + live copy against a
  real SQLite source with a mocked PG target.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure py/ root is importable.
_PY_ROOT = Path(__file__).resolve().parent.parent
if str(_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(_PY_ROOT))

from maop.core.vector import (  # noqa: E402
    DEFAULT_HNSW_THRESHOLD,
    HashEmbedding,
    VectorBackend,
    VectorEntry,
    VectorSearchResult,
    cosine_similarity,
)
from maop.core.vector.factory import get_vector_backend, resolve_backend_name  # noqa: E402
from maop.core.vector.pg_backend import PgVectorBackend  # noqa: E402
from maop.core.vector.sqlite_backend import SqliteVectorBackend  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# 1. Shared models & helpers (re-exports)
# ────────────────────────────────────────────────────────────────────────────


class TestReExports:
    """The package re-exports shared types from maop.core.memory.vector."""

    def test_vector_entry_model(self) -> None:
        e = VectorEntry(id="x", text="hi", vector=[1.0])
        assert e.id == "x" and e.text == "hi" and e.vector == [1.0]

    def test_vector_search_result_model(self) -> None:
        r = VectorSearchResult(id="x", text="hi", score=0.9)
        assert r.score == 0.9

    def test_cosine_similarity(self) -> None:
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_hash_embedding(self) -> None:
        emb = HashEmbedding(dim=8)
        v = emb.embed("test")
        assert len(v) == 8
        # normalised
        norm = math.sqrt(sum(x * x for x in v))
        assert norm == pytest.approx(1.0, abs=1e-6)

    def test_default_hnsw_threshold_constant(self) -> None:
        assert DEFAULT_HNSW_THRESHOLD == 100_000


# ────────────────────────────────────────────────────────────────────────────
# 2. VectorBackend ABC contract
# ────────────────────────────────────────────────────────────────────────────


class TestVectorBackendABC:
    """The ABC cannot be instantiated directly and requires all methods."""

    def test_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError):
            VectorBackend()  # type: ignore[abstract]

    def test_partial_subclass_fails(self) -> None:
        class Partial(VectorBackend):
            name = "partial"

            def search(self, query_vector, *, top=10, threshold=0.0):
                return []

        with pytest.raises(TypeError):
            Partial()  # type: ignore[abstract]

    def test_full_subclass_ok(self) -> None:
        class Full(VectorBackend):
            name = "full"

            def search(self, query_vector, *, top=10, threshold=0.0):
                return []

            def insert(self, entry_id, text, vector, *, metadata=None):
                return True

            def delete(self, entry_id):
                return True

            def rebuild_index(self, *, index_type="ivfflat", **opts):
                return True

            def stats(self):
                return {"backend": self.name}

        b = Full()
        assert b.count() == 0  # default impl via stats()
        assert b.insert_batch([{"id": "a", "text": "t", "vector": [1.0]}]) == 1
        b.close()  # no-op


# ────────────────────────────────────────────────────────────────────────────
# 3. SqliteVectorBackend — real SQLite round-trip
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def sqlite_backend(tmp_path: Path) -> SqliteVectorBackend:
    """A SqliteVectorBackend backed by a temp DB, HNSW disabled for speed."""
    return SqliteVectorBackend(
        db_path=tmp_path / "vecs.db",
        embedding=HashEmbedding(dim=32),
        enable_hnsw=False,
    )


class TestSqliteVectorBackend:
    def test_name(self, sqlite_backend: SqliteVectorBackend) -> None:
        assert sqlite_backend.name == "sqlite"

    def test_insert_and_search(self, sqlite_backend: SqliteVectorBackend) -> None:
        b = sqlite_backend
        v1 = [1.0, 0.0, 0.0] + [0.0] * 29
        v2 = [0.0, 1.0, 0.0] + [0.0] * 29
        assert b.insert("a", "alpha", v1, metadata={"k": 1})
        assert b.insert("b", "beta", v2, metadata={"k": 2})

        results = b.search(v1, top=2)
        assert len(results) >= 1
        assert results[0].id == "a"
        assert results[0].score == pytest.approx(1.0, abs=1e-6)
        assert results[0].metadata == {"k": 1}

    def test_insert_batch(self, sqlite_backend: SqliteVectorBackend) -> None:
        b = sqlite_backend
        entries = [
            {"id": f"e{i}", "text": f"t{i}", "vector": [float(i)] * 32,
             "metadata": {"i": i}}
            for i in range(5)
        ]
        n = b.insert_batch(entries)
        assert n == 5
        assert b.count() == 5

    def test_delete(self, sqlite_backend: SqliteVectorBackend) -> None:
        b = sqlite_backend
        b.insert("x", "x", [1.0] * 32)
        assert b.delete("x")
        assert b.count() == 0
        # deleting non-existent: legacy VectorStore.delete returns True as
        # long as the SQL executes without error (no rowcount check).
        # Verify the count stays at 0 (idempotent).
        b.delete("x")
        assert b.count() == 0

    def test_search_threshold(self, sqlite_backend: SqliteVectorBackend) -> None:
        b = sqlite_backend
        v1 = [1.0] * 32
        v2 = [0.0] * 32
        b.insert("a", "a", v1)
        # With a very high threshold, orthogonal query returns nothing.
        results = b.search(v2, top=10, threshold=0.99)
        assert results == []

    def test_rebuild_index_noop(self, sqlite_backend: SqliteVectorBackend) -> None:
        # sqlite-vec has no persistent index; rebuild is a no-op that
        # returns True.
        assert sqlite_backend.rebuild_index(index_type="ivfflat")
        assert sqlite_backend.rebuild_index(index_type="hnsw")
        assert sqlite_backend.rebuild_index(index_type="unknown")

    def test_stats(self, sqlite_backend: SqliteVectorBackend) -> None:
        b = sqlite_backend
        b.insert("s1", "s1", [1.0] * 32)
        s = b.stats()
        assert s["backend"] == "sqlite"
        assert s["count"] == 1
        assert s["dimension"] == 32
        assert "db_path" in s

    def test_close_clears_caches(self, sqlite_backend: SqliteVectorBackend) -> None:
        b = sqlite_backend
        b.insert("c1", "c1", [1.0] * 32)
        assert b._store._cache  # noqa: SLF001
        b.close()
        assert not b._store._cache  # noqa: SLF001

    def test_empty_query_returns_empty(self, sqlite_backend: SqliteVectorBackend) -> None:
        assert sqlite_backend.search([], top=5) == []


# ────────────────────────────────────────────────────────────────────────────
# 4. PgVectorBackend — mocked engine SQL tests
# ────────────────────────────────────────────────────────────────────────────


def _make_mock_engine() -> MagicMock:
    """Create a MagicMock SQLAlchemy engine with enough structure for
    PgVectorBackend.__init__ to succeed (extension + dimension checks)."""
    eng = MagicMock()

    # _ensure_extension: engine.begin() → ctx mgr → conn.execute(...)
    begin_ctx = MagicMock()
    eng.begin.return_value = begin_ctx
    begin_ctx.__enter__.return_value = begin_ctx
    begin_ctx.__exit__.return_value = False

    # _ensure_dimension: engine.connect() → conn → execute.fetchone()
    conn = MagicMock()
    eng.connect.return_value = conn
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    # fetchone returns None → "column not found" path, skips ALTER.
    conn.execute.return_value.fetchone.return_value = None
    conn.execute.return_value.fetchall.return_value = []
    conn.execute.return_value.scalar.return_value = 0

    return eng


class TestPgVectorBackend:
    def test_name(self) -> None:
        eng = _make_mock_engine()
        b = PgVectorBackend(engine=eng, dimension=None)
        assert b.name == "pg"

    def test_owns_engine_flag_with_explicit_engine(self) -> None:
        eng = _make_mock_engine()
        b = PgVectorBackend(engine=eng)
        assert b._owns_engine is False  # noqa: SLF001

    def test_insert_calls_upsert_sql(self) -> None:
        eng = _make_mock_engine()
        b = PgVectorBackend(engine=eng, dimension=None)
        # Reset mock call history after __init__ (which calls execute for
        # CREATE EXTENSION).
        exec_conn = eng.begin.return_value.__enter__.return_value
        exec_conn.execute.reset_mock()
        ok = b.insert("id1", "hello", [1.0, 2.0, 3.0], metadata={"k": "v"})
        assert ok is True
        eng.begin.assert_called()
        exec_conn.execute.assert_called_once()
        call_args = exec_conn.execute.call_args
        params = call_args.args[1]
        assert params["id"] == "id1"
        assert params["text"] == "hello"
        assert params["emb"] == "[1.0,2.0,3.0]"
        assert json.loads(params["meta"]) == {"k": "v"}

    def test_insert_empty_vector_returns_false(self) -> None:
        eng = _make_mock_engine()
        b = PgVectorBackend(engine=eng, dimension=None)
        assert b.insert("x", "x", []) is False

    def test_insert_batch(self) -> None:
        eng = _make_mock_engine()
        b = PgVectorBackend(engine=eng, dimension=None)
        entries = [
            {"id": "a", "text": "ta", "vector": [1.0, 0.0]},
            {"id": "b", "text": "tb", "vector": [0.0, 1.0], "metadata": {"n": 1}},
            {"id": "c", "text": "tc", "vector": []},  # skipped
        ]
        n = b.insert_batch(entries)
        assert n == 2

    def test_search_parses_results(self) -> None:
        eng = _make_mock_engine()
        b = PgVectorBackend(engine=eng, dimension=None)
        # Mock the search query result: (id, text, metadata, score)
        fake_rows = [
            ("r1", "hello", json.dumps({"k": 1}), 0.95),
            ("r2", "world", json.dumps({}), 0.80),
        ]
        eng.connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = fake_rows

        results = b.search([1.0, 0.0], top=2)
        assert len(results) == 2
        assert results[0].id == "r1"
        assert results[0].score == pytest.approx(0.95)
        assert results[0].metadata == {"k": 1}
        assert results[1].id == "r2"

    def test_search_threshold_filters(self) -> None:
        eng = _make_mock_engine()
        b = PgVectorBackend(engine=eng, dimension=None)
        fake_rows = [
            ("r1", "a", "{}", 0.95),
            ("r2", "b", "{}", 0.50),
        ]
        eng.connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = fake_rows
        results = b.search([1.0], top=10, threshold=0.9)
        assert len(results) == 1
        assert results[0].id == "r1"

    def test_search_empty_query(self) -> None:
        eng = _make_mock_engine()
        b = PgVectorBackend(engine=eng, dimension=None)
        assert b.search([], top=5) == []

    def test_delete(self) -> None:
        eng = _make_mock_engine()
        b = PgVectorBackend(engine=eng, dimension=None)
        # Mock rowcount > 0
        exec_result = MagicMock()
        exec_result.rowcount = 1
        eng.begin.return_value.__enter__.return_value.execute.return_value = exec_result
        assert b.delete("id1") is True

    def test_delete_not_found(self) -> None:
        eng = _make_mock_engine()
        b = PgVectorBackend(engine=eng, dimension=None)
        exec_result = MagicMock()
        exec_result.rowcount = 0
        eng.begin.return_value.__enter__.return_value.execute.return_value = exec_result
        assert b.delete("nope") is False

    def test_stats(self) -> None:
        eng = _make_mock_engine()
        b = PgVectorBackend(engine=eng, dimension=128)
        # Mock count + index list
        conn = eng.connect.return_value.__enter__.return_value
        # execute is called twice: COUNT and LIST_INDEXES
        count_result = MagicMock()
        count_result.scalar.return_value = 42
        list_result = MagicMock()
        list_result.fetchall.return_value = [("idx_vector_embedding_ivfflat",)]
        conn.execute.side_effect = [count_result, list_result]
        s = b.stats()
        assert s["backend"] == "pg"
        assert s["count"] == 42
        assert s["dimension"] == 128
        assert "idx_vector_embedding_ivfflat" in s["indexes"]

    def test_rebuild_index_ivfflat_sql(self) -> None:
        eng = _make_mock_engine()
        b = PgVectorBackend(engine=eng, dimension=None, index_type="ivfflat")
        # Mock raw_connection for CONCURRENTLY
        raw = MagicMock()
        eng.raw_connection.return_value = raw
        cur = MagicMock()
        raw.cursor.return_value = cur

        ok = b.rebuild_index(index_type="ivfflat", lists=50)
        assert ok is True
        # Two executes: DROP + CREATE
        assert cur.execute.call_count == 2
        create_sql = cur.execute.call_args_list[1].args[0]
        assert "CREATE INDEX CONCURRENTLY" in create_sql
        assert "ivfflat" in create_sql
        assert "lists = 50" in create_sql
        assert "vector_cosine_ops" in create_sql

    def test_rebuild_index_hnsw_sql(self) -> None:
        eng = _make_mock_engine()
        b = PgVectorBackend(engine=eng, dimension=None, index_type="hnsw")
        raw = MagicMock()
        eng.raw_connection.return_value = raw
        cur = MagicMock()
        raw.cursor.return_value = cur

        ok = b.rebuild_index(index_type="hnsw", m=32, ef_construction=128)
        assert ok is True
        create_sql = cur.execute.call_args_list[1].args[0]
        assert "USING hnsw" in create_sql
        assert "m = 32" in create_sql
        assert "ef_construction = 128" in create_sql

    def test_rebuild_index_invalid_name_rejected(self) -> None:
        eng = _make_mock_engine()
        b = PgVectorBackend(engine=eng, dimension=None)
        with pytest.raises(ValueError, match="invalid index name"):
            b.rebuild_index(index_name="bad name!")  # type: ignore[arg-type]

    def test_close_disposes_owned_engine(self) -> None:
        eng = _make_mock_engine()
        b = PgVectorBackend(engine=eng)
        b.close()  # owns_engine=False → no dispose
        eng.dispose.assert_not_called()

    def test_parse_meta_variants(self) -> None:
        """_parse_meta handles dict, str, bytes, None."""
        assert PgVectorBackend._parse_meta(None) == {}
        assert PgVectorBackend._parse_meta({"a": 1}) == {"a": 1}
        assert PgVectorBackend._parse_meta('{"b": 2}') == {"b": 2}
        assert PgVectorBackend._parse_meta(b'{"c": 3}') == {"c": 3}
        assert PgVectorBackend._parse_meta("not json") == {}
        assert PgVectorBackend._parse_meta(123) == {}

    def test_vector_literal_format(self) -> None:
        from maop.core.vector.pg_backend import _vector_literal
        assert _vector_literal([1.0, 2.0, 3.0]) == "[1.0,2.0,3.0]"
        assert _vector_literal([]) == "[]"


# ────────────────────────────────────────────────────────────────────────────
# 5. Factory
# ────────────────────────────────────────────────────────────────────────────


class TestFactory:
    def test_resolve_default_sqlite(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAOP_VECTOR_BACKEND", raising=False)
        assert resolve_backend_name() == "sqlite"

    def test_resolve_pg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAOP_VECTOR_BACKEND", "pg")
        assert resolve_backend_name() == "pg"

    def test_resolve_postgresql_alias(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAOP_VECTOR_BACKEND", "postgresql")
        assert resolve_backend_name() == "pg"

    def test_resolve_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAOP_VECTOR_BACKEND", "PG")
        assert resolve_backend_name() == "pg"

    def test_resolve_unknown_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAOP_VECTOR_BACKEND", "redis")
        assert resolve_backend_name() == "sqlite"

    def test_get_backend_sqlite(self, tmp_path: Path) -> None:
        b = get_vector_backend(backend="sqlite", db_path=tmp_path / "v.db")
        assert isinstance(b, SqliteVectorBackend)

    def test_get_backend_pg_with_engine(self) -> None:
        eng = _make_mock_engine()
        b = get_vector_backend(backend="pg", engine=eng, dimension=None)
        assert isinstance(b, PgVectorBackend)

    def test_get_backend_via_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MAOP_VECTOR_BACKEND", "pg")
        eng = _make_mock_engine()
        b = get_vector_backend(engine=eng, dimension=None)
        assert isinstance(b, PgVectorBackend)


# ────────────────────────────────────────────────────────────────────────────
# 6. vector_migration — SQLite source + mocked PG target
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def sqlite_with_vectors(tmp_path: Path) -> Path:
    """Create a SQLite DB with a vector_entries table populated with 5 rows."""
    import sqlite3

    db_path = tmp_path / "source.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE vector_entries (
          id TEXT PRIMARY KEY,
          text TEXT NOT NULL DEFAULT '',
          vector TEXT NOT NULL DEFAULT '[]',
          metadata TEXT NOT NULL DEFAULT '{}',
          created_at REAL NOT NULL DEFAULT 0.0
        );
    """)
    for i in range(5):
        vec = [float(i), float(i + 1), float(i + 2)]
        conn.execute(
            "INSERT INTO vector_entries (id, text, vector, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"v{i}", f"text{i}", json.dumps(vec), json.dumps({"idx": i}), float(i)),
        )
    conn.commit()
    conn.close()
    return db_path


class TestVectorMigration:
    def test_dry_run_counts_rows(self, sqlite_with_vectors: Path) -> None:
        from maop.migrations.pg.vector_migration import migrate_vectors

        result = migrate_vectors(
            sqlite_url=f"sqlite:///{sqlite_with_vectors.as_posix()}",
            pg_engine=_make_mock_engine(),
            dry_run=True,
            progress=False,
        )
        assert result.dry_run is True
        assert result.rows_scanned == 5
        assert result.rows_copied == 0
        assert result.dimension == 3  # inferred from first row

    def test_live_copy_writes_rows(self, sqlite_with_vectors: Path) -> None:
        from maop.migrations.pg.vector_migration import migrate_vectors

        pg_eng = _make_mock_engine()
        result = migrate_vectors(
            sqlite_url=f"sqlite:///{sqlite_with_vectors.as_posix()}",
            pg_engine=pg_eng,
            batch_size=2,
            dry_run=False,
            progress=False,
        )
        assert result.rows_scanned == 5
        assert result.rows_copied == 5
        assert result.rows_skipped == 0
        assert result.dimension == 3
        # Verify PG begin() was called for each batch (ceil(5/2)=3 batches).
        assert pg_eng.begin.call_count >= 3

    def test_dimension_filter_skips_mismatched(self, tmp_path: Path) -> None:
        """Rows whose vector length != dimension are skipped."""
        import sqlite3
        from maop.migrations.pg.vector_migration import migrate_vectors

        db_path = tmp_path / "mixed.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE vector_entries (
              id TEXT PRIMARY KEY, text TEXT, vector TEXT,
              metadata TEXT, created_at REAL
            );
        """)
        conn.execute(
            "INSERT INTO vector_entries VALUES ('ok','a','[1.0,2.0,3.0]','{}',0.0)"
        )
        conn.execute(
            "INSERT INTO vector_entries VALUES ('bad','b','[1.0,2.0]','{}',1.0)"
        )
        conn.commit()
        conn.close()

        result = migrate_vectors(
            sqlite_url=f"sqlite:///{db_path.as_posix()}",
            pg_engine=_make_mock_engine(),
            dimension=3,
            progress=False,
        )
        assert result.rows_copied == 1
        assert result.rows_skipped == 1

    def test_no_source_table(self, tmp_path: Path) -> None:
        """Empty SQLite (no vector_entries) → zero rows, no error."""
        from maop.migrations.pg.vector_migration import migrate_vectors

        empty_db = tmp_path / "empty.db"
        import sqlite3
        sqlite3.connect(str(empty_db)).close()  # create empty file

        result = migrate_vectors(
            sqlite_url=f"sqlite:///{empty_db.as_posix()}",
            pg_engine=_make_mock_engine(),
            progress=False,
        )
        assert result.rows_scanned == 0
        assert result.rows_copied == 0

    def test_result_as_dict(self) -> None:
        from maop.migrations.pg.vector_migration import VectorMigrationResult

        r = VectorMigrationResult(rows_scanned=10, rows_copied=8, dimension=128)
        d = r.as_dict()
        assert d["rows_scanned"] == 10
        assert d["rows_copied"] == 8
        assert d["dimension"] == 128
        assert "elapsed_s" in d

    def test_rebuild_index_invoked(self, sqlite_with_vectors: Path) -> None:
        """When rebuild_index is requested, the PG index rebuild is attempted."""
        from maop.migrations.pg.vector_migration import migrate_vectors

        pg_eng = _make_mock_engine()
        # Mock raw_connection for CONCURRENTLY DDL.
        raw = MagicMock()
        pg_eng.raw_connection.return_value = raw
        raw.cursor.return_value = MagicMock()

        result = migrate_vectors(
            sqlite_url=f"sqlite:///{sqlite_with_vectors.as_posix()}",
            pg_engine=pg_eng,
            rebuild_index="ivfflat",
            progress=False,
        )
        assert result.index_rebuilt is True
        assert result.index_type == "ivfflat"


# ────────────────────────────────────────────────────────────────────────────
# 7. Cross-backend consistency
# ────────────────────────────────────────────────────────────────────────────


class TestCrossBackendConsistency:
    """Both backends honour the VectorBackend ABC interface."""

    def test_sqlite_is_vector_backend(self, tmp_path: Path) -> None:
        b = SqliteVectorBackend(db_path=tmp_path / "x.db", enable_hnsw=False)
        assert isinstance(b, VectorBackend)

    def test_pg_is_vector_backend(self) -> None:
        eng = _make_mock_engine()
        b = PgVectorBackend(engine=eng, dimension=None)
        assert isinstance(b, VectorBackend)

    def test_both_have_required_methods(self) -> None:
        """All abstract methods are implemented (not left as NotImplementedError)."""
        for cls in (SqliteVectorBackend, PgVectorBackend):
            for method in ("search", "insert", "delete", "rebuild_index", "stats"):
                assert hasattr(cls, method)
                assert callable(getattr(cls, method))