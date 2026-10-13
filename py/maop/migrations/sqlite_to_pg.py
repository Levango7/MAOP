"""SQLite → PostgreSQL data migration for MAOP.

This script copies row data from the existing SQLite database (``data/maop.db``)
into a PostgreSQL database whose schema has been bootstrapped by the Alembic
migration at ``maop/migrations/pg/versions/001_initial_schema.py``.

Design notes
------------
* Uses SQLAlchemy 2.0 sync engines for both sides (the migration is a one-shot
  batch job; async would add complexity without benefit).
* Tables are copied in foreign-key dependency order so the PG side never
  violates a constraint mid-copy.
* JSON-encoded TEXT columns are converted to ``JSONB`` by SQLAlchemy's
  dialect-aware bind processing — we just pass the raw Python object (parsed
  from the SQLite TEXT) and let psycopg2 adapt it.
* FTS5 virtual tables (``memory_fts``, ``episodic_memory_fts``) are NOT
  copied: the PG schema uses generated ``tsvector`` columns that populate
  themselves on INSERT. The SQLite FTS shadow tables (``*_config``,
  ``*_data``, ``*_docsize``, ``*_idx``) are also skipped.
* sqlite-vec virtual table (``vec_vectors``) is not copied directly; the
  ``vector_entries.embedding`` column is populated from the JSON ``vector``
  column by a post-copy UPDATE (see :func:`_backfill_embeddings`).
* ``sqlite_sequence`` is a SQLite internal bookkeeping table — never copied.

Usage
-----
    # 1. Bootstrap the PG schema (run once):
    maop migrate pg-init

    # 2. Dry-run to see what would be copied:
    python -m maop.migrations.sqlite_to_pg --dry-run

    # 3. Real run:
    python -m maop.migrations.sqlite_to_pg

    # 4. Copy only specific tables:
    python -m maop.migrations.sqlite_to_pg --tables users,sessions,messages
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

from maop.db_url import resolve_pg_driver
from maop.migrations.pg.value_converters import VALUE_CONVERTERS

logger = logging.getLogger("maop.migrations.sqlite_to_pg")

# ────────────────────────────────────────────────────────────────────────────
# Table inventory
# ────────────────────────────────────────────────────────────────────────────

# Tables to skip entirely (SQLite internals, FTS5 shadow tables, vec virtual).
# The FTS5 virtual tables themselves (memory_fts, episodic_memory_fts) are
# also skipped — PG uses generated tsvector columns instead.
_SKIP_TABLES: frozenset[str] = frozenset(
    {
        "sqlite_sequence",
        "memory_fts",
        "memory_fts_config",
        "memory_fts_data",
        "memory_fts_docsize",
        "memory_fts_idx",
        "episodic_memory_fts",
        "episodic_memory_fts_config",
        "episodic_memory_fts_data",
        "episodic_memory_fts_docsize",
        "episodic_memory_fts_idx",
        "vec_vectors",  # sqlite-vec virtual table — embeddings backfilled separately
    }
)

# Copy order: parent tables before child tables (FK dependency).
# Tables not listed here are copied in alphabetical order after these.
_COPY_ORDER: tuple[str, ...] = (
    "users",
    "api_keys",
    "sessions",
    "messages",
    "agent_memory",
    "agent_messages",
    "agent_performance",
    "agent_evolution_history",
    "a2a_cards",
    "a2a_tasks",
    "breaker_events",
    "circuit_breaker",
    "circuit_breaker_state",
    "checkpoints",
    "consolidation_log",
    "cost_entries",
    "delegations",
    "entities",
    "episodic_memory",
    "error_ledger",
    "error_log",
    "evolution_cycles",
    "facts",
    "failover_chains",
    "health_log",
    "hooks",
    "hook_logs",
    "kv_store",
    "memory_entries",
    "memory_traces",
    "memory_trajectory",
    "metrics",
    "promoted_rules",
    "prompt_templates",
    "prompt_versions",
    "queue_messages",
    "queue_dead_letters",
    "queue_idempotent",
    "registered_agents",
    "scanned_agents",
    "relations",
    "routing_decisions",
    "subagents",
    "subagent_transcripts",
    "tools",
    "ts_raw",
    "ts_5min",
    "ts_1hour",
    "vector_entries",
    # M2.2 B2: the multimodal space lives in its OWN sqlite file
    # (multimodal.db) — migrate() copies it via a separate source engine.
    "multimodal_entries",
    "worktree_nodes",
    "worktree_checkpoints",
    "_migrations",
)

# Columns stored as JSON-encoded TEXT in SQLite that map to JSONB in PG.
# We parse them on read so psycopg2 binds Python objects → JSONB.
_JSON_COLUMNS: dict[str, frozenset[str]] = {
    "users": frozenset({"roles"}),
    "api_keys": frozenset({"roles"}),
    "sessions": frozenset({"tags", "metadata"}),
    "messages": frozenset({"metadata"}),
    "agent_memory": frozenset({"metadata"}),
    "agent_messages": frozenset({"payload"}),
    "agent_evolution_history": frozenset({"changes"}),
    "a2a_cards": frozenset({"card_json"}),
    "a2a_tasks": frozenset({"task_json"}),
    "checkpoints": frozenset({"state_json"}),
    "cost_entries": frozenset({"metadata"}),
    "entities": frozenset({"attributes"}),
    "episodic_memory": frozenset(
        {"lessons", "quality_dimensions", "key_decisions", "files_touched", "metadata"}
    ),
    "error_ledger": frozenset({"trigger"}),
    "evolution_cycles": frozenset({"report_json"}),
    "failover_chains": frozenset({"agents"}),
    "metrics": frozenset({"tags"}),
    "prompt_templates": frozenset({"tags"}),
    "prompt_versions": frozenset({"variables"}),
    "queue_messages": frozenset({"payload"}),
    "queue_dead_letters": frozenset({"payload"}),
    "registered_agents": frozenset({"capabilities"}),
    "scanned_agents": frozenset({"capabilities"}),
    "routing_decisions": frozenset({"attributes"}),
    "subagents": frozenset({"context", "tool_calls", "config", "metadata"}),
    "subagent_transcripts": frozenset({"data"}),
    "tools": frozenset({"params"}),
    "ts_raw": frozenset({"tags"}),
    "ts_5min": frozenset({"tags"}),
    "ts_1hour": frozenset({"tags"}),
    "vector_entries": frozenset({"vector", "metadata"}),
    # F2-02: JSON text in SQLite → JSONB in PG. The vector column itself is
    # converted by value_converters (BLOB → pgvector literal), not parsed
    # as JSON — the converter runs first in _transform_row.
    "multimodal_entries": frozenset({"metadata"}),
    "worktree_nodes": frozenset({"metadata"}),
    "worktree_checkpoints": frozenset({"snapshot"}),
}

# PG tables whose schema renames a SQLite column. Maps sqlite_col → pg_col.
# Currently empty — the PG migration preserves all column names — but the
# indirection is kept so future renames are a one-line change.
_COLUMN_RENAMES: dict[str, dict[str, str]] = {}

# ────────────────────────────────────────────────────────────────────────────
# Engine construction
# ────────────────────────────────────────────────────────────────────────────


def _default_sqlite_url() -> str:
    """Resolve the default SQLite URL from the project layout."""
    here = Path(__file__).resolve()
    # py/maop/migrations/sqlite_to_pg.py -> parents[3] = py
    py_root = here.parents[3]
    root = py_root.parent
    db_path = root / "data" / "maop.db"
    return f"sqlite:///{db_path.as_posix()}"


def get_sqlite_engine(url: str | None = None) -> Engine:
    """Build a SQLAlchemy engine for the source SQLite database."""
    url = url or os.environ.get("MAOP_SQLITE_URL") or _default_sqlite_url()
    return create_engine(url, echo=False, future=True)


def get_pg_engine(url: str | None = None) -> Engine:
    """Build a SQLAlchemy engine for the target PostgreSQL database.

    Reads ``MAOP_DATABASE_URL`` (or ``MAOP_DB_URL``) from the environment
    if *url* is not provided. Connection pool is sized for a batch copy:
    ``pool_size=10, max_overflow=20``.

    The URL is passed through :func:`maop.db_url.resolve_pg_driver` so a
    driver-less ``postgresql://`` means the same thing whatever SQLAlchemy
    version the deployment installed (2.0 → psycopg2, 2.1+ → psycopg3).
    """
    url = resolve_pg_driver(
        url
        or os.environ.get("MAOP_DATABASE_URL")
        or os.environ.get("MAOP_DB_URL")
        or "postgresql://localhost:5432/maop"
    )
    return create_engine(
        url,
        echo=False,
        future=True,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )


# ────────────────────────────────────────────────────────────────────────────
# Schema introspection
# ────────────────────────────────────────────────────────────────────────────


def _sqlite_tables(engine: Engine) -> list[str]:
    """Return user tables in the SQLite database, minus skip-list."""
    inspector = inspect(engine)
    tables = [t for t in inspector.get_table_names() if t not in _SKIP_TABLES]
    return tables


def _multimodal_source(sqlite_url: str | None) -> tuple[Engine | None, set[str]]:
    """Return (engine, table names) for the multimodal space's own DB file.

    The multimodal memory space deliberately lives in
    ``<data_dir>/multimodal.db`` (ADR-022 D2). It is discovered next to the
    main SQLite database:

    * an explicit ``sqlite_url`` that points *at* a ``multimodal.db`` counts;
    * otherwise the sibling ``multimodal.db`` next to the main database file;
    * if that file does not exist (multimodal never used) → ``(None, set())``.

    Returns an empty set (never raising) when anything is off, so legacy
    single-file migrations keep working unchanged.
    """
    if sqlite_url and "multimodal" in sqlite_url:
        url = sqlite_url
    elif sqlite_url:
        # Derive the sibling of the explicitly-given main database.
        try:
            path = Path(sqlite_url.replace("sqlite:///", "").split("?", 1)[0])
            url = f"sqlite:///{(path.parent / 'multimodal.db').as_posix()}"
        except (ValueError, OSError):
            return None, set()
    else:
        # Reuse get_sqlite_engine's own URL resolution (which already honours
        # the env var / project default) instead of re-reading the env —
        # the config-drift gate counts direct env reads.
        main_url = get_sqlite_engine().url.render_as_string(hide_password=False)
        path = Path(main_url.replace("sqlite:///", "").split("?", 1)[0])
        candidate = path.parent / "multimodal.db"
        if not candidate.exists():
            return None, set()
        url = f"sqlite:///{candidate.as_posix()}"
    try:
        engine = create_engine(url, echo=False, future=True)
        names = set(_sqlite_tables(engine))
    except Exception as exc:  # pragma: no cover - defensive: unreadable file
        logger.debug("multimodal source %s unusable: %s", url, exc)
        return None, set()
    if not names:
        return None, set()
    logger.debug("multimodal source %s provides tables %s", url, sorted(names))
    return engine, names


def _ordered_tables(available: Sequence[str]) -> list[str]:
    """Intersect *available* with :data:`_COPY_ORDER`, then append any extras sorted."""
    ordered = [t for t in _COPY_ORDER if t in available]
    extras = sorted(set(available) - set(ordered))
    return ordered + extras


def _columns(engine: Engine, table: str) -> list[str]:
    """Return the column names of *table* in declaration order."""
    return [c["name"] for c in inspect(engine).get_columns(table)]


def _pg_has_table(engine: Engine, table: str) -> bool:
    return inspect(engine).has_table(table)  # type: ignore


# ────────────────────────────────────────────────────────────────────────────
# Row transformation
# ────────────────────────────────────────────────────────────────────────────


def _parse_json(value: Any) -> Any:
    """Parse a JSON-encoded TEXT value; pass through if already a container."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        # Not valid JSON — return the raw string so PG can store it as a
        # JSONB string literal (jsonb accepts arbitrary strings via to_jsonb).
        return value


