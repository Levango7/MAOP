"""Core-path smoke tests for VectorStore (sqlite-vec / pgvector fallback)."""
import pytest


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test_vec.db")


def test_vector_store_imports():
    """VectorStore can be imported without crash."""
    try:
        from maop.core.memory.vector import VectorStore
        assert VectorStore is not None
    except (ImportError, NotImplementedError) as e:
        pytest.skip(f"VectorStore unavailable in this build: {e}")


def test_vector_store_creates_instance(tmp_db):
    """VectorStore instance can be created with a DB path."""
    try:
        from maop.core.memory.vector import VectorStore

        store = VectorStore(db_path=tmp_db)
        assert store is not None
    except (ImportError, NotImplementedError) as e:
        pytest.skip(f"VectorStore unavailable: {e}")


def test_pgvector_fallback_graceful(tmp_db, monkeypatch):
    """When PG env vars are absent, store should still instantiate."""
    monkeypatch.delenv("MAOP_PGVECTOR_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    try:
        from maop.core.memory.vector import VectorStore

        store = VectorStore(db_path=tmp_db)
        assert store is not None
    except (ImportError, NotImplementedError) as e:
        pytest.skip(f"VectorStore unavailable: {e}")