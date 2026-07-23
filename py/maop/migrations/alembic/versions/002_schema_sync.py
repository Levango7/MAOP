"""Schema sync: queue_messages columns, pipeline checkpoints, etc.

Revision ID: 002
Revises: 001
Create Date: 2026-07-24
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Execute the SQL migration script
    import os
    from pathlib import Path

    sql_path = Path(os.environ.get("MAOP_ROOT", ".")) / "data" / "migrations" / "002_schema_sync.sql"
    if sql_path.exists():
        op.execute(sql_path.read_text(encoding="utf-8"))


def downgrade() -> None:
    # Cannot easily reverse ALTER TABLE ADD COLUMN in SQLite
    pass