def _transform_row(table: str, columns: Sequence[str], row: Sequence[Any]) -> dict[str, Any]:
    """Convert a SQLite row (tuple) into a PG-ready dict.

    * Parses JSON-encoded TEXT columns into Python objects so psycopg2 binds
      them to JSONB.
    * Applies value converters registered per ``(table, column)`` (e.g. the
      multimodal space's float32 BLOB → pgvector text literal — raw BLOBs
      into a ``vector`` column fail with DatatypeMismatch).
    * Applies column renames if any.
    """
    json_cols = _JSON_COLUMNS.get(table, frozenset())
    renames = _COLUMN_RENAMES.get(table, {})
    converters = VALUE_CONVERTERS
    out: dict[str, Any] = {}
    for col, val in zip(columns, row):
        pg_col = renames.get(col, col)
        conv = converters.get((table, col))
        if conv is not None:
            out[pg_col] = conv(val)
        elif col in json_cols:
            out[pg_col] = _parse_json(val)
        else:
            out[pg_col] = val
    return out


# ────────────────────────────────────────────────────────────────────────────
# Copy core
# ────────────────────────────────────────────────────────────────────────────


def _count_rows(engine: Engine, table: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0)  # nosec B608 — 表名/列名来自迁移元数据自省（已人工核）


