"""PgMultimodalBackend unit tests (M2.2 B2, PR-1 stage).

The SQLAlchemy engine is a stub that records statements and answers the few
SELECTs the backend issues, so no PostgreSQL server is required. Real-PG
integration (DDL execution, ANN search, BLOB→vector conversion) lives in
``tests/test_multimodal_pg_integration.py`` and is skipped without a server.

Assertions here focus on things that would otherwise only show up against a
live database: SQL text (table/columns/params), single-transaction batching,
protocol conformance, and stats parity with the SQLite backend.
"""

from __future__ import annotations

from typing import Any

from maop.core.memory.multimodal_backend import MultimodalBackend
from maop.core.memory.multimodal_embed import HashModalityEmbedding
from maop.core.memory.multimodal_pg import (
    PgMultimodalBackend,
    _default_pg_dsn,
    _vector_literal,
)
from maop.core.memory.multimodal_schema import MM_TABLE
from maop.core.memory.multimodal_store import MultimodalStore
from maop.core.memory.vector_embed import VectorSearchResult

VEC = [0.5] * 512


class _Result:
    """Minimal result proxy (rows / rowcount)."""

    def __init__(self, rows: list[tuple] | None = None, rowcount: int = 1) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Txn:
    """Transaction context proxy — forwards ``execute`` to the engine."""

    def __init__(self, engine: _StubEngine) -> None:
        self._engine = engine

    def execute(self, sql: Any, params: dict | None = None) -> _Result:
        return self._engine._record(sql, params)

    def __enter__(self) -> _Txn:  # noqa: PYI034 - test stub returns self
        return self

    def __exit__(self, *exc: object) -> bool:
        self._engine.committed += 1
        return False


