"""MAOP Multimodal Memory service — F2-02 (M2.2 B1).

Combines the multimodal vector space (:class:`MultimodalStore`) with an
optional text-memory retriever and fuses both legs with RRF
(:func:`~maop.core.memory.fusion.reciprocal_rank_fusion`).

The service is the layer behind ``MemoryFacade.store_multimodal()`` /
``retrieve_multimodal()`` (PRD R04). It never raises for a missing
``[multimodal]`` extra: without torch the store runs on the deterministic
hash fallback, and every consumer can see that state via ``stats()``
(``degraded=True``) and per-hit metadata.

The text-memory leg is an **injected callable** rather than a direct
method reference because both implementations expose
``long_term_search(query, *, top=...)`` as keyword-only — the Facade
adapts with ``lambda query, n: impl.long_term_search(query, top=n)``
(design review N1).
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import resolve_data_dir
from maop.core.memory.fusion import reciprocal_rank_fusion
from maop.core.memory.multimodal_backend import MultimodalBackend
from maop.core.memory.multimodal_embed import ImageInput
from maop.core.memory.multimodal_store import (
    MODALITY_IMAGE,
    MODALITY_TEXT,
    MultimodalStore,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MultimodalMemory",
    "get_multimodal_memory",
    "reset_multimodal_memory",
]

#: Text-memory leg adapter. Callers adapt keyword-only signatures themselves
#: (see module docstring); returns a list of dict-like hits.
TextSearchFn = Callable[..., list[dict[str, Any]]]


class MultimodalMemory:
    """Store/retrieve over the multimodal space, with optional RRF fusion."""

    def __init__(
        self,
        store: MultimodalStore | MultimodalBackend | None = None,
        text_search: TextSearchFn | None = None,
    ) -> None:
        self._store: MultimodalBackend = (
            store if store is not None else MultimodalStore()
        )
        self._text_search = text_search

    # ── properties ────────────────────────────────────────────

    @property
    def space(self) -> MultimodalBackend:
        """The underlying multimodal vector space (read-only accessor)."""
        return self._store

    @property
    def degraded(self) -> bool:
        """True when the space runs on a non-semantic fallback embedder."""
        return bool(self._store.stats().get("degraded", False))

    # ── write path ────────────────────────────────────────────

    def store(
        self,
        content: str | ImageInput,
        *,
        modality: str | None = None,
        caption: str = "",
        metadata: dict[str, Any] | None = None,
        entry_id: str | None = None,
        ext: str | None = None,
    ) -> str:
        """Store one entry; returns the entry id ("" on failure).

        ``content`` is text (str) or an image (path/bytes). ``modality`` is
        inferred from the content kind when omitted.
        """
        eid = entry_id or f"mm-{uuid.uuid4().hex[:16]}"
        resolved = modality or self._infer_modality(content)
        if resolved == MODALITY_IMAGE:
            return self._store.index_image(
                eid, content, caption=caption, metadata=metadata, ext=ext,  # type: ignore[arg-type]
            )
        if not isinstance(content, str):
            raise TypeError(
                f"text modality requires str content, got {type(content).__name__}"
            )
        return self._store.index_text(eid, content, metadata=metadata)

    def store_batch(self, items: list[dict[str, Any]]) -> list[str]:
        """Store many entries (modality-grouped batch embedding)."""
        return self._store.index_many(items)

    @staticmethod
    def _infer_modality(content: Any) -> str:
        if isinstance(content, str):
            return MODALITY_TEXT
        if isinstance(content, (bytes, bytearray, Path)):
            return MODALITY_IMAGE
        raise TypeError(
            f"unsupported multimodal content type: {type(content).__name__}"
        )

    # ── read path ─────────────────────────────────────────────

    def retrieve(
        self,
        query: str | ImageInput,
        *,
        top: int = 10,
        modalities: list[str] | None = None,
        include_text_memory: bool = True,
        ext: str | None = None,
    ) -> list[dict[str, Any]]:
        """Cross-modal retrieval with RRF fusion.

        Returns dicts: ``{id, score, sources, modality, text, metadata,
        best_score}``. ``score`` is the RRF score (rank-derived — see
        :mod:`maop.core.memory.fusion`), not a cosine similarity;
        ``best_score`` keeps the per-source raw scores.

        Degradation is local: a failing text-memory leg is skipped with a
        warning, never propagated.
        """
        per_leg = max(top * 2, top + 10)
        legs: list[tuple[str, list[tuple[str, float]]]] = []
        payloads: dict[str, dict[str, Any]] = {}

        # (a) multimodal space leg
        try:
            hits = self._multimodal_leg(query, per_leg, modalities, ext)
            legs.append(("multimodal", [(h.id, h.score) for h in hits]))
            for hit in hits:
                payloads[hit.id] = {
                    "text": hit.text,
                    "metadata": hit.metadata,
                    "modality": hit.metadata.get("modality"),
                    "raw_score": hit.score,
                }
        except Exception as exc:
            logger.warning("[multimodal] space leg failed (skipped): %s", exc)

        # (b) text memory leg (only meaningful for text queries)
        if include_text_memory and isinstance(query, str) and self._text_search:
            try:
                normalized = self._normalize_text_hits(self._text_search(query, per_leg))
                if normalized:
                    legs.append(
                        ("text_memory", [(did, score) for did, score, _p in normalized])
                    )
                    for did, _score, payload in normalized:
                        payloads.setdefault(did, payload)
            except Exception as exc:
                logger.warning("[multimodal] text-memory leg failed (skipped): %s", exc)

        if not legs:
            return []

        fused = reciprocal_rank_fusion(legs, top=top)
        results: list[dict[str, Any]] = []
        for hit in fused:
            payload = payloads.get(hit.id, {})
            results.append({
                "id": hit.id,
                "score": hit.score,
                "sources": list(hit.sources),
                "modality": payload.get("modality"),
                "text": payload.get("text", ""),
                "metadata": payload.get("metadata", {}),
                "best_score": hit.payload.get("best_score", {}),
            })
        return results

    def _multimodal_leg(
        self,
        query: str | ImageInput,
        per_leg: int,
        modalities: list[str] | None,
        ext: str | None,
    ) -> list[Any]:
        wanted = set(modalities) if modalities else None
        single = modalities[0] if modalities and len(modalities) == 1 else None
        if isinstance(query, str):
            hits = self._store.search(query, top=per_leg, modality=single)
        else:
            hits = self._store.search_image(query, top=per_leg, ext=ext, modality=single)
        if wanted and len(wanted) > 1:
            # Multi-modality request: filter after search (exact result set,
            # documented recall trade-off for B1's in-memory filtering).
            hits = [h for h in hits if str(h.metadata.get("modality", "")) in wanted]
        return hits

    @staticmethod
    def _normalize_text_hits(
        raw: list[Any],
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Normalize text-memory hit shapes to ``(id, score, payload)``.

        The two facade modes return different shapes: agent-mode
        ``long_term_search`` yields dicts, chat-mode yields pydantic
        ``VectorResult`` objects (legacy ``VectorSearch``). Both are
        accepted here — dropping one silently would quietly disable the
        fusion leg.
        """
        out: list[tuple[str, float, dict[str, Any]]] = []
        seen: set[str] = set()
        for item in raw:
            if hasattr(item, "model_dump") and not isinstance(item, dict):
                try:
                    item = item.model_dump()
                except Exception:
                    item = {
                        key: getattr(item, key, None)
                        for key in ("id", "score", "text", "metadata")
                    }
            if not isinstance(item, dict):
                obj = item
                item = {
                    "id": getattr(obj, "id", None),
                    "score": getattr(obj, "score", None),
                    "text": getattr(obj, "text", None),
                    "metadata": getattr(obj, "metadata", None),
                }
            did = str(
                item.get("id") or item.get("entry_id") or item.get("doc_id") or ""
            )
            if not did or did in seen:
                continue
            seen.add(did)
            try:
                score = float(item.get("score", item.get("similarity", 0.0)) or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            text = str(
                item.get("content") or item.get("text") or item.get("summary") or ""
            )
            meta = item.get("metadata")
            out.append((
                did,
                score,
                {
                    "text": text,
                    "metadata": meta if isinstance(meta, dict) else {},
                    "modality": item.get("modality"),
                    "raw_score": score,
                    "source_layer": item.get("layer", "long_term"),
                },
            ))
        return out

    # ── stats ─────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        info = self._store.stats()
        info["text_memory_leg"] = "injected" if self._text_search else "absent"
        return info


# ── Module-level singleton (ADR-019 testability pattern) ──────

_lock = threading.Lock()
_instances: dict[str, MultimodalMemory] = {}


def _pg_backend_selected() -> bool:
    """True iff PostgreSQL is selected for the multimodal space.

    Resolution order (all through :mod:`maop.config.settings` — the
    config-drift gate counts direct environment reads):

    * ``MAOP_MULTIMODAL_BACKEND`` explicit override wins;
    * otherwise the data-plane selection (``MAOP_DB_BACKEND`` /
      ``MAOP_VECTOR_BACKEND``) decides, via the existing resolvers.
    """
    try:
        from maop.config.settings import get_settings
    except Exception:  # pragma: no cover - defensive: settings import failure
        return False
    override = get_settings().multimodal_backend.strip().lower()
    if override in ("sqlite", "pg", "postgresql"):
        return override in ("pg", "postgresql")
    try:
        from maop.core.backends.db_utils import get_db_backend
        from maop.core.vector.factory import resolve_backend_name

        return str(get_db_backend()).strip().lower() in ("pg", "postgresql") or (
            resolve_backend_name() == "pg"
        )
    except Exception:  # pragma: no cover - defensive: resolver import/read failure
        return False


def _default_pg_dsn() -> str:
    """Default PG DSN — reads ``MAOP_DATABASE_URL`` via settings."""
    try:
        from maop.config.settings import get_settings

        return get_settings().database_url or (
            "postgresql+psycopg2://localhost:5432/maop"
        )
    except Exception:  # pragma: no cover - defensive
        return "postgresql+psycopg2://localhost:5432/maop"


def _build_store(
    data_dir: Path, dsn: str | None
) -> MultimodalBackend:
    """Construct the backend matching the environment (SQLite default)."""
    if dsn is None and _pg_backend_selected():
        from maop.core.memory.multimodal_pg import PgMultimodalBackend

        return PgMultimodalBackend()
    return MultimodalStore(db_path=data_dir / "multimodal.db")


def get_multimodal_memory(
    *,
    text_search: TextSearchFn | None = None,
    data_dir: Path | str | None = None,
    dsn: str | None = None,
) -> MultimodalMemory:
    """Shared :class:`MultimodalMemory`, keyed by resolved backend identity.

    Keys are ``sqlite:<data_dir>`` or ``pg:<dsn>`` so a SQLite instance and a
    PG instance in the same process cannot collide (ADR-019 single-instance
    discipline — the previous data-dir-only key silently shared state).

    The text-memory adapter is (re)bound on every call so a facade that
    changed mode/impl never keeps a stale leg. Tests reset with
    :func:`reset_multimodal_memory`.
    """
    resolved = Path(data_dir) if data_dir is not None else resolve_data_dir()
    backend_pg = dsn is not None or _pg_backend_selected()
    key = f"pg:{dsn or _default_pg_dsn()}" if backend_pg else f"sqlite:{resolved}"
    with _lock:
        instance = _instances.get(key)
        if instance is None:
            store = _build_store(resolved, dsn)
            instance = MultimodalMemory(store=store, text_search=text_search)
            _instances[key] = instance
        else:
            instance._text_search = text_search  # rebind per call (see docstring)
        return instance


def reset_multimodal_memory() -> None:
    """Drop cached instances (test isolation)."""
    with _lock:
        _instances.clear()
