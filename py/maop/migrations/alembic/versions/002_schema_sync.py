"""Schema sync: queue_messages columns, pipeline checkpoints, etc.

Revision ID: 002
Revises: 001
Create Date: 2026-07-24
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Execute the SQL migration script
    import os
    from pathlib import Path

    sql_path = Path(os.environ.get("MAOP_ROOT", ".")) / "data" / "migrations" / "002_schema_sync.sql"
    if sql_path.exists():
        # SQLite cursor.execute() 不支持多语句，按 ';' 分割逐条执行
        sql = sql_path.read_text(encoding="utf-8")
        conn = op.get_bind()
        for stmt in sql.split(";"):
            lines = [ln for ln in stmt.splitlines() if not ln.strip().startswith("--")]
            cleaned = "\n".join(lines).strip()
            if cleaned:
                conn.execute(sa.text(cleaned))


def _require_destructive_ack(revision_name: str) -> None:
    """OPS-34 fix: guard destructive downgrades (see 001_init.py).

    Downgrading 002 DROPS jwt_revoked (JWT blacklist — a SECURITY
    regression: revoked tokens become valid again), budget_ledger,
    checkpoints and evolve history. Refuse outside dev/test unless
    explicitly overridden.
    """
    import os
    env = os.environ.get("MAOP_ENV", "").strip().lower()
    if env in ("dev", "development", "local", "test", "ci"):
        return
    if os.environ.get("MAOP_ALLOW_DESTRUCTIVE_DOWNGRADE", "") == "1":
        return
    raise RuntimeError(
        f"SAFETY: downgrade of {revision_name} DROPS jwt_revoked (security "
        "regression) and operational tables. Refusing outside dev/test. Set "
        "MAOP_ALLOW_DESTRUCTIVE_DOWNGRADE=1 to override (make a backup first)."
    )


def downgrade() -> None:
    """回滚 002 创建的表和索引。

    注意：queue_messages 新增的列无法在 SQLite 中 DROP COLUMN（旧版本限制），
    但 001 的 downgrade 会 DROP 整个 queue_messages 表，所以不影响幂等性。
    OPS-34 fix: 生产环境默认拒绝（jwt_revoked 被 DROP = 已吊销 token 复活）。
    """
    _require_destructive_ack("002_schema_sync (jwt_revoked, budget_ledger, ...)")
    # DROP 002 创建的索引（逆序）
    op.execute("DROP INDEX IF EXISTS idx_eh_applied_at")
    op.execute("DROP INDEX IF EXISTS idx_eh_agent")
    op.execute("DROP INDEX IF EXISTS idx_bl_agent")
    op.execute("DROP INDEX IF EXISTS idx_bl_tenant_ts")
    op.execute("DROP INDEX IF EXISTS idx_ckpt_status")
    op.execute("DROP INDEX IF EXISTS idx_ckpt_run")
    op.execute("DROP INDEX IF EXISTS idx_qm_cg_status_visible")
    op.execute("DROP INDEX IF EXISTS idx_qm_status_dequeued")
    # DROP 002 创建的表（逆序）
    op.execute("DROP TABLE IF EXISTS evolve_history")
    op.execute("DROP TABLE IF EXISTS agent_performance")
    op.execute("DROP TABLE IF EXISTS budget_ledger")
    op.execute("DROP TABLE IF EXISTS jwt_revoked")
    op.execute("DROP TABLE IF EXISTS pipeline_step_checkpoints")
    op.execute("DROP TABLE IF EXISTS pipeline_runs")
