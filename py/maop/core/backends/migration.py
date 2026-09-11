"""MAOP DB Migration - Schema versioning and migration tool.

Provides:
  1. Migration: A single migration step (up/down SQL)
  2. MigrationManager: Track applied migrations, apply pending, rollback
  3. AlembicBridge: Optional Alembic integration for enterprise PostgreSQL

Migration files are stored in data/migrations/ as numbered SQL files:
  001_create_agents.sql
  002_add_memory_index.sql
  ...

For enterprise edition with PostgreSQL, set MAOP_MIGRATION_BACKEND=alembic
to use Alembic instead of the built-in SQL-file migration system.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from maop.core.backends.db_utils import sqlite_connect

logger = logging.getLogger(__name__)


def _split_sql_statements(sql: str) -> list[str]:
    """P1-9 fix: 将 SQL 脚本拆分为单条语句列表。

    使用字符级状态机判断语句边界，正确处理多行语句、单引号字符串字面量
    内嵌的分号、以及 ``--`` 行注释。替代 ``executescript`` 的隐式 commit
    行为，使逐条 ``execute`` 在事务内运行、失败可回滚。

    块注释 ``/* ... */`` 不在此处特殊处理（migration 文件约定使用 ``--``）。
    """
    statements: list[str] = []
    buffer = ""
    in_string = False  # 单引号字符串内
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        # 检测行注释 "--"
        if not in_string and ch == "-" and i + 1 < n and sql[i + 1] == "-":
            # 跳到行尾
            j = sql.find("\n", i)
            if j == -1:
                # 注释到末尾
                if buffer.strip():
                    statements.append(buffer)
                return statements
            # 将注释行加入 buffer（保留可读性），然后继续
            buffer += sql[i:j]
            i = j
            continue
        # 单引号转义（SQL 中用 '' 表示字面量单引号）
        if ch == "'":
            if in_string and i + 1 < n and sql[i + 1] == "'":
                buffer += "''"
                i += 2
                continue
            in_string = not in_string
            buffer += ch
            i += 1
            continue
        # 分号且不在字符串内 → 语句边界
        if ch == ";" and not in_string:
            buffer += ch
            stmt = buffer.strip()
            if stmt:
                statements.append(stmt)
            buffer = ""
            i += 1
            continue
        buffer += ch
        i += 1
    # 末尾残留（无分号结尾的语句）
    # 修复: 纯注释行（strip 后以 "--" 开头）不是有效 SQL 语句，不应加入
    # statements。例如 "SELECT 1; -- comment\n" 末尾的 "-- comment" 会被
    # buffer.strip() 捕获并误当作 SQL 执行，导致执行报错。
    residual = buffer.strip()
    if residual and not residual.startswith("--"):
        statements.append(residual)
    return statements


class Migration(BaseModel):
    """A single migration step."""
    version: int
    name: str
    up_sql: str = ""
    down_sql: str = ""
    checksum: str = ""  # SHA256 of up_sql for integrity check


class MigrationRecord(BaseModel):
    """Record of an applied migration."""
    version: int
    name: str
    applied_at: str = ""
    checksum: str = ""
    execution_ms: float = 0.0


class MigrationManager:
    """Manage database schema migrations.

    Uses a _migrations table to track applied versions.
    """

    def __init__(self, db_path: str | Path, migrations_dir: str | Path | None = None):
        self.db_path = Path(db_path)
        self.migrations_dir = Path(migrations_dir) if migrations_dir else self.db_path.parent / "migrations"
        self._ensure_migration_table()

    def _ensure_migration_table(self) -> None:
        with sqlite_connect(self.db_path, wal=True, foreign_keys=False) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    checksum TEXT NOT NULL DEFAULT '',
                    execution_ms REAL NOT NULL DEFAULT 0.0
                )
            """)

    def current_version(self) -> int:
        with sqlite_connect(self.db_path, wal=True, foreign_keys=False) as conn:
            row = conn.execute("SELECT MAX(version) FROM _migrations").fetchone()
            return row[0] if row and row[0] is not None else 0

    def applied_versions(self) -> list[MigrationRecord]:
        with sqlite_connect(self.db_path, wal=True, foreign_keys=False) as conn:
            rows = conn.execute(
                "SELECT version, name, applied_at, checksum, execution_ms FROM _migrations ORDER BY version"
            ).fetchall()
            return [
                MigrationRecord(version=r[0], name=r[1], applied_at=r[2], checksum=r[3], execution_ms=r[4])
                for r in rows
            ]

    def pending_migrations(self) -> list[Migration]:
        """Find migrations that haven't been applied yet."""
        current = self.current_version()
        available = self._discover_migrations()
        return [m for m in available if m.version > current]

    def _discover_migrations(self) -> list[Migration]:
        """Discover migration files from the migrations directory."""
        if not self.migrations_dir.is_dir():
            return []

        migrations = []
        pattern = re.compile(r"^(\d+)_(.+)\.sql$")

        for f in sorted(self.migrations_dir.glob("*.sql")):
            match = pattern.match(f.name)
            if match:
                version = int(match.group(1))
                name = match.group(2)
                content = f.read_text(encoding="utf-8")

                # Split on -- DOWN: marker
                parts = content.split("-- DOWN:")
                up_sql = parts[0].strip()
                down_sql = parts[1].strip() if len(parts) > 1 else ""

                # Compute checksum
                import hashlib
                checksum = hashlib.sha256(up_sql.encode()).hexdigest()[:16]

                migrations.append(Migration(
                    version=version,
                    name=name,
                    up_sql=up_sql,
                    down_sql=down_sql,
                    checksum=checksum,
                ))

        return sorted(migrations, key=lambda m: m.version)

    def apply(self, migration: Migration) -> MigrationRecord:
        with sqlite_connect(self.db_path, wal=True, foreign_keys=False) as conn:
            if migration.checksum:
                existing = conn.execute(
                    "SELECT checksum FROM _migrations WHERE version = ?",
                    (migration.version,),
                ).fetchone()
                if existing and existing[0] and existing[0] != migration.checksum:
                    raise ValueError(
                        f"Migration v{migration.version} checksum mismatch: "
                        f"expected {existing[0]}, got {migration.checksum}. "
                        f"Migration file may have been tampered with."
                    )
            t0 = time.monotonic()
            # P1-9 fix: 逐条执行而非 executescript，支持失败回滚。
            # executescript 会隐式 commit，中途失败时已执行语句无法回滚；
            # 逐条 execute 在事务内运行，失败时显式 ROLLBACK。
            statements = _split_sql_statements(migration.up_sql)
            try:
                for stmt in statements:
                    conn.execute(stmt)
            except Exception as exc:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise RuntimeError(
                    f"Migration v{migration.version} failed during execution, rolled back: {exc}"
                ) from exc
            exec_ms = (time.monotonic() - t0) * 1000

            applied_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO _migrations (version, name, applied_at, checksum, execution_ms) VALUES (?, ?, ?, ?, ?)",
                (migration.version, migration.name, applied_at, migration.checksum, exec_ms),
            )

            record = MigrationRecord(
                version=migration.version,
                name=migration.name,
                applied_at=applied_at,
                checksum=migration.checksum,
                execution_ms=exec_ms,
            )
            logger.info("Applied migration v%d: %s (%.1fms)", migration.version, migration.name, exec_ms)
            return record

    def rollback(self, migration: Migration) -> None:
        if not migration.down_sql:
            raise ValueError(f"Migration v{migration.version} has no down_sql (irreversible)")

        with sqlite_connect(self.db_path, wal=True, foreign_keys=False) as conn:
            # P1-9 fix: 逐条执行 down_sql，失败时回滚（与 apply 对齐）
            statements = _split_sql_statements(migration.down_sql)
            try:
                for stmt in statements:
                    conn.execute(stmt)
            except Exception as exc:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise RuntimeError(
                    f"Rollback of migration v{migration.version} failed, rolled back: {exc}"
                ) from exc
            conn.execute("DELETE FROM _migrations WHERE version = ?", (migration.version,))
            logger.info("Rolled back migration v%d: %s", migration.version, migration.name)

    def upgrade(self, target_version: int | None = None) -> list[MigrationRecord]:
        """Apply all pending migrations up to target_version (or all)."""
        pending = self.pending_migrations()
        if target_version is not None:
            pending = [m for m in pending if m.version <= target_version]

        if not pending:
            logger.info("No pending migrations")
            return []

        results = []
        for m in pending:
            results.append(self.apply(m))
        logger.info("Applied %d migration(s), now at v%d", len(results), self.current_version())
        return results

    def downgrade(self, steps: int = 1) -> list[Migration]:
        """Rollback the last N migrations."""
        applied = self.applied_versions()
        available = self._discover_migrations()
        avail_map = {m.version: m for m in available}

        to_rollback = applied[-steps:] if steps <= len(applied) else applied
        to_rollback = list(reversed(to_rollback))

        results = []
        for record in to_rollback:
            migration = avail_map.get(record.version)
            if migration:
                self.rollback(migration)
                results.append(migration)
            else:
                logger.warning("Migration v%d not found in migrations dir, skipping rollback", record.version)

        return results

    def status(self) -> dict[str, Any]:
        """Get migration status summary."""
        current = self.current_version()
        applied = self.applied_versions()
        pending = self.pending_migrations()
        return {
            "current_version": current,
            "applied_count": len(applied),
            "pending_count": len(pending),
            "applied": [r.model_dump() for r in applied],
            "pending": [{"version": m.version, "name": m.name} for m in pending],
        }


