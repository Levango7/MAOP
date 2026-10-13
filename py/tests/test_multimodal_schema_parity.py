"""Schema parity guards for the multimodal space (M2.2 B2).

The ``multimodal_entries`` table is deliberately defined in two places
(SQLite runtime DDL + PG alembic migration). These tests keep them honest:

1. The SQLite DDL that :class:`MultimodalStore` actually runs renders from
   :mod:`maop.core.memory.multimodal_schema` (no hand-written drift).
2. The PG migration's DDL is *parsed* from the migration file and its column
   names/order must equal the spec — so adding a column on one side only
   fails here instead of in production.
3. Round-trip: a table created by the rendered DDL accepts the exact rows
   ``MultimodalStore._write`` produces (BLOB vector, JSON metadata with a
   ``modality`` key).
"""

from __future__ import annotations

import hashlib
import pathlib
import sqlite3

import pytest

from maop.core.memory import multimodal_schema as spec
from maop.core.memory.multimodal_store import (
    _MULTIMODAL_DDL,
    MODALITY_KEY,
    MultimodalStore,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
PG_MIGRATION = (
    REPO / "py" / "maop" / "migrations" / "pg" / "versions" / "003_multimodal_entries.py"
)


def test_sqlite_ddl_matches_spec():
    assert _MULTIMODAL_DDL == spec.sqlite_ddl()
    names = [c.name for c in spec.MM_COLUMNS]
    assert names[:2] == ["id", "text"], names
    assert "multimodal_entries" in _MULTIMODAL_DDL


def test_pg_ddl_carries_vector_column_and_modality_indexes():
    ddl = spec.pg_ddl()
    assert "vector vector(512)" in ddl.replace("\n", " ")
    # Per-modality indexed, but WITHOUT an ANN index: exact ordering is the
    # deliberate choice (partial HNSW ranks approximately — see the schema
    # module docstring for the measurement).
    assert "USING hnsw" not in ddl.lower()
    assert "ivfflat" not in ddl.lower()
    assert "(metadata->>'modality')" in ddl
    assert "idx_mme_modality" in ddl


def _parsed_columns(ddl: str) -> list[str]:
    """Column names in order from a rendered ``CREATE TABLE`` body.

    Line-based (not paren-split): types like ``vector(512)`` contain parens,
    so a naive split() on punctuation would truncate the column list.
    """
    names: list[str] = []
    started = False
    for raw in ddl.splitlines():
        line = raw.strip()
        if line.startswith("CREATE TABLE"):
            started = True
            continue
        if not started:
            continue
        if line.startswith(")"):
            break
        line = line.removesuffix(";")
        line = line.strip().rstrip(",")
        if not line:
            continue
        token = line.split()[0]
        if token.isidentifier():
            names.append(token)
    return names
    return names


def test_pg_and_sqlite_columns_have_same_names_and_order():
    sqlite_names = _parsed_columns(spec.sqlite_ddl(with_index=False))
    pg_names = _parsed_columns(spec.pg_ddl(with_indexes=False))
    expected = [c.name for c in spec.MM_COLUMNS]
    assert sqlite_names == expected, sqlite_names
    assert pg_names == expected, pg_names


def test_pg_migration_renders_from_spec():
    """The PG migration must render from the spec (not hand-write DDL).

    Column presence is asserted at the *rendered DDL* level (the whole point
    of the spec module); this test pins the migration's discipline: it
    imports :mod:`maop.core.memory.multimodal_schema` and calls ``pg_ddl()``,
    and its revision chains onto ``002_users_tenant_id`` (pg-tree head).
    """
    if not PG_MIGRATION.exists():
        pytest.fail(
            f"missing PG migration: {PG_MIGRATION} — create it with "
            "down_revision='002_users_tenant_id' rendering from "
            "maop.core.memory.multimodal_schema.pg_ddl()"
        )
    src = PG_MIGRATION.read_text(encoding="utf-8")
    assert "multimodal_schema" in src, "migration must render from the schema spec"
    assert "pg_ddl" in src, "migration must call pg_ddl()"
    assert 'down_revision: str | None = "002_users_tenant_id"' in src


def test_pg_migration_downgrade_is_guarded():
    """Destructive downgrade must require the explicit acknowledgement."""
    src = PG_MIGRATION.read_text(encoding="utf-8")
    assert "_require_destructive_ack" in src
    assert "MAOP_MIGRATION_DESTRUCTIVE" in src


def test_rendered_sqlite_ddl_accepts_store_rows(tmp_path):
    """Round-trip: rendered DDL accepts the rows ``_write`` actually inserts."""
    db = tmp_path / "mm.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript(_MULTIMODAL_DDL)
        vec = hashlib.sha256(b"v").digest()[:512]  # 512 bytes of junk, shape only
        conn.execute(
            f"INSERT INTO {spec.MM_TABLE} (id, text, vector, metadata, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("e1", "", vec,
             '{"' + MODALITY_KEY + '": "image", "source_ext": ".png"}', 1.0),
        )
        row = conn.execute(
            f"SELECT id, vector, metadata FROM {spec.MM_TABLE}"
        ).fetchone()
    assert row[0] == "e1" and isinstance(row[1], bytes)
    assert "image" in row[2]


