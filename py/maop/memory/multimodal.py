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

import hashlib
import logging
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import resolve_data_dir
from maop.core.memory.fusion import reciprocal_rank_fusion
from maop.core.memory.multimodal_backend import MultimodalBackend
from maop.core.memory.multimodal_embed import ImageInput, MultimodalEmbeddingProvider
from maop.core.memory.multimodal_ingest import MultimodalIngestQueue
from maop.core.memory.multimodal_ingest_cp import CrossProcessIngestQueue
from maop.core.memory.multimodal_store import (
    MODALITY_IMAGE,
    MODALITY_TEXT,
    MultimodalStore,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MultimodalMemory",
    "auto_index_enabled",
    "drain_multimodal_ingest",
    "get_multimodal_memory",
    "get_multimodal_memory_if_exists",
    "maybe_index_uploaded_image",
    "reset_multimodal_memory",
    "uploaded_image_entry_id",
    "warm_multimodal_space",
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
        # 具体类型取决于 multimodal_ingest_mode（inprocess | crossprocess），
        # 两者共享 submit/drain/close/start/pending/stats/closed 接口。
        self._queue: MultimodalIngestQueue | CrossProcessIngestQueue | None = None
        self._queue_closed = False

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
        """Store many entries (modality-grouped batch embedding).

        Items may target any modality the space accepts: an item with an
        explicit ``modality`` other than text/image (and its own ``vector``)
        goes through the generic path (ADR-022 D11) instead of being
        re-interpreted as text or an image.
        """
        return self._store.index_many(items)

    # ── async ingest (M2.2 B2 PR-3, ADR-022 D4) ─────────────────

    @property
    def ingest_queue(self):
        """Background batcher for non-blocking ingestion (lazily started).

        The concrete class depends on configuration (``multimodal_ingest_mode``):
        the in-process queue by default, the cross-process ``MessageQueue``
        transport when enabled. Both expose the same submit/drain/close API.
        """
        if self._queue is None:
            from maop.core.memory.multimodal_ingest_cp import create_ingest_queue

            self._queue = create_ingest_queue(self._store)
        return self._queue

    def store_async(
        self,
        content: str | ImageInput,
        *,
        modality: str | None = None,
        caption: str = "",
        metadata: dict[str, Any] | None = None,
        entry_id: str | None = None,
        ext: str | None = None,
    ) -> str:
        """Enqueue one entry; returns its id immediately (no embedding wait).

        This is the latency-budget path: a single image embed costs 65-293 ms
        (ADR-022 D4) while the background batch amortises it to ~42 ms/image.
        The entry is not searchable until the queue flushes (`drain()` or the
        flush interval elapses).

        Returns the entry id — **also when the queue is already closed**, in
        which case the entry is written synchronously (the queue's own
        "degrade to sync on shutdown" promise). Returning ``""`` would make a
        closed queue a silent drop, and callers could not tell success from
        loss.
        """
        eid = entry_id or f"mm-{uuid.uuid4().hex[:16]}"
        item: dict[str, Any] = {"id": eid}
        resolved = modality or self._infer_modality(content)
        if resolved == MODALITY_IMAGE:
            item["image"] = content
            item["modality"] = MODALITY_IMAGE
            item["caption"] = caption
            if ext is not None:
                item["ext"] = ext
        else:
            if not isinstance(content, str):
                raise TypeError(
                    f"text modality requires str content, got {type(content).__name__}"
                )
            item["text"] = content
            item["modality"] = MODALITY_TEXT
        if metadata:
            item["metadata"] = dict(metadata)
        queued = "" if self._queue_closed else self.ingest_queue.submit(item)
        if queued:
            return queued
        # Queue closed (shutdown in progress): write synchronously instead of
        # dropping the entry. The id is returned so the caller can tell.
        logger.warning(
            "[multimodal] ingest queue closed — storing %s synchronously", eid
        )
        stored = self.store(
            content,
            modality=resolved,
            caption=caption,
            metadata=metadata,
            entry_id=eid,
            ext=ext,
        )
        return stored or eid

    def drain_ingest(self, timeout: float = 5.0) -> int:
        """Flush queued ingest entries now; returns the number written.

        Note this only sees items still *in the queue*; entries a running
        worker already pulled into its batch need :meth:`close_ingest`.
        """
        if self._queue is None or self._queue_closed:
            return 0
        return self.ingest_queue.drain(timeout=timeout)

    def close_ingest(self, *, timeout: float = 5.0) -> int:
        """Close the background batcher, flushing what is still pending.

        Unlike :meth:`drain_ingest` this also covers items the worker already
        took off the queue into a partial batch (``drain`` cannot see those) —
        without it every graceful shutdown loses up to ``flush_interval_s``
        of submissions. Returns the number of entries flushed. Idempotent.
        """
        self._queue_closed = True
        if self._queue is None:
            # Never opened: nothing to flush. The flag still sticks, so every
            # later submission takes the synchronous path — closing a queue
            # that was never started must not leave a fresh one to be created.
            return 0
        # Keep the (now closed) queue around: the ingest_queue property hands
        # it back out, submit() refuses, and store_async degrades to a
        # synchronous write — instead of resurrecting a fresh worker thread.
        return self._queue.close(drain=True, timeout=timeout)

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
    data_dir: Path,
    dsn: str | None,
    embedding: MultimodalEmbeddingProvider | None = None,
    space: str | None = None,
) -> MultimodalBackend:
    """Construct the backend for one registered space (ADR-022 D11).

    The space registry is the single construction path: the default space
    resolves to the same table/dimension/modalities the store hard-coded
    before B3, and any other space (enterprise audio, a second CLIP
    dimension) is a declaration rather than a core edit.

    The embedding provider comes from the factory unless the caller injects
    one (tests, or an operator wiring a different model).
    """
    from maop.core.memory.multimodal_embed import HashModalityEmbedding, resolve_multimodal_provider
    from maop.core.memory.multimodal_spaces import get_multimodal_space

    spec = get_multimodal_space(space)
    if embedding is not None:
        provider = embedding
    else:
        provider = resolve_multimodal_provider()
        if provider.dimension != spec.dimension:
            # The factory provider serves the default CLIP width. A space with
            # a different dimension (enterprise audio, a second CLIP size)
            # degrades to a deterministic provider of the right width instead
            # of failing — stats()["degraded"] tells the truth either way, and
            # the enterprise edition injects its own provider when it has one.
            logger.warning(
                "[multimodal] space %r wants %d-dim vectors but the resolved "
                "provider produces %d; using a deterministic fallback of the "
                "right width (degraded, NOT semantic)",
                spec.name, spec.dimension, provider.dimension,
            )
            provider = HashModalityEmbedding(dim=spec.dimension)
    wants_pg = spec.backend == "pg" or (
        spec.backend == "auto" and (dsn is not None or _pg_backend_selected())
    )
    if wants_pg:
        from maop.core.memory.multimodal_pg import PgMultimodalBackend

        return PgMultimodalBackend(dsn=dsn, embedding=provider, space=spec)
    return MultimodalStore(
        db_path=data_dir / "multimodal.db", embedding=provider, space=spec
    )


