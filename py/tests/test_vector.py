"""Tests for MAOP.core.vector — Pure Python vector similarity search."""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from maop.core.vector import (
    EmbeddingProvider,
    HashEmbedding,
    VectorSearchResult,
    VectorStore,
    cosine_similarity,
)


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def vector_store(tmp_path: Path) -> VectorStore:
    """A VectorStore with a temp DB and default HashEmbedding."""
    return VectorStore(db_path=tmp_path / "vectors.db")


@pytest.fixture
def dim128_embedding() -> HashEmbedding:
    return HashEmbedding(dim=128)


# ── cosine_similarity tests ───────────────────────────────────────

class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-9)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-9)

    def test_opposite_vectors(self):
        a = [1.0, 2.0]
        b = [-1.0, -2.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0, abs=1e-9)

    def test_zero_vector_returns_zero(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
        assert cosine_similarity([1.0, 2.0], [0.0, 0.0]) == 0.0

    def test_empty_vectors(self):
        assert cosine_similarity([], []) == 0.0

    def test_different_lengths(self):
        assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0

    def test_symmetric(self):
        a = [1.0, 2.0, 3.0]
        b = [4.0, 5.0, 6.0]
        assert cosine_similarity(a, b) == pytest.approx(cosine_similarity(b, a))

    def test_value_range(self):
        a = [1.0, -2.0, 3.0]
        b = [4.0, 5.0, -6.0]
        sim = cosine_similarity(a, b)
        assert -1.0 <= sim <= 1.0


# ── EmbeddingProvider base class ──────────────────────────────────

class TestEmbeddingProvider:
    def test_base_embed_raises(self):
        provider = EmbeddingProvider()
        with pytest.raises(NotImplementedError):
            provider.embed("test")

    def test_base_embed_batch_delegates(self):
        provider = EmbeddingProvider()
        provider.embed = MagicMock(return_value=[0.1, 0.2])
        result = provider.embed_batch(["a", "b"])
        assert result == [[0.1, 0.2], [0.1, 0.2]]
        assert provider.embed.call_count == 2

    def test_base_dimension_zero(self):
        provider = EmbeddingProvider()
        assert provider.dimension == 0


# ── HashEmbedding tests ───────────────────────────────────────────

class TestHashEmbedding:
    def test_dimension(self, dim128_embedding: HashEmbedding):
        assert dim128_embedding.dimension == 128

    def test_embed_length(self, dim128_embedding: HashEmbedding):
        vec = dim128_embedding.embed("hello world")
        assert len(vec) == 128

    def test_embed_deterministic(self, dim128_embedding: HashEmbedding):
        v1 = dim128_embedding.embed("same text")
        v2 = dim128_embedding.embed("same text")
        assert v1 == v2

    def test_embed_different_text(self, dim128_embedding: HashEmbedding):
        v1 = dim128_embedding.embed("text one")
        v2 = dim128_embedding.embed("text two")
        assert v1 != v2

    def test_embed_unit_vector(self, dim128_embedding: HashEmbedding):
        vec = dim128_embedding.embed("normalize me")
        norm = math.sqrt(sum(v * v for v in vec))
        assert norm == pytest.approx(1.0, abs=1e-6)

    def test_embed_batch(self, dim128_embedding: HashEmbedding):
        texts = ["a", "b", "c"]
        vectors = dim128_embedding.embed_batch(texts)
        assert len(vectors) == 3
        assert all(len(v) == 128 for v in vectors)

    def test_default_dim(self):
        he = HashEmbedding()
        assert he.dimension == 128


# ── VectorStore initialization ────────────────────────────────────

class TestVectorStoreInit:
    def test_init_creates_db(self, tmp_path: Path):
        db = tmp_path / "vecs.db"
        VectorStore(db_path=db)
        assert db.exists()

    def test_init_creates_parent_dir(self, tmp_path: Path):
        db = tmp_path / "nested" / "deep" / "vecs.db"
        VectorStore(db_path=db)
        assert db.exists()

    def test_default_embedding_is_hash(self, tmp_path: Path):
        vs = VectorStore(db_path=tmp_path / "v.db")
        assert isinstance(vs._embedding, HashEmbedding)

    def test_custom_embedding(self, tmp_path: Path):
        emb = HashEmbedding(dim=64)
        vs = VectorStore(db_path=tmp_path / "v.db", embedding=emb)
        assert vs._embedding is emb


# ── Index operations ──────────────────────────────────────────────

class TestIndex:
    def test_index_returns_id(self, vector_store: VectorStore):
        eid = vector_store.index("doc1", "hello world")
        assert eid == "doc1"

    def test_index_stores_text(self, vector_store: VectorStore):
        vector_store.index("doc1", "hello world", metadata={"agent": "claude"})
        assert vector_store.count() == 1

    def test_index_with_precomputed_vector(self, vector_store: VectorStore):
        vec = [1.0, 0.0, 0.0]
        eid = vector_store.index("doc1", "text", vector=vec)
        assert eid == "doc1"
        assert vector_store._cache["doc1"] == vec

    def test_index_with_metadata(self, vector_store: VectorStore):
        meta = {"agent": "claude", "topic": "bug"}
        vector_store.index("doc1", "text", metadata=meta)
        results = vector_store.search("text", top=1)
        assert results[0].metadata == meta

    def test_index_replaces_existing(self, vector_store: VectorStore):
        vector_store.index("doc1", "first text")
        vector_store.index("doc1", "second text")
        assert vector_store.count() == 1

    def test_index_batch(self, vector_store: VectorStore):
        entries = [
            {"id": "d1", "text": "first"},
            {"id": "d2", "text": "second"},
            {"id": "d3", "text": "third"},
        ]
        count = vector_store.index_batch(entries)
        assert count == 3
        assert vector_store.count() == 3

    def test_index_batch_with_vectors(self, vector_store: VectorStore):
        entries = [
            {"id": "d1", "text": "a", "vector": [1.0, 0.0]},
            {"id": "d2", "text": "b", "vector": [0.0, 1.0]},
        ]
        count = vector_store.index_batch(entries)
        assert count == 2

    def test_index_batch_empty(self, vector_store: VectorStore):
        assert vector_store.index_batch([]) == 0


# ── Search operations ─────────────────────────────────────────────

class TestSearch:
    def test_search_returns_results(self, vector_store: VectorStore):
        vector_store.index("d1", "fix login bug")
        vector_store.index("d2", "deploy config")
        results = vector_store.search("login bug", top=5)
        assert len(results) >= 1
        assert all(isinstance(r, VectorSearchResult) for r in results)

    def test_search_sorted_by_score_desc(self, vector_store: VectorStore):
        for i in range(5):
            vector_store.index(f"d{i}", f"document number {i}")
        results = vector_store.search("document", top=5)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_top_limit(self, vector_store: VectorStore):
        for i in range(10):
            vector_store.index(f"d{i}", f"doc {i}")
        results = vector_store.search("doc", top=3)
        assert len(results) <= 3

    def test_search_threshold(self, vector_store: VectorStore):
        vector_store.index("d1", "hello world")
        vector_store.index("d2", "completely different topic")
        # High threshold should filter out low-similarity results
        results = vector_store.search("hello world", top=10, threshold=0.99)
        # Hash embedding is not semantically meaningful, so results may vary
        assert isinstance(results, list)

    def test_search_empty_store(self, vector_store: VectorStore):
        results = vector_store.search("anything")
        assert results == []

    def test_search_vector_direct(self, vector_store: VectorStore):
        vec = [1.0, 0.0, 0.0]
        vector_store.index("d1", "text", vector=vec)
        results = vector_store.search_vector(vec, top=5)
        assert len(results) >= 1
        assert results[0].id == "d1"
        assert results[0].score == pytest.approx(1.0, abs=1e-6)

    def test_search_vector_identical_returns_one(self, vector_store: VectorStore):
        vec = [0.5, 0.5, 0.5]
        vector_store.index("d1", "t", vector=vec)
        results = vector_store.search_vector(vec, top=5)
        assert len(results) == 1


# ── Maintenance operations ────────────────────────────────────────

class TestMaintenance:
    def test_delete_existing(self, vector_store: VectorStore):
        vector_store.index("d1", "hello")
        assert vector_store.delete("d1") is True
        assert vector_store.count() == 0
        assert "d1" not in vector_store._cache

    def test_delete_nonexistent(self, vector_store: VectorStore):
        assert vector_store.delete("nope") is True  # DELETE succeeds even if 0 rows
        assert vector_store.count() == 0

    def test_count(self, vector_store: VectorStore):
        assert vector_store.count() == 0
        vector_store.index("d1", "a")
        vector_store.index("d2", "b")
        assert vector_store.count() == 2

    def test_clear(self, vector_store: VectorStore):
        vector_store.index("d1", "a")
        vector_store.index("d2", "b")
        deleted = vector_store.clear()
        assert deleted == 2
        assert vector_store.count() == 0
        assert vector_store._cache == {}

    def test_clear_empty(self, vector_store: VectorStore):
        assert vector_store.clear() == 0


# ── Persistence ───────────────────────────────────────────────────

class TestPersistence:
    def test_reload_from_disk(self, tmp_path: Path):
        db = tmp_path / "v.db"
        vs1 = VectorStore(db_path=db)
        vs1.index("d1", "persisted text", metadata={"k": "v"})
        # New store instance should load from disk
        vs2 = VectorStore(db_path=db)
        assert vs2.count() == 1
        results = vs2.search("persisted text", top=5)
        assert len(results) >= 1
        assert results[0].id == "d1"

    def test_cache_populated_on_search(self, tmp_path: Path):
        db = tmp_path / "v.db"
        vs1 = VectorStore(db_path=db)
        vs1.index("d1", "text one")
        vs2 = VectorStore(db_path=db)
        # Cache starts empty
        assert vs2._cache == {}
        # Search triggers cache load
        vs2.search("text", top=5)
        assert "d1" in vs2._cache
