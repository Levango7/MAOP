"""PostgreSQL + pgvector backend for the multimodal memory space (M2.2 B2).

Stores CLIP text+image vectors in a dedicated ``multimodal_entries`` table —
**independent** of the legacy ``vector_entries`` table used by
``PgVectorBackend``. The split is deliberate (ADR-022 D2): ``vector_entries``
is a single ``vector(N)`` column whose dimension is set by an
ALTER-on-init, so a 512-dim CLIP vector cannot coexist there with the
384-dim MiniLM / 1536-dim OpenAI text embeddings (an ``ALTER … USING`` cast
with existing rows of the other dimension fails outright — verified locally).

Implements the :class:`~maop.core.memory.multimodal_backend.MultimodalBackend`
protocol, so the service layer and facade work unchanged on either backend.

Schema & indexes
----------------
The DDL comes from :mod:`maop.core.memory.multimodal_schema` (single source
of truth, parity-tested against the SQLite backend). Highlights:

* column ``vector vector(512)`` — the column may share its type's name;
* ``metadata JSONB`` carries the ``modality`` discriminator (same write
  shape as SQLite — no dual-source truth);
* per-modality **partial** HNSW indexes
  ``WHERE (metadata->>'modality') = 'image'`` (needs pgvector ≥ 0.5.0).

Batching
--------
``index_many`` writes all rows in **one transaction** (upserts via
``ON CONFLICT``), so a half-batch failure cannot leave partial data — the
same discipline ``PgVectorBackend.insert_batch`` uses.

Not included (documented scope): cross-process persistence, embedding
pipeline threads, dashboard listing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from maop.core.memory.multimodal_embed import (
    MULTIMODAL_DIM,
    HashModalityEmbedding,
    ImageInput,
    MultimodalEmbeddingProvider,
    validate_image_input,
)
from maop.core.memory.multimodal_schema import MM_TABLE, pg_ddl
from maop.core.memory.vector_embed import VectorSearchResult

logger = logging.getLogger(__name__)

__all__ = ["PgMultimodalBackend"]


# ``{MM_TABLE}`` is a module constant and every *value* is bound; the two
# f-strings below interpolate only the table name and a fixed clause, so the
# resulting SQL text has no caller-controlled fragment.
# The table name is inlined as a literal (MM_TABLE == "multimodal_entries");
# a test in tests/test_multimodal_pg_backend.py pins this against the spec so
# a spec rename can never leave the two disagreeing. Every *value* is bound.
_UPSERT_SQL = """
    INSERT INTO multimodal_entries (id, text, vector, metadata, created_at)
    VALUES (:id, :text, (:vec)::vector, CAST(:meta AS jsonb), :ts)
    ON CONFLICT (id) DO UPDATE SET
      text = EXCLUDED.text,
      vector = EXCLUDED.vector,
      metadata = EXCLUDED.metadata,
      created_at = EXCLUDED.created_at
""".strip()

# ``{where}`` is a fixed clause ("WHERE (metadata->>'modality') = :mod") or "".
_SEARCH_SQL = """
    SELECT id, text, metadata,
           1 - (vector <=> (:q)::vector) AS score
    FROM multimodal_entries
    {where}
    ORDER BY vector <=> (:q)::vector
    LIMIT :top
