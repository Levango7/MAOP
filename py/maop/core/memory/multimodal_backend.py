"""Backend protocol for the multimodal memory space (M2.2 B2).

The multimodal space has two interchangeable backends:

* :class:`maop.core.memory.multimodal_store.MultimodalStore` — SQLite
  (its own ``multimodal.db`` file, [[multimodal] optional extra not required).
* :class:`maop.core.memory.multimodal_pg.PgMultimodalBackend` — PostgreSQL
  + pgvector (``multimodal_entries`` table, ADR-022 D2).

Both must present the same surface so the service layer
(:class:`maop.memory.multimodal.MultimodalMemory`) and the facade
(``store_multimodal`` / ``retrieve_multimodal``) work identically on either.

A ``Protocol`` (rather than an ABC tree) keeps this a *structural* contract:
existing code that ducks-types to the SQLite store keeps working, tests can
``isinstance``-check both implementations against the same protocol, and no
inheritance reordering is needed in the (hot) storage classes.

Core vs optional members
------------------------
The **core** members (write/read/search/stats bookkeeping) must exist on every
backend and are pinned by ``isinstance`` tests. Three members inherited from
``VectorStore`` are marked optional: ``index`` (text convenience wrapper),
``index_batch`` (int-counting LSP shim) and ``list_all`` (dashboard
pagination) — they exist on the SQLite backend but the protocol does not
force them, so callers must ``getattr`` before use.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from maop.core.memory.multimodal_embed import ImageInput
from maop.core.memory.vector_embed import VectorSearchResult

__all__ = ["MultimodalBackend"]


@runtime_checkable
class MultimodalBackend(Protocol):
    """Structural contract shared by the SQLite and PG multimodal backends."""

    # ── write path ────────────────────────────────────────────

    def index_text(
        self,
        entry_id: str,
        content: str,
        *,
        vector: list[float] | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Store a text entry (CLIP text encoder side)."""
        ...

    def index_image(
        self,
        entry_id: str,
        image: ImageInput,
        *,
        caption: str = "",
        ext: str | None = None,
        vector: list[float] | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Store an image entry (path or bytes + optional caption)."""
        ...

    def index_many(self, items: list[dict]) -> list[str]:
        """Store many entries, one batched encoding pass per modality."""
        ...

    # ── read path ─────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        top: int = 10,
        threshold: float = 0.0,
        modality: str | None = None,
    ) -> list[VectorSearchResult]:
        """Text query; ``modality=None`` spans all modalities."""
        ...

    def search_image(
        self,
        image: ImageInput,
        *,
        top: int = 10,
        threshold: float = 0.0,
        ext: str | None = None,
        modality: str | None = "image",
    ) -> list[VectorSearchResult]:
        """Image→image similarity search."""
        ...

    def search_vector(
        self,
        query_vector: list[float],
        *,
        top: int = 10,
        threshold: float = 0.0,
        modality: str | None = None,
    ) -> list[VectorSearchResult]:
        """Search by pre-computed query embedding."""
        ...

    # ── bookkeeping ───────────────────────────────────────────

    def modality_of(self, entry_id: str) -> str:
        """Modality of an entry (``""`` when unknown)."""
        ...

    def delete(self, entry_id: str) -> bool:
        """Delete an entry by id."""
        ...

    def count(self) -> int:
        """Total entries in the space."""
        ...

    def stats(self) -> dict:
        """Per-modality counts + backend identity + degradation flag.

        Consumed by ``MultimodalMemory.degraded`` (``stats()["degraded"]``),
        so both backends must expose at least ``degraded``, ``modalities``,
        ``total`` and the ``embedding_*`` keys.
        """
        ...
