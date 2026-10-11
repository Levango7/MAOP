"""MAOP Multimodal Vector Space — independent storage for F2-02 (M2.2 B1).

ADR-022 decision: multimodal vectors live in their **own space** rather
than sharing ``vector_entries`` with the text embeddings. The legacy table
is a single ``vector(N)`` column whose dimension is chosen by an
ALTER-on-init (see ``pg_backend``), so a 512-dim CLIP vector cannot coexist
with the 384-dim MiniLM / 1536-dim OpenAI text vectors. A separate space
gives independent dimensions and indexes, and rollback is "delete the file".

Implementation notes
--------------------
- :class:`MultimodalStore` subclasses :class:`VectorStore` (F2-02 B1 made
  ``table_name``/``create_sql``/``expected_dim`` constructor parameters),
  so the search-tier chain, caches, ``delete``/``clear``/``count``/
  ``list_all`` are *inherited*, not copied.
- Vectors are stored as little-endian **float32 BLOBs** (2048 B per
  512-dim vector instead of ~10.5 KB of JSON). The base class reads both
  encodings, so mixed legacy data keeps working.
- ``index()`` is overridden to write through this space's BLOB path —
  otherwise an inherited call would silently write JSON and skew the
  storage budget.
- Modality lives in ``metadata["modality"]`` and is always derived from
  ``_meta_cache`` (never a second dict), so the inherited
  ``delete``/``clear`` cannot leave stale modality state behind.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import struct
import time
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import resolve_data_dir
from maop.core.memory.multimodal_embed import (
    MULTIMODAL_DIM,
    HashModalityEmbedding,
    ImageInput,
    MultimodalEmbeddingProvider,
    validate_image_input,
)
from maop.core.memory.vector_embed import VectorSearchResult, cosine_similarity
from maop.core.memory.vector_store import VectorStore

logger = logging.getLogger(__name__)

__all__ = ["MODALITY_IMAGE", "MODALITY_TEXT", "MultimodalStore"]

MODALITY_TEXT = "text"
MODALITY_IMAGE = "image"

#: Metadata key holding the modality discriminator.
MODALITY_KEY = "modality"

_MULTIMODAL_DDL = """
CREATE TABLE IF NOT EXISTS multimodal_entries (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL DEFAULT '',
  vector BLOB NOT NULL DEFAULT X'',
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_mme_created ON multimodal_entries(created_at);
"""


def _warn_if_non_sqlite_backend_selected() -> None:
    """B1 ships the SQLite path only; make a silent env mismatch visible.

    ``MAOP_VECTOR_BACKEND`` / ``MAOP_DB_BACKEND`` select PostgreSQL for the
    *legacy* text stores. The multimodal space ignores them until batch B2 —
    warn instead of pretending the selection applies. Uses the existing
    resolvers rather than new environment reads (config-drift gate).
    """
    selected: list[str] = []
    try:
        from maop.core.vector.factory import resolve_backend_name

        if resolve_backend_name() == "pg":
            selected.append("MAOP_VECTOR_BACKEND")
    except Exception:  # pragma: no cover - defensive: resolver import/read failure
        pass
    try:
        from maop.core.backends.db_utils import get_db_backend

        if str(get_db_backend()).strip().lower() in {"pg", "postgresql"}:
            selected.append("MAOP_DB_BACKEND")
    except Exception:  # pragma: no cover - defensive
        pass
    if selected:
        logger.warning(
            "[multimodal] %s selects PostgreSQL, but the multimodal space is "
            "SQLite-only until M2.2 batch B2; using SQLite at %s.",
            " and ".join(selected), resolve_data_dir() / "multimodal.db",
        )


class MultimodalStore(VectorStore):
    """512-dim CLIP space backed by ``<data_dir>/multimodal.db``.

    Write API (BLOB encoding): :meth:`index_text`, :meth:`index_image`,
    :meth:`index_many` (modality-grouped batch embedding, returns ids), plus
    the overridden :meth:`index` (routes to the text path) and
    :meth:`index_batch` (base-compatible ``int`` count override).

    Read API: :meth:`search` (text query, optionally modality-filtered),
    :meth:`search_image` (image→image), :meth:`search_vector`, and the
    inherited tier chain for unfiltered cross-modal queries.
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        embedding: MultimodalEmbeddingProvider | None = None,
        *,
        enable_hnsw: bool = False,
    ) -> None:
        if db_path is None:
            _warn_if_non_sqlite_backend_selected()
            db_path = resolve_data_dir() / "multimodal.db"
        provider = embedding or HashModalityEmbedding()
        # Typed handle: the base class attribute is typed as the text-only
        # EmbeddingProvider, which has no image methods.
        self._mm_embedding: MultimodalEmbeddingProvider = provider
        super().__init__(
            db_path,
            provider,
            enable_hnsw=enable_hnsw,
            table_name="multimodal_entries",
            create_sql=_MULTIMODAL_DDL,
            expected_dim=MULTIMODAL_DIM,
        )

    # ── Write path ────────────────────────────────────────────

    def index(
        self,
        entry_id: str,
        text: str,
        *,
        vector: list[float] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Override the base write path so stored vectors are BLOBs.

        Inherited ``VectorStore.index`` would write JSON text (~10.5 KB per
        vector) — decodable, but it silently breaks the 2048 B/entry storage
        budget recorded in ADR-022 D7.
        """
        return self.index_text(entry_id, text, vector=vector, metadata=metadata)

    def index_text(
        self,
        entry_id: str,
        content: str,
        *,
        vector: list[float] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a text entry (CLIP text encoder side of the shared space)."""
        meta = dict(metadata or {})
        meta[MODALITY_KEY] = MODALITY_TEXT
        if vector is None:
            vector = self._embedding.embed(content)
        return self._write(entry_id, content, vector, meta)

    def index_image(
        self,
        entry_id: str,
        image: ImageInput,
        *,
        caption: str = "",
        ext: str | None = None,
        vector: list[float] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store an image entry.

        The original payload is **not** persisted here (ADR-022 D6): paths
        are recorded as ``source_ref`` and byte payloads as
        ``source_sha256``/``source_bytes``. Durable storage of the original
        file belongs to :class:`~maop.core.backends.image_store.ImageStore`
        (wired in B2).
        """
        meta = dict(metadata or {})
        meta[MODALITY_KEY] = MODALITY_IMAGE
        data, resolved_ext = validate_image_input(image, ext=ext)
        meta.setdefault("source_ext", resolved_ext)
        meta.setdefault("source_bytes", len(data))
        meta.setdefault("source_sha256", hashlib.sha256(data).hexdigest())
        if isinstance(image, (str, Path)):
            meta.setdefault("source_ref", str(image))
        if vector is None:
            vector = self._mm_embedding.embed_image(image, ext=ext)
        return self._write(entry_id, caption, vector, meta)

    def index_many(self, items: list[dict[str, Any]]) -> list[str]:
        """Store many entries with one batched encoding pass per modality.

        Each item: ``{"id", "text" | "image", "caption"?, "metadata"?,
        "vector"?, "ext"?}``. Items with a pre-computed ``vector`` skip
        embedding. Returns the list of stored ids (items that fail
        validation are skipped with a warning).

        See :meth:`index_batch` for the LSP-compatible override that keeps
        the base class's ``int`` count contract.
        """
        if not items:
            return []
        text_items: list[dict[str, Any]] = []
        image_items: list[dict[str, Any]] = []
        for item in items:
            if item.get("vector") is not None:
                # Pre-computed vectors need no batching; write directly.
                kind = item.get("image", None)
                if kind is not None or item.get("modality") == MODALITY_IMAGE:
                    image_items.append(item)
                else:
                    text_items.append(item)
            elif item.get("image", None) is not None:
                image_items.append(item)
            else:
                text_items.append(item)

        stored: list[str] = []

        # Text group: one embed_batch call (single CLIP text forward pass).
        pending_text = [it for it in text_items if it.get("vector") is None]
        if pending_text:
            text_vectors = self._mm_embedding.embed_batch(
                [str(it.get("text", "")) for it in pending_text]
            )
            for it, text_vec in zip(pending_text, text_vectors):
                it["vector"] = text_vec
        for it in text_items:
            eid = str(it.get("id") or "")
            if not eid:
                logger.warning("[multimodal] index_many item missing id, skipped")
                continue
            # Batch items without an explicit vector carry their vector in
            # the item dict, not as a kwarg.
            meta = it.get("metadata")
            stored_id = self.index_text(
                eid, str(it.get("text", "")),
                vector=it.get("vector"), metadata=meta,
            )
            if stored_id:
                stored.append(stored_id)

        # Image group: one embed_image_batch call (single forward pass).
        pending_img = [it for it in image_items if it.get("vector") is None]
        if pending_img:
            try:
                img_vectors = self._mm_embedding.embed_image_batch(
                    [it["image"] for it in pending_img]
                )
            except Exception as exc:
                logger.warning(
                    "[multimodal] image batch embed failed (%d item(s) skipped): %s",
                    len(pending_img), exc,
                )
                pending_img = []
                img_vectors = []
            for it, img_vec in zip(pending_img, img_vectors):
                it["vector"] = img_vec
        for it in image_items:
            eid = str(it.get("id") or "")
            if not eid:
                logger.warning("[multimodal] index_many item missing id, skipped")
                continue
            pre_vec = it.get("vector")
            if pre_vec is None:
                continue  # failed batch member
            try:
                stored_id = self.index_image(
                    eid, it["image"],
                    caption=str(it.get("caption", "")),
                    ext=it.get("ext"),
                    vector=pre_vec,
                    metadata=it.get("metadata"),
                )
            except Exception as exc:
                logger.warning("[multimodal] index_image %s failed: %s", eid, exc)
                continue
            if stored_id:
                stored.append(stored_id)

        return stored

    def index_batch(self, entries: list[dict[str, Any]]) -> int:
        """LSP-compatible override of the base ``index_batch`` (returns count).

        Inherited behaviour would write JSON vectors and break the ADR-022
        D7 storage budget (audit N2); delegating to :meth:`index_many`
        keeps the multimodal BLOB path. Callers that need the stored ids
        should call :meth:`index_many` directly.
        """
        return len(self.index_many(entries))

    def _write(
        self,
        entry_id: str,
        text: str,
        vector: list[float],
        meta: dict[str, Any],
    ) -> str:
        if len(vector) != MULTIMODAL_DIM:
            logger.warning(
                "[multimodal] refusing vector with dim %d (space expects %d) for %s",
                len(vector), MULTIMODAL_DIM, entry_id,
            )
            return ""
        now = time.time()
        try:
            with self._connect() as conn:
                conn.execute(
                    f"""INSERT OR REPLACE INTO {self._table}
                        (id, text, vector, metadata, created_at)
                        VALUES (?, ?, ?, ?, ?)""",
                    (entry_id, text, self._encode_vector(vector), json.dumps(meta), now),
                )
        except sqlite3.Error as exc:
            logger.warning("[multimodal] index failed: %s", exc)
            return ""
        except Exception as exc:
            logger.warning("[multimodal] index failed (non-SQLite): %s", exc, exc_info=True)
            return ""

        # Mirror the base class cache maintenance (same LRU eviction rules).
        with self._cache_lock:
            self._cache[entry_id] = vector
            self._text_cache[entry_id] = text
            self._meta_cache[entry_id] = meta
            if len(self._cache) > self._cache_max_size:
                oldest = next(iter(self._cache))
                self._cache.pop(oldest, None)
            if len(self._text_cache) > self._text_cache_max_size:
                oldest = next(iter(self._text_cache))
                self._text_cache.pop(oldest, None)
            if len(self._meta_cache) > self._meta_cache_max_size:
                oldest = next(iter(self._meta_cache))
                self._meta_cache.pop(oldest, None)
        self._hnsw_add([(entry_id, vector)])
        return entry_id

    @staticmethod
    def _encode_vector(vector: list[float]) -> bytes:
        return struct.pack(f"<{len(vector)}f", *vector)

    # ── Read path ─────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        top: int = 10,
        threshold: float = 0.0,
        modality: str | None = None,
    ) -> list[VectorSearchResult]:
        """Text query against the shared space.

        ``modality=None`` searches everything (the cross-modal case: a text
        query ranks both text and image entries). A concrete modality
        filters to that subset — exact, in-memory, appropriate at
        personal-edition scale (ADR-022 D2 records the PG partial-index
        counterpart for B2).
        """
        if modality is None:
            return super().search(query, top=top, threshold=threshold)
        query_vec = self._embedding.embed(query)
        return self._search_filtered(query_vec, top, threshold, modality)

    def search_vector(
        self,
        query_vector: list[float],
        *,
        top: int = 10,
        threshold: float = 0.0,
        modality: str | None = None,
    ) -> list[VectorSearchResult]:
        if modality is None:
            return super().search_vector(query_vector, top=top, threshold=threshold)
        return self._search_filtered(query_vector, top, threshold, modality)

    def search_image(
        self,
        image: ImageInput,
        *,
        top: int = 10,
        threshold: float = 0.0,
        ext: str | None = None,
        modality: str | None = MODALITY_IMAGE,
    ) -> list[VectorSearchResult]:
        """Image→image similarity search (filters to image entries by default)."""
        query_vec = self._mm_embedding.embed_image(image, ext=ext)
        if modality is None:
            return super().search_vector(query_vec, top=top, threshold=threshold)
        return self._search_filtered(query_vec, top, threshold, modality)

    def _search_filtered(
        self,
        query_vector: list[float],
        top: int,
        threshold: float,
        modality: str,
    ) -> list[VectorSearchResult]:
        """Exact cosine search restricted to one modality.

        Runs on the in-memory snapshot; vectors of unexpected dimension are
        skipped defensively so one corrupt row cannot break every query.
        """
        with self._cache_lock:
            cache_empty = not self._cache
        if cache_empty:
            self._load_cache()
        with self._cache_lock:
            snapshot = [
                (eid, vec)
                for eid, vec in self._cache.items()
                if self._meta_cache.get(eid, {}).get(MODALITY_KEY) == modality
            ]
        scored: list[tuple[float, str]] = []
        for eid, vec in snapshot:
            if len(vec) != len(query_vector):
                continue
            sim = cosine_similarity(query_vector, vec)
            if sim >= threshold:
                scored.append((sim, eid))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        results: list[VectorSearchResult] = []
        for sim, eid in scored[: max(0, top)]:
            text, meta = self._get_entry_info(eid)
            results.append(
                VectorSearchResult(id=eid, text=text, score=sim, metadata=meta)
            )
        return results

    def modality_of(self, entry_id: str) -> str:
        """Modality of an entry, derived from the metadata cache ("" if absent)."""
        with self._cache_lock:
            meta = self._meta_cache.get(entry_id, {})
        return str(meta.get(MODALITY_KEY, ""))

    # ── Stats ─────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Space statistics, including per-modality counts.

        Counts come from SQL (``json_extract`` over the metadata column) so
        they stay authoritative even when the in-memory cache is paged; a
        cache-derived fallback covers databases without JSON1.
        """
        modalities: dict[str, int] = {}
        total = 0
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT json_extract(metadata, '$.{MODALITY_KEY}') AS m, "
                    f"COUNT(*) AS cnt FROM {self._table} GROUP BY m"
                ).fetchall()
            for row in rows:
                key = str(row["m"] or "unknown")
                cnt = int(row["cnt"])
                modalities[key] = modalities.get(key, 0) + cnt
                total += cnt
        except Exception as exc:
            logger.debug("[multimodal] SQL stats failed, deriving from cache: %s", exc)
            with self._cache_lock:
                total = len(self._cache)
                for meta in self._meta_cache.values():
                    key = str(meta.get(MODALITY_KEY, "unknown"))
                    modalities[key] = modalities.get(key, 0) + 1
        provider = self._embedding
        return {
            "space": f"multimodal-{MULTIMODAL_DIM}",
            "table": self._table,
            "path": str(self._path),
            "dimension": MULTIMODAL_DIM,
            "total": total,
            "modalities": modalities,
            "embedding_model": getattr(provider, "model_id", "unknown"),
            "embedding_provider": getattr(provider, "provider_id", "unknown"),
            "degraded": not getattr(provider, "is_semantic", False),
        }
