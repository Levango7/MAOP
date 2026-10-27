"""Add hooks and hook_logs tables.

Revision ID: 005
Revises: 004
Create Date: 2026-08-26

Creates the ``hooks`` and ``hook_logs`` tables used by ``HookManager``
(see maop/core/agent/plugins_hooks/hook_manager.py).
Previously these tables were created only at runtime via ``_init_db()``
with no Alembic version control on SQLite. PostgreSQL already creates
them in 001_initial_schema.py.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hooks",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("hook_type", sa.Text(), nullable=False, server_default="callback"),
        sa.Column("callback", sa.Text(), nullable=False, server_default=""),
        sa.Column("callback_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("url", sa.Text(), nullable=False, server_default=""),
        sa.Column("headers", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("timeout_s", sa.REAL(), nullable=False, server_default="10.0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default="api"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_hooks_event ON hooks(event, enabled)")

    op.create_table(
        "hook_logs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("hook_id", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("success", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("response_code", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_hook_logs_event ON hook_logs(event, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_hook_logs_hook_id ON hook_logs(hook_id, created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_hook_logs_hook_id")
    op.execute("DROP INDEX IF EXISTS idx_hook_logs_event")
    op.execute("DROP TABLE IF EXISTS hook_logs")
    op.execute("DROP INDEX IF EXISTS idx_hooks_event")
    op.execute("DROP TABLE IF EXISTS hooks")
