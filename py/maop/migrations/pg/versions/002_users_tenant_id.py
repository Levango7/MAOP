"""Add ``users.tenant_id`` — 打通 JWT 会话的租户归属。

Revision ID: 002_users_tenant_id
Revises: 001_initial_schema
Create Date: 2026-09-21

背景
----
租户上下文此前只有 **API Key** 链路能拿到真实租户（``api_keys`` 表已有
``tenant_id`` 列），JWT / dashboard 会话拿到的一律是空串——JWT payload 里
根本没有 ``tenant`` claim，``AuthResult`` 也没有该字段。结果是配额 / RBAC /
合规 / 通知里的租户隔离对这些会话完全空转。

本次在 **数据面** 补上 ``users.tenant_id``，配合 JWT 的 ``tenant`` claim
（见 ``maop.core.security.auth``）使链路闭合。

设计约束：缺省必须是空串
------------------------
存量用户升级后 ``tenant_id`` 仍为 ``''``（未分配），其行为与升级前完全一致。
只有**显式分配**了租户的用户才进入隔离。这样"补链路"本身不构成行为变更，
不需要额外的开关——也因此本迁移是**纯加法**，无破坏性。

向下兼容
--------
``downgrade`` 会删列。生产上执行前请确认没有用户已分配租户，或先备份；
本仓库的 destructive 保护见 ``_require_destructive_ack``。
"""

from __future__ import annotations

import os

import sqlalchemy as sa  # noqa: F401
from alembic import op

# Revision identifiers, used by Alembic.
revision = "002_users_tenant_id"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def _require_destructive_ack(label: str) -> None:
    """Guard destructive downgrades behind an explicit env acknowledgement.

    Mirrors the guard used by 001_initial_schema: destructive operations must
    be acknowledged with ``MAOP_MIGRATION_DESTRUCTIVE=1``.
    """
    if os.environ.get("MAOP_MIGRATION_DESTRUCTIVE", "0") != "1":
        raise RuntimeError(
            f"refusing destructive migration {label!r}; "
            "set MAOP_MIGRATION_DESTRUCTIVE=1 to proceed"
        )


def upgrade() -> None:
    """Add the ``tenant_id`` column to ``users`` if absent."""
    op.execute(
        """
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';
        """
    )


def downgrade() -> None:
    """Drop the ``tenant_id`` column from ``users``."""
    _require_destructive_ack("002_users_tenant_id (drop users.tenant_id)")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS tenant_id;")
