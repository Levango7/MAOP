"""Alembic migration environment for MAOP.

Uses SQLite (data/maop.db) by default. The URL can be overridden via
MAOP_DB_URL environment variable.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure maop is importable
MAOP_ROOT = Path(__file__).resolve().parents[4]  # py/maop/migrations/alembic -> root
sys.path.insert(0, str(MAOP_ROOT / "py"))

config = context.config

# Override SQLAlchemy URL with MAOP_DB_URL or default
db_url = os.environ.get("MAOP_DB_URL", f"sqlite:///{MAOP_ROOT}/data/maop.db")
config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata (None for autogenerate disabled — we use raw SQL)
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect to DB and execute)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()