"""Legacy → Unified Memory 数据迁移工具 (F1-03).

背景
----
MAOP 历史上存在多套记忆实现，各自维护独立的 DB 文件 / JSON 文件：

1. **legacy MemoryStore** (``maop.memory.store``)
   - SQLite ``data/memory.db`` (per-module 模式) 或 ``data/maop.db`` (unified)
   - 表：``memory_entries`` / ``memory_traces`` / ``memory_trajectory``
   - JSON 双写已在 ADR-011 移除，但旧部署可能仍有 ``data/memory.json`` /
     ``data/wiki.json`` 残留

2. **legacy ThreeLayerMemory** (``maop.core.memory.three_layer_memory``)
   - 独立 SQLite ``data/episodic.db``
   - 表：``episodic_memory``
   - 已由 ``shared_db.migrate_legacy_episodic_db`` 处理

3. **Unified 目标** (F1-03)
   - 统一 SQLite ``data/maop.db``
   - 同时包含 ``memory_entries`` (chat) 与 ``episodic_memory`` (agent)
   - 由 ``MemoryFacade`` 统一访问

本模块的作用
------------
提供从 legacy 格式到 Unified 格式的迁移工具：

- ``migrate_legacy_memory_db(root_dir, dry_run=False, progress=False)``
  从独立的 ``data/memory.db`` 迁移 ``memory_entries`` 等表到统一 ``maop.db``。
- ``migrate_legacy_episodic_db(root_dir, dry_run=False, progress=False)``
  从独立的 ``data/episodic.db`` 迁移 ``episodic_memory`` 表。封装
  ``shared_db.migrate_legacy_episodic_db`` 并增加 dry_run / progress。
- ``migrate_legacy_json_files(root_dir, dry_run=False, progress=False)``
  从 ``data/memory.json`` / ``data/wiki.json`` 导入到 SQLite。
- ``migrate_all(root_dir, dry_run=False, progress=False)``
  一键执行上述全部迁移，返回汇总报告。
- ``MigrationReport`` pydantic model：迁移进度与结果。

设计原则
--------
- **幂等**：重复执行不产生重复数据（使用 ``INSERT OR IGNORE``）。
- **dry-run**：不写入，仅报告将要迁移的行数。
- **progress**：可选回调，每迁移一批行调用一次。
- **不删除源文件**：迁移成功后保留原文件，由调用方决定清理。
- **向后兼容**：迁移后 legacy 路径仍可读（数据未删）。

使用示例
--------
::

    from maop.migrations.memory_migration import migrate_all, MigrationReport

    # Dry-run 查看将要迁移什么
    report = migrate_all(root_dir="/path/to/MAOP", dry_run=True)
    print(report.summary())

    # 实际迁移
    report = migrate_all(root_dir="/path/to/MAOP", dry_run=False, progress=True)
    print(report.summary())

CLI::

    python -m maop.migrations.memory_migration --root /path/to/MAOP --dry-run
    python -m maop.migrations.memory_migration --root /path/to/MAOP
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import sqlite_connect
from maop.memory.shared_db import get_memory_db_path

logger = logging.getLogger("maop.migrations.memory_migration")


# ── 迁移报告 ────────────────────────────────────────────────────


class TableMigrationResult(BaseModel):
    """单表迁移结果。"""

    table: str
    source: str = ""
    destination: str = ""
    candidates: int = 0  # 源表中读取的行数
    migrated: int = 0  # 实际写入目标表的行数
    skipped: int = 0  # 因主键冲突跳过的行数
    errors: int = 0  # 写入失败行数
    dry_run: bool = False
    duration_s: float = 0.0
    error_messages: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        flag = " [dry-run]" if self.dry_run else ""
        return (
            f"  {self.table}{flag}: "
            f"{self.migrated}/{self.candidates} migrated, "
            f"{self.skipped} skipped, {self.errors} errors "
            f"({self.duration_s:.2f}s)"
        )


class MigrationReport(BaseModel):
    """整体迁移报告。"""

    root_dir: str
    dry_run: bool = False
    started_at: str = ""
    finished_at: str = ""
    duration_s: float = 0.0
    tables: list[TableMigrationResult] = Field(default_factory=list)

    @property
    def total_candidates(self) -> int:
        return sum(t.candidates for t in self.tables)

    @property
    def total_migrated(self) -> int:
        return sum(t.migrated for t in self.tables)

    @property
    def total_skipped(self) -> int:
        return sum(t.skipped for t in self.tables)

    @property
    def total_errors(self) -> int:
        return sum(t.errors for t in self.tables)

    def summary(self) -> str:
        lines = [
            f"MigrationReport(root={self.root_dir}, dry_run={self.dry_run})",
            (f"  total: {self.total_migrated}/{self.total_candidates} migrated, "
            f"{self.total_skipped} skipped, {self.total_errors} errors "
            f"({self.duration_s:.2f}s)"),
        ]
        for t in self.tables:
            lines.append(t.summary())
        return "\n".join(lines)


# ── 进度回调类型 ────────────────────────────────────────────────

ProgressCallback = Callable[[str, int, int], None]
"""进度回调：(table_name, current, total) → None."""


def _noop_progress(table: str, current: int, total: int) -> None:
    """默认 no-op 进度回调。"""
    return


# ── 通用工具 ────────────────────────────────────────────────────


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cur = conn.execute(f"SELECT * FROM {table} LIMIT 0")
    return [d[0] for d in cur.description] if cur.description else []


def _count_rows(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return 0


def _ensure_target_schema(db_path: Path) -> None:
    """确保目标 DB 有 memory_entries / episodic_memory 表及 FTS5 索引。

    Schema 由 MemoryStore / ThreeLayerMemory 在初始化时创建。迁移前若
    目标 DB 不存在这些表，则用与源 DB 相同的列定义创建（CREATE TABLE IF NOT EXISTS）。
    同时创建 FTS5 虚拟表与 trigger，使迁移 INSERT 的数据被全文索引。
    """
    from maop.memory.models import _FTS5_DDL, _MEMORY_DDL

    try:
        with sqlite_connect(db_path, foreign_keys=False) as conn:
            conn.executescript(_MEMORY_DDL)
            # 尝试创建 FTS5 表与 trigger（若 SQLite 不支持 FTS5 则跳过）
            try:
                conn.executescript(_FTS5_DDL)
            except Exception as exc:
                logger.debug("[memory_migration] FTS5 schema skipped: %s", exc)
    except Exception as exc:
        logger.warning("[memory_migration] ensure memory_entries schema failed: %s", exc)

    # episodic_memory schema
    try:
        # T2 拆分后 _EPISODIC_DDL 位于 episodic_store.py（three_layer_memory
        # 主文件不再承载该常量）。
        from maop.core.memory.episodic_store import _EPISODIC_DDL

        with sqlite_connect(db_path, foreign_keys=False) as conn:
            conn.executescript(_EPISODIC_DDL)
    except Exception as exc:
        logger.warning("[memory_migration] ensure episodic_memory schema failed: %s", exc)


def _migrate_table(
    src_path: Path,
    dst_path: Path,
    table: str,
    *,
    dry_run: bool = False,
    batch_size: int = 500,
    progress: ProgressCallback | None = None,
) -> TableMigrationResult:
    """通用单表迁移：从 src_path.{table} → dst_path.{table}。

    使用 ``INSERT OR IGNORE`` 保证幂等。源与目标列定义可以不同——只迁移
    交集列，源独有列被丢弃，目标独有列使用默认值。
    """
    result = TableMigrationResult(
        table=table,
        source=str(src_path),
        destination=str(dst_path),
        dry_run=dry_run,
    )
    start = time.time()
    progress = progress or _noop_progress

    if not src_path.exists():
        result.duration_s = time.time() - start
        return result
    if src_path.resolve() == dst_path.resolve():
        # 同一文件，无需迁移
        result.duration_s = time.time() - start
        return result

    try:
        with sqlite3.connect(str(src_path)) as src:
            src.row_factory = sqlite3.Row
            if not _table_exists(src, table):
                result.duration_s = time.time() - start
                return result

            total = _count_rows(src, table)
            result.candidates = total
            if total == 0:
                result.duration_s = time.time() - start
                return result

            src_cols = _table_columns(src, table)

            # 读取所有行（小表足够；大表可改用分页）
            cursor = src.execute(f"SELECT * FROM {table}")
            rows = cursor.fetchall()
    except sqlite3.Error as exc:
        result.errors = 1
        result.error_messages.append(f"read source failed: {exc}")
        result.duration_s = time.time() - start
        return result

    if dry_run:
        # dry-run 只报告 candidates，不写入
        result.migrated = 0
        result.skipped = total
        progress(table, total, total)
        result.duration_s = time.time() - start
        return result

    # 写入目标
    try:
        _ensure_target_schema(dst_path)
        with sqlite_connect(dst_path, foreign_keys=False) as dst:
            # 取目标表实际列
            if not _table_exists(dst, table):
                result.errors = 1
                result.error_messages.append(f"target table {table} not exists")
                result.duration_s = time.time() - start
                return result
            dst_cols = _table_columns(dst, table)
            common_cols = [c for c in src_cols if c in dst_cols]
            if not common_cols:
                result.errors = 1
                result.error_messages.append("no common columns between source and target")
                result.duration_s = time.time() - start
                return result

            placeholders = ",".join("?" * len(common_cols))
            cols_csv = ",".join(common_cols)
            sql = (
                f"INSERT OR IGNORE INTO {table} ({cols_csv}) "
                f"VALUES ({placeholders})"
            )

            migrated = 0
            skipped = 0
            errors = 0
            error_msgs: list[str] = []
            for i, row in enumerate(rows):
                try:
                    values = [row[c] for c in common_cols]
                    before = dst.total_changes
                    dst.execute(sql, values)
                    after = dst.total_changes
                    if after > before:
                        migrated += 1
                    else:
                        skipped += 1
                except sqlite3.Error as exc:
                    errors += 1
                    if len(error_msgs) < 10:
                        error_msgs.append(str(exc))

                if (i + 1) % batch_size == 0 or (i + 1) == len(rows):
                    progress(table, i + 1, len(rows))

            result.migrated = migrated
            result.skipped = skipped
            result.errors = errors
            result.error_messages.extend(error_msgs)
    except Exception as exc:
        result.errors += 1
        result.error_messages.append(f"write target failed: {exc}")

    result.duration_s = time.time() - start
    return result


# ── 三个迁移入口 ────────────────────────────────────────────────


def migrate_legacy_memory_db(
    root_dir: str | Path,
    *,
    dry_run: bool = False,
    progress: ProgressCallback | None = None,
) -> list[TableMigrationResult]:
    """从独立的 ``data/memory.db`` 迁移到统一 ``maop.db``。

    迁移表：``memory_entries`` / ``memory_traces`` / ``memory_trajectory``。
    若源文件不存在或与目标同路径，返回空列表。
    """
    root = Path(root_dir)
    src_path = root / "data" / "memory.db"
    dst_path = get_memory_db_path()

    if not src_path.exists() or src_path.resolve() == dst_path.resolve():
        return []

    results: list[TableMigrationResult] = []
    for table in ("memory_entries", "memory_traces", "memory_trajectory"):
        logger.info("[memory_migration] migrating table %s from %s", table, src_path)
        r = _migrate_table(
            src_path, dst_path, table,
            dry_run=dry_run, progress=progress,
        )
        results.append(r)
    return results


def migrate_legacy_episodic_db(
    root_dir: str | Path,
    *,
    dry_run: bool = False,
    progress: ProgressCallback | None = None,
) -> TableMigrationResult:
    """从独立的 ``data/episodic.db`` 迁移 ``episodic_memory`` 表。

    封装 ``shared_db.migrate_legacy_episodic_db`` 并增加 dry_run / progress。
    """
    root = Path(root_dir)
    src_path = root / "data" / "episodic.db"
    dst_path = get_memory_db_path()

    if not src_path.exists() or src_path.resolve() == dst_path.resolve():
        return TableMigrationResult(
            table="episodic_memory",
            source=str(src_path),
            destination=str(dst_path),
            dry_run=dry_run,
        )

    return _migrate_table(
        src_path, dst_path, "episodic_memory",
        dry_run=dry_run, progress=progress,
    )


def migrate_legacy_json_files(
    root_dir: str | Path,
    *,
    dry_run: bool = False,
    progress: ProgressCallback | None = None,
) -> list[TableMigrationResult]:
    """从 legacy JSON 文件 (``data/memory.json`` / ``data/wiki.json``) 导入。

    legacy ``memory.json`` 格式（ADR-011 前的 dual-write）::

        {
          "entries": [
            {"id": "...", "agent": "...", "task": "...", "content": "...",
             "tags": ["a","b"], "topic": "...", "timestamp": "..."},
            ...
          ]
        }

    legacy ``wiki.json`` 格式类似但 ``entries`` 字段名可能为 ``wiki_entries``。
    本函数将两者合并导入到 ``memory_entries`` 表。
    """
    root = Path(root_dir)
    dst_path = get_memory_db_path()
    progress = progress or _noop_progress

    results: list[TableMigrationResult] = []

    for json_file, table in (
        (root / "data" / "memory.json", "memory_entries"),
        (root / "data" / "wiki.json", "memory_entries"),
    ):
        result = TableMigrationResult(
            table=table,
            source=str(json_file),
            destination=str(dst_path),
            dry_run=dry_run,
        )
        start = time.time()

        if not json_file.exists():
            result.duration_s = time.time() - start
            results.append(result)
            continue

        try:
            with json_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            result.errors = 1
            result.error_messages.append(f"read json failed: {exc}")
            result.duration_s = time.time() - start
            results.append(result)
            continue

        # 兼容多种字段名
        entries: list[dict[str, Any]] = (
            data.get("entries")
            or data.get("wiki_entries")
            or data.get("memory_entries")
            or []
        )
        if not isinstance(entries, list):
            entries = []

        result.candidates = len(entries)
        progress(table, 0, len(entries))

        if dry_run or not entries:
            result.skipped = len(entries)
            progress(table, len(entries), len(entries))
            result.duration_s = time.time() - start
            results.append(result)
            continue

        # 写入目标
        try:
            _ensure_target_schema(dst_path)
            with sqlite_connect(dst_path, foreign_keys=False) as dst:
                # 取目标列
                dst_cols = _table_columns(dst, table)
                for i, entry in enumerate(entries):
                    if not isinstance(entry, dict):
                        result.errors += 1
                        continue
                    # 只取目标存在的列
                    common = [c for c in dst_cols if c in entry]
                    if not common:
                        result.errors += 1
                        continue
                    placeholders = ",".join("?" * len(common))
                    cols_csv = ",".join(common)
                    sql = (
                        f"INSERT OR IGNORE INTO {table} ({cols_csv}) "
                        f"VALUES ({placeholders})"
                    )
                    try:
                        before = dst.total_changes
                        dst.execute(sql, [entry[c] for c in common])
                        after = dst.total_changes
                        if after > before:
                            result.migrated += 1
                        else:
                            result.skipped += 1
                    except sqlite3.Error as exc:
                        result.errors += 1
                        if len(result.error_messages) < 10:
                            result.error_messages.append(str(exc))

                    if (i + 1) % 500 == 0 or (i + 1) == len(entries):
                        progress(table, i + 1, len(entries))
        except Exception as exc:
            result.errors += 1
            result.error_messages.append(f"write target failed: {exc}")

        result.duration_s = time.time() - start
        results.append(result)

    return results


def migrate_all(
    root_dir: str | Path,
    *,
    dry_run: bool = False,
    progress: bool | ProgressCallback = False,
) -> MigrationReport:
    """一键执行全部 legacy → Unified 迁移。

    Parameters
    ----------
    root_dir : str | Path
        MAOP 项目根目录。
    dry_run : bool
        若为 True，仅报告将要迁移的行数，不实际写入。
    progress : bool | ProgressCallback
        若为 True，使用默认 stdout 进度回调；若为 callable，使用该回调；
        若为 False，不输出进度。

    Returns
    -------
    MigrationReport
        包含所有表迁移结果的汇总报告。
    """
    start = time.time()
    from datetime import datetime, timezone

    started_at = datetime.now(timezone.utc).isoformat()

    cb: ProgressCallback
    if callable(progress):
        cb = progress
    elif progress is True:
        def _stdout_progress(table: str, current: int, total: int) -> None:
            if total > 0 and (current % 500 == 0 or current == total):
                pct = current * 100 // total
                logger.info("[memory_migration] %s: %d/%d (%d%%)", table, current, total, pct)

        cb = _stdout_progress
    else:
        cb = _noop_progress

    report = MigrationReport(
        root_dir=str(root_dir),
        dry_run=dry_run,
        started_at=started_at,
    )

    # 1. legacy memory.db → maop.db
    logger.info("[memory_migration] phase 1: legacy memory.db")
    report.tables.extend(migrate_legacy_memory_db(
        root_dir, dry_run=dry_run, progress=cb,
    ))

    # 2. legacy episodic.db → maop.db
    logger.info("[memory_migration] phase 2: legacy episodic.db")
    report.tables.append(migrate_legacy_episodic_db(
        root_dir, dry_run=dry_run, progress=cb,
    ))

    # 3. legacy JSON files → maop.db
    logger.info("[memory_migration] phase 3: legacy JSON files")
    report.tables.extend(migrate_legacy_json_files(
        root_dir, dry_run=dry_run, progress=cb,
    ))

    report.finished_at = datetime.now(timezone.utc).isoformat()
    report.duration_s = time.time() - start
    return report


# ── CLI ────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maop.migrations.memory_migration",
        description="Migrate legacy memory data to unified maop.db",
    )
    parser.add_argument(
        "--root", default=".", help="MAOP project root directory (default: cwd)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be migrated without writing",
    )
    parser.add_argument(
        "--progress", action="store_true",
        help="Show progress on stdout",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose logging",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。返回 0 表示成功，非 0 表示有错误。"""
    args = _build_parser().parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    report = migrate_all(
        root_dir=args.root,
        dry_run=args.dry_run,
        progress=args.progress,
    )
    print(report.summary())
    return 0 if report.total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "MigrationReport",
    "ProgressCallback",
    "TableMigrationResult",
    "main",
    "migrate_all",
    "migrate_legacy_episodic_db",
    "migrate_legacy_json_files",
    "migrate_legacy_memory_db",
]