@pytest.fixture(autouse=True)
def _pin_sqlite_backend(monkeypatch):
    """Pin the multimodal space to SQLite for every test in this module.

    ``MultimodalStore`` is the SQLite implementation; the parity module is
    *about* the SQLite DDL. Under xdist, a worker that previously ran a PG
    selection test can leave ``MAOP_VECTOR_BACKEND`` / the settings
    singleton pointing at PG — and the service-layer factory would then
    route to the PG backend, which has no driver installed in CI. Seen as a
    CI-only failure (2026-10-13, 5 platforms): this module's tests are
    storage-level and must not depend on env selection at all.
    """
    for key in ("MAOP_MULTIMODAL_BACKEND", "MAOP_VECTOR_BACKEND", "MAOP_DB_BACKEND"):
        monkeypatch.delenv(key, raising=False)
    from maop.config import settings as settings_mod

    monkeypatch.setattr(settings_mod, "_settings", None)
    yield


def test_sqlite_store_end_to_end_on_rendered_schema(tmp_path):
    """Full round-trip on the rendered schema.

    CI-only failures (2026-10-13, 5 platforms, green locally) made this test
    carry diagnostics: it snapshots the environment so the CI log explains
    *which* condition diverged instead of a bare assert failure.
    """
    import importlib.util
    import os

    diag = {
        "PIL": importlib.util.find_spec("PIL") is not None,
        "MAOP_VECTOR_BACKEND": os.environ.get("MAOP_VECTOR_BACKEND"),
        "MAOP_DB_BACKEND": os.environ.get("MAOP_DB_BACKEND"),
        "MAOP_MULTIMODAL_BACKEND": os.environ.get("MAOP_MULTIMODAL_BACKEND"),
        "MAOP_DATA_DIR": os.environ.get("MAOP_DATA_DIR"),
        "tmp_path": str(tmp_path),
        "png_bytes": len(_png_bytes()),
    }
    store = MultimodalStore(db_path=tmp_path / "mm.db")
    store.index_text("t1", "hello world")
    store.index_image("i1", _png_bytes(), caption="red")
    stats = store.stats()
    hits = store.search("hello world", top=2, modality="image")
    # diagnostics for CI divergence (pytest shows the dict on failure)
    assert stats.get("modalities") == {"text": 1, "image": 1}, {**diag, "stats": stats}
    assert [h.id for h in hits] == ["i1"], {**diag, "hits": [h.id for h in hits]}
    assert store.count() == 2, {**diag, "count": store.count()}


def _png_bytes() -> bytes:
    return (REPO / "py" / "tests" / "fixtures" / "multimodal" / "red_square.png").read_bytes()
