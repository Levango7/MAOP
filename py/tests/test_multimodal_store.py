"""F2-02 B1 — the multimodal vector space (MultimodalStore)."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from maop.core.memory.multimodal_embed import (
    MULTIMODAL_DIM,
    HashModalityEmbedding,
    MultimodalEmbeddingProvider,
    validate_image_input,
)
from maop.core.memory.multimodal_store import (
    MODALITY_IMAGE,
    MODALITY_TEXT,
    MultimodalStore,
)
from maop.core.memory.vector_embed import EmbeddingProvider

FIXTURE_DIR = Path(__file__).parent / "fixtures"
RED = FIXTURE_DIR / "multimodal" / "red_square.png"
BLUE = FIXTURE_DIR / "multimodal" / "blue_square.png"


def _mkstore(tmp_path, embedding=None) -> MultimodalStore:
    return MultimodalStore(db_path=tmp_path / "multimodal.db", embedding=embedding)


class _CountingProvider(HashModalityEmbedding):
    """Fallback provider that records batch-call counts (structural asserts).

    ``embed_image_batch`` deliberately does **not** delegate to the serial
    base implementation, so a serial-call counter stays a true signal that
    the store bypassed the batch API.
    """

    def __init__(self) -> None:
        super().__init__()
        self.text_batch_calls = 0
        self.image_batch_calls = 0
        self.single_image_calls = 0

    def embed_batch(self, texts):
        self.text_batch_calls += 1
        return [self._hash_bytes(t.encode("utf-8")) for t in texts]

    def embed_image_batch(self, images, *, ext=None):
        self.image_batch_calls += 1
        return [
            self._hash_bytes(validate_image_input(img, ext=ext)[0])
            for img in images
        ]

    def embed_image(self, image, *, ext=None):
        self.single_image_calls += 1
        return super().embed_image(image, ext=ext)


# ── roundtrip / storage ─────────────────────────────────────


def test_index_and_search_roundtrip(tmp_path):
    store = _mkstore(tmp_path)
    assert store.index_text("t1", "hello world") == "t1"
    assert store.index_image("i1", RED, caption="red") == "i1"
    assert store.count() == 2
    hits = store.search("hello world", top=5)
    assert {h.id for h in hits} == {"t1", "i1"}
    assert store.modality_of("t1") == MODALITY_TEXT
    assert store.modality_of("i1") == MODALITY_IMAGE


def test_vectors_stored_as_float32_blob_not_json(tmp_path):
    """ADR-022 D7: 2048 B per 512-dim vector, not ~10.5 KB of JSON."""
    store = _mkstore(tmp_path)
    store.index_text("t1", "hello")
    with store._connect() as conn:
        raw = conn.execute(
            f"SELECT vector FROM {store._table} WHERE id = 't1'"
        ).fetchone()["vector"]
    assert isinstance(raw, bytes), "expected BLOB storage"
    assert len(raw) == MULTIMODAL_DIM * 4
    assert len(struct.unpack(f"<{MULTIMODAL_DIM}f", raw)) == MULTIMODAL_DIM


def test_overridden_index_writes_blob_too(tmp_path):
    """Base-class index() must not silently write JSON (audit N2)."""
    store = _mkstore(tmp_path)
    assert store.index("t1", "via base signature") == "t1"
    with store._connect() as conn:
        raw = conn.execute(
            f"SELECT vector FROM {store._table} WHERE id = 't1'"
        ).fetchone()["vector"]
    assert isinstance(raw, bytes)
    assert store.modality_of("t1") == MODALITY_TEXT


def test_dimension_mismatch_is_rejected(tmp_path, caplog):
    store = _mkstore(tmp_path)
    assert store.index_text("bad", "x", vector=[0.1] * 10) == ""
    assert store.count() == 0


def test_search_on_empty_store(tmp_path):
    assert _mkstore(tmp_path).search("anything", top=5) == []


def test_threshold_filters(tmp_path):
    store = _mkstore(tmp_path)
    store.index_text("t1", "hello world")
    # identical text → cosine 1.0 (passes), unrelated text → filtered out
    assert [h.id for h in store.search("hello world", top=5, threshold=0.999)] == ["t1"]
    assert store.search("completely unrelated zzz", top=5, threshold=0.999) == []


# ── modality filtering ──────────────────────────────────────


def test_modality_filter_selects_subset(tmp_path):
    store = _mkstore(tmp_path)
    store.index_text("t1", "hello")
    store.index_image("i1", RED)
    assert {h.id for h in store.search("hello", top=5, modality=MODALITY_TEXT)} == {"t1"}
    assert {h.id for h in store.search("hello", top=5, modality=MODALITY_IMAGE)} == {"i1"}
    assert {h.id for h in store.search("hello", top=5)} == {"t1", "i1"}


def test_search_image_finds_image_entries(tmp_path):
    store = _mkstore(tmp_path)
    store.index_image("i1", RED)
    store.index_image("i2", BLUE)
    hits = store.search_image(RED, top=5)
    assert hits[0].id == "i1"
    assert hits[0].score == pytest.approx(1.0, abs=1e-6)


def test_delete_and_clear_keep_modality_state_consistent(tmp_path):
    """Modality is derived from _meta_cache, so inherited delete/clear can't leak."""
    store = _mkstore(tmp_path)
    store.index_text("t1", "hello")
    store.index_image("i1", RED)
    assert store.delete("t1") is True
    assert store.modality_of("t1") == ""
    assert store.stats()["modalities"] == {"image": 1}
    assert store.clear() == 1
    assert store.stats()["total"] == 0
    assert store.stats()["modalities"] == {}


