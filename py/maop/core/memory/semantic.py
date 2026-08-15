"""ThreeLayerMemory Layer 3 — Semantic Memory (vector) mixin.

T2 架构债治理：从 ``three_layer_memory.py`` 拆分。公开 API 不变。
VectorStore 惰性加载；依赖宿主的 ``self._vector_store`` / ``self._data_dir``。
"""

from __future__ import annotations

import logging
from typing import Any, cast

logger = logging.getLogger(__name__)


class SemanticMixin:
    """Layer 3: Semantic Memory（向量索引）方法。"""

    # ── Layer 3: Semantic Memory ─────────────────────────────

    def _get_vector_store(self):
        """Lazy-load VectorStore for Semantic Memory."""
        if self._vector_store is None:
            from maop.core.memory.vector import VectorStore
            self._vector_store = VectorStore(db_path=str(self._data_dir / "vectors.db"))
        return self._vector_store

    def semantic_index(
        self, doc_id: str, text: str, metadata: dict[str, Any] | None = None
    ) -> str:
        """Index a document in Semantic Memory (vector search)."""
        vs = self._get_vector_store()
        return cast(str, vs.index(doc_id, text, metadata=metadata))

    def semantic_search(self, query: str, top: int = 5) -> list[Any]:
        """Search Semantic Memory by text query."""
        vs = self._get_vector_store()
        return cast(list[Any], vs.search(query, top=top))

