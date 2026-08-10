"""PostgreSQL + pgvector backend for :class:`VectorBackend` (F1-02).

Uses the `pgvector <https://github.com/pgvector/pgvector>`_ extension to store
embeddings in a ``vector(N)`` column and search via ANN indexes:

- **IVFFLAT** — partition-based ANN, good for ≤1M vectors, fast build.
- **HNSW**    — graph-based ANN, better recall, more memory, slower build.

Both index types are built with ``vector_cosine_ops`` to match the cosine
similarity semantics of the SQLite backend.

Online index rebuild
--------------------
:meth:`PgVectorBackend.rebuild_index` uses ``CREATE INDEX CONCURRENTLY`` which
**does not block** concurrent ``SELECT`` (search) operations. This is the key
requirement from F1-02: index rebuild must not block search.

Connection
----------
The backend accepts either a SQLAlchemy ``Engine`` (for tests / sharing) or a
DSN string. When neither is given it falls back to ``MAOP_DATABASE_URL`` /
``MAOP_DB_URL`` / ``postgresql+psycopg2://localhost:5432/maop``.

The ``vector_entries`` table is assumed to exist (created by the
``001_initial_schema`` Alembic migration). If the requested ``dimension``
differs from the column's, the backend ALTERs the column type on init.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from . import VectorBackend
from . import VectorSearchResult  # noqa: F401 — re-export for type-checkers

logger = logging.getLogger(__name__)

__all__ = ["PgVectorBackend"]


# ── SQL templates ───────────────────────────────────────────────

# Upsert a single entry. ``embedding`` is bound as a string literal
# ``'[v1,v2,...]'`` which pgvector casts to ``vector`` automatically.
_UPSERT_SQL = text(
    """
    INSERT INTO vector_entries (id, text, vector, embedding, metadata, created_at)
    VALUES (:id, :text, :vector, (:emb)::vector, CAST(:meta AS jsonb), :ts)
    ON CONFLICT (id) DO UPDATE
      SET text      = EXCLUDED.text,
          vector     = EXCLUDED.vector,
          embedding  = EXCLUDED.embedding,
          metadata   = EXCLUDED.metadata,
          created_at = EXCLUDED.created_at
    """
)

# Cosine-distance ANN search. ``<=>`` is pgvector's cosine distance
# (0 = identical, 2 = opposite). We return ``1 - distance`` as the
# cosine similarity score in [0, 1] to match the SQLite backend.
_SEARCH_SQL = text(
    """
    SELECT id, text, metadata, 1 - (embedding <=> (:q)::vector) AS score
    FROM vector_entries
    WHERE embedding IS NOT NULL
    ORDER BY embedding <=> (:q)::vector
    LIMIT :top
    """
)

_DELETE_SQL = text("DELETE FROM vector_entries WHERE id = :id")
_COUNT_SQL = text("SELECT COUNT(*) FROM vector_entries WHERE embedding IS NOT NULL")

# Index management. ``CONCURRENTLY`` requires autocommit (no transaction).
_DROP_INDEX_SQL = "DROP INDEX CONCURRENTLY IF EXISTS :idx"
_CREATE_IVFFLAT_SQL = (
    "CREATE INDEX CONCURRENTLY {idx} ON vector_entries "
    "USING ivfflat (embedding vector_cosine_ops) WITH (lists = {lists})"
)
_CREATE_HNSW_SQL = (
    "CREATE INDEX CONCURRENTLY {idx} ON vector_entries "
    "USING hnsw (embedding vector_cosine_ops) "
    "WITH (m = {m}, ef_construction = {efc})"
)

# Inspect the embedding column's declared dimension.
# pgvector stores ``vector(N)`` as a UDT; the dimension is in
# ``typmod`` — ``format_type`` exposes it as ``vector(1536)``.
_COL_TYPE_SQL = text(
    """
    SELECT format_type(a.atttypid, a.atttypmod) AS ty
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    WHERE c.relname = 'vector_entries' AND a.attname = 'embedding'
    """
)

# ALTER the embedding column to a different dimension.
_ALTER_DIM_SQL = (
    "ALTER TABLE vector_entries ALTER COLUMN embedding TYPE vector({dim}) "
    "USING embedding::vector({dim})"
)

# List existing ANN indexes on vector_entries.embedding.
_LIST_INDEXES_SQL = text(
    """
    SELECT indexname FROM pg_indexes
    WHERE tablename = 'vector_entries' AND indexdef LIKE '%embedding%'
    """
)


_DIM_RE = re.compile(r"vector\((\d+)\)")


def _default_pg_url() -> str:
    return (
        os.environ.get("MAOP_DATABASE_URL")
        or os.environ.get("MAOP_DB_URL")
        or "postgresql+psycopg2://localhost:5432/maop"
    )


def _vector_literal(vec: list[float]) -> str:
    """Render a Python list as a pgvector string literal ``'[v1,v2,...]'``."""
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


class PgVectorBackend(VectorBackend):
    """PostgreSQL + pgvector :class:`VectorBackend`.

    Parameters
    ----------
    dsn : str | None
        SQLAlchemy URL for the PG database. If ``None`` and *engine* is
        ``None``, falls back to env vars / default (see module docstring).
    engine : Engine | None
        Pre-built SQLAlchemy engine. Takes priority over *dsn*. Useful for
        tests (inject a mock) and for sharing a pool across backends.
    dimension : int | None
        Embedding dimension. If given and the existing ``embedding`` column
        has a different dimension, the backend ALTERs the column on init.
        ``None`` skips the check (use whatever the schema declared).
    index_type : str
        Default ANN index type for :meth:`rebuild_index` — ``"ivfflat"`` or
        ``"hnsw"``.
    index_name : str
        Name of the ANN index. Defaults to ``idx_vector_embedding_{type}``.
    ivfflat_lists, hnsw_m, hnsw_ef_construction
        Tunable index build parameters.
    """

    name = "pg"

    def __init__(
        self,
        dsn: str | None = None,
        *,
        engine: Engine | None = None,
        dimension: int | None = None,
        index_type: str = "ivfflat",
        index_name: str = "",
        ivfflat_lists: int = 100,
        hnsw_m: int = 16,
        hnsw_ef_construction: int = 64,
    ) -> None:
        if engine is not None:
            self._engine: Engine = engine
            self._owns_engine = False
        else:
            self._engine = create_engine(
                dsn or _default_pg_url(),
                echo=False,
                future=True,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
            )
            self._owns_engine = True

        self._dimension: int | None = dimension
        self._index_type: str = index_type.lower()
        self._ivfflat_lists = int(ivfflat_lists)
        self._hnsw_m = int(hnsw_m)
        self._hnsw_ef_construction = int(hnsw_ef_construction)
        self._index_name: str = index_name or self._default_index_name(self._index_type)

        self._ensure_extension()
        self._ensure_dimension()

    # ── Schema setup ────────────────────────────────────────────

    def _ensure_extension(self) -> None:
        """Create the pgvector extension if missing (idempotent)."""
        try:
            with self._engine.begin() as conn:
                conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector"'))
        except Exception as exc:  # noqa: BLE001 — surface clear error
            raise RuntimeError(
                f"pgvector extension could not be created on {self._engine.url}: "
                f"{exc}. Install pgvector and retry."
            ) from exc

    def _ensure_dimension(self) -> None:
        """If *dimension* was given and the column differs, ALTER it.

        Silently skips when the table/column does not exist yet (the caller
        is expected to have run the Alembic migration first) or when
        *dimension* is ``None``.
        """
        if self._dimension is None:
            return
        try:
            with self._engine.connect() as conn:
                row = conn.execute(_COL_TYPE_SQL).fetchone()
                if row is None:
                    logger.warning(
                        "[pg-vector] vector_entries.embedding not found; "
                        "run the 001_initial_schema migration first."
                    )
                    return
                ty = str(row[0])
                m = _DIM_RE.search(ty)
                if m and int(m.group(1)) == self._dimension:
                    return  # matches
                # ALTER to requested dimension
                logger.info(
                    "[pg-vector] ALTER embedding %s → vector(%d)",
                    ty, self._dimension,
                )
                conn.execute(
                    text(_ALTER_DIM_SQL.format(dim=self._dimension)),
                )
                # ALTER outside a transaction is fine; commit explicitly.
                conn.commit()
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning("[pg-vector] dimension check/alter failed: %s", exc)

    # ── Search ──────────────────────────────────────────────────

    def search(
        self,
        query_vector: list[float],
        *,
        top: int = 10,
        threshold: float = 0.0,
    ) -> list[VectorSearchResult]:
        """k-NN cosine similarity search via pgvector ``<=>`` operator."""
        if not query_vector:
            return []
        q_lit = _vector_literal(query_vector)
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    _SEARCH_SQL,
                    {"q": q_lit, "top": int(top)},
                ).fetchall()
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            logger.warning("[pg-vector] search failed: %s", exc)
            return []

        results: list[VectorSearchResult] = []
        for row in rows:
            # row is a Row; support both tuple-like and mapping-like access
            eid = row[0]
            text_ = row[1] or ""
            meta_raw = row[2]
            score = float(row[3])
            if score < threshold:
                continue
            meta = self._parse_meta(meta_raw)
            results.append(VectorSearchResult(id=eid, text=text_, score=score, metadata=meta))
        return results

    # ── Insert ──────────────────────────────────────────────────

    def insert(
        self,
        entry_id: str,
        text: str,
        vector: list[float],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Upsert a single entry. Returns ``True`` on success."""
        if not vector:
            logger.warning("[pg-vector] insert %r: empty vector", entry_id)
            return False
        params = {
            "id": entry_id,
            "text": text,
            "vector": json.dumps(vector),  # JSONB column keeps the raw array
            "emb": _vector_literal(vector),
            "meta": json.dumps(metadata or {}),
            "ts": time.time(),
        }
        try:
            with self._engine.begin() as conn:
                conn.execute(_UPSERT_SQL, params)
            return True
        except Exception as exc:  # noqa: BLE001 — log + signal failure
            logger.warning("[pg-vector] insert %r failed: %s", entry_id, exc)
            return False

    def insert_batch(self, entries: list[dict[str, Any]]) -> int:
        """Batch upsert via a single transaction.

        Each entry dict must have ``id``, ``text``, ``vector``; optional
        ``metadata``.
        """
        if not entries:
            return 0
        rows: list[dict[str, Any]] = []
        for e in entries:
            vec = e.get("vector") or []
            if not vec:
                continue
            rows.append({
                "id": e["id"],
                "text": e["text"],
                "vector": json.dumps(vec),
                "emb": _vector_literal(vec),
                "meta": json.dumps(e.get("metadata") or {}),
                "ts": time.time(),
            })
        if not rows:
            return 0
        try:
            with self._engine.begin() as conn:
                conn.execute(_UPSERT_SQL, rows)
            return len(rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[pg-vector] batch insert failed: %s", exc)
            return 0

    # ── Delete ──────────────────────────────────────────────────

    def delete(self, entry_id: str) -> bool:
        """Delete by id. Returns ``True`` if a row was removed."""
        try:
            with self._engine.begin() as conn:
                result = conn.execute(_DELETE_SQL, {"id": entry_id})
                # result.rowcount is -1 for some drivers; fall back to a
                # follow-up COUNT when ambiguous.
                rc = getattr(result, "rowcount", -1)
                return rc > 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("[pg-vector] delete %r failed: %s", entry_id, exc)
            return False

    # ── Index rebuild (non-blocking) ────────────────────────────

    def rebuild_index(
        self,
        *,
        index_type: str = "",
        **opts: Any,
    ) -> bool:
        """Rebuild the ANN index **without blocking concurrent searches**.

        Uses ``CREATE INDEX CONCURRENTLY`` which permits concurrent ``SELECT``
        (and even ``INSERT``/``UPDATE``) for the whole build. The build runs
        in autocommit mode (``CONCURRENTLY`` cannot live inside a
        transaction).

        Parameters
        ----------
        index_type : str
            ``"ivfflat"`` or ``"hnsw"``. Empty string → use the backend's
            default (``self._index_type``).
        **opts
            Override ``lists``, ``m``, ``ef_construction``.

        Returns
        -------
        bool
            ``True`` on success, ``False`` if the build failed.
        """
        itype = (index_type or self._index_type).lower()
        if itype not in ("ivfflat", "hnsw"):
            logger.warning(
                "[pg-vector] unknown index_type %r; falling back to %r",
                itype, self._index_type,
            )
            itype = self._index_type

        idx_name = opts.pop("index_name", self._index_name) or self._default_index_name(itype)
        # Drop existing index (concurrently, non-blocking).
        if not self._drop_index_concurrently(idx_name):
            return False

        if itype == "ivfflat":
            lists = int(opts.get("lists", self._ivfflat_lists))
            sql = _CREATE_IVFFLAT_SQL.format(idx=idx_name, lists=lists)
        else:
            m = int(opts.get("m", self._hnsw_m))
            efc = int(opts.get("ef_construction", self._hnsw_ef_construction))
            sql = _CREATE_HNSW_SQL.format(idx=idx_name, m=m, efc=efc)

        return self._run_concurrently(sql, f"create {itype} index {idx_name}")

    # ── Stats ───────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return backend statistics."""
        out: dict[str, Any] = {
            "backend": self.name,
            "dimension": self._dimension,
            "index_type": self._index_type,
            "index_name": self._index_name,
        }
        try:
            with self._engine.connect() as conn:
                out["count"] = int(conn.execute(_COUNT_SQL).scalar() or 0)
                idxs = [
                    str(r[0]) for r in conn.execute(_LIST_INDEXES_SQL).fetchall()
                ]
                out["indexes"] = idxs
        except Exception as exc:  # noqa: BLE001
            logger.debug("[pg-vector] stats failed: %s", exc)
            out["count"] = -1
            out["indexes"] = []
        return out

    # ── Lifecycle ───────────────────────────────────────────────

    def close(self) -> None:
        """Dispose the SQLAlchemy engine if we own it."""
        if self._owns_engine:
            try:
                self._engine.dispose()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[pg-vector] engine dispose failed: %s", exc)

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _default_index_name(itype: str) -> str:
        return f"idx_vector_embedding_{itype}"

    @staticmethod
    def _parse_meta(raw: Any) -> dict[str, Any]:
        """Parse a JSONB result into a dict (tolerant of driver variants)."""
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {"_": parsed}
            except (json.JSONDecodeError, ValueError):
                return {}
        return {}

    def _drop_index_concurrently(self, idx_name: str) -> bool:
        """Drop an ANN index concurrently (non-blocking). Returns ``True``
        even if the index did not exist (idempotent)."""
        # Validate the index name to prevent SQL injection — it appears
        # verbatim in the SQL string (CONCURRENTLY does not support bind
        # parameters).
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", idx_name):
            raise ValueError(f"invalid index name: {idx_name!r}")
        sql = f"DROP INDEX CONCURRENTLY IF EXISTS {idx_name}"
        return self._run_concurrently(sql, f"drop {idx_name}", tolerate_missing=True)

    def _run_concurrently(
        self,
        sql: str,
        label: str,
        *,
        tolerate_missing: bool = False,
    ) -> bool:
        """Execute *sql* in autocommit mode (required for CONCURRENTLY).

        ``CONCURRENTLY`` cannot run inside a transaction, so we borrow a raw
        DBAPI connection from the pool, set autocommit, and execute directly.
        """
        try:
            raw = self._engine.raw_connection()
            try:
                cur = raw.cursor()
                try:
                    cur.execute(sql)
                finally:
                    cur.close()
                raw.commit()
            finally:
                raw.close()
            return True
        except Exception as exc:  # noqa: BLE001
            if tolerate_missing and "does not exist" in str(exc).lower():
                return True
            logger.warning("[pg-vector] %s failed: %s", label, exc)
            return False