# ── batch path (structural assert: one call per modality) ───


def test_index_many_groups_by_modality_and_batches(tmp_path):
    provider = _CountingProvider()
    store = _mkstore(tmp_path, embedding=provider)
    ids = store.index_many([
        {"id": "t1", "text": "alpha"},
        {"id": "t2", "text": "beta"},
        {"id": "i1", "image": RED},
        {"id": "i2", "image": BLUE, "caption": "blue"},
    ])
    assert set(ids) == {"t1", "t2", "i1", "i2"}
    assert provider.text_batch_calls == 1, "text group must be one batch call"
    assert provider.image_batch_calls == 1, "image group must be one batch call"
    assert provider.single_image_calls == 0, "batch path must not fall back to serial"
    stats = store.stats()
    assert stats["modalities"] == {"text": 2, "image": 2}


def test_index_many_with_precomputed_vectors_skips_embedding(tmp_path):
    provider = _CountingProvider()
    store = _mkstore(tmp_path, embedding=provider)
    vec = [0.0] * MULTIMODAL_DIM
    vec[0] = 1.0
    ids = store.index_many([
        {"id": "t1", "text": "alpha", "vector": vec},
        {"id": "i1", "image": RED, "vector": vec},
    ])
    assert set(ids) == {"t1", "i1"}
    assert provider.text_batch_calls == 0
    assert provider.image_batch_calls == 0


def test_index_many_empty(tmp_path):
    assert _mkstore(tmp_path).index_many([]) == []


def test_index_batch_override_counts_and_keeps_blob_encoding(tmp_path):
    """Base-compatible index_batch must not fall back to JSON writes (audit N2)."""
    store = _mkstore(tmp_path)
    count = store.index_batch([
        {"id": "t1", "text": "alpha"},
        {"id": "i1", "image": RED, "ext": ".png"},
    ])
    assert count == 2
    with store._connect() as conn:
        raw = conn.execute(
            f"SELECT vector FROM {store._table} WHERE id = 't1'"
        ).fetchone()["vector"]
    assert isinstance(raw, bytes), "index_batch override must keep BLOB storage"
    assert store.modality_of("t1") == MODALITY_TEXT
    assert store.modality_of("i1") == MODALITY_IMAGE


# ── stats ───────────────────────────────────────────────────


def test_stats_shape_and_degraded_flag(tmp_path):
    store = _mkstore(tmp_path, embedding=HashModalityEmbedding())
    store.index_text("t1", "hello")
    stats = store.stats()
    assert stats["space"] == f"multimodal-{MULTIMODAL_DIM}"
    assert stats["dimension"] == MULTIMODAL_DIM
    assert stats["total"] == 1
    assert stats["modalities"] == {"text": 1}
    assert stats["embedding_model"] == "hash-modality-fallback"
    assert stats["degraded"] is True
    assert store._table in stats["path"] or stats["path"].endswith("multimodal.db")


def test_stats_not_degraded_for_semantic_provider(tmp_path):
    class _FakeSemantic(MultimodalEmbeddingProvider):
        model_id = "fake-semantic"

        def __init__(self) -> None:
            self._dim = MULTIMODAL_DIM

        def embed(self, text: str) -> list[float]:
            return [1.0] + [0.0] * (MULTIMODAL_DIM - 1)

        def embed_image(self, image, *, ext=None) -> list[float]:
            return [0.0, 1.0] + [0.0] * (MULTIMODAL_DIM - 2)

    store = _mkstore(tmp_path, embedding=_FakeSemantic())
    assert store.stats()["degraded"] is False


def test_provider_dimension_mismatch_is_refused(tmp_path):
    """A provider that cannot produce the space's width is a config error.

    B3 added the check: without it the store builds, then silently refuses
    every write with a dimension warning (data loss shaped like success).
    """
    class _WrongDim(MultimodalEmbeddingProvider):
        model_id = "wrong-dim"

        def __init__(self) -> None:
            self._dim = 8

        def embed(self, text: str) -> list[float]:
            return [1.0] + [0.0] * 7

        def embed_image(self, image, *, ext=None) -> list[float]:
            return [0.0, 1.0] + [0.0] * 6

    with pytest.raises(ValueError, match="expects 512-dim"):
        _mkstore(tmp_path, embedding=_WrongDim())


def test_fallback_provider_dims_match_space(tmp_path):
    store = _mkstore(tmp_path)
    assert store._expected_dim == MULTIMODAL_DIM
    assert store._embedding.dimension == MULTIMODAL_DIM
    assert isinstance(store._embedding, EmbeddingProvider)