# ── Alembic Bridge (Enterprise) ──────────────────────────────

class AlembicBridge:
    """Bridge to Alembic for enterprise PostgreSQL migrations.

    Activated when MAOP_MIGRATION_BACKEND=alembic.
    Falls back to MigrationManager if Alembic is not installed.

    Usage::

        from maop.core.backends.migration import get_migration_backend
        mgr = get_migration_backend(db_path)
        mgr.upgrade()
    """

    def __init__(self, db_url: str | None = None) -> None:
        try:
            from alembic.config import Config

        except ImportError:
            raise ImportError(
                "alembic not installed. Install with: pip install maop-orchestrator[enterprise]"
            )
        self._db_url = db_url or os.getenv("MAOP_DATABASE_URL", "")
        self._config = Config()
        self._config.set_main_option("script_location", "maop/enterprise/alembic")
        if self._db_url:
            self._config.set_main_option("sqlalchemy.url", self._db_url)

    def upgrade(self, revision: str = "head") -> None:
        from alembic import command
        command.upgrade(self._config, revision)

    def downgrade(self, revision: str = "-1") -> None:
        from alembic import command
        command.downgrade(self._config, revision)

    def current(self) -> str | None:

        from alembic.runtime.migration import MigrationContext
        from sqlalchemy import create_engine
        engine = create_engine(self._db_url or "")
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            return context.get_current_revision()  # type: ignore

    def status(self) -> dict[str, Any]:
        try:
            rev = self.current()
            return {"backend": "alembic", "current_revision": rev, "db_url": self._db_url}
        except Exception as exc:
            return {"backend": "alembic", "error": str(exc)}



def get_migration_backend(
    db_path: str | Path | None = None,
    *,
    db_url: str | None = None,
) -> MigrationManager | AlembicBridge:
    """Get the appropriate migration backend based on configuration.

    Returns MigrationManager (SQLite, personal) or AlembicBridge (PostgreSQL, enterprise).
    Controlled by MAOP_MIGRATION_BACKEND env var: 'sql' (default) or 'alembic'.
    """
    backend = os.getenv("MAOP_MIGRATION_BACKEND", "sql").lower()
    if backend == "alembic":
        return AlembicBridge(db_url=db_url)
    if db_path is None:
        from maop.core.backends.db_utils import get_db_path
        db_path = get_db_path("migration")
    return MigrationManager(db_path)