def get_multimodal_memory(
    *,
    text_search: TextSearchFn | None = None,
    data_dir: Path | str | None = None,
    dsn: str | None = None,
    embedding: MultimodalEmbeddingProvider | None = None,
    space: str | None = None,
) -> MultimodalMemory:
    """Shared :class:`MultimodalMemory`, keyed by backend **and** space.

    Keys are ``sqlite:<data_dir>:<space>`` / ``pg:<dsn>:<space>`` so neither
    two backends nor two spaces in the same process can collide (ADR-019
    single-instance discipline; B3 added the space component).

    The text-memory adapter is (re)bound on every call so a facade that
    changed mode/impl never keeps a stale leg — but only when one is passed:
    binding ``None`` would silently drop the RRF text leg for every other
    caller of the shared instance (the upload path has no text adapter to
    give). Tests reset with :func:`reset_multimodal_memory`.
    """
    resolved = Path(data_dir) if data_dir is not None else resolve_data_dir()
    backend_pg = dsn is not None or _pg_backend_selected()
    from maop.core.memory.multimodal_spaces import get_multimodal_space

    space_name = get_multimodal_space(space).name
    key = (
        f"pg:{dsn or _default_pg_dsn()}:{space_name}"
        if backend_pg
        else f"sqlite:{resolved}:{space_name}"
    )
    with _lock:
        instance = _instances.get(key)
        if instance is None:
            store = _build_store(resolved, dsn, embedding, space=space_name)
            instance = MultimodalMemory(store=store, text_search=text_search)
            _instances[key] = instance
        elif text_search is not None:
            instance._text_search = text_search  # rebind per call (see docstring)
        return instance


