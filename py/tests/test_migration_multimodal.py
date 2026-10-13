"""SQLite → PG migration: multimodal multi-source + value converters (M2.2 B2).

Covers the two additions:

1. ``value_converters`` — the float32 BLOB → pgvector literal conversion
   (raw BLOBs into a ``vector`` column fail with ``DatatypeMismatch``),
   including the failure modes that must not silently corrupt the target.
2. ``migrate()`` discovering the multimodal space's *own* database file —
   without which the table is invisible to the source introspection and the
   copy reports success with 0 rows (the green lie the design review flagged).

PostgreSQL is not required: the copy runs in dry-run mode against the SQLite
side, and the converter is asserted directly.
"""

from __future__ import annotations

import sqlite3
import struct

import pytest

from maop.migrations.pg.value_converters import (
    VALUE_CONVERTERS,
    blob_to_vector_literal,
    run_converter,
)
from maop.migrations.sqlite_to_pg import (
    _multimodal_source,
    _transform_row,
)

RGB = (220, 30, 30)


def _vec(*floats: float) -> bytes:
    return struct.pack(f"<{len(floats)}f", *floats)


# ── value converters ─────────────────────────────────────────


def test_blob_round_trips_bit_exact():
    blob = _vec(0.5, -1.0, 0.0, 1e-8, 3.4e38)
    lit = blob_to_vector_literal(blob)
    assert lit.startswith("[") and lit.endswith("]")
    values = lit[1:-1].split(",")
    assert len(values) == 5
    back = struct.unpack("<5f", b"".join(struct.pack("<f", float(v)) for v in values))
    assert back == struct.unpack("<5f", blob)


def test_blob_rejects_non_float32_length():
    with pytest.raises(ValueError, match="divisible"):
        blob_to_vector_literal(b"\x01\x02\x03")  # 3 bytes


def test_blob_rejects_text_payload():
    with pytest.raises(ValueError, match="BLOB"):
        blob_to_vector_literal("[0.1, 0.2]")


def test_blob_rejects_none():
    with pytest.raises(ValueError):
        blob_to_vector_literal(None)


def test_registry_covers_multimodal_vector_only():
    assert ("multimodal_entries", "vector") in VALUE_CONVERTERS
    # no other pair may silently gain a converter
    assert len(VALUE_CONVERTERS) == 1


def test_run_converter_passes_through_unregistered():
    assert run_converter("users", "roles_extra", 42) == 42
    assert run_converter("vector_entries", "vector", "[0.1]") == "[0.1]"


def test_transform_row_converts_vector_and_parses_metadata():
    blob = _vec(0.25, 0.75)
    out = _transform_row(
        "multimodal_entries",
        ["id", "text", "vector", "metadata", "created_at"],
        ("e1", "cap", blob, '{"modality": "image"}', 12.5),
    )
    assert out["vector"] == "[0.25,0.75]"
    assert out["metadata"] == {"modality": "image"}
    assert out["created_at"] == 12.5
    assert out["text"] == "cap"


def test_transform_row_passes_other_tables_unchanged():
    out = _transform_row(
        "users", ["id", "username", "roles"], ("u1", "admin", '["adm"]'),
    )
    assert out["roles"] == ["adm"] and out["username"] == "admin"


# ── multi-source discovery ───────────────────────────────────


def _write_multimodal_db(path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS multimodal_entries (
              id TEXT PRIMARY KEY,
              text TEXT NOT NULL,
              vector BLOB NOT NULL,
              metadata TEXT NOT NULL,
              created_at REAL NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO multimodal_entries VALUES (?, ?, ?, ?, ?)",
            ("e1", "cap", _vec(0.5, -1.0), '{"modality": "image"}', 1.0),
        )
        conn.commit()
    finally:
        conn.close()


def test_multimodal_source_discovers_sibling_file(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "maop.db").write_bytes(b"")
    _write_multimodal_db(data / "multimodal.db")
    engine, names = _multimodal_source(
        f"sqlite:///{(data / 'maop.db').as_posix()}"
    )
    assert engine is not None
    assert "multimodal_entries" in names
    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT id FROM multimodal_entries"
        ).fetchone()
    assert row[0] == "e1"


def test_multimodal_source_absent_file_is_not_an_error(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "maop.db").write_bytes(b"")
    engine, names = _multimodal_source(
        f"sqlite:///{(data / 'maop.db').as_posix()}"
    )
    assert engine is None and names == set()


def test_multimodal_source_accepts_direct_url(tmp_path):
    path = tmp_path / "multimodal.db"
    _write_multimodal_db(path)
    engine, names = _multimodal_source(f"sqlite:///{path.as_posix()}")
    assert engine is not None and "multimodal_entries" in names


def test_migrate_dry_run_copies_multimodal_rows(tmp_path, capsys):
    """End-to-end dry-run: the multimodal rows must be *counted* (not skipped)."""
    from maop.migrations import sqlite_to_pg as mod

    data = tmp_path / "data"
    data.mkdir()
    (data / "maop.db").write_bytes(b"")
    _write_multimodal_db(data / "multimodal.db")
    results = mod.migrate(
        sqlite_url=f"sqlite:///{(data / 'maop.db').as_posix()}",
        pg_url="postgresql://u:p@127.0.0.1:1/nodb",
        tables=["multimodal_entries"],
        dry_run=True,
        progress=False,
    )
    # dry-run returns the row count; the pre-fix behaviour was a silent 0
    assert results.get("multimodal_entries") == 1, results


def test_copy_order_registers_multimodal_entries():
    from maop.migrations.sqlite_to_pg import _COPY_ORDER

    assert "multimodal_entries" in _COPY_ORDER


def test_json_columns_register_multimodal_metadata():
    from maop.migrations.sqlite_to_pg import _JSON_COLUMNS

    assert "metadata" in _JSON_COLUMNS["multimodal_entries"]
    # the vector column must NOT be JSON-parsed — the converter owns it
    assert "vector" not in _JSON_COLUMNS["multimodal_entries"]
