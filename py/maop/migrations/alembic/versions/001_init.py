"""Initial schema (baseline from 001_init.sql).

Revision ID: 001
Revises: 
Create Date: 2026-07-24
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Baseline — tables already created by 001_init.sql
    # This revision is a no-op marker for databases already on 001
    # For fresh databases, run data/migrations/001_init.sql first, then alembic stamp 001
    pass


def downgrade() -> None:
    pass