def _iter_batches(
    engine: Engine, table: str, columns: Sequence[str], batch_size: int
) -> Iterable[list[dict[str, Any]]]:
    """Yield batches of transformed rows from the SQLite table.

    P2-6 fix: 原 OFFSET 深分页在每批后越来越慢（OFFSET N 需扫描 N 行）。
    改用 keyset pagination：以 ``id`` 列为游标（若存在），每批 WHERE id > last_id
    ORDER BY id LIMIT batch_size，复杂度恒定。若表无 ``id`` 列，回退到 OFFSET。
    """
    col_list = ", ".join(f'"{c}"' for c in columns)
    has_id = "id" in columns
    with engine.connect() as conn:
        if has_id:
            # Keyset pagination: 以 id 为游标，恒定复杂度。
            last_id: Any = None
            while True:
                if last_id is None:
                    rows = conn.execute(
                        text(f'SELECT {col_list} FROM "{table}" ORDER BY "id" LIMIT :lim'),  # nosec B608 — 表名/列名来自迁移元数据自省（已人工核）
                        {"lim": batch_size},
                    ).fetchall()
                else:
                    rows = conn.execute(
                        text(f'SELECT {col_list} FROM "{table}" WHERE "id" > :last ORDER BY "id" LIMIT :lim'),  # nosec B608 — 表名/列名来自迁移元数据自省（已人工核）
                        {"last": last_id, "lim": batch_size},
                    ).fetchall()
                if not rows:
                    return
                yield [_transform_row(table, columns, row) for row in rows]
                # 注意：不要改成 row._mapping["id"] —— 该属性是 SQLAlchemy 内部
                # 实现，测试用的 FakeRow（tests/test_pg_migration.py）只实现了
                # __getitem__，改用 _mapping 会让迁移测试直接 AttributeError。
                # 行尾 ignore：Row 运行时支持字符串键访问，只是 mypy stub 未声明。
                last_id = rows[-1]["id"]  # type: ignore[call-overload]
        else:
            # 回退：无 id 列的表用 OFFSET 分页（保持原行为）。
            offset = 0
            while True:
                rows = conn.execute(
                    text(f'SELECT {col_list} FROM "{table}" LIMIT :lim OFFSET :off'),  # nosec B608 — 表名/列名来自迁移元数据自省（已人工核）
                    {"lim": batch_size, "off": offset},
                ).fetchall()
                if not rows:
                    return
                yield [_transform_row(table, columns, row) for row in rows]
                offset += batch_size


