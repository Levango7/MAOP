"""MAOP initial schema

Revision ID: 001
Revises:
Create Date: 2026-07-24

This migration bootstraps the MAOP schema by executing the canonical
SQL DDL in data/migrations/001_init.sql. Subsequent migrations use
Alembic op.* helpers.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _find_sql_file() -> Path:
    """Locate 001_init.sql relative to the MAOP project root."""
    here = Path(__file__).resolve()
    # 001_init.py lives at <root>/py/maop/migrations/alembic/versions/001_init.py
    # so the project root is parents[5]. Fallbacks cover alternate layouts.
    candidates = [
        here.parents[5] / "data" / "migrations" / "001_init.sql",
        here.parents[4] / "data" / "migrations" / "001_init.sql",
        here.parents[3] / "data" / "migrations" / "001_init.sql",
        Path(os.environ.get("MAOP_ROOT", "")) / "data" / "migrations" / "001_init.sql",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError("Could not locate data/migrations/001_init.sql")


def _exec_section(section: str) -> None:
    """Execute a ';' delimited SQL section, stripping ``--`` comment lines."""
    conn = op.get_bind()
    for stmt in section.split(";"):
        # Drop full-line SQL comments so a chunk like
        # "-- header\nCREATE TABLE ..." is still executed.
        lines = [ln for ln in stmt.splitlines() if not ln.strip().startswith("--")]
        cleaned = "\n".join(lines).strip()
        if cleaned:
            conn.execute(sa.text(cleaned))


def upgrade() -> None:
    """Apply the initial schema by executing the UP section of 001_init.sql.

    The SQL file embeds both an UP block (CREATE TABLE/INDEX/INSERT) and a
    DOWN block (DROP TABLE) separated by a ``-- DOWN:`` marker. Only the UP
    block runs here; the DOWN block is reserved for downgrade().
    """
    sql_path = _find_sql_file()
    sql = sql_path.read_text(encoding="utf-8")
    # Split off the DOWN section; only the UP DDL runs on upgrade.
    up_sql = sql.split("-- DOWN:", 1)[0]
    _exec_section(up_sql)


def downgrade() -> None:
    """Downgrade is not supported for the initial schema (would lose all data)."""
    raise NotImplementedError("Cannot downgrade initial schema — would lose all data")
