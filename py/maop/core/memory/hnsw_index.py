"""HNSW index wrapper for the MAOP vector store.

Provides :class:`_HnswIndex`, a thin wrapper around ``hnswlib`` for
cosine-similarity approximate-nearest-neighbour search. The wrapper is
used by :class:`maop.core.memory.vector_store.VectorStore` as the top
tier of its four-tier fallback chain:

    HNSW (hnswlib, optional) → sqlite-vec → NumPy → pure Python

Split out of :mod:`maop.core.memory.vector_store` to keep the HNSW
lifecycle logic independent of the SQLite-backed storage layer.

The wrapper never raises on missing ``hnswlib`` — callers detect
availability via :meth:`_HnswIndex.available` and skip the tier silently.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class _HnswIndex:
    """Thin wrapper around ``hnswlib`` for cosine-similarity ANN.

    Lifecycle:
      * Built lazily on first search when ``len(vectors) >= threshold``.
      * Persisted to ``<db_path>.hnsw`` so restarts do not rebuild.
      * Marked dirty on delete/clear; rebuilt on next search.

    The wrapper never raises on missing ``hnswlib`` — callers detect
    availability via :meth:`available` and skip the tier silently.
    """

    def __init__(
        self,
        path: Path,
        dim: int,
        *,
        max_elements: int = 1_000_000,
        m: int = 16,
        ef_construction: int = 200,
        ef_search: int = 64,
    ) -> None:
        self._path = path
        self._dim = dim
        self._max_elements = max_elements
        self._m = m
        self._ef_construction = ef_construction
        self._ef_search = ef_search
        self._index: Any = None
        # internal label → entry_id mapping (hnswlib returns labels)
        self._label_to_id: dict[int, str] = {}
        self._id_to_label: dict[str, int] = {}
        self._dirty = True

    @staticmethod
    def available() -> bool:
        """Return True iff ``hnswlib`` is importable."""
        try:
            import hnswlib  # noqa: F401
            return True
        except ImportError:
            return False

    def _new_index(self) -> Any:
        import hnswlib
        idx = hnswlib.Index(space="cosine", dim=self._dim)
        idx.init_index(
            max_elements=self._max_elements,
            ef_construction=self._ef_construction,
            M=self._m,
        )
        idx.set_ef(self._ef_search)
        return idx

    def build(self, items: list[tuple[str, list[float]]]) -> None:
        """Build the HNSW index from ``(id, vector)`` pairs.

        Reuses the on-disk index when possible and only adds missing
        labels, keeping incremental indexing cheap.
        """
        if not items:
            self._dirty = False
            return
        # Load existing index if present and compatible
        if self._index is None and self._path.exists():
            try:
                import hnswlib
                idx = hnswlib.Index(space="cosine", dim=self._dim)
                idx.load_index(str(self._path))
                self._index = idx
            except Exception as exc:
                logger.debug("[vector] HNSW load failed, will rebuild: %s", exc)
                self._index = None

        if self._index is None:
            # Size max_elements to 2x current count, capped at config
            target_max = max(self._max_elements, len(items) * 2)
            self._max_elements = target_max
            self._index = self._new_index()
            self._label_to_id.clear()
            self._id_to_label.clear()

        # Ensure capacity
        cur_count = self._index.get_current_count()
        if cur_count + len(items) > self._max_elements:
            new_max = max(self._max_elements * 2, cur_count + len(items))
            self._index.resize_index(new_max)
            self._max_elements = new_max

        # Add items with sequential labels
        import numpy as np
        new_labels: list[int] = []
        new_vecs: list[list[float]] = []
        for entry_id, vec in items:
            if entry_id in self._id_to_label:
                continue  # already indexed
            if len(vec) != self._dim:
                # dim mismatch — skip silently; tier downgrade will handle
                logger.debug(
                    "[vector] HNSW skip %s: dim %d != %d",
                    entry_id, len(vec), self._dim,
                )
                continue
            label = cur_count + len(new_labels)
            self._label_to_id[label] = entry_id
            self._id_to_label[entry_id] = label
            new_labels.append(label)
            new_vecs.append(vec)

        if new_labels:
            data = np.array(new_vecs, dtype=np.float32)
            self._index.add_items(data, np.array(new_labels))
            try:
                self._index.save_index(str(self._path))
            except Exception as exc:
                logger.debug("[vector] HNSW save failed: %s", exc)

        self._dirty = False

    def mark_dirty(self) -> None:
        """Mark the index as needing rebuild on next search."""
        self._dirty = True

    def invalidate(self) -> None:
        """Drop the in-memory index and delete the on-disk file."""
        self._index = None
        self._label_to_id.clear()
        self._id_to_label.clear()
        self._dirty = True
        try:
            if self._path.exists():
                self._path.unlink()
        except Exception as exc:
            logger.debug("[vector] HNSW unlink failed: %s", exc)

    def search(
        self,
        query_vector: list[float],
        top: int,
    ) -> list[tuple[str, float]]:
        """Return ``(entry_id, similarity)`` pairs.

        Similarity is cosine in [0, 1] (1 − distance).
        """
        if self._index is None or self._dirty:
            raise RuntimeError("HNSW index not built or dirty")
        if len(query_vector) != self._dim:
            raise RuntimeError(
                f"HNSW dim mismatch: query {len(query_vector)} != index {self._dim}"
            )
        import numpy as np
        labels, distances = self._index.knn_query(
            np.array([query_vector], dtype=np.float32), k=top,
        )
        out: list[tuple[str, float]] = []
        for label, dist in zip(labels[0], distances[0]):
            entry_id = self._label_to_id.get(int(label))
            if entry_id is None:
                continue
            # cosine space: distance = 1 - cos_sim
            sim = max(0.0, 1.0 - float(dist))
            out.append((entry_id, sim))
        return out

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def size(self) -> int:
        return len(self._id_to_label)


__all__ = ["_HnswIndex"]