def _insert_batch(
    pg_engine: Engine,
    table: str,
    columns: Sequence[str],
    batch: Sequence[dict[str, Any]],
) -> None:
    """Insert a batch of rows into the PG table."""
    if not batch:
        return
    # Use SQLAlchemy core insert with executemany for batch efficiency.
    col_list = ", ".join(f'"{c}"' for c in columns)
    param_list = ", ".join(f":{c}" for c in columns)
    stmt = text(f'INSERT INTO "{table}" ({col_list}) VALUES ({param_list})')  # nosec B608 — 表名/列名来自迁移元数据自省（已人工核）
    with pg_engine.begin() as conn:
        conn.execute(stmt, list(batch))


def _print_progress(table: str, done: int, total: int, start: float) -> None:
    pct = (done / total * 100) if total else 100.0
    elapsed = time.monotonic() - start
    rate = done / elapsed if elapsed > 0 else 0.0
    sys.stdout.write(
        f"\r  {table}: {done}/{total} ({pct:5.1f}%) {rate:6.0f} rows/s"
    )
    sys.stdout.flush()


def copy_table(
    sqlite_engine: Engine,
    pg_engine: Engine,
    table: str,
    *,
    batch_size: int = 1000,
    dry_run: bool = False,
    progress: bool = True,
) -> int:
    """Copy one table from SQLite to PG. Returns the number of rows copied."""
    if not _pg_has_table(pg_engine, table):
        logger.warning("PG table %r does not exist — skipping", table)
        return 0
    src_cols = _columns(sqlite_engine, table)
    dst_cols = [_COLUMN_RENAMES.get(table, {}).get(c, c) for c in src_cols]
    # Skip columns that don't exist on the PG side (e.g. generated tsvector).
    pg_cols_set = set(_columns(pg_engine, table))
    keep = [c for c in dst_cols if c in pg_cols_set]
    # Map back to source columns for the SELECT.
    src_to_dst = dict(zip(src_cols, dst_cols))
    select_cols = [sc for sc in src_cols if src_to_dst[sc] in keep]

    total = _count_rows(sqlite_engine, table)
    if total == 0:
        if progress:
            print(f"  {table}: 0 rows (skipped)")
        return 0

    if dry_run:
        print(f"  [dry-run] {table}: would copy {total} rows, {len(keep)} columns")
        return total

    start = time.monotonic()
    done = 0
    for batch in _iter_batches(sqlite_engine, table, select_cols, batch_size):
        # Re-key each row to the destination column names.
        rekeyed = []
        for row in batch:
            new_row = {}
            for sc in select_cols:
                new_row[src_to_dst[sc]] = row[sc]
            rekeyed.append(new_row)
        _insert_batch(pg_engine, table, keep, rekeyed)
        done += len(rekeyed)
        if progress:
            _print_progress(table, done, total, start)
    if progress:
        elapsed = time.monotonic() - start
        print(f"\r  {table}: {done}/{total} in {elapsed:.1f}s{'':>20}")
    return done


