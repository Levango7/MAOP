"""Add config_snapshots table for configuration history.

Revision ID: 004
Revises: 003_pg_enterprise
Create Date: 2026-08-26

Creates the ``config_snapshots`` table used by ``ConfigHistory`` to persist
runtime configuration snapshots (see maop/core/config/config_history.py).
Previously this table was created only at runtime via ``_init_schema()``
with no Alembic version control.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: str | None = "003_pg_enterprise"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "config_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, unique=True),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("changed_by", sa.Text(), nullable=False),
        sa.Column("changed_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_config_snapshots_version "
        "ON config_snapshots(version DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_config_snapshots_version")
    op.execute("DROP TABLE IF EXISTS config_snapshots")
