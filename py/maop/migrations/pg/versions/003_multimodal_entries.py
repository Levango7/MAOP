"""PG ``multimodal_entries`` — the CLIP text+image vector space (F2-02, M2.2 B2).

Revision ID: 003_multimodal_entries
Revises: 002_users_tenant_id
Create Date: 2026-10-13

Background
----------
ADR-022 decided the multimodal (CLIP 512-dim) vectors live in their **own
space** rather than the legacy ``vector_entries`` table, because that table
is a single ``vector(N)`` column whose dimension is chosen by an
ALTER-on-init; a 512-dim CLIP vector cannot coexist with the 384-dim
MiniLM / 1536-dim OpenAI text vectors (an ALTER with existing rows of the
other dimension fails the cast outright — see
``PgVectorBackend._ensure_dimension``).

This migration creates the PG side of that space. The DDL is **rendered from
the single source of truth** in :mod:`maop.core.memory.multimodal_schema`
(the same spec renders the SQLite DDL that ``MultimodalStore`` creates at
runtime in its own ``multimodal.db``), and
``tests/test_multimodal_schema_parity.py`` asserts the two stay in lockstep.

Design notes
------------
- The column is named ``vector`` even though its type is ``vector(512)``:
  PG accepts a column named after its type, and keeping the name identical
  across both backends means ``sqlite_to_pg`` needs no column rename map.
- ``modality`` is **not** a physical column: it lives in ``metadata`` (JSONB)
  so the SQLite and PG write paths share one shape. Modality-filtered ANN
  indexes use partial expression indexes
  ``WHERE (metadata->>'modality') = 'image'`` — legal on PG16/pgvector
  0.8.7 (verified locally with `pgvector/pgvector:pg16`) and requires
  **pgvector ≥ 0.5.0** for partial HNSW on expressions.
- Idempotent: every object is created only when absent so the migration can
  re-run safely (matches 001/002's ``IF NOT EXISTS`` posture).

Rollback is destructive: ``downgrade`` drops the table. Follow the repo's
``_require_destructive_ack`` guard — the CI migration job sets
``MAOP_MIGRATION_DESTRUCTIVE=1`` against a throwaway database.
"""

from __future__ import annotations

import os

from alembic import op

from maop.core.memory.multimodal_schema import MM_TABLE, pg_ddl

# Revision identifiers, used by Alembic.
revision: str = "003_multimodal_entries"
down_revision: str | None = "002_users_tenant_id"
branch_labels = None
depends_on = None


def _require_destructive_ack(label: str) -> None:
    """Guard destructive downgrades behind an explicit env acknowledgement."""
    if os.environ.get("MAOP_MIGRATION_DESTRUCTIVE", "0") != "1":
        raise RuntimeError(
            f"refusing destructive migration {label!r}; "
            "set MAOP_MIGRATION_DESTRUCTIVE=1 to proceed"
        )


def upgrade() -> None:
    """Create ``multimodal_entries`` + modality partial HNSW indexes."""
    for statement in pg_ddl().split(";"):
        stmt = statement.strip()
        if not stmt:
            continue
        op.execute(stmt + ";")


def downgrade() -> None:
    """Drop ``multimodal_entries`` (destructive, acknowledgement required)."""
    _require_destructive_ack("003_multimodal_entries (drop multimodal_entries)")
    op.execute(f"DROP TABLE IF EXISTS {MM_TABLE};")
