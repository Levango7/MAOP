"""Alembic migration environment for MAOP PostgreSQL backend.

Differs from the SQLite-targeted env at ``maop/migrations/alembic/env.py``:
this env is PostgreSQL-only and enables pgvector / tsvector extensions
before running migrations. The DB URL is read from ``MAOP_DATABASE_URL``
(or ``MAOP_DB_URL`` for backwards compatibility) and falls back to a
local ``postgresql+psycopg2://localhost:5432/maop`` default.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure maop is importable when alembic is invoked from the project root.
# py/maop/migrations/pg/env.py -> parents[4] is the project root.
MAOP_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(MAOP_ROOT / "py"))

config = context.config

# Resolve DB URL: MAOP_DATABASE_URL > MAOP_DB_URL > default local PG.
default_pg_url = "postgresql+psycopg2://localhost:5432/maop"
db_url = os.environ.get("MAOP_DATABASE_URL") or os.environ.get("MAOP_DB_URL") or default_pg_url
config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Autogenerate is disabled — migrations are hand-authored to map the
# SQLite schema onto idiomatic PG types (BIGSERIAL, JSONB, tsvector, vector).
target_metadata = None


def _enable_extensions(connection) -> None:
    """Enable PG extensions required by the schema (idempotent).

    - ``pgvector``: ``vector`` type for sqlite-vec table replacement.
    - ``pg_trgm``: trigram index for fuzzy text search (optional, best-effort).

    Errors are swallowed because the extensions may already be installed
    at the template/database level and CREATE EXTENSION requires superuser
    privileges — failing here should not abort migrations in CI where the
    extension is pre-provisioned.
    """
    from sqlalchemy import text

    for ext in ("vector", "pg_trgm"):
        try:
            connection.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{ext}"'))
        except Exception:  # noqa: BLE001 — extension may be pre-installed or unsupported
            pass


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
    """Run migrations in 'online' mode (connect to PG and execute)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _enable_extensions(connection)
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()