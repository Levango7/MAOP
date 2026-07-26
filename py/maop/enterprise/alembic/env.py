"""MAOP Enterprise Alembic env.py — PostgreSQL migration support.

This module configures Alembic for the MAOP enterprise edition:
- Reads DATABASE_URL from MAOP_DATABASE_URL env var or alembic.ini
- Supports both online (PostgreSQL) and offline (SQL generation) mode
- Auto-generates migrations from SQLAlchemy models (if available)

For personal edition, use maop.core.migration.MigrationManager instead.

.. note::
   **B3 (2026-07-22):** The previous version of this file referenced
   ``maop.enterprise.models.Base``, which **does not exist** in the
   repository. The ``try/except ImportError: pass`` block silently set
   ``target_metadata = None``, which made Alembic's ``--autogenerate``
   produce empty migrations (no diff against the schema). This has been
   fixed by:
   1. Logging an explicit warning when ``maop.enterprise.models`` is
      missing (instead of silently passing).
   2. Documenting the expected future state: Phase C/H will add a real
      ``maop/enterprise/models.py`` with SQLAlchemy ORM models for the
      RBAC/tenant/audit/SSO tables. Until that lands, autogeneration
      will remain disabled (offline SQL migrations still work).
"""

from __future__ import annotations

import logging
import os
from logging.config import fileConfig

from alembic import context

config = context.config

if config is not None and config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.getenv("MAOP_DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = None

# B3: explicit warning instead of silent `pass`. The `maop.enterprise.models`
# module is referenced but does not exist yet (Phase C/H will create it
# with SQLAlchemy ORM models for RBAC/tenant/audit/SSO tables). Until then,
# Alembic autogenerate will produce empty diffs — use explicit migrations
# (alembic revision --autogenerate -m "..." will yield an empty upgrade()
# body until models are present).
try:
    from maop.enterprise.models import Base
    target_metadata = Base.metadata
except ImportError as _e:
    logging.getLogger("alembic.env").warning(
        "[alembic] maop.enterprise.models not available (%s). "
        "Autogenerate is disabled — target_metadata is None. "
        "Phase C/H will add ORM models to enable autogenerate.",
        _e,
    )


def run_migrations_offline() -> None:
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
    from sqlalchemy import engine_from_config, pool

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
