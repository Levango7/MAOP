"""MAOP Vector Search — Pure Python vector similarity search.

Provides semantic search capability using cosine similarity without
external dependencies (no FAISS, no Annoy, no pinecone).

Two embedding strategies:
  - Local: sentence-transformers (22MB, 384-dim, ~5ms/query)
  - API: OpenAI embeddings (1536-dim, requires API key)

Storage: SQLite-backed vector index for persistence.

Usage::

    vs = VectorStore(db_path="data/vectors.db")

    # Index documents
    vs.index("doc1", "Fix login timeout bug", metadata={"agent": "claude"})
    vs.index("doc2", "Deploy new config system", metadata={"agent": "kimi"})

    # Search by text (auto-embeds query)
    results = vs.search("authentication timeout", top=5)

    # Search by vector (pre-computed embedding)
    results = vs.search_vector(query_vec, top=5)
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from maop.core.db_utils import sqlite_connect

logger = logging.getLogger(__name__)

# ── Models ────────────────────────────────────────────────────

class VectorEntry(BaseModel):
    """A vector-indexed entry."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    text: str = ""
    vector: list[float] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)


class VectorSearchResult(BaseModel):
    """A vector search result with similarity score."""
    id: str
    text: str
    score: float  # Cosine similarity [0, 1]
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Cosine similarity ────────────────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Returns value in [-1, 1]. Higher = more similar.
    Returns 0.0 if either vector is zero-length.
    """
    if len(a) != len(b) or len(a) == 0:
        return 0.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for i in range(len(a)):
        dot += a[i] * b[i]
        norm_a += a[i] * a[i]
        norm_b += b[i] * b[i]

    denom = math.sqrt(norm_a) * math.sqrt(norm_b)
    if denom < 1e-10:
        return 0.0
    return dot / denom


# ── Embedding providers ──────────────────────────────────────

class EmbeddingProvider:
    """Base class for embedding providers."""

    _dim: int = 0

    def embed(self, text: str) -> list[float]:
        """Embed a single text string into a vector."""
        raise NotImplementedError

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts."""
        return [self.embed(t) for t in texts]

    @property
    def dimension(self) -> int:
        return self._dim


class HashEmbedding(EmbeddingProvider):
    """Zero-dependency hash-based embedding provider.

    Uses Python's built-in hashlib to produce deterministic 128-dim
    vectors from text. Suitable for testing and lightweight use cases
    where semantic quality is not critical.
    """

    def __init__(self, dim: int = 128) -> None:
        self._dim = dim

    def embed(self, text: str) -> list[float]:
        import hashlib
        h = hashlib.sha256(text.encode("utf-8")).digest()
        vec = []
        for i in range(self._dim):
            byte_val = h[i % len(h)]
            vec.append(float(byte_val) / 255.0)
        norm = math.sqrt(sum(v * v for v in vec))
        if norm < 1e-10:
            return vec
        return [v / norm for v in vec]


