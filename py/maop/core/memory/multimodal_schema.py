"""Single source of truth for the multimodal ``multimodal_entries`` schema (M2.2 B2).

Why this module exists
---------------------
The same table is defined **twice** in this repo: once as the SQLite DDL that
:class:`~maop.core.memory.multimodal_store.MultimodalStore` creates at runtime
(its own database file, outside alembic's reach) and once as the PostgreSQL
migration in ``migrations/pg/versions/003_multimodal_entries.py``. Two
hand-written DDLs drift silently the moment either side adds a column — which
is exactly the class of bug this repo's docs-consistency gate exists for.

So: define the columns **once** here (name / nullability / default / dialect
renderings) and let both sides render from it. A parity test
(``tests/test_multimodal_schema_parity.py``) asserts the SQLite DDL, the PG
DDL, and this spec agree, so any future one-sided edit fails the suite
instead of drifting in production.

The ``modality`` discriminator lives in ``metadata`` (JSON text on SQLite,
JSONB on Postgres) rather than a dedicated column — one write shape for both
backends, and modality-filtered indexes on PG use an expression index
(``metadata->>'modality' = 'image'``), which pgvector 0.8.7 renders and
executes without a physical column. See ADR-022 D2 for the full rationale.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "MM_COLUMNS",
    "MM_TABLE",
    "pg_ddl",
    "sqlite_ddl",
]

#: Table name shared by both backends.
MM_TABLE = "multimodal_entries"

#: SQLite pragma applied to the database (WAL is managed by callers).
SQLITE_TABLE_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_mme_created "
    f"ON {MM_TABLE}(created_at);"
)


@dataclass(frozen=True)
class Column:
    """One column of :data:`MM_TABLE`.

    Attributes
    ----------
    name:
        Column name (identical on both backends).
    nullable:
        Whether the column accepts NULL.
    default:
        SQL literal default (``''``, ``'{}'`` …) or ``None`` for none.
    sqlite_ddl, pg_ddl:
        The per-backend type/not-null/default fragments.
    primary_key:
        Whether the column is the primary key.
    """

    name: str
    sqlite_ddl: str
    pg_ddl: str
    nullable: bool = False
    default: str | None = None
    primary_key: bool = False


#: Canonical column order. Both DDLs MUST preserve this order.
MM_COLUMNS: tuple[Column, ...] = (
    Column(
        name="id",
        sqlite_ddl="TEXT PRIMARY KEY",
        pg_ddl="TEXT PRIMARY KEY",
        primary_key=True,
    ),
    Column(name="text", sqlite_ddl="TEXT NOT NULL", pg_ddl="TEXT NOT NULL"),
    Column(
        name="vector",
        # float32 little-endian BLOB (2048 B per 512-dim vector) instead of
        # JSON (~10.5 KB / vector) — see ADR-022 D7. PG renders the pgvector
        # type; the column may legitimately share the type's name.
        sqlite_ddl="BLOB NOT NULL",
        pg_ddl="vector(512) NOT NULL",
    ),
    Column(
        name="metadata",
        sqlite_ddl="TEXT NOT NULL",
        pg_ddl="JSONB NOT NULL",
    ),
    Column(
        name="created_at",
        sqlite_ddl="REAL NOT NULL",
        pg_ddl="DOUBLE PRECISION NOT NULL",
    ),
)


def sqlite_ddl(*, with_index: bool = True) -> str:
    """Render the SQLite ``CREATE TABLE`` (+ created_at index) DDL."""
    cols = []
    for c in MM_COLUMNS:
        parts = [c.name, c.sqlite_ddl]
        if c.default is not None and not c.primary_key:
            parts.append(f"DEFAULT {c.default}")
        cols.append(" ".join(parts))
    ddl = (
        f"CREATE TABLE IF NOT EXISTS {MM_TABLE} (\n  "
        + ",\n  ".join(cols)
        + "\n);"
    )
    if with_index:
        ddl += "\n\n" + SQLITE_TABLE_INDEX_DDL
    return ddl


def pg_ddl(*, with_indexes: bool = True) -> str:
    """Render the PostgreSQL ``CREATE TABLE`` (+ modality partial indexes).

    Requires the ``vector`` extension (pgvector ≥ 0.5.0 for partial HNSW
    indexes on expressions); the extension is created by the alembic env, not
    here, so this function stays pure DDL.
    """
    cols = []
    for c in MM_COLUMNS:
        parts = [c.name, c.pg_ddl]
        if c.default is not None and not c.primary_key:
            parts.append(f"DEFAULT {c.default}")
        cols.append(" ".join(parts))
    ddl = (
        f"CREATE TABLE IF NOT EXISTS {MM_TABLE} (\n  "
        + ",\n  ".join(cols)
        + "\n);"
    )
    if not with_indexes:
        return ddl
    index_lines = []
    for modality in ("text", "image"):
        index_lines.append(
            f"CREATE INDEX IF NOT EXISTS idx_mme_{modality}_embedding\n"
            f"  ON {MM_TABLE} USING hnsw (vector vector_cosine_ops)\n"
            f"  WHERE (metadata->>'modality') = '{modality}'"
        )
    index_lines.append(
        f"CREATE INDEX IF NOT EXISTS idx_mme_created ON {MM_TABLE}(created_at)"
    )
    return ddl + "\n\n" + ";\n\n".join(index_lines) + ";"