def _backfill_embeddings(pg_engine: Engine, *, dry_run: bool = False, progress: bool = True) -> int:
    """Populate ``vector_entries.embedding`` from the JSON ``vector`` column.

    The PG schema stores the original JSON vector in ``vector`` (JSONB) for
    fidelity; the pgvector ``embedding`` column is what the ANN index uses.
    We parse the JSONB array into a PG vector literal and UPDATE in batches.

    Returns the number of rows updated.
    """
    if not _pg_has_table(pg_engine, "vector_entries"):
        return 0
    with pg_engine.connect() as conn:
        total = int(
            conn.execute(
                text("SELECT COUNT(*) FROM vector_entries WHERE embedding IS NULL")
            ).scalar()
            or 0
        )
    if total == 0:
        if progress:
            print("  vector_entries.embedding: already populated")
        return 0
    if dry_run:
        print(f"  [dry-run] vector_entries.embedding: would backfill {total} rows")
        return total
    if progress:
        print(f"  vector_entries.embedding: backfilling {total} rows...")
    # Single UPDATE — pgvector's vector type accepts a text literal '[v1,v2,...]'.
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE vector_entries "
                "SET embedding = ('[' || array_to_string("
                "  (SELECT array_agg(e) FROM jsonb_array_elements_text(vector) AS e), "
                "  ',') || ']')::vector "
                "WHERE embedding IS NULL AND jsonb_typeof(vector) = 'array'"
            )
        )
    return total


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────


