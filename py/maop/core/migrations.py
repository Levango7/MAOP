"""MAOP Database Migration Runner.

Applies SQL migration scripts from data/migrations/ in order.
Tracks applied migrations in the schema_migrations table.

Usage::

    from maop.core.migrations import run_migrations
    run_migrations(root_dir="/path/to/MAOP")
"""

from __future__ import annotations

import logging
from pathlib import Path

from maop.core.db_utils import sqlite_connect

logger = logging.getLogger(__name__)


def run_migrations(root_dir: str | Path, db_name: str = "maop.db") -> list[str]:
    """Apply pending SQL migrations.
    
    Returns list of applied migration versions.
    """
    root = Path(root_dir)
    data_dir = root / "data"
    migrations_dir = data_dir / "migrations"
    db_path = data_dir / db_name

    if not migrations_dir.exists():
        logger.warning("[migrations] No migrations directory found at %s", migrations_dir)
        return []

    applied: list[str] = []

    with sqlite_connect(db_path, timeout=30) as conn:
        # Ensure schema_migrations table exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

        # Find all migration files
        migration_files = sorted(migrations_dir.glob("*.sql"))

        for mf in migration_files:
            version = mf.stem.split("_")[0]
            name = mf.stem

            # Check if already applied
            row = conn.execute(
                "SELECT version FROM schema_migrations WHERE version = ?", (version,)
            ).fetchone()

            if row is not None:
                logger.debug("[migrations] Skip %s (already applied)", name)
                continue

            # Read and execute migration
            sql = mf.read_text(encoding="utf-8")

            # Remove DOWN section if present
            if "-- DOWN:" in sql:
                sql = sql.split("-- DOWN:")[0]

            try:
                conn.executescript(sql)
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (?, ?)",
                    (version, name),
                )
                conn.commit()
                applied.append(version)
                logger.info("[migrations] Applied %s (version %s)", name, version)
            except Exception as exc:
                # Column already exists is OK (idempotent)
                if "duplicate column name" in str(exc).lower():
                    logger.debug("[migrations] %s: columns already exist, marking as applied", name)
                    conn.execute(
                        "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (?, ?)",
                        (version, name),
                    )
                    conn.commit()
                    applied.append(version)
                else:
                    logger.error("[migrations] Failed to apply %s: %s", name, exc)
                    raise

    if applied:
        logger.info("[migrations] Applied %d migrations: %s", len(applied), applied)

    # Note: For Alembic-managed migrations, use:
    #   alembic upgrade head
    # This SQL runner and Alembic can coexist (dual-track).
    # Alembic revisions 001/002 call the same SQL scripts, ensuring consistency.
    return applied


def get_applied_migrations(root_dir: str | Path, db_name: str = "maop.db") -> list[str]:
    """Get list of applied migration versions."""
    root = Path(root_dir)
    db_path = root / "data" / db_name

    if not db_path.exists():
        return []

    try:
        with sqlite_connect(db_path, timeout=10) as conn:
            rows = conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            return [r[0] for r in rows]
    except Exception:
        return []