def get_multimodal_memory_if_exists() -> list[MultimodalMemory]:
    """All existing shared instances — never creates one.

    The shutdown path needs this: calling :func:`get_multimodal_memory` there
    would build a store (and with a CLIP provider load ~600 MB of weights) in
    a process that never used the multimodal space. Returns a **list** since
    B3: several spaces can be live in one process, and every one of them owns
    an ingest queue that must be flushed.
    """
    with _lock:
        return list(_instances.values())


def reset_multimodal_memory() -> None:
    """Drop cached instances and their queues (test isolation).

    Closing the ingest queues matters: each one owns a daemon thread, and
    leaving them alive across tests is the ADR-019 singleton-poisoning
    pattern — a background thread keeps embedding into a store whose data
    dir the next test has already swapped out.
    """
    from maop.core.memory.multimodal_embed import reset_multimodal_provider

    with _lock:
        instances = list(_instances.values())
        _instances.clear()
    for instance in instances:
        try:
            instance.close_ingest(timeout=1.0)
        except Exception as exc:
            # Teardown must not raise, but the failure is logged: a queue
            # thread that survived a test is exactly the ADR-019 poisoning
            # this reset exists to prevent.
            logger.debug("[multimodal] ingest close during reset failed: %s", exc)
    reset_multimodal_provider()


# ── 上传自动索引（M2.2 B2 收尾，2026-10-14）──────────────────────
# The real arrival event for an image in a running MAOP is a chat upload:
# routers/chat.py → services/chat_service.save_image → ImageStore.save (disk +
# SQLite metadata). Wiring the async ingest queue *here* is what makes the
# ADR-022 D4 latency budget real in production: the upload request returns
# while the (expensive) embedding happens in the background batch.


def _auto_index_mode() -> str:
    """Normalise ``MAOP_MULTIMODAL_AUTO_INDEX`` to auto/on/off.

    Closed-set parsing with a warning on typos — an unknown value silently
    meaning "on" would index (and write ~2 KB/entry of) vectors for users who
    thought they had turned it off.
    """
    raw = _settings_value("multimodal_auto_index").strip().lower()
    if raw in ("", "auto"):
        return "auto"
    if raw in ("1", "true", "on", "yes"):
        return "on"
    if raw in ("0", "false", "off", "no"):
        return "off"
    logger.warning(
        "[multimodal] unknown MAOP_MULTIMODAL_AUTO_INDEX=%r, treating as 'auto'",
        raw,
    )
    return "auto"


def auto_index_enabled() -> bool:
    """Whether uploads should be indexed into the multimodal space.

    ``auto`` follows the *resolved* provider: on when it is semantic (real
    CLIP), off on the hash fallback — an extra-less install keeps its exact
    previous behaviour instead of writing non-semantic vectors.
    """
    mode = _auto_index_mode()
    if mode == "off":
        return False
    if mode == "auto" and not _background_tasks_enabled():
        # Multi-worker mode (server main() sets MAOP_BACKGROUND_TASKS=0 when
        # workers>1): the ingest queue is per-process, so N workers would each
        # load their own CLIP copy and serialise on the shared store. Auto
        # steps aside — loudly, not silently; an explicit 'on' still wins.
        _warn_once(
            "[multimodal] auto-index skipped: MAOP_BACKGROUND_TASKS=0 "
            "(multi-worker deployment). Set MAOP_MULTIMODAL_AUTO_INDEX=1 to "
            "force it (every worker then holds its own ingest queue)."
        )
        return False
    if mode == "on":
        return True
    try:
        from maop.core.memory.multimodal_embed import resolve_multimodal_provider

        return bool(resolve_multimodal_provider().is_semantic)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[multimodal] auto-index decision failed (%s); off", exc)
        return False


