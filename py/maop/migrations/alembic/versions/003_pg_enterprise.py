"""PG enterprise schema: rbac_grants, tenants, tenant_usage, audit_events.

Revision ID: 003_pg_enterprise
Revises: 002
Create Date: 2026-07-25

Creates the PostgreSQL-specific enterprise tables that back the RBAC,
tenant, and audit persistence layers (see maop/enterprise/pg_persist.py).
The migration is idempotent — each table is only created when absent
(inspector.has_table guard) and indexes use CREATE INDEX IF NOT EXISTS.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003_pg_enterprise"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # 003 是 PostgreSQL 专用企业表（含 JSONB 类型），SQLite 等非 PG 方言直接跳过
    if bind.dialect.name != "postgresql":
        # OPS-36 fix: alembic still stamps this revision as applied even
        # though nothing ran. If this database's schema is later migrated
        # to PostgreSQL (e.g. dump/restore + repointing alembic), the
        # enterprise tables will be MISSING while alembic believes 003 is
        # applied. Remediation on the new PG database:
        #   alembic stamp 002 && alembic upgrade head
        # (or run this file's DDL manually). Emit a loud warning so the
        # skip is visible in migration logs instead of silent.
        import logging
        logging.getLogger("alembic.runtime.migration").warning(
            "003_pg_enterprise SKIPPED on dialect %r (PostgreSQL-only DDL) "
            "but will be stamped as applied. If you later move this "
            "database to PostgreSQL, run `alembic stamp 002 && alembic "
            "upgrade head` there to actually create the enterprise tables.",
            bind.dialect.name,
        )
        return
    inspector = sa.inspect(bind)

    # ── rbac_grants (PgRBACStore) ──────────────────────────────
    if not inspector.has_table("rbac_grants"):
        op.create_table(
            "rbac_grants",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Text(), nullable=False),
            sa.Column("role", sa.Text(), nullable=False),
            sa.Column("tenant_id", sa.Text(), nullable=True, server_default=""),
            sa.Column("granted_by", sa.Text(), nullable=True, server_default=""),
            sa.Column("granted_at", sa.Float(), nullable=True, server_default="0"),
            sa.Column("expires_at", sa.Float(), nullable=True),
            sa.UniqueConstraint(
                "user_id", "role", "tenant_id",
                name="uq_rbac_grants_user_role_tenant",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS idx_rbac_user ON rbac_grants(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_rbac_tenant ON rbac_grants(tenant_id)")

    # ── tenants (PgTenantStore) ────────────────────────────────
    if not inspector.has_table("tenants"):
        op.create_table(
            "tenants",
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=True, server_default="trial"),
            sa.Column("plan", sa.Text(), nullable=True, server_default="starter"),
            sa.Column(
                "quota",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("created_at", sa.Float(), nullable=True, server_default="0"),
            sa.Column("updated_at", sa.Float(), nullable=True, server_default="0"),
            sa.Column("expires_at", sa.Float(), nullable=True),
            sa.Column(
                "metadata",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.PrimaryKeyConstraint("tenant_id"),
        )

    # ── tenant_usage (PgTenantStore) ───────────────────────────
    if not inspector.has_table("tenant_usage"):
        op.create_table(
            "tenant_usage",
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("api_calls_today", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("storage_mb", sa.Float(), nullable=True, server_default="0"),
            sa.Column("active_agents", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("concurrent_tasks", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("active_users", sa.Integer(), nullable=True, server_default="0"),
            sa.ForeignKeyConstraint(
                ["tenant_id"], ["tenants.tenant_id"],
                ondelete="CASCADE",
                name="fk_tenant_usage_tenant_id",
            ),
            sa.PrimaryKeyConstraint("tenant_id"),
        )

    # ── audit_events (PgAuditStore) ────────────────────────────
    if not inspector.has_table("audit_events"):
        op.create_table(
            "audit_events",
            sa.Column("event_id", sa.Text(), nullable=False),
            sa.Column("timestamp", sa.Float(), nullable=False),
            sa.Column("action", sa.Text(), nullable=False),
            sa.Column("severity", sa.Text(), nullable=True, server_default="info"),
            sa.Column("actor", sa.Text(), nullable=True, server_default=""),
            sa.Column("tenant_id", sa.Text(), nullable=True, server_default=""),
            sa.Column("resource", sa.Text(), nullable=True, server_default=""),
            sa.Column("detail", sa.Text(), nullable=True, server_default=""),
            sa.Column("result", sa.Text(), nullable=True, server_default="success"),
            sa.Column("ip_address", sa.Text(), nullable=True, server_default=""),
            sa.Column("user_agent", sa.Text(), nullable=True, server_default=""),
            sa.Column(
                "metadata",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.PrimaryKeyConstraint("event_id"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events(timestamp)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_events(tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events(action)")


def _require_destructive_ack(revision_name: str) -> None:
    """OPS-35 fix: guard destructive downgrades (see 001_init.py).

    Downgrading 003 DROPS audit_events (compliance/audit trail), tenants
    and RBAC grants. Refuse outside dev/test unless explicitly overridden.
    """
    import os
    env = os.environ.get("MAOP_ENV", "").strip().lower()
    if env in ("dev", "development", "local", "test", "ci"):
        return
    if os.environ.get("MAOP_ALLOW_DESTRUCTIVE_DOWNGRADE", "") == "1":
        return
    raise RuntimeError(
        f"SAFETY: downgrade of {revision_name} DROPS audit_events "
        "(compliance data), tenants and rbac_grants. Refusing outside "
        "dev/test. Set MAOP_ALLOW_DESTRUCTIVE_DOWNGRADE=1 to override "
        "(make a backup first)."
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # upgrade() was a no-op on this dialect; nothing to drop.
        return
    _require_destructive_ack("003_pg_enterprise (audit_events, tenants, rbac_grants)")
    op.execute("DROP INDEX IF EXISTS idx_audit_action")
    op.execute("DROP INDEX IF EXISTS idx_audit_tenant")
    op.execute("DROP INDEX IF EXISTS idx_audit_actor")
    op.execute("DROP INDEX IF EXISTS idx_audit_timestamp")
    op.execute("DROP TABLE IF EXISTS audit_events")
    op.execute("DROP TABLE IF EXISTS tenant_usage")
    op.execute("DROP TABLE IF EXISTS tenants")
    op.execute("DROP INDEX IF EXISTS idx_rbac_tenant")
    op.execute("DROP INDEX IF EXISTS idx_rbac_user")
    op.execute("DROP TABLE IF EXISTS rbac_grants")