""".strip()


def _default_pg_dsn() -> str:
    """Default PG DSN from settings (``MAOP_DATABASE_URL``) / local default.

    Mirrors ``PgVectorBackend._default_pg_url`` so both backends resolve the
    same database. The read goes through :mod:`maop.config.settings` rather
    than the process environment directly, because the config-drift gate
    only allows env access via settings fields.
    """
    try:
        from maop.config.settings import get_settings

        return get_settings().database_url or (
            "postgresql+psycopg2://localhost:5432/maop"
        )
    except Exception:  # pragma: no cover - defensive: settings import failure
        return "postgresql+psycopg2://localhost:5432/maop"


class PgMultimodalBackend:
    """pgvector-backed multimodal space implementing ``MultimodalBackend``."""

    name = "pg-multimodal"

    def __init__(
        self,
        dsn: str | None = None,
        *,
        engine: Engine | None = None,
        embedding: MultimodalEmbeddingProvider | None = None,
        ensure_schema: bool = True,
    ) -> None:
        if engine is not None:
            self._engine: Engine = engine
            self._owns_engine = False
        else:
            from maop.db_url import resolve_pg_driver

            self._engine = create_engine(
                resolve_pg_driver(dsn or _default_pg_dsn()),
                echo=False,
                future=True,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
            )
            self._owns_engine = True
        self._embedding: MultimodalEmbeddingProvider = embedding or HashModalityEmbedding()
        if ensure_schema:
            self.ensure_schema()

    # ── schema ─────────────────────────────────────────────────

    def ensure_schema(self) -> None:
        """Create the table + indexes if missing (idempotent).

        The ``vector`` extension is normally created by the alembic env
        (``migrations/pg/env.py:_enable_extensions``); we still attempt it
        here so a directly-instantiated backend works on a fresh database.
        """
        with self._engine.begin() as conn:
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector"'))
            for statement in pg_ddl().split(";"):
                stmt = statement.strip()
                if stmt:
                    conn.execute(text(stmt + ";"))

    # ── write ──────────────────────────────────────────────────

    def index_text(
        self,
        entry_id: str,
        content: str,
        *,
        vector: list[float] | None = None,
        metadata: dict | None = None,
    ) -> str:
        return self._write_one(entry_id, content, vector, metadata, "text")

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
        meta = dict(metadata or {})
        data, resolved_ext = validate_image_input(image, ext=ext)
        meta.setdefault("source_ext", resolved_ext)
        meta.setdefault("source_bytes", len(data))
        meta.setdefault("source_sha256", hashlib.sha256(data).hexdigest())
        if isinstance(image, (str, Path)):
            meta.setdefault("source_ref", str(image))
        return self._write_one(entry_id, caption, vector, meta, "image")

    def _write_one(
        self,
        entry_id: str,
        text_: str,
        vector: list[float] | None,
        metadata: dict | None,
        modality: str,
    ) -> str:
        if not entry_id:
            logger.warning("[multimodal-pg] empty entry_id, refusing write")
            return ""
        if vector is None:
            vector = self._embedding.embed(text_)
        if len(vector) != MULTIMODAL_DIM:
            logger.warning(
                "[multimodal-pg] refusing vector with dim %d (space expects %d) for %s",
                len(vector), MULTIMODAL_DIM, entry_id,
            )
            return ""
        meta = dict(metadata or {})
        meta["modality"] = modality
        stored = self._upsert_rows([(entry_id, text_, vector, meta)])
        return stored[0] if stored else ""

    def index_many(self, items: list[dict]) -> list[str]:
        """Upsert many entries in one batched encoding pass per modality.

        Returns the stored entry ids; items that fail validation or embed
        are skipped with a warning (matching the SQLite backend's contract).
        """
        if not items:
            return []
        text_items: list[dict] = []
        image_items: list[dict] = []
        for item in items:
            if not item.get("id"):
                logger.warning("[multimodal-pg] batch item missing id, skipped")
                continue
            if item.get("image") is not None or item.get("modality") == "image":
                image_items.append(item)
            else:
                text_items.append(item)

        rows: list[tuple[str, str, list[float], dict]] = []

        pending_text = [it for it in text_items if it.get("vector") is None]
        if pending_text:
            text_vectors = self._embedding.embed_batch(
                [str(it.get("text", "")) for it in pending_text]
            )
            for it, vec in zip(pending_text, text_vectors):
                it["vector"] = vec
        for it in text_items:
            row_vec = it.get("vector")
            if not row_vec:
                continue
            meta = dict(it.get("metadata") or {})
            meta["modality"] = "text"
            rows.append((str(it["id"]), str(it.get("text", "")), row_vec, meta))

        pending_img = [it for it in image_items if it.get("vector") is None]
        if pending_img:
            try:
                img_vectors = self._embedding.embed_image_batch(
                    [it["image"] for it in pending_img]
                )
                for it, vec in zip(pending_img, img_vectors):
                    it["vector"] = vec
            except Exception as exc:
                # Per-item degradation (same contract as the SQLite backend):
                # one unreadable image must not drop its batch-mates.
                logger.warning(
                    "[multimodal-pg] image batch embed failed (%d item(s)); "
                    "retrying per item: %s",
                    len(pending_img), exc,
                )
                for it in pending_img:
                    try:
                        it["vector"] = self._embedding.embed_image(it["image"])
                    except Exception as item_exc:
                        logger.warning(
                            "[multimodal-pg] image %s skipped (embed failed): %s",
                            it.get("id"), item_exc,
                        )
        for it in image_items:
            row_vec = it.get("vector")
            if not row_vec:
                continue
            meta = dict(it.get("metadata") or {})
            meta["modality"] = "image"
            try:
                data, resolved_ext = validate_image_input(it["image"], ext=it.get("ext"))
                meta.setdefault("source_ext", resolved_ext)
                meta.setdefault("source_bytes", len(data))
                meta.setdefault("source_sha256", hashlib.sha256(data).hexdigest())
            except Exception as exc:
                logger.warning("[multimodal-pg] image validation %s: %s", it["id"], exc)
                continue
            rows.append((str(it["id"]), str(it.get("caption", "")), row_vec, meta))

        return self._upsert_rows(rows)

    def _upsert_rows(
        self, rows: list[tuple[str, str, list[float], dict]]
    ) -> list[str]:
        """Single-transaction upsert — a half-batch failure leaves no trace."""
        if not rows:
            return []
        stored: list[str] = []
        sql = text(_UPSERT_SQL)
        now = time.time()
        with self._engine.begin() as conn:
            for entry_id, text_, vector, meta in rows:
                conn.execute(
                    sql,
                    {
                        "id": entry_id,
                        "text": text_,
                        "vec": _vector_literal(vector),
                        "meta": json.dumps(meta),
                        "ts": now,
                    },
                )
                stored.append(entry_id)
        return stored

    # ── read ─────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        top: int = 10,
        threshold: float = 0.0,
        modality: str | None = None,
    ) -> list[VectorSearchResult]:
        return self.search_vector(
            self._embedding.embed(query), top=top, threshold=threshold, modality=modality,
        )

    def search_image(
        self,
        image: ImageInput,
        *,
        top: int = 10,
        threshold: float = 0.0,
        ext: str | None = None,
        modality: str | None = "image",
    ) -> list[VectorSearchResult]:
        query_vector = self._embedding.embed_image(image, ext=ext)
        return self.search_vector(query_vector, top=top, threshold=threshold,
                                  modality=modality)

    def search_vector(
        self,
        query_vector: list[float],
        *,
        top: int = 10,
        threshold: float = 0.0,
        modality: str | None = None,
    ) -> list[VectorSearchResult]:
        if not query_vector:
            return []
        return self._search_filtered(query_vector, top, threshold, modality)

    def _search_filtered(
        self,
        query_vector: list[float],
        top: int,
        threshold: float,
        modality: str | None,
    ) -> list[VectorSearchResult]:
        """Ranked search over the space.

        The space ships *exact* cosine ordering (no ANN index — see
        ``multimodal_schema.pg_ddl`` for why partial HNSW was rejected), so
        results are deterministic: identical vectors first, then by cosine
        similarity.
        """
        where = "" if modality is None else "WHERE (metadata->>'modality') = :mod"
        sql = text(_SEARCH_SQL.replace("{where}", where))
        params: dict[str, Any] = {"q": _vector_literal(query_vector), "top": max(0, int(top))}
        if modality is not None:
            params["mod"] = modality
        results: list[VectorSearchResult] = []
        with self._engine.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        for row in rows:
            score = max(0.0, float(row[3]))
            if score < threshold:
                continue
            meta = row[2] if isinstance(row[2], dict) else json.loads(row[2] or "{}")
            results.append(
                VectorSearchResult(id=str(row[0]), text=str(row[1] or ""),
                                   score=score, metadata=meta)
            )
        return results

    # ── bookkeeping ──────────────────────────────────────────────

    def modality_of(self, entry_id: str) -> str:
        sql = text(f"SELECT metadata->>'modality' FROM {MM_TABLE} WHERE id = :id")  # nosec B608 — 表名为模块常量，值全部绑定参数
        with self._engine.connect() as conn:
            row = conn.execute(sql, {"id": entry_id}).fetchone()
        return str(row[0]) if row and row[0] else ""

    def delete(self, entry_id: str) -> bool:
        # RETURNING makes "was a row removed" deterministic — SQLAlchemy's
        # rowcount is not reliable across dialects/driver result states.
        sql = text(f"DELETE FROM {MM_TABLE} WHERE id = :id RETURNING id")  # nosec B608 — 表名为模块常量，值绑定
        with self._engine.begin() as conn:
            row = conn.execute(sql, {"id": entry_id}).fetchone()
        return row is not None

    def clear(self) -> int:
        """Delete every entry (not part of the protocol; admin utility)."""
        # COUNT-then-DELETE keeps the return honest (rowcount is unreliable).
        total = self.count()
        if total:
            with self._engine.begin() as conn:
                conn.execute(text(f"DELETE FROM {MM_TABLE}"))  # nosec B608 — 常量表名
        return total

    def count(self) -> int:
        sql = text(f"SELECT COUNT(*) AS c FROM {MM_TABLE}")  # nosec B608 — 表名为模块常量，值全部绑定参数
        with self._engine.connect() as conn:
            row = conn.execute(sql).fetchone()
        return int(row[0]) if row else 0

    def stats(self) -> dict[str, Any]:
        """Same key set as the SQLite backend (+ ``backend: "pg"``)."""
        try:
            sql = text(
                f"SELECT COALESCE(metadata->>'modality', 'unknown') AS m, COUNT(*) AS c "
                f"FROM {MM_TABLE} GROUP BY m"  # nosec B608 — 表名为模块常量，值全部绑定参数
            )
            with self._engine.connect() as conn:
                rows = conn.execute(sql).fetchall()
            modalities = {str(r[0]): int(r[1]) for r in rows}
        except Exception as exc:
            logger.debug("[multimodal-pg] SQL stats failed: %s", exc)
            modalities = {}
        provider = self._embedding
        return {
            "space": f"multimodal-{MULTIMODAL_DIM}",
            "table": MM_TABLE,
            "backend": "pg",
            "dimension": MULTIMODAL_DIM,
            "total": sum(modalities.values()) or self.count(),
            "modalities": modalities,
            "embedding_model": getattr(provider, "model_id", "unknown"),
            "embedding_provider": getattr(provider, "provider_id", "unknown"),
            "degraded": not getattr(provider, "is_semantic", False),
        }

    # ── compatibility helpers (declared optional in the protocol) ─

    def index(self, entry_id: str, text_: str, *, vector=None, metadata=None) -> str:
        """Legacy ``VectorStore.index`` shim → text path (LSP-friendly)."""
        return self.index_text(entry_id, text_, vector=vector, metadata=metadata)

    def index_batch(self, entries: list[dict]) -> int:
        """Legacy int-counting shim over :meth:`index_many`."""
        return len(self.index_many(entries))


def _vector_literal(vec: list[float]) -> str:
    """Render a Python list as a pgvector string literal ``'[v1,v2,...]'``."""
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"
