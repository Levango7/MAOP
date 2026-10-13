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

from maop.core.memory.multimodal_embed import MULTIMODAL_DIM

__all__ = [
    "MM_COLUMNS",
    "MM_TABLE",
    "MULTIMODAL_DIM",
    "index_prefix",
    "pg_ddl",
    "sqlite_ddl",
]

#: Table name shared by both backends.
MM_TABLE = "multimodal_entries"

#: SQLite index DDL for the default table (kept as a module constant because
#: the legacy import sites and the schema-parity test compare against it).
SQLITE_TABLE_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_mme_created "
    f"ON {MM_TABLE}(created_at);"
)


def index_prefix(table: str) -> str:
    """Short, unique-per-database index name prefix for *table*.

    The default table keeps the historical ``mme`` prefix so the rendered DDL
    stays byte-identical (a second space in the same database must not collide
    on index names — ``CREATE INDEX IF NOT EXISTS`` would silently skip the
    second table's index otherwise).
    """
    return "mme" if table == MM_TABLE else table


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


def sqlite_ddl(*, table: str = MM_TABLE, with_index: bool = True) -> str:
    """Render the SQLite ``CREATE TABLE`` (+ created_at index) DDL.

    *table* selects the target table; the default keeps the historical output
    byte-identical (the schema-parity test compares the rendered string, so
    the default must not drift).
    """
    cols = []
    for c in MM_COLUMNS:
        parts = [c.name, c.sqlite_ddl]
        if c.default is not None and not c.primary_key:
            parts.append(f"DEFAULT {c.default}")
        cols.append(" ".join(parts))
    ddl = (
        f"CREATE TABLE IF NOT EXISTS {table} (\n  "
        + ",\n  ".join(cols)
        + "\n);"
    )
    if with_index:
        if table == MM_TABLE:
            ddl += "\n\n" + SQLITE_TABLE_INDEX_DDL
        else:
            prefix = index_prefix(table)
            ddl += (
                "\n\nCREATE INDEX IF NOT EXISTS idx_"
                f"{prefix}_created ON {table}(created_at);"
            )
    return ddl


def pg_ddl(*, table: str = MM_TABLE, dim: int = MULTIMODAL_DIM,
           with_indexes: bool = True) -> str:
    """Render the PostgreSQL ``CREATE TABLE`` (+ modality indexes).

    The ANN index is deliberately **not** partial-HNSW-per-modality:
    pgvector's approximate indexes trade recall for speed, and a
    partial-HNSW set on a table smaller than the index's ``ef_construction``
    can rank a better vector below a worse one (measured 2026-10-13 on
    pgvector 0.8.7: HNSW returned img-blue above img-red for the query
    "a red square"). The space therefore ships **exact** cosine scans with
    B-tree indexes on ``created_at`` and modality-filtered lookups; an ANN
    index can be added by a later migration once the corpus justifies the
    recall trade-off (pgvector documents that exact search is preferred for
    small datasets).

    Requires the ``vector`` extension (pgvector), created by the alembic
    env, so this function stays pure DDL.
    """
    prefix = index_prefix(table)
    cols = []
    for c in MM_COLUMNS:
        pg_type = f"vector({dim}) NOT NULL" if c.name == "vector" else c.pg_ddl
        parts = [c.name, pg_type]
        if c.default is not None and not c.primary_key:
            parts.append(f"DEFAULT {c.default}")
        cols.append(" ".join(parts))
    ddl = (
        f"CREATE TABLE IF NOT EXISTS {table} (\n  "
        + ",\n  ".join(cols)
        + "\n);"
    )
    if not with_indexes:
        return ddl
    index_lines = [
        # B-tree on the modality expression: the filter is the common access
        # pattern ("give me image entries") and stays exact.
        (
            f"CREATE INDEX IF NOT EXISTS idx_{prefix}_modality"
            f"  ON {table} ((metadata->>'modality'))"
        ),
        f"CREATE INDEX IF NOT EXISTS idx_{prefix}_created ON {table}(created_at)",
    ]
    return ddl + "\n\n" + ";\n\n".join(index_lines) + ";"