def _background_tasks_enabled() -> bool:
    """MAOP_BACKGROUND_TASKS via settings (0 in multi-worker deployments)."""
    return bool(_settings_value("background_tasks"))


_WARNED_BG_DISABLED = False


def _warn_once(message: str) -> None:
    """Log a warning at most once per process (upload paths are hot)."""
    global _WARNED_BG_DISABLED
    if not _WARNED_BG_DISABLED:
        _WARNED_BG_DISABLED = True
        logger.warning(message)


def uploaded_image_entry_id(checksum: str) -> str:
    """Deterministic multimodal entry id for an uploaded image.

    Content-derived, not upload-derived: re-uploading the same file updates
    the same entry (upsert) instead of appending a duplicate that would show
    up twice in retrieval results.
    """
    return f"img-{checksum[:16]}"


def maybe_index_uploaded_image(
    maop_root: Path | str,
    image_id: str,
    *,
    session_id: str = "",
    filename: str = "",
    image_path: Path | str | None = None,
) -> str:
    """Index one uploaded image into the multimodal space (async, fail-soft).

    Returns the entry id when the image was queued/written, ``""`` when
    auto-index is off or the image could not be read. Never raises: the
    upload is the primary path and must not fail because memory indexing did.
    """
    if not image_id or not auto_index_enabled():
        return ""
    try:
        path = Path(image_path) if image_path is not None else _upload_path(maop_root, image_id)
        if path is None or not path.is_file():
            logger.warning(
                "[multimodal] uploaded image %s not found on disk; not indexed", image_id
            )
            return ""
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        memory = get_multimodal_memory()
        return memory.store_async(
            path,
            metadata={
                "source": "chat-upload",
                "session_id": session_id,
                "image_id": image_id,
                "filename": filename,
                "checksum": checksum,
            },
            entry_id=uploaded_image_entry_id(checksum),
        )
    except Exception as exc:
        logger.warning("[multimodal] auto-index of image %s failed: %s", image_id, exc)
        return ""


def _upload_path(maop_root: Path | str, image_id: str) -> Path | None:
    """Resolve an upload id back to its file via the image store."""
    from maop.core.backends.image_store import ImageStore

    return Path(ImageStore(root_dir=str(maop_root)).get_path(image_id) or "")


def _settings_value(field: str) -> str:
    """Read a settings field by name (empty when settings are unavailable)."""
    try:
        from maop.config.settings import get_settings

        return str(getattr(get_settings(), field, "") or "")
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[multimodal] settings read of %s failed: %s", field, exc)
        return ""


def drain_multimodal_ingest(timeout: float = 3.0) -> int:
    """Flush and close the ingest queue at shutdown.

    Uses the non-creating accessor so a process that never used the
    multimodal space pays nothing. Returns the number of flushed entries;
    every failure is swallowed (shutdown must not fail) but counted in logs.
    """
    memories = get_multimodal_memory_if_exists()
    if not memories:
        return 0
    flushed = 0
    for memory in memories:
        try:
            flushed += memory.close_ingest(timeout=timeout)
        except Exception as exc:
            logger.warning("[multimodal] shutdown ingest drain failed: %s", exc)
    if flushed:
        logger.info("[multimodal] shutdown flushed %d queued entr(ies)", flushed)
    return flushed


def warm_multimodal_space() -> str:
    """Resolve (and cache) the provider before any request can hit it.

    Returns a short human-readable description for the startup log. The first
    image upload would otherwise pay the ~600 MB CLIP load inline.
    """
    from maop.core.memory.multimodal_embed import resolve_multimodal_provider

    try:
        provider = resolve_multimodal_provider()
    except Exception as exc:
        logger.error("[multimodal] provider warm-up failed: %s", exc)
        return f"unavailable ({exc})"
    return f"{provider.provider_id} (semantic={provider.is_semantic}), auto_index={auto_index_enabled()}"
