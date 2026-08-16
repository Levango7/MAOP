"""MAOP Semantic Vector Search — sentence-transformers powered semantic search.

Upgrades from HashEmbedding (non-semantic) to sentence-transformers for
true semantic similarity. Falls back gracefully when the library is unavailable.

Architecture:
  - Primary: sentence-transformers (all-MiniLM-L6-v2 by default)
  - Fallback: HashEmbedding (existing, non-semantic but functional)
  - SQLite + numpy for vector storage and cosine similarity
  - Incremental indexing — only new entries get embedded

Usage::

    from maop.memory.vector_search import VectorSearch

    vs = VectorSearch(root_dir="/path/to/MAOP")
    vs.index_all()  # Index all unindexed memory entries
    results = vs.search("authentication flow", top=5)
"""

from __future__ import annotations

import hashlib
import logging
import struct
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384
DEFAULT_MODEL = "all-MiniLM-L6-v2"


class VectorResult(BaseModel):
    id: str = ""
    score: float = 0.0
    text: str = ""


class VectorSearch:
    """Semantic vector search with sentence-transformers + graceful fallback.

    When sentence-transformers is available, uses real semantic embeddings.
    When unavailable, falls back to deterministic hash-based pseudo-embeddings
    that provide basic keyword-level matching without true semantic understanding.
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        model_name: str = DEFAULT_MODEL,
        dim: int = EMBEDDING_DIM,
    ) -> None:
        self._root = Path(root_dir)
        self._model_name = model_name
        self._dim = dim
        self._db_path = get_db_path("vector_search")
        self._model = None
        self._semantic_available = False
        self._ensure_db()
        self._try_load_model()

    def _ensure_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vectors (
                    id TEXT PRIMARY KEY,
                    embedding BLOB NOT NULL,
                    text_hash TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    indexed_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS index_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_name TEXT NOT NULL,
                    entries_indexed INTEGER DEFAULT 0,
                    indexed_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_vectors_hash ON vectors(text_hash)")

    def _try_load_model(self) -> None:
        """Try to load sentence-transformers model."""
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)
            self._semantic_available = True
            logger.info("[vector] Loaded semantic model: %s", self._model_name)
        except ImportError:
            logger.info("[vector] sentence-transformers not available, using hash fallback")
            self._semantic_available = False
        except Exception as exc:
            logger.warning("[vector] Failed to load model %s: %s", self._model_name, exc)
            self._semantic_available = False

    @property
    def is_semantic(self) -> bool:
        """Whether true semantic search is available."""
        return self._semantic_available

    def embed(self, text: str) -> np.ndarray:
        """Generate embedding for a text string."""
        if self._semantic_available and self._model is not None:
            return self._model.encode(text, normalize_embeddings=True)
        return self._hash_embed(text)

    def _hash_embed(self, text: str) -> np.ndarray:
        """Deterministic hash-based pseudo-embedding (fallback).

        Not semantically meaningful, but provides basic similarity
        for exact/near-exact keyword matches.
        """
        vec = np.zeros(self._dim, dtype=np.float64)
        words = text.lower().split()
        for i, word in enumerate(words):
            h = hashlib.sha256(f"{word}:{i}".encode()).digest()
            for j in range(min(8, self._dim)):
                idx = (i * 8 + j) % self._dim
                raw = struct.unpack("f", h[j * 4:(j + 1) * 4])[0]
                if np.isfinite(raw):
                    vec[idx] += np.clip(raw, -1e3, 1e3) * 0.01
        norm = np.linalg.norm(vec)
        if norm > 1e-8:
            vec /= norm
        return vec.astype(np.float32)

    def _blob_to_vec(self, blob: bytes) -> np.ndarray:
        """Convert SQLite BLOB to numpy array."""
        return np.frombuffer(blob, dtype=np.float32)

    def _vec_to_blob(self, vec: np.ndarray) -> bytes:
        """Convert numpy array to SQLite BLOB."""
        return vec.astype(np.float32).tobytes()

    def index_entry(self, entry_id: str, text: str) -> bool:
        """Index a single entry by its ID and text content."""
        text_hash = hashlib.sha256(text.encode()).hexdigest()
        from datetime import datetime, timezone

        existing = None
        with sqlite_connect(self._db_path) as conn:
            try:
                row = conn.execute(
                    "SELECT text_hash FROM vectors WHERE id = ?", (entry_id,),
                ).fetchone()
                if row:
                    existing = row["text_hash"]
            except Exception:
                logger.debug('swallowed exception', exc_info=True)

        if existing == text_hash:
            return False

        vec = self.embed(text)
        now = datetime.now(timezone.utc).isoformat()

        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO vectors (id, embedding, text_hash, model_name, indexed_at)
                   VALUES (?,?,?,?,?)""",
                (entry_id, self._vec_to_blob(vec), text_hash, self._model_name, now),
            )
        return True

    def index_all(self, memory_store=None) -> int:
        """Index all unindexed memory entries.

        Parameters
        ----------
        memory_store : MemoryStore, optional
            If provided, will scan its entries. Otherwise, reads directly
            from the memory_entries table.
        """
        indexed = 0
        with sqlite_connect(self._db_path) as conn:
            try:
                already = {r["id"] for r in conn.execute("SELECT id FROM vectors").fetchall()}
            except Exception:
                already = set()

        mem_db = self._root / "data" / "memory.db"
        if not mem_db.exists():
            return 0

        with sqlite_connect(mem_db) as conn:
            try:
                rows = conn.execute(
                    "SELECT id, task, content, agent FROM memory_entries ORDER BY timestamp DESC",
                ).fetchall()
            except Exception:
                return 0

        for row in rows:
            if row["id"] in already:
                continue
            text = f"{row['task']} {row['content']} {row['agent']}"
            if self.index_entry(row["id"], text):
                indexed += 1

        if indexed > 0:
            from datetime import datetime, timezone
            with sqlite_connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO index_log (model_name, entries_indexed, indexed_at) VALUES (?,?,?)",
                    (self._model_name, indexed, datetime.now(timezone.utc).isoformat()),
                )

        logger.info("[vector] Indexed %d entries (semantic=%s)", indexed, self._semantic_available)
        return indexed

    def search(self, query: str, top: int = 10) -> list[VectorResult]:
        """Search for entries similar to the query.

        Uses cosine similarity between query embedding and stored vectors.
        """
        query_vec = self.embed(query)

        with sqlite_connect(self._db_path) as conn:
            try:
                rows = conn.execute("SELECT id, embedding FROM vectors").fetchall()
            except Exception:
                return []

        if not rows:
            return []

        scored: list[tuple[float, str]] = []
        for row in rows:
            vec = self._blob_to_vec(row["embedding"])
            if vec.shape[0] != query_vec.shape[0]:
                continue
            similarity = float(np.dot(query_vec, vec))
            scored.append((similarity, row["id"]))

        scored.sort(key=lambda x: x[0], reverse=True)

        results: list[VectorResult] = []
        for score, entry_id in scored[:top]:
            text = self._get_entry_text(entry_id)
            results.append(VectorResult(id=entry_id, score=round(score, 4), text=text[:200]))

        return results

    def _get_entry_text(self, entry_id: str) -> str:
        """Get the text content for an entry ID from the memory store."""
        mem_db = self._root / "data" / "memory.db"
        if not mem_db.exists():
            return ""
        with sqlite_connect(mem_db) as conn:
            try:
                row = conn.execute(
                    "SELECT task, content FROM memory_entries WHERE id = ?", (entry_id,),
                ).fetchone()
                if row:
                    return f"{row['task']}: {row['content']}"
            except Exception:
                logger.debug('swallowed exception', exc_info=True)
        return ""

    def stats(self) -> dict[str, Any]:
        """Get vector search statistics."""
        with sqlite_connect(self._db_path) as conn:
            try:
                total = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
                last_log = conn.execute(
                    "SELECT * FROM index_log ORDER BY id DESC LIMIT 1",
                ).fetchone()
            except Exception:
                total = 0
                last_log = None

        return {
            "total_vectors": total,
            "model_name": self._model_name,
            "is_semantic": self._semantic_available,
            "dimension": self._dim,
            "last_index_log": dict(last_log) if last_log else None,
        }
