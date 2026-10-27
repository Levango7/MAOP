"""Unify api_keys schema between SQLite runtime DDL and Alembic 001.

Revision ID: 006
Revises: 005
Create Date: 2026-08-26

SQLite 001_init.sql creates api_keys with ``key_id`` as PK plus ``scopes``,
``created``, ``revoked`` columns. The runtime (auth.py _init_db) uses
``key_hash`` as PK plus ``roles``, ``created_at``, ``enabled``, ``rate_limit``.
This migration aligns the SQLite table to the runtime/PG schema:

- Rename ``key_id`` → ``key_hash`` (PK), drop ``key_hash`` text column
- Rename ``scopes`` → ``roles``
- Rename ``created`` → ``created_at``
- Add ``enabled`` (default 1) and ``rate_limit`` (default 0)
- Drop ``revoked``

PostgreSQL 001_initial_schema.py already matches the runtime schema, so
this migration is a no-op on PG dialect.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    if not _is_sqlite():
        return
    bind = op.get_bind()
    cols = {r["name"] for r in sa.inspect(bind).get_columns("api_keys")}
    if "key_id" in cols and "key_hash" not in cols:
        op.alter_column("api_keys", "key_id", new_column_name="key_hash")
    if "scopes" in cols and "roles" not in cols:
        op.alter_column("api_keys", "scopes", new_column_name="roles")
    if "created" in cols and "created_at" not in cols:
        op.alter_column("api_keys", "created", new_column_name="created_at")
    if "enabled" not in cols:
        op.add_column("api_keys", sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"))
    if "rate_limit" not in cols:
        op.add_column("api_keys", sa.Column("rate_limit", sa.Integer(), nullable=False, server_default="0"))
    if "revoked" in cols:
        op.drop_column("api_keys", "revoked")


def downgrade() -> None:
    if not _is_sqlite():
        return
    bind = op.get_bind()
    cols = {r["name"] for r in sa.inspect(bind).get_columns("api_keys")}
    if "key_hash" in cols and "key_id" not in cols:
        op.alter_column("api_keys", "key_hash", new_column_name="key_id")
    if "roles" in cols and "scopes" not in cols:
        op.alter_column("api_keys", "roles", new_column_name="scopes")
    if "created_at" in cols and "created" not in cols:
        op.alter_column("api_keys", "created_at", new_column_name="created")
    if "enabled" in cols:
        op.drop_column("api_keys", "enabled")
    if "rate_limit" in cols:
        op.drop_column("api_keys", "rate_limit")
    if "revoked" not in cols:
        op.add_column("api_keys", sa.Column("revoked", sa.Integer(), nullable=False, server_default="0"))
