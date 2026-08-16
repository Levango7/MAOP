"""sqlite-vec → pgvector migration tool (F1-02).

Copies vector data from the SQLite ``vector_entries`` table (where
``vector`` is a JSON-encoded TEXT column and ANN search is done via the
sqlite-vec ``vec_vectors`` virtual table) into the PostgreSQL
``vector_entries`` table whose ``embedding`` column is a pgvector
``vector(N)`` and ANN search uses IVFFLAT / HNSW.

This is a **focused** companion to :mod:`maop.migrations.sqlite_to_pg`
(which copies every table). Use this script when you want to:

- Re-run only the vector copy after a partial migration.
- Rebuild the pgvector ANN index after bulk-loading vectors.
- Validate row counts between the two stores.

Usage
-----
    # 1. Dry-run (count + sample, no writes):
    python -m maop.migrations.pg.vector_migration --dry-run

    # 2. Full copy + rebuild IVFFLAT index:
    python -m maop.migrations.pg.vector_migration --rebuild-index ivfflat

    # 3. HNSW with custom build params:
    python -m maop.migrations.pg.vector_migration --rebuild-index hnsw \
        --hnsw-m 32 --hnsw-ef-construction 128
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("maop.migrations.pg.vector_migration")

__all__ = [
    "VectorMigrationResult",
    "get_pg_engine",
    "get_sqlite_engine",
    "migrate_vectors",
]


# ── Result container ────────────────────────────────────────────


class VectorMigrationResult:
    """Outcome of a vector migration run (lightweight, JSON-serialisable)."""

    def __init__(
        self,
        *,
        rows_scanned: int = 0,
        rows_copied: int = 0,
        rows_skipped: int = 0,
        dimension: int | None = None,
        index_rebuilt: bool = False,
        index_type: str = "",
        elapsed_s: float = 0.0,
        dry_run: bool = False,
    ) -> None:
        self.rows_scanned = rows_scanned
        self.rows_copied = rows_copied
        self.rows_skipped = rows_skipped
        self.dimension = dimension
        self.index_rebuilt = index_rebuilt
        self.index_type = index_type
        self.elapsed_s = elapsed_s
        self.dry_run = dry_run

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows_scanned": self.rows_scanned,
            "rows_copied": self.rows_copied,
            "rows_skipped": self.rows_skipped,
            "dimension": self.dimension,
            "index_rebuilt": self.index_rebuilt,
            "index_type": self.index_type,
            "elapsed_s": round(self.elapsed_s, 3),
            "dry_run": self.dry_run,
        }

    def __repr__(self) -> str:
        return f"VectorMigrationResult({self.as_dict()})"


# ── Engine construction ────────────────────────────────────────


def _default_sqlite_url() -> str:
    """Resolve the default SQLite URL from the project layout.

    ``py/maop/migrations/pg/vector_migration.py`` → ``parents[4]`` is the
    project root (``F:\\Nexus\\MAOP``); the SQLite DB lives at
    ``<root>/data/maop.db``.
    """
    here = Path(__file__).resolve()
    root = here.parents[4]
    db_path = root / "data" / "maop.db"
    return f"sqlite:///{db_path.as_posix()}"


def get_sqlite_engine(url: str | None = None) -> Engine:
    """Build a SQLAlchemy engine for the source SQLite database."""
    url = url or os.environ.get("MAOP_SQLITE_URL") or _default_sqlite_url()
    return create_engine(url, echo=False, future=True)


def get_pg_engine(url: str | None = None) -> Engine:
    """Build a SQLAlchemy engine for the target PostgreSQL database.

    Reads ``MAOP_DATABASE_URL`` (or ``MAOP_DB_URL``) when *url* is ``None``.
    """
    url = (
        url
        or os.environ.get("MAOP_DATABASE_URL")
        or os.environ.get("MAOP_DB_URL")
        or "postgresql+psycopg2://localhost:5432/maop"
    )
    return create_engine(
        url,
        echo=False,
        future=True,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )


# ── Source introspection ───────────────────────────────────────


def _sqlite_has_vector_table(engine: Engine) -> bool:
    """Return True iff the SQLite DB has a ``vector_entries`` table."""
    try:
        return inspect(engine).has_table("vector_entries")  # type: ignore
    except Exception as exc:
        logger.warning("SQLite introspection failed: %s", exc)
        return False


def _count_sqlite_vectors(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(text("SELECT COUNT(*) FROM vector_entries")).scalar() or 0
        )


def _iter_sqlite_batches(
    engine: Engine,
    batch_size: int,
) -> Iterable[list[dict[str, Any]]]:
    """Yield batches of ``{id, text, vector, metadata, created_at}`` dicts.

    The ``vector`` column is JSON-encoded TEXT in SQLite; we parse it into a
    Python list here so the PG writer can bind it to both JSONB (``vector``)
    and ``vector(N)`` (``embedding``).
    """
    cols = "id, text, vector, metadata, created_at"
    with engine.connect() as conn:
        offset = 0
        while True:
            rows = conn.execute(
                text(
                    f"SELECT {cols} FROM vector_entries "
                    "ORDER BY created_at LIMIT :lim OFFSET :off"
                ),
                {"lim": batch_size, "off": offset},
            ).fetchall()
            if not rows:
                return
            batch: list[dict[str, Any]] = []
            for r in rows:
                vec_raw = r[2]
                try:
                    vec = json.loads(vec_raw) if isinstance(vec_raw, str) else vec_raw
                except (json.JSONDecodeError, TypeError):
                    vec = []
                meta_raw = r[3]
                try:
                    meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
                except (json.JSONDecodeError, TypeError):
                    meta = {}
                batch.append({
                    "id": r[0],
                    "text": r[1] or "",
                    "vector": vec if isinstance(vec, list) else [],
                    "metadata": meta if isinstance(meta, dict) else {},
                    "created_at": float(r[4] or 0.0),
                })
            yield batch
            offset += batch_size


# ── Target writes ──────────────────────────────────────────────


_UPSERT_SQL = text(
    """
    INSERT INTO vector_entries (id, text, vector, embedding, metadata, created_at)
    VALUES (:id, :text, CAST(:vec_json AS jsonb), (:emb)::vector,
            CAST(:meta AS jsonb), :ts)
    ON CONFLICT (id) DO UPDATE
      SET text      = EXCLUDED.text,
          vector     = EXCLUDED.vector,
          embedding  = EXCLUDED.embedding,
          metadata   = EXCLUDED.metadata,
          created_at = EXCLUDED.created_at
    """
)


def _vector_literal(vec: list[float]) -> str:
    """Render a list as a pgvector string literal ``'[v1,v2,...]'``."""
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


def _write_batch(pg_engine: Engine, batch: Sequence[dict[str, Any]]) -> int:
    """Upsert one batch into PG. Returns rows written."""
    if not batch:
        return 0
    rows: list[dict[str, Any]] = []
    for e in batch:
        vec = e.get("vector") or []
        if not vec:
            continue
        rows.append({
            "id": e["id"],
            "text": e["text"],
            "vec_json": json.dumps(vec),
            "emb": _vector_literal(vec),
            "meta": json.dumps(e.get("metadata") or {}),
            "ts": e.get("created_at") or time.time(),
        })
    if not rows:
        return 0
    with pg_engine.begin() as conn:
        conn.execute(_UPSERT_SQL, rows)
    return len(rows)


# ── Index rebuild ──────────────────────────────────────────────


def _rebuild_pg_index(
    pg_engine: Engine,
    *,
    index_type: str = "ivfflat",
    index_name: str = "",
    dimension: int | None = None,
    ivfflat_lists: int = 100,
    hnsw_m: int = 16,
    hnsw_ef_construction: int = 64,
) -> bool:
    """Rebuild the pgvector ANN index concurrently (non-blocking).

    Returns ``True`` on success. Uses ``CREATE INDEX CONCURRENTLY`` so
    concurrent searches are not blocked.
    """
    import re

    itype = index_type.lower()
    if itype not in ("ivfflat", "hnsw"):
        logger.warning("Unknown index_type %r; defaulting to ivfflat", itype)
        itype = "ivfflat"
    idx_name = index_name or f"idx_vector_embedding_{itype}"
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", idx_name):
        raise ValueError(f"invalid index name: {idx_name!r}")

    # Ensure pgvector extension is available.
    with pg_engine.begin() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector"'))

    # Drop existing index concurrently.
    if not _run_concurrently(pg_engine, f"DROP INDEX CONCURRENTLY IF EXISTS {idx_name}",
                             label=f"drop {idx_name}", tolerate_missing=True):
        return False

    if itype == "ivfflat":
        sql = (
            f"CREATE INDEX CONCURRENTLY {idx_name} ON vector_entries "
            f"USING ivfflat (embedding vector_cosine_ops) WITH (lists = {int(ivfflat_lists)})"
        )
    else:
        sql = (
            f"CREATE INDEX CONCURRENTLY {idx_name} ON vector_entries "
            f"USING hnsw (embedding vector_cosine_ops) "
            f"WITH (m = {int(hnsw_m)}, ef_construction = {int(hnsw_ef_construction)})"
        )
    return _run_concurrently(pg_engine, sql, label=f"create {itype} {idx_name}")


def _run_concurrently(
    engine: Engine,
    sql: str,
    label: str,
    *,
    tolerate_missing: bool = False,
) -> bool:
    """Run *sql* in autocommit (required for CONCURRENTLY)."""
    try:
        raw = engine.raw_connection()
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
    except Exception as exc:
        if tolerate_missing and "does not exist" in str(exc).lower():
            return True
        logger.warning("%s failed: %s", label, exc)
        return False


# ── Public API ─────────────────────────────────────────────────


def migrate_vectors(
    *,
    sqlite_url: str | None = None,
    pg_url: str | None = None,
    sqlite_engine: Engine | None = None,
    pg_engine: Engine | None = None,
    batch_size: int = 1000,
    dry_run: bool = False,
    rebuild_index: str = "",
    index_name: str = "",
    dimension: int | None = None,
    ivfflat_lists: int = 100,
    hnsw_m: int = 16,
    hnsw_ef_construction: int = 64,
    progress: bool = True,
) -> VectorMigrationResult:
    """Migrate vector data from SQLite to PostgreSQL pgvector.

    Parameters
    ----------
    sqlite_url, pg_url : str | None
        Source / target DB URLs. ``None`` → env vars / defaults.
    sqlite_engine, pg_engine : Engine | None
        Pre-built engines (take priority over URLs; useful for tests).
    batch_size : int
        Rows per INSERT batch.
    dry_run : bool
        Count and log, but do not write to PG.
    rebuild_index : str
        If non-empty (``"ivfflat"`` or ``"hnsw"``), rebuild the ANN index
        after the copy. Uses ``CREATE INDEX CONCURRENTLY`` (non-blocking).
    index_name : str
        Override the ANN index name.
    dimension : int | None
        Expected embedding dimension. If given, rows whose vector length
        differs are skipped (counted in ``rows_skipped``). If ``None``,
        the dimension is inferred from the first non-empty vector.
    ivfflat_lists, hnsw_m, hnsw_ef_construction
        Index build parameters.
    progress : bool
        Print progress to stdout.

    Returns
    -------
    VectorMigrationResult
    """
    start = time.monotonic()
    result = VectorMigrationResult(dry_run=dry_run, index_type=rebuild_index)

    src = sqlite_engine or get_sqlite_engine(sqlite_url)
    dst = pg_engine or get_pg_engine(pg_url)

    if not _sqlite_has_vector_table(src):
        logger.warning("SQLite has no vector_entries table; nothing to migrate.")
        result.elapsed_s = time.monotonic() - start
        return result

    total = _count_sqlite_vectors(src)
    result.rows_scanned = total
    if progress:
        mode = "dry-run" if dry_run else "live"
        print(f"vector migration ({mode}): {total} rows in SQLite")

    if total == 0:
        result.elapsed_s = time.monotonic() - start
        return result

    if dry_run:
        # Peek at the first row to infer dimension for the report.
        for batch in _iter_sqlite_batches(src, batch_size=1):
            if batch and batch[0]["vector"]:
                result.dimension = len(batch[0]["vector"])
            break
        result.elapsed_s = time.monotonic() - start
        if progress:
            print(f"  [dry-run] would copy {total} rows, dim={result.dimension}")
        return result

    # Live copy.
    inferred_dim: int | None = dimension
    written = 0
    skipped = 0
    for batch in _iter_sqlite_batches(src, batch_size):
        # Filter by dimension when known.
        if inferred_dim is not None:
            keep: list[dict[str, Any]] = []
            for e in batch:
                v = e.get("vector") or []
                if not v:
                    skipped += 1
                    continue
                if len(v) != inferred_dim:
                    skipped += 1
                    logger.debug(
                        "skip %r: dim %d != %d", e["id"], len(v), inferred_dim,
                    )
                    continue
                keep.append(e)
            batch = keep
        else:
            # Infer dim from first non-empty vector.
            for e in batch:
                v = e.get("vector") or []
                if v:
                    inferred_dim = len(v)
                    break
            # Drop empty-vector rows.
            batch = [e for e in batch if (e.get("vector") or [])]
            skipped += sum(1 for _ in batch if False)  # already filtered

        if not batch:
            continue
        try:
            written += _write_batch(dst, batch)
        except Exception as exc:
            logger.error("batch write failed: %s", exc)
            skipped += len(batch)

        if progress:
            sys.stdout.write(f"\r  copied {written}/{total} (skipped {skipped})")
            sys.stdout.flush()

    result.rows_copied = written
    result.rows_skipped = skipped
    result.dimension = inferred_dim

    if progress:
        print(f"\r  copied {written}/{total} (skipped {skipped}){'':>20}")

    # Index rebuild.
    if rebuild_index:
        ok = _rebuild_pg_index(
            dst,
            index_type=rebuild_index,
            index_name=index_name,
            dimension=inferred_dim,
            ivfflat_lists=ivfflat_lists,
            hnsw_m=hnsw_m,
            hnsw_ef_construction=hnsw_ef_construction,
        )
        result.index_rebuilt = ok
        if progress:
            status = "OK" if ok else "FAILED"
            print(f"  index rebuild ({rebuild_index}): {status}")

    result.elapsed_s = time.monotonic() - start
    return result


# ── CLI ────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="maop.migrations.pg.vector_migration",
        description="Migrate MAOP vector data from SQLite (sqlite-vec) to PostgreSQL (pgvector).",
    )
    p.add_argument("--sqlite-url", default=None, help="Source SQLite URL.")
    p.add_argument("--pg-url", default=None, help="Target PostgreSQL URL.")
    p.add_argument("--batch-size", type=int, default=1000)
    p.add_argument("--dry-run", action="store_true", help="Count only, no writes.")
    p.add_argument(
        "--rebuild-index",
        choices=["", "ivfflat", "hnsw"],
        default="",
        help="Rebuild the pgvector ANN index after copy (non-blocking).",
    )
    p.add_argument("--index-name", default="", help="Override ANN index name.")
    p.add_argument("--dimension", type=int, default=None, help="Expected embedding dim.")
    p.add_argument("--ivfflat-lists", type=int, default=100)
    p.add_argument("--hnsw-m", type=int, default=16)
    p.add_argument("--hnsw-ef-construction", type=int, default=64)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--log-level", default="INFO")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s [%(name)s] %(message)s",
    )
    result = migrate_vectors(
        sqlite_url=args.sqlite_url,
        pg_url=args.pg_url,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        rebuild_index=args.rebuild_index,
        index_name=args.index_name,
        dimension=args.dimension,
        ivfflat_lists=args.ivfflat_lists,
        hnsw_m=args.hnsw_m,
        hnsw_ef_construction=args.hnsw_ef_construction,
        progress=not args.quiet,
    )
    print(f"\nResult: {result.as_dict()}")
    return 0 if (result.rows_copied >= 0 and result.index_rebuilt or not args.rebuild_index) else 1


if __name__ == "__main__":
    sys.exit(main())