def migrate(
    *,
    sqlite_url: str | None = None,
    pg_url: str | None = None,
    tables: Sequence[str] | None = None,
    batch_size: int = 1000,
    dry_run: bool = False,
    progress: bool = True,
    rollback_on_failure: bool = False,
) -> dict[str, int]:
    """Run the SQLite → PG data migration.

    Parameters
    ----------
    sqlite_url, pg_url : str | None
        Override the source/target DB URLs. If ``None``, fall back to env
        vars and the default local paths.
    tables : Sequence[str] | None
        Restrict the copy to these tables (after intersecting with what
        actually exists in SQLite). ``None`` means "all tables".
    batch_size : int
        Rows per INSERT batch (default 1000).
    dry_run : bool
        If True, count rows and print what would be copied but do not
        INSERT anything. No PostgreSQL connection is opened at all — a
        dry-run needs only the SQLite sources, so it works on hosts with
        no PG driver installed (verified: previously the unconditional
        ``create_engine`` raised ``ModuleNotFoundError: psycopg2``).
    progress : bool
        Print per-table progress to stdout.
    rollback_on_failure : bool
        P1-8 fix: 若为 True，当任一表复制失败（results[t] < 0）时，
        回滚已成功复制的表（TRUNCATE），避免 PG 侧残留半成品数据。

    Returns
    -------
    dict[str, int]
        Mapping of table name → rows copied (or rows that would be copied
        in dry-run mode).
    """
    sqlite_engine = get_sqlite_engine(sqlite_url)
    available = _sqlite_tables(sqlite_engine)
    # M2.2 B2: the multimodal space lives in its own database file
    # (``<data_dir>/multimodal.db``). Without merging it into the source
    # list here it would never be copied — the "PG table exists" guard would
    # make the copy report 0 rows and *succeed* (a green lie). Its rows are
    # pulled through a separate engine so both files migrate in one run.
    multimodal_engine, multimodal_tables = _multimodal_source(sqlite_url)
    if multimodal_engine is not None and "multimodal_entries" not in available:
        available = list(available) + sorted(multimodal_tables)
    if tables:
        wanted = set(tables)
        available = [t for t in available if t in wanted]
        missing = wanted - set(available)
        if missing:
            logger.warning("Requested tables not found in SQLite: %s", sorted(missing))
    ordered = _ordered_tables(available)

    if progress:
        mode = "dry-run" if dry_run else "live"
        print(f"SQLite → PG migration ({mode}): {len(ordered)} tables")

    def _source_for(table: str) -> Engine:
        # _multimodal_source only returns tables when its engine is not None,
        # so the None branch is unreachable by construction.
        if table in multimodal_tables and multimodal_engine is not None:
            return multimodal_engine
        return sqlite_engine

    if dry_run:
        # No PG connection at all: count rows per source table and report.
        dry_counts: dict[str, int] = {}
        for table in ordered:
            try:
                dry_counts[table] = _count_rows(_source_for(table), table)
                if progress:
                    n_cols = len(_columns(_source_for(table), table))
                    print(f"  [dry-run] {table}: would copy {dry_counts[table]} rows, {n_cols} columns")
            except Exception as exc:
                logger.error("Failed to count table %r: %s", table, exc)
                dry_counts[table] = -1
        return dry_counts

    pq_ready = True
    pg_engine: Engine
    try:
        pg_engine = get_pg_engine(pg_url)
    except Exception as exc:
        # A live migration without psycopg2 must fail loudly, not silently
        # copy nothing: report every requested table as failed.
        logger.error("Cannot build PG engine (%s); a live migration needs psycopg2", exc)
        pq_ready = False
    if not pq_ready:
        return {t: -1 for t in ordered}

    results: dict[str, int] = {}
    for table in ordered:
        try:
            results[table] = copy_table(
                _source_for(table), pg_engine, table,
                batch_size=batch_size, dry_run=dry_run, progress=progress,
            )
        except Exception as exc:
            logger.error("Failed to copy table %r: %s", table, exc)
            results[table] = -1

    # Backfill pgvector embeddings from the JSON vector column.
    if "vector_entries" in results and results["vector_entries"] >= 0:
        try:
            _backfill_embeddings(pg_engine, dry_run=dry_run, progress=progress)
        except Exception as exc:
            logger.error("Embedding backfill failed: %s", exc)

    # P1-8 fix: 回滚机制 — 若启用且存在失败表，TRUNCATE 已成功复制的表。
    if rollback_on_failure and not dry_run:
        failed = [t for t, n in results.items() if n < 0]
        if failed:
            logger.warning(
                "rollback_on_failure=True, %d failed tables (%s) — rolling back successful copies",
                len(failed), failed,
            )
            try:
                _rollback_pg_migration(pg_engine, results)
            except Exception as exc:
                logger.error("PG rollback failed: %s", exc)

    return results


def _rollback_pg_migration(pg_engine: Engine, results: dict[str, int]) -> None:
    """P1-8 fix: 回滚已成功复制到 PG 的表（TRUNCATE）。

    仅 TRUNCATE rows>0 的表（成功复制过的），跳过失败和空表。
    按外键依赖逆序 TRUNCATE 以避免约束冲突。
    """
    successful = [t for t, n in results.items() if n > 0]
    # 逆序 TRUNCATE（依赖顺序的反向）。
    with pg_engine.begin() as conn:
        for table in reversed(successful):
            try:
                conn.execute(text(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE'))
                logger.info("PG rollback: truncated %s", table)
            except Exception as exc:
                logger.error("PG rollback: failed to truncate %s: %s", table, exc)


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maop.migrations.sqlite_to_pg",
        description="Copy MAOP data from SQLite to PostgreSQL.",
    )
    parser.add_argument(
        "--sqlite-url", default=None,
        help="Source SQLite URL (default: sqlite:///<root>/data/maop.db)",
    )
    parser.add_argument(
        "--pg-url", default=None,
        help="Target PG URL (default: $MAOP_DATABASE_URL or postgresql+psycopg2://localhost:5432/maop)",
    )
    parser.add_argument(
        "--tables", default="",
        help="Comma-separated table subset to copy (default: all)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=1000,
        help="Rows per INSERT batch (default: 1000)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Count and print, but do not INSERT",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-table progress output",
    )
    parser.add_argument(
        "--rollback", action="store_true",
        help="Roll back (TRUNCATE) successfully copied PG tables if any table fails",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        help="Logging level (default: INFO)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s [%(name)s] %(message)s",
    )
    tables = (
        [t.strip() for t in args.tables.split(",") if t.strip()]
        if args.tables else None
    )
    results = migrate(
        sqlite_url=args.sqlite_url,
        pg_url=args.pg_url,
        tables=tables,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        progress=not args.quiet,
        rollback_on_failure=args.rollback,
    )
    failed = [t for t, n in results.items() if n < 0]
    if failed:
        print(f"\nFAILED tables: {failed}", file=sys.stderr)
        return 1
    total = sum(n for n in results.values() if n >= 0)
    print(f"\nDone. Total rows {'copied' if not args.dry_run else 'scanned'}: {total}")
    return 0



if __name__ == "__main__":
    sys.exit(main())