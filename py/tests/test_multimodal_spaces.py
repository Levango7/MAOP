"""Tests for the multimodal space extension contract (M2.2 B3).

What B3 adds, and what these tests pin:

* a **registry** of spaces (declarative table/dimension/modalities) whose
  default entry reproduces the pre-B3 configuration byte-for-byte — the
  existing pins (``stats()["space"] == "multimodal-512"``, the
  ``multimodal_entries`` table, ``vector(512)``) must keep holding;
* a **generic write path** (``index_modality``) for modalities the core has
  no embedder for, so the enterprise edition can add audio/video without
  editing core (ADR-022 D11);
* per-space **SQLite/PG DDL** rendering, including the index-name derivation
  that keeps two spaces in one database from silently sharing an index.

The second-space tests use tiny dimensions (8) and a stub engine, so they run
everywhere — no models, no server.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.memory.multimodal_embed import HashModalityEmbedding
from maop.core.memory.multimodal_pg import PgMultimodalBackend
from maop.core.memory.multimodal_schema import (
    MM_TABLE,
    MULTIMODAL_DIM,
    pg_ddl,
    sqlite_ddl,
)
from maop.core.memory.multimodal_spaces import (
    DEFAULT_SPACE_NAME,
    MultimodalSpaceSpec,
    get_multimodal_space,
    list_multimodal_spaces,
    register_multimodal_space,
    reset_multimodal_spaces,
)
from maop.core.memory.multimodal_store import MultimodalStore


@pytest.fixture(autouse=True)
def _clean_spaces():
    reset_multimodal_spaces()
    yield
    reset_multimodal_spaces()


AUDIO_SPACE = MultimodalSpaceSpec(
    name="audio-8",
    table_name="audio_entries_stub",
    dimension=8,
    modalities=("audio",),
    model_id="stub-audio",
    description="test double for the enterprise audio space",
)


def _audio_vector(fill: float = 1.0) -> list[float]:
    return [fill] * 8


# ── registry ───────────────────────────────────────────────────


def test_default_space_matches_the_pre_b3_configuration() -> None:
    spec = get_multimodal_space()
    assert spec.name == DEFAULT_SPACE_NAME == "multimodal-512"
    assert spec.table_name == MM_TABLE
    assert spec.dimension == MULTIMODAL_DIM == 512
    assert spec.modalities == ("text", "image")


def test_register_list_and_reset() -> None:
    register_multimodal_space(AUDIO_SPACE)
    assert [s.name for s in list_multimodal_spaces()] == ["audio-8", "multimodal-512"]
    assert get_multimodal_space("audio-8").table_name == "audio_entries_stub"
    reset_multimodal_spaces()
    assert [s.name for s in list_multimodal_spaces()] == ["multimodal-512"]


def test_unknown_space_raises_with_the_available_list() -> None:
    with pytest.raises(KeyError, match="available: multimodal-512"):
        get_multimodal_space("nope")


def test_re_registering_with_a_different_definition_raises() -> None:
    """Overwriting silently would leave live stores serving the old table."""
    register_multimodal_space(AUDIO_SPACE)
    with pytest.raises(ValueError, match="already registered with a different"):
        register_multimodal_space(
            MultimodalSpaceSpec(
                name="audio-8", table_name="other_table", dimension=8, modalities=("audio",)
            )
        )
    # Identical re-registration is fine (idempotent import-side registration).
    register_multimodal_space(AUDIO_SPACE)


def test_spec_validates_its_own_fields() -> None:
    with pytest.raises(ValueError, match="at least one modality"):
        MultimodalSpaceSpec(name="x", table_name="x_entries", modalities=())
    with pytest.raises(ValueError, match="dimension must be positive"):
        MultimodalSpaceSpec(name="x", table_name="x_entries", dimension=0)
    with pytest.raises(ValueError, match="backend"):
        MultimodalSpaceSpec(name="x", table_name="x_entries", backend="mysql")
    with pytest.raises(ValueError, match="name and table_name"):
        MultimodalSpaceSpec(name="", table_name="x_entries")


# ── DDL: defaults unchanged, second space derived ──────────────


def test_default_ddl_is_byte_identical_to_pre_b3() -> None:
    """The pins the repo already had must keep holding after parameterising."""
    assert sqlite_ddl() == sqlite_ddl(table=MM_TABLE)
    assert "CREATE TABLE IF NOT EXISTS multimodal_entries" in sqlite_ddl()
    assert "vector(512)" in pg_ddl()
    assert "idx_mme_modality" in pg_ddl() and "idx_mme_created" in pg_ddl()
    assert 'CREATE TABLE IF NOT EXISTS multimodal_entries' in sqlite_ddl()


def test_second_space_ddl_uses_its_own_table_and_dimension() -> None:
    ddl = sqlite_ddl(table=AUDIO_SPACE.table_name)
    assert f"CREATE TABLE IF NOT EXISTS {AUDIO_SPACE.table_name}" in ddl
    assert "idx_audio_entries_stub_created" in ddl
    assert "multimodal_entries" not in ddl

    pg = pg_ddl(table=AUDIO_SPACE.table_name, dim=AUDIO_SPACE.dimension)
    assert "vector(8)" in pg
    assert "idx_audio_entries_stub_modality" in pg
    assert "multimodal_entries" not in pg


def test_two_spaces_in_one_database_get_distinct_index_names() -> None:
    """Same database + shared index name = IF NOT EXISTS silently skips."""
    for table in (MM_TABLE, AUDIO_SPACE.table_name):
        ddl = sqlite_ddl(table=table)
        assert f"idx_{table if table != MM_TABLE else 'mme'}_created" in ddl


# ── SQLite backend on a second space ───────────────────────────


def test_sqlite_second_space_round_trip(tmp_path) -> None:
    register_multimodal_space(AUDIO_SPACE)
    store = MultimodalStore(
        db_path=tmp_path / "mm.db", space=AUDIO_SPACE,
        embedding=HashModalityEmbedding(dim=8),
    )
    assert store.stats()["space"] == "audio-8"
    assert store.stats()["table"] == "audio_entries_stub"
    assert store.stats()["dimension"] == 8

    stored = store.index_modality("audio", "a1", vector=_audio_vector(0.5))
    assert stored == "a1"
    hits = store.search("anything", modality="audio")
    assert [h.id for h in hits] == ["a1"]

    # The default space in the same file is untouched.
    assert store.search("anything", modality="text") == []


def test_sqlite_default_space_is_unchanged(tmp_path) -> None:
    store = MultimodalStore(db_path=tmp_path / "mm.db")
    stats = store.stats()
    assert stats["space"] == "multimodal-512"
    assert stats["table"] == MM_TABLE
    assert stats["dimension"] == 512
    assert store.index_text("t1", "hello") == "t1"
    assert [h.id for h in store.search("hello")] == ["t1"]


def test_generic_write_rejects_a_modality_the_space_does_not_accept(tmp_path) -> None:
    store = MultimodalStore(db_path=tmp_path / "mm.db")
    with pytest.raises(ValueError, match="does not accept modality 'audio'"):
        store.index_modality("audio", "x", vector=[0.0] * 512)
    with pytest.raises(ValueError, match="supported: image, text"):
        store.index_modality("audio", "x", vector=[0.0] * 512)


def test_generic_write_requires_a_vector_for_unknown_modalities(tmp_path) -> None:
    register_multimodal_space(AUDIO_SPACE)
    store = MultimodalStore(
        db_path=tmp_path / "mm.db", space=AUDIO_SPACE,
        embedding=HashModalityEmbedding(dim=8),
    )
    with pytest.raises(ValueError, match="needs an explicit vector"):
        store.index_modality("audio", "x")


def test_index_many_routes_by_explicit_modality(tmp_path) -> None:
    register_multimodal_space(AUDIO_SPACE)
    store = MultimodalStore(
        db_path=tmp_path / "mm.db", space=AUDIO_SPACE,
        embedding=HashModalityEmbedding(dim=8),
    )
    stored = store.index_many(
        [
            {"id": "a1", "modality": "audio", "vector": _audio_vector(1.0)},
            {"id": "a2", "modality": "audio", "vector": _audio_vector(0.9)},
        ]
    )
    assert sorted(stored) == ["a1", "a2"]
    assert store.stats()["modalities"] == {"audio": 2}


def test_provider_dimension_mismatch_is_refused(tmp_path) -> None:
    register_multimodal_space(AUDIO_SPACE)
    with pytest.raises(ValueError, match="expects 8-dim"):
        MultimodalStore(
            db_path=tmp_path / "mm.db", space=AUDIO_SPACE,
            embedding=HashModalityEmbedding(dim=512),
        )


# ── PG backend on a second space (stub engine, no server) ──────


class _StubConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, clause, params=None):
        sql = str(clause)
        self.statements.append(sql)
        if sql.strip().upper().startswith("SELECT COUNT("):
            return _StubResult([(0,)])
        if sql.strip().upper().startswith("SELECT ID"):
            return _StubResult([])
        if "RETURNING id" in sql:
            return _StubResult([("stub-id",)])
        if sql.strip().upper().startswith("INSERT"):
            return _StubResult([])
        return _StubResult([])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _StubResult:
    def __init__(self, rows) -> None:
        self._rows = rows

    def fetchall(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0][0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _StubEngine:
    def __init__(self) -> None:
        self.connection = _StubConnection()

    def connect(self):
        return self.connection

    def begin(self):
        return self.connection

    def dispose(self):
        return None


def test_pg_second_space_targets_its_own_table() -> None:
    register_multimodal_space(AUDIO_SPACE)
    engine = _StubEngine()
    backend = PgMultimodalBackend(
        engine=engine, space=AUDIO_SPACE, embedding=HashModalityEmbedding(dim=8),
    )
    assert backend.stats()["space"] == "audio-8"
    assert backend.stats()["table"] == "audio_entries_stub"
    assert backend.stats()["dimension"] == 8
    # ensure_schema ran against the audio table, not the CLIP one.
    joined = "\n".join(engine.connection.statements)
    assert "audio_entries_stub" in joined
    assert "vector(8)" in joined
    assert "multimodal_entries" not in joined


def test_pg_default_space_targets_multimodal_entries() -> None:
    engine = _StubEngine()
    PgMultimodalBackend(engine=engine)
    joined = "\n".join(engine.connection.statements)
    assert "multimodal_entries" in joined
    assert "vector(512)" in joined
    assert "audio_entries_stub" not in joined


def test_pg_rejects_a_non_identifier_table() -> None:
    with pytest.raises(ValueError, match="invalid table name"):
        PgMultimodalBackend(
            engine=_StubEngine(),
            space=MultimodalSpaceSpec(
                name="bad", table_name="audio; DROP TABLE x", dimension=8,
                modalities=("audio",),
            ),
            embedding=HashModalityEmbedding(dim=8),
        )


def test_pg_generic_write_is_routed() -> None:
    register_multimodal_space(AUDIO_SPACE)
    engine = _StubEngine()
    backend = PgMultimodalBackend(
        engine=engine, space=AUDIO_SPACE, embedding=HashModalityEmbedding(dim=8),
    )
    assert backend.index_modality("audio", "a1", vector=_audio_vector()) == "a1"
    joined = "\n".join(engine.connection.statements)
    assert "INSERT INTO audio_entries_stub" in joined


# ── service layer: two spaces in one process ───────────────────


def test_service_keeps_spaces_apart(tmp_path, monkeypatch) -> None:
    from maop.memory import multimodal as mm

    monkeypatch.setattr(mm, "resolve_data_dir", lambda: tmp_path)
    monkeypatch.setattr(mm, "_pg_backend_selected", lambda: False)
    register_multimodal_space(AUDIO_SPACE)

    default = mm.get_multimodal_memory()
    audio = mm.get_multimodal_memory(space="audio-8")
    assert default is not audio
    assert default.space.stats()["space"] == "multimodal-512"
    assert audio.space.stats()["space"] == "audio-8"

    # Same instance on a second call (singleton discipline preserved).
    assert mm.get_multimodal_memory(space="audio-8") is audio
    # …and shutdown now drains *both* queues.
    assert len(mm.get_multimodal_memory_if_exists()) == 2
    assert mm.drain_multimodal_ingest(timeout=1.0) == 0


def test_reset_multimodal_memory_clears_both_spaces(tmp_path, monkeypatch) -> None:
    from maop.memory import multimodal as mm

    monkeypatch.setattr(mm, "resolve_data_dir", lambda: tmp_path)
    monkeypatch.setattr(mm, "_pg_backend_selected", lambda: False)
    register_multimodal_space(AUDIO_SPACE)
    mm.get_multimodal_memory()
    mm.get_multimodal_memory(space="audio-8")
    mm.reset_multimodal_memory()
    assert mm.get_multimodal_memory_if_exists() == []


# ── migration rendering (ADR-022 D5's "new space = new migration") ──


def test_render_pg_migration_matches_the_space_ddl() -> None:
    from maop.core.memory.multimodal_spaces import render_pg_migration

    register_multimodal_space(AUDIO_SPACE)
    text = render_pg_migration(AUDIO_SPACE, "004_audio_space", "003_multimodal_entries")
    assert 'revision = "004_audio_space"' in text
    assert 'down_revision = "003_multimodal_entries"' in text
    assert "audio_entries_stub" in text
    assert "vector(8)" in text
    assert "idx_audio_entries_stub_created" in text
    # The only mention of the default table is the chained revision id.
    assert "multimodal_entries" not in text.replace('down_revision = "003_multimodal_entries"', "")
    assert "DROP TABLE IF EXISTS audio_entries_stub" in text


def test_render_pg_migration_rejects_bad_revision_ids() -> None:
    from maop.core.memory.multimodal_spaces import render_pg_migration

    with pytest.raises(ValueError, match="revision ids must match"):
        render_pg_migration(AUDIO_SPACE, "004 x; DROP TABLE y", "003")


def test_default_space_migration_is_the_committed_one() -> None:
    """The generator must reproduce the shipped 003 migration's DDL.

    If it does not, the generator and the hand-written migration have drifted
    and the D5 promise ("new space = new migration") is broken again.
    """
    from maop.core.memory.multimodal_spaces import render_pg_migration

    spec = get_multimodal_space()
    text = render_pg_migration(spec, "003_multimodal_entries", "002_users_tenant_id")
    committed = (
        Path(__file__).resolve().parents[1]
        / "maop"
        / "migrations"
        / "pg"
        / "versions"
        / "003_multimodal_entries.py"
    ).read_text(encoding="utf-8")
    # Compare the rendered SQL statements, not the file text (the committed
    # migration carries extra comments around them).
    rendered = [
        ln.strip()
        for ln in text.splitlines()
        if "CREATE " in ln and ln.strip().startswith('"""')
    ]
    committed_stmts = [
        ln.strip() for ln in committed.splitlines() if "CREATE " in ln
    ]
    for stmt in committed_stmts:
        needle = stmt.strip().strip('"').strip()
        assert any(needle in r for r in rendered), needle
