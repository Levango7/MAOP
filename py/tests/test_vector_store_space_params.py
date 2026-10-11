"""F2-02 B1 — VectorStore space parameters and robustness (ADR-022 D2/D7).

Covers the three additive constructor parameters (``table_name`` /
``create_sql`` / ``expected_dim``), the JSON↔float32-BLOB decoder, and the
bad-row hardening that keeps one corrupt vector from breaking every search.
"""

from __future__ import annotations

import json
import struct

import pytest

from maop.core.memory.vector_store import (
    VectorStore,
    _decode_stored_vector,
)


def _mkstore(tmp_path, name="v.db", **kwargs):
    return VectorStore(tmp_path / name, enable_hnsw=False, **kwargs)


# ── decoder unit tests ───────────────────────────────────────


def test_decode_json_text_legacy():
    assert _decode_stored_vector("[0.5, 0.25]") == [0.5, 0.25]


def test_decode_float32_blob():
    blob = struct.pack("<2f", 0.5, 0.25)
    decoded = _decode_stored_vector(blob)
    assert decoded == pytest.approx([0.5, 0.25])


def test_decode_empty_inputs():
    assert _decode_stored_vector(None) == []
    assert _decode_stored_vector("") == []
    assert _decode_stored_vector(b"") == []


def test_decode_rejects_unknown_encoding():
    with pytest.raises(ValueError):
        _decode_stored_vector("not-a-vector")
    with pytest.raises(ValueError):
        _decode_stored_vector(b"\x00\x01\x02")  # not divisible by 4


# ── default parameters stay equivalent to legacy behavior ────


def test_defaults_use_legacy_table(tmp_path):
    store = _mkstore(tmp_path)
    assert store._table == "vector_entries"
    assert store.index("a", "hello world") == "a"
    hits = store.search("hello world", top=1)
    assert [h.id for h in hits] == ["a"]
    with store._connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "vector_entries" in tables


def test_expected_dim_none_is_permissive(tmp_path):
    """expected_dim defaults to None → mixed dims load as before (no skip)."""
    store = _mkstore(tmp_path)
    store.index("a", "x", vector=[1.0, 0.0])
    store.index("b", "y", vector=[1.0, 0.0, 0.0])
    store._load_cache()
    assert set(store._cache) == {"a", "b"}


# ── space isolation: two stores, one file, two tables ───────


def test_custom_table_and_ddl_isolate_spaces(tmp_path):
    ddl = """
    CREATE TABLE IF NOT EXISTS other_entries (
      id TEXT PRIMARY KEY,
      text TEXT NOT NULL DEFAULT '',
      vector TEXT NOT NULL DEFAULT '[]',
      metadata TEXT NOT NULL DEFAULT '{}',
      created_at REAL NOT NULL DEFAULT 0.0
    );
    """
    from maop.core.memory.vector_embed import HashEmbedding

    db = tmp_path / "shared.db"
    # dim=2 provider matches the explicit vectors below (the default
    # HashEmbedding is 128-dim and would make the numpy tier compare
    # mismatched shapes — exactly the class of bug expected_dim guards).
    legacy = VectorStore(db, embedding=HashEmbedding(dim=2), enable_hnsw=False)
    other = VectorStore(
        db, embedding=HashEmbedding(dim=2), enable_hnsw=False,
        table_name="other_entries", create_sql=ddl,
    )
    legacy.index("legacy-1", "legacy content", vector=[1.0, 0.0])
    other.index("other-1", "other content", vector=[0.0, 1.0])
    assert legacy.count() == 1
    assert other.count() == 1
    assert [h.id for h in legacy.search("legacy", top=5)] == ["legacy-1"]
    assert [h.id for h in other.search("other", top=5)] == ["other-1"]
    legacy.clear()
    assert other.count() == 1


# ── BLOB + hardening ────────────────────────────────────────


def test_load_cache_reads_blob_rows(tmp_path):
    """A float32 BLOB row (multimodal encoding) is readable by the base store."""
    store = _mkstore(tmp_path)
    blob = struct.pack("<3f", 1.0, 0.0, 0.0)
    with store._connect() as conn:
        conn.execute(
            f"INSERT INTO {store._table} (id, text, vector, metadata, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("blob-1", "blob row", blob, json.dumps({}), 1.0),
        )
    hits = store.search_vector([1.0, 0.0, 0.0], top=5)
    assert [h.id for h in hits] == ["blob-1"]


def test_bad_dim_row_is_skipped_but_others_searchable(tmp_path):
    """expected_dim + one corrupt row: search still works (audit I4).

    Without the guard, the ragged cache makes ``np.array`` raise
    ``ValueError`` inside ``search_vector`` and *every* query fails.
    """
    store = _mkstore(tmp_path, expected_dim=3)
    store.index("good-1", "good one", vector=[1.0, 0.0, 0.0])
    with store._connect() as conn:
        conn.execute(
            f"INSERT INTO {store._table} (id, text, vector, metadata, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("bad-1", "bad row", json.dumps([1.0, 0.0]), json.dumps({}), 2.0),
        )
    store.index("good-2", "good two", vector=[0.0, 1.0, 0.0])
    hits = store.search_vector([1.0, 0.0, 0.0], top=10)
    hit_ids = [h.id for h in hits]
    assert "good-1" in hit_ids and "good-2" in hit_ids
    assert "bad-1" not in hit_ids


def test_bad_metadata_row_does_not_abort_cache_load(tmp_path):
    """Corrupt metadata parses to {} instead of aborting the whole load."""
    from maop.core.memory.vector_embed import HashEmbedding

    store = _mkstore(tmp_path, embedding=HashEmbedding(dim=2))
    with store._connect() as conn:
        conn.execute(
            f"INSERT INTO {store._table} (id, text, vector, metadata, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("ok-1", "ok", json.dumps([1.0, 0.0]), json.dumps({"k": 1}), 1.0),
        )
        conn.execute(
            f"INSERT INTO {store._table} (id, text, vector, metadata, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("broken-meta", "boom", json.dumps([0.0, 1.0]), "{not json", 2.0),
        )
    # cache starts empty → search triggers the (previously aborting) load
    hits = store.search_vector([1.0, 0.0], top=10)
    assert {h.id for h in hits} == {"ok-1", "broken-meta"}
    assert store._meta_cache["broken-meta"] == {}
    assert store._meta_cache["ok-1"] == {"k": 1}


def test_undecodable_vector_row_is_skipped(tmp_path):
    store = _mkstore(tmp_path)
    store.index("ok-1", "ok", vector=[1.0, 0.0])
    with store._connect() as conn:
        conn.execute(
            f"INSERT INTO {store._table} (id, text, vector, metadata, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("garbage", "??", "not-a-vector", json.dumps({}), 2.0),
        )
    hits = store.search_vector([1.0, 0.0], top=10)
    assert [h.id for h in hits] == ["ok-1"]