class _Conn:
    """Connection proxy — forwards execute to the engine's recorder."""

    def __init__(self, engine: _StubEngine) -> None:
        self._engine = engine

    def execute(self, sql: Any, params: dict | None = None) -> _Result:
        return self._engine._record(sql, params)

    def __enter__(self) -> _Conn:  # noqa: PYI034 - test stub returns self
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _StubEngine:
    """Records every statement; transaction contexts counted."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.executed: list[tuple[str, dict]] = []
        self.committed = 0
        self.url = "stub://"

    def _record(self, sql: Any, params: dict | None = None) -> _Result:
        self.statements.append(str(sql))
        self.executed.append((str(sql), dict(params or {})))
        text_ = str(sql)
        if "COUNT" in text_ and "GROUP BY" not in text_:
            return _Result([(0,)], rowcount=0)
        if "GROUP BY" in text_:
            return _Result([], rowcount=0)
        if "1 - (vector" in text_:
            # search(): id, text, metadata, score
            return _Result([("e1", "cap", {"modality": "image"}, 0.9)], rowcount=0)
        if text_.strip().upper().startswith("SELECT"):
            # modality_of() and other single-column projections
            return _Result([("image",)], rowcount=0)
        return _Result([], rowcount=1)

    def begin(self) -> _Txn:
        return _Txn(self)

    def connect(self) -> _Conn:
        return _Conn(self)


def _backend(engine: _StubEngine | None = None) -> PgMultimodalBackend:
    eng = engine or (_eng := _StubEngine())
    return PgMultimodalBackend(engine=eng, embedding=HashModalityEmbedding())


# ── protocol / schema ─────────────────────────────────────────


def test_implements_multimodal_backend_protocol():
    assert isinstance(_backend(), MultimodalBackend)


def test_inlined_table_name_matches_spec():
    """The hoisted SQL literals must agree with the schema spec's table name.

    The SQl was de-f-stringed (bandit B608: nosec cannot live inside a
    multi-line f-string), which means the table name is now a literal in
    two places — this pins it to :data:`MM_TABLE` so a rename cannot drift.
    """
    from maop.core.memory.multimodal_pg import _SEARCH_SQL, _UPSERT_SQL

    assert MM_TABLE in _UPSERT_SQL
    assert MM_TABLE in _SEARCH_SQL
    assert "{where}" in _SEARCH_SQL, "search SQL keeps a placeholder for the clause"


def test_ensure_schema_runs_spec_ddl():
    eng = _StubEngine()
    _backend(eng)
    joined = " ".join(eng.statements)
    assert 'CREATE EXTENSION IF NOT EXISTS "vector"' in joined
    assert MM_TABLE in joined
    assert "vector(512)" in joined
    assert "USING hnsw" in joined
    assert "(metadata->>'modality') = 'image'" in joined


def test_default_dsn_reads_settings(monkeypatch):
    """DSN resolution goes through settings (config-drift gate), not env."""
    from maop.config import settings as settings_mod

    monkeypatch.setattr(settings_mod, "_settings", None)
    monkeypatch.delenv("MAOP_DATABASE_URL", raising=False)
    assert _default_pg_dsn().endswith("localhost:5432/maop")

    monkeypatch.setenv("MAOP_DATABASE_URL", "postgresql://x/y")
    monkeypatch.setattr(settings_mod, "_settings", None)  # singleton reset
    assert _default_pg_dsn() == "postgresql://x/y"


def test_vector_literal_shape():
    lit = _vector_literal([0.5, -1.0])
    assert lit.startswith("[") and lit.endswith("]")
    assert "0.5" in lit and "-1.0" in lit


# ── write path ─────────────────────────────────────────────────


def test_index_text_upserts_with_modality_and_vector_literal():
    eng = _StubEngine()
    be = _backend(eng)
    assert be.index_text("e1", "hello", vector=VEC, metadata={"k": 1}) == "e1"
    sql, params = eng.executed[-1]
    assert "ON CONFLICT (id) DO UPDATE" in sql
    assert params["id"] == "e1"
    assert params["vec"].startswith("[")
    assert '"modality": "text"' in params["meta"]
    assert "jsonb" in sql.lower()


def test_index_text_rejects_wrong_dimension():
    eng = _StubEngine()
    be = _backend(eng)
    assert be.index_text("e1", "x", vector=[0.1] * 10) == ""


def test_index_text_rejects_empty_id():
    eng = _StubEngine()
    be = _backend(eng)
    assert be.index_text("", "x", vector=VEC) == ""


def test_index_image_writes_source_provenance():
    eng = _StubEngine()
    be = _backend(eng)
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    png = repo / "py" / "tests" / "fixtures" / "multimodal" / "red_square.png"
    out = be.index_image("i1", png, caption="red", vector=VEC)
    assert out == "i1"
    _sql, params = eng.executed[-1]
    assert '"modality": "image"' in params["meta"]
    assert "source_sha256" in params["meta"]


def test_index_many_batches_one_transaction_and_groups_by_modality():
    eng = _StubEngine()
    be = _backend(eng)
    # 4 items: 2 text + 2 image → 2 batch calls (one per modality), 1 txn
    items = [
        {"id": "t1", "text": "a"},
        {"id": "t2", "text": "b"},
        {"id": "i1", "image": _png()},
        {"id": "i2", "image": _png()},
    ]
    stored = be.index_many(items)
    assert sorted(stored) == ["i1", "i2", "t1", "t2"]
    upserts = [s for s in eng.statements if "ON CONFLICT" in s]
    assert len(upserts) == 4, "each row is one upsert"
    # committed counts every transaction (ensure_schema = 1, the batch = 1);
    # the batch itself must be exactly ONE transaction.
    assert eng.committed == 2, "schema + one batch transaction"


def test_index_many_with_precomputed_vectors_skips_embedding():
    eng = _StubEngine()
    be = _backend(eng)
    stored = be.index_many([
        {"id": "t1", "text": "a", "vector": VEC},
        {"id": "i1", "image": _png(), "vector": VEC},
    ])
    assert sorted(stored) == ["i1", "t1"]


# ── read path ──────────────────────────────────────────────────


def test_search_defaults_to_image_modality_for_images():
    eng = _StubEngine()
    be = _backend(eng)
    hits = be.search("hello", top=5)
    assert hits and isinstance(hits[0], VectorSearchResult)
    _sql, params = eng.executed[-1]
    assert "mod" not in params, "modality=None must not filter"


def test_search_with_modality_filters():
    eng = _StubEngine()
    be = _backend(eng)
    be.search("hello", top=5, modality="text")
    _sql, params = eng.executed[-1]
    assert params["mod"] == "text"
    assert "metadata->>'modality'" in eng.statements[-1]


def test_search_threshold_drops_low_scores():
    eng = _StubEngine()
    be = _backend(eng)
    assert be.search("q", top=5, threshold=0.99) == []


def test_search_image_filters_to_images_by_default():
    eng = _StubEngine()
    be = _backend(eng)
    be.search_image(_png(), top=3)
    assert eng.executed[-1][1]["mod"] == "image"


# ── bookkeeping ────────────────────────────────────────────────


def test_modality_of_and_delete_and_count():
    eng = _StubEngine()
    be = _backend(eng)
    assert be.modality_of("e1") == "image"
    assert be.delete("e1") is True
    assert be.count() == 0


def test_stats_keys_match_sqlite_surface():
    eng = _StubEngine()
    be = _backend(eng)
    stats = be.stats()
    for key in ("space", "dimension", "total", "modalities",
                "embedding_model", "embedding_provider", "degraded"):
        assert key in stats, key
    assert stats["backend"] == "pg"
    assert stats["degraded"] is True  # HashModalityEmbedding fixture
    # key parity with the SQLite backend, normalising the one
    # backend-specific key (pg: backend, sqlite: path)
    import tempfile
    from pathlib import Path

    sqlite_store = MultimodalStore(db_path=Path(tempfile.mkdtemp()) / "mm.db")
    sq = sqlite_store.stats()
    normalised = (set(stats) - {"backend"})
    expected = set(sq) - {"path"}
    assert normalised == expected, (
        f"pg={sorted(normalised)} sqlite={sorted(expected)}"
    )


def _png() -> str:
    from pathlib import Path

    return str(Path(__file__).resolve().parents[2]
               / "py" / "tests" / "fixtures" / "multimodal" / "red_square.png")