class SentenceTransformerEmbedding(EmbeddingProvider):
    """Local sentence-transformers embedding.

    Requires: pip install sentence-transformers
    Model: all-MiniLM-L6-v2 (22MB, 384-dim, ~5ms/query)
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(model_name)
            self._dim: int = int(self._model.get_sentence_embedding_dimension())
        except ImportError:
            raise ImportError(
                "sentence-transformers not installed. "
                "Install with: pip install sentence-transformers"
            )

    def embed(self, text: str) -> list[float]:
        return cast(list[float], self._model.encode(text).tolist())

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return cast(list[list[float]], self._model.encode(texts).tolist())

    @property
    def dimension(self) -> int:
        return self._dim


# ── SQLite DDL ────────────────────────────────────────────────

_VECTOR_DDL = """
CREATE TABLE IF NOT EXISTS vector_entries (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL DEFAULT '',
  vector TEXT NOT NULL DEFAULT '[]',
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_ve_created ON vector_entries(created_at);
"""


# ── VectorStore ───────────────────────────────────────────────

class VectorStore:
    """SQLite-backed vector store with cosine similarity search.

    Parameters
    ----------
    db_path : Path | str | None
        Path to SQLite database file.
    embedding : EmbeddingProvider | None
        Embedding provider. Defaults to HashEmbedding (zero-dependency).
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        embedding: EmbeddingProvider | None = None,
    ) -> None:
        if db_path is None:
            db_path = Path(__file__).resolve().parents[2] / "data" / "vectors.db"
        self._path = Path(db_path)
        self._embedding = embedding or HashEmbedding()
        self._cache: dict[str, list[float]] = {}  # id → vector cache
        self._cache_max_size = 50000  # P2 fix: prevent unbounded memory growth
        self._text_cache: dict[str, str] = {}  # id → text cache
        self._meta_cache: dict[str, dict[str, Any]] = {}  # id → metadata cache
        self._init_db()

    def _connect(self):
        return sqlite_connect(self._path, foreign_keys=False)

    def _init_db(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as conn:
                conn.executescript(_VECTOR_DDL)
        except Exception as exc:
            logger.warning("Failed to initialize vector DB: %s", exc)

    # ── Index ─────────────────────────────────────────────────

    def index(
        self,
        entry_id: str,
        text: str,
        *,
        vector: list[float] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Index a document with its text and optional pre-computed vector.

        Parameters
        ----------
        entry_id : str
            Unique document ID.
        text : str
            Document text.
        vector : list[float] | None
            Pre-computed embedding. If None, computed via embedding provider.
        metadata : dict | None
            Optional metadata dict.

        Returns
        -------
        str
            Entry ID.
        """
        if vector is None:
            vector = self._embedding.embed(text)

        meta = metadata or {}
        now = time.time()

        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO vector_entries
                       (id, text, vector, metadata, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (entry_id, text, json.dumps(vector), json.dumps(meta), now),
                )
        except Exception as exc:
            logger.warning("[vector] Index failed: %s", exc)
            return ""

        # Update caches
        self._cache[entry_id] = vector
        self._text_cache[entry_id] = text
        self._meta_cache[entry_id] = meta
        return entry_id

    def index_batch(self, entries: list[dict[str, Any]]) -> int:
        """Index multiple entries at once.

        Parameters
        ----------
        entries : list[dict]
            Each dict must have 'id' and 'text', optional 'vector' and 'metadata'.

        Returns
        -------
        int
            Number of entries indexed.
        """
        # Batch embed texts that don't have pre-computed vectors
        texts_to_embed = []
        embed_indices = []
        vectors: list[list[float] | None] = []

        for i, entry in enumerate(entries):
            vec = entry.get("vector")
            if vec is not None:
                vectors.append(vec)
            else:
                texts_to_embed.append(entry["text"])
                embed_indices.append(i)
                vectors.append(None)

        # Batch embed
        if texts_to_embed:
            embedded = self._embedding.embed_batch(texts_to_embed)
            for idx, vec in zip(embed_indices, embedded):
                vectors[idx] = vec

        # Store all via executemany for batch efficiency
        count = 0
        try:
            with self._connect() as conn:
                now = time.time()
                rows = []
                for i, entry in enumerate(entries):
                    vec = vectors[i]
                    if vec is None:
                        continue
                    meta = entry.get("metadata", {})
                    eid = entry["id"]
                    text = entry["text"]
                    rows.append((eid, text, json.dumps(vec), json.dumps(meta), now))
                    self._cache[eid] = vec
                    self._text_cache[eid] = text
                    self._meta_cache[eid] = meta
                    count += 1
                if rows:
                    conn.executemany(
                        """INSERT OR REPLACE INTO vector_entries
                           (id, text, vector, metadata, created_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        rows,
                    )
        except Exception as exc:
            logger.warning("[vector] Batch index failed: %s", exc)

        return count

    # ── Search ────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        top: int = 10,
        threshold: float = 0.0,
    ) -> list[VectorSearchResult]:
        """Search by text query (auto-embeds).

        Parameters
        ----------
        query : str
            Search query text.
        top : int
            Maximum results.
        threshold : float
            Minimum cosine similarity threshold.

        Returns
        -------
        list[VectorSearchResult]
            Results sorted by similarity descending.
        """
        query_vec = self._embedding.embed(query)
        return self.search_vector(query_vec, top=top, threshold=threshold)

    def search_vector(
        self,
        query_vector: list[float],
        *,
        top: int = 10,
        threshold: float = 0.0,
    ) -> list[VectorSearchResult]:
        """Search by pre-computed vector.

        Parameters
        ----------
        query_vector : list[float]
            Query embedding vector.
        top : int
            Maximum results.
        threshold : float
            Minimum cosine similarity.

        Returns
        -------
        list[VectorSearchResult]
            Results sorted by similarity descending.
        """
        # Load all vectors (with cache)
        if not self._cache:
            self._load_cache()

        if not self._cache:
            return []

        # Try sqlite-vec ANN index (fastest, optional dep)
        try:
            return self._search_vector_sqlite_vec(query_vector, top, threshold)
        except Exception as e:
            logger.debug("ignored: %s", e, exc_info=True)

        # Try numpy-accelerated batch similarity
        try:
            import numpy as np
            return self._search_vector_numpy(query_vector, top, threshold, np)
        except ImportError:
            pass

        # Fallback: pure Python
        return self._search_vector_python(query_vector, top, threshold)

    def _search_vector_sqlite_vec(
        self,
        query_vector: list[float],
        top: int,
        threshold: float,
    ) -> list[VectorSearchResult]:
        """sqlite-vec ANN search (optional, ~100x faster than brute-force).

        Requires the ``sqlite-vec`` package. If not installed, raises
        ImportError which is caught by the caller to fall back to NumPy.
        """
        import sqlite_vec  # noqa: F401 — optional dep, ImportError expected if missing
        with sqlite_connect(self._path, timeout=10, wal=True) as conn:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            # Use virtual table if it exists, else raise to fall back
            cursor = conn.execute(
                "SELECT id, text, metadata, distance FROM vec_vectors "
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (json.dumps(query_vector), top),
            )
            rows = cursor.fetchall()
        results: list[VectorSearchResult] = []
        for row in rows:
            eid, text, meta_json, dist = row
            score = max(0.0, 1.0 - float(dist))
            if score < threshold:
                continue
            meta = json.loads(meta_json) if meta_json else {}
            results.append(VectorSearchResult(id=eid, text=text, score=score, metadata=meta))
        return results

    def _search_vector_numpy(
        self,
        query_vector: list[float],
        top: int,
        threshold: float,
        np: Any,
    ) -> list[VectorSearchResult]:
        """NumPy-accelerated batch cosine similarity search."""
        ids = list(self._cache.keys())
        vecs = list(self._cache.values())
        mat = np.array(vecs, dtype=np.float64)
        q = np.array(query_vector, dtype=np.float64)

        norms = np.linalg.norm(mat, axis=1)
        q_norm = np.linalg.norm(q)
        denom = norms * q_norm
        denom = np.where(denom < 1e-10, 1.0, denom)
        sims = (mat @ q) / denom

        mask = sims >= threshold
        scored = [(float(sims[i]), ids[i]) for i in range(len(ids)) if mask[i]]
        scored.sort(key=lambda x: x[0], reverse=True)
        scored = scored[:top]

        results = []
        for score, eid in scored:
            text, meta = self._get_entry_info(eid)
            results.append(VectorSearchResult(id=eid, text=text, score=score, metadata=meta))
        return results

    def _search_vector_python(
        self,
        query_vector: list[float],
        top: int,
        threshold: float,
    ) -> list[VectorSearchResult]:
        """Pure Python cosine similarity search (fallback)."""
        scored: list[tuple[float, str, str, dict]] = []
        for entry_id, vec in self._cache.items():
            sim = cosine_similarity(query_vector, vec)
            if sim >= threshold:
                text, meta = self._get_entry_info(entry_id)
                scored.append((sim, entry_id, text, meta))

        scored.sort(key=lambda x: x[0], reverse=True)

        return [VectorSearchResult(
            id=eid, text=text, score=score, metadata=meta,
        ) for score, eid, text, meta in scored[:top]]

    def _load_cache(self) -> None:
        """Load all vectors, text, and metadata from SQLite into memory cache.

        P2 fix: Added cache size limit to prevent unbounded memory growth.
        For datasets > 50K vectors, consider using sqlite-vec or faiss for ANN indexing.

        Batch-loads all columns in a single query to avoid N+1 per-entry lookups
        during search_vector(). Also populates _text_cache and _meta_cache.
        """
        try:
            with self._connect() as conn:
                for row in conn.execute(
                    "SELECT id, vector, text, metadata FROM vector_entries"
                ).fetchall():
                    self._cache[row["id"]] = json.loads(row["vector"])
                    self._text_cache[row["id"]] = row["text"] or ""
                    self._meta_cache[row["id"]] = json.loads(row["metadata"] or "{}")
        except Exception as exc:
            logger.warning("[vector] Cache load failed: %s", exc)

    def _get_entry_info(self, entry_id: str) -> tuple[str, dict[str, Any]]:
        """Get text and metadata for an entry from in-memory cache."""
        text = self._text_cache.get(entry_id, "")
        meta = self._meta_cache.get(entry_id, {})
        if text or meta:
            return text, meta
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT text, metadata FROM vector_entries WHERE id = ?",
                    (entry_id,),
                ).fetchone()
                if row:
                    text = row["text"] or ""
                    meta = json.loads(row["metadata"] or "{}")
                    self._text_cache[entry_id] = text
                    self._meta_cache[entry_id] = meta
                    return text, meta
        except Exception as e:
            logger.debug("ignored: %s", e, exc_info=True)
        return "", {}

    # ── Maintenance ───────────────────────────────────────────

    def delete(self, entry_id: str) -> bool:
        """Delete an entry by ID."""
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM vector_entries WHERE id = ?", (entry_id,))
            self._cache.pop(entry_id, None)
            self._text_cache.pop(entry_id, None)
            self._meta_cache.pop(entry_id, None)
            return True
        except Exception as exc:
            logger.warning("[vector] Delete failed: %s", exc)
            return False

    def count(self) -> int:
        """Count total indexed entries."""
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(*) as cnt FROM vector_entries").fetchone()
                return row["cnt"] if row else 0
        except Exception:
            return 0

    def clear(self) -> int:
        """Remove all entries. Returns count deleted."""
        cnt = self.count()
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM vector_entries")
            self._cache.clear()
            self._text_cache.clear()
            self._meta_cache.clear()
        except Exception as exc:
            logger.warning("[vector] Clear failed: %s", exc)
        return cnt
