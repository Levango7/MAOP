"""MAOP Hybrid Search — Combined vector + keyword search with RRF fusion.

Implements Reciprocal Rank Fusion (RRF) to merge results from:
  - Vector (semantic) search: finds conceptually similar items
  - FTS5 (keyword) search: finds lexically matching items

RRF formula: score(d) = Σ 1/(k + rank_i(d))  where k=60 (default)

This combines the strengths of both approaches: semantic understanding
from vectors + precise keyword matching from FTS5.

Usage::

    from maop.core.hybrid_search import HybridSearch

    hs = HybridSearch(root_dir="/path/to/MAOP")

    results = hs.search("authentication timeout", top=10)
    for r in results:
        print(f"[{r.source}] {r.text[:80]} (score={r.rrf_score:.4f})")
"""

from __future__ import annotations


import logging
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.db_utils import sqlite_connect

logger = logging.getLogger(__name__)


class HybridSearchResult(BaseModel):
    """A single hybrid search result with RRF fusion score."""
    id: str = ""
    text: str = ""
    source: str = ""
    vector_score: float = 0.0
    keyword_score: float = 0.0
    rrf_score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class HybridSearchStats(BaseModel):
    """Statistics from a hybrid search operation."""
    query: str = ""
    vector_results: int = 0
    keyword_results: int = 0
    fused_results: int = 0
    duration_ms: int = 0


_RRF_K = 60


def rrf_fuse(
    vector_results: list[tuple[str, float]],
    keyword_results: list[tuple[str, float]],
    k: int = _RRF_K,
) -> dict[str, float]:
    """Reciprocal Rank Fusion of two ranked lists.

    Parameters
    ----------
    vector_results : list of (id, score)
        Results from vector search, ranked by similarity.
    keyword_results : list of (id, score)
        Results from keyword search, ranked by relevance.
    k : int
        RRF constant (default 60). Higher k dampens the effect of
        high ranks, making fusion more democratic.

    Returns
    -------
    dict mapping id -> fused RRF score
    """
    scores: dict[str, float] = {}

    for rank, (doc_id, _score) in enumerate(vector_results, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)

    for rank, (doc_id, _score) in enumerate(keyword_results, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)

    return scores


class HybridSearch:
    """Hybrid vector + keyword search with RRF fusion.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"

    def search(
        self,
        query: str,
        top: int = 10,
        vector_weight: float = 0.6,
        keyword_weight: float = 0.4,
    ) -> list[HybridSearchResult]:
        """Execute a hybrid search combining vector and keyword results.

        Parameters
        ----------
        query : str
            Search query text.
        top : int
            Maximum results to return.
        vector_weight : float
            Weight for vector search results (0.0-1.0).
        keyword_weight : float
            Weight for keyword search results (0.0-1.0).

        Returns
        -------
        list[HybridSearchResult]
            Fused and ranked results.
        """
        start = time.time()

        vector_results = self._vector_search(query, top=top * 3)
        keyword_results = self._keyword_search(query, top=top * 3)

        vector_ids = [(r[0], r[1]) for r in vector_results]
        keyword_ids = [(r[0], r[1]) for r in keyword_results]

        fused = rrf_fuse(vector_ids, keyword_ids)

        all_data: dict[str, dict[str, Any]] = {}
        for doc_id, score in vector_results:
            all_data.setdefault(doc_id, {"vector_score": score, "keyword_score": 0.0, "text": "", "source": "vector", "metadata": {}})
            all_data[doc_id]["vector_score"] = score
            all_data[doc_id]["source"] = "both" if doc_id in {r[0] for r in keyword_results} else "vector"
        for doc_id, score in keyword_results:
            all_data.setdefault(doc_id, {"vector_score": 0.0, "keyword_score": score, "text": "", "source": "keyword", "metadata": {}})
            all_data[doc_id]["keyword_score"] = score
            if all_data[doc_id]["source"] == "vector":
                all_data[doc_id]["source"] = "both"

        ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:top]

        results: list[HybridSearchResult] = []
        for doc_id, rrf_score in ranked:
            data = all_data.get(doc_id, {})
            results.append(HybridSearchResult(
                id=doc_id,
                text=data.get("text", ""),
                source=data.get("source", ""),
                vector_score=data.get("vector_score", 0.0),
                keyword_score=data.get("keyword_score", 0.0),
                rrf_score=round(rrf_score, 6),
                metadata=data.get("metadata", {}),
            ))

        duration_ms = int((time.time() - start) * 1000)
        logger.debug(
            "[hybrid_search] '%s': vec=%d kw=%d fused=%d (%dms)",
            query[:40], len(vector_results), len(keyword_results), len(results), duration_ms,
        )

        return results

    def _vector_search(self, query: str, top: int = 30) -> list[tuple[str, float]]:
        """使用 VectorStore 做语义检索。

        优先使用 sentence-transformers 的真实语义 embedding；若未安装则降级到
        HashEmbedding（伪语义），并在日志中给出 warning 提示。
        """
        try:
            from maop.core.vector import (
                EmbeddingProvider,
                HashEmbedding,
                SentenceTransformerEmbedding,
                VectorStore,
            )

            # 选择 embedding provider：优先 SentenceTransformer，失败时降级 Hash
            embedding: EmbeddingProvider
            try:
                embedding = SentenceTransformerEmbedding()
            except ImportError:
                embedding = HashEmbedding()
                logger.warning(
                    "Using HashEmbedding (not semantically meaningful). "
                    "Install sentence-transformers for real semantic search."
                )

            vs = VectorStore(
                db_path=str(self._data_dir / "vectors.db"),
                embedding=embedding,
            )
            results = vs.search(query, top=top)
            return [(r.id, r.score) for r in results]
        except Exception as exc:
            logger.debug("[hybrid_search] Vector search skipped: %s", exc)
            return []

    def _keyword_search(self, query: str, top: int = 30) -> list[tuple[str, float]]:
        """Search using FTS5 (keyword)."""
        episodic_path = self._data_dir / "episodic.db"
        if not episodic_path.exists():
            return []

        try:
            fts_query = " OR ".join(query.split())
            with sqlite_connect(episodic_path, foreign_keys=False) as conn:
                try:
                    cursor = conn.execute(
                        """SELECT em.id, em.score FROM episodic_memory em
                           JOIN episodic_memory_fts fts ON em.rowid = fts.rowid
                           WHERE episodic_memory_fts MATCH ?
                           ORDER BY em.score DESC LIMIT ?""",
                        (fts_query, top),
                    )
                    return [(row[0], row[1]) for row in cursor.fetchall()]
                except Exception:
                    cursor = conn.execute(
                        "SELECT id, score FROM episodic_memory WHERE task LIKE ? ORDER BY score DESC LIMIT ?",
                        (f"%{query}%", top),
                    )
                    return [(row[0], row[1]) for row in cursor.fetchall()]
        except Exception as exc:
            logger.debug("[hybrid_search] Keyword search skipped: %s", exc)
            return []