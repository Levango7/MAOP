"""MAOP Agent Catalog Store — AgentCatalog 的 SQLite 持久化层.

将 ``AgentDescriptor`` 序列化为 ``agent_catalog`` 表行，提供 CRUD：
  - ``ensure_table``  — 幂等建表
  - ``upsert``        — 插入或更新（按 name 主键）
  - ``load_all``      — 全量加载
  - ``load_one``      — 按 name 加载
  - ``delete``        — 按 name 删除
  - ``update_health`` — 更新健康状态 + 检查时间戳

复用 ``maop.core.backends.db_utils.sqlite_connect`` 上下文管理器（WAL +
busy_timeout + 自动 commit/rollback），与 AgentProxy / AgentRegistry 保持
一致的持久化风格。

Schema 设计说明：
  - ``name`` 为 PRIMARY KEY，与 AgentCatalog 内存索引对齐。
  - 列表/字典字段（capabilities / adapter_config / billing_config /
    fallback_agents）以 JSON 文本存储，读写时 json.loads/dumps。
  - 枚举字段（billing_model / auth_method / adapter_type）以 TEXT 存储，
    存原始字符串值，由 AgentDescriptor 在反序列化时校验。
  - ``created_at`` / ``updated_at`` 用 Unix epoch（REAL），与 agent_proxy_state
    保持一致。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


# ── DDL ────────────────────────────────────────────────────────────
# 幂等建表：CREATE TABLE IF NOT EXISTS。列定义与 AgentDescriptor 字段一一对应。
_AGENT_CATALOG_DDL = """
CREATE TABLE IF NOT EXISTS agent_catalog (
    name                 TEXT PRIMARY KEY,
    display_name         TEXT NOT NULL DEFAULT '',
    vendor               TEXT DEFAULT '',
    version              TEXT DEFAULT '',
    adapter_type         TEXT DEFAULT '',
    adapter_config       TEXT DEFAULT '{}',
    capabilities         TEXT DEFAULT '[]',
    max_context_length   INTEGER DEFAULT 0,
    supports_streaming   INTEGER DEFAULT 0,
    supports_tool_call   INTEGER DEFAULT 0,
    billing_model        TEXT DEFAULT 'blackbox',
    billing_config       TEXT DEFAULT '{}',
    auth_method          TEXT DEFAULT 'none',
    region               TEXT DEFAULT 'international',
    auth_credentials_ref TEXT DEFAULT '',
    max_concurrent       INTEGER DEFAULT 1,
    rate_limit_per_min   INTEGER DEFAULT 0,
    timeout_s            REAL DEFAULT 30.0,
    retry_count          INTEGER DEFAULT 3,
    fallback_agents      TEXT DEFAULT '[]',
    enabled              INTEGER DEFAULT 1,
    healthy              INTEGER DEFAULT 1,
    last_health_check    REAL DEFAULT 0.0,
    created_at           REAL NOT NULL,
    updated_at           REAL DEFAULT 0.0
);
"""

# 列名顺序（与 _row_to_dict 对齐），用于 INSERT OR REPLACE。
_COLUMNS = (
    "name", "display_name", "vendor", "version",
    "adapter_type", "adapter_config", "capabilities",
    "max_context_length", "supports_streaming", "supports_tool_call",
    "billing_model", "billing_config", "auth_method", "region", "auth_credentials_ref",
    "max_concurrent", "rate_limit_per_min", "timeout_s", "retry_count",
    "fallback_agents", "enabled", "healthy", "last_health_check",
    "created_at", "updated_at",
)


class AgentCatalogStore:
    """SQLite 持久化层 for AgentCatalog.

    Parameters
    ----------
    db_path : str | Path | None
        SQLite 数据库路径。``None`` 时通过 ``get_db_path("agent_catalog")``
        解析（统一 maop.db 或 per-module 模式）。
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path: Path = Path(db_path) if db_path else get_db_path("agent_catalog")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ensure_table()

    def _connect(self):
        """上下文管理器包装的 SQLite 连接（WAL + 自动 commit/rollback）。"""
        return sqlite_connect(self._db_path, foreign_keys=False)

    # ── 建表 ──────────────────────────────────────────────────────
    def ensure_table(self) -> None:
        """幂等建表：重复调用安全（CREATE TABLE IF NOT EXISTS）。

        包含向后兼容的列迁移：旧表缺少 ``region`` 列时自动
        ALTER TABLE ADD COLUMN，已有数据默认 'international'。
        """
        with self._connect() as conn:
            conn.executescript(_AGENT_CATALOG_DDL)
            # 迁移：为旧表补充 region 列（SQLite 不支持 ADD COLUMN IF NOT EXISTS）
            self._migrate_add_region(conn)

    @staticmethod
    def _migrate_add_region(conn: sqlite3.Connection) -> None:
        """若 agent_catalog 表缺少 region 列，则 ALTER TABLE ADD COLUMN。

        已有数据默认 region='international'，保证向后兼容。
        幂等：列已存在时跳过。
        """
        cursor = conn.execute("PRAGMA table_info(agent_catalog)")
        columns = {row[1] for row in cursor.fetchall()}
        if "region" not in columns:
            conn.execute(
                "ALTER TABLE agent_catalog ADD COLUMN region TEXT DEFAULT 'international'"
            )
            logger.info("[agent_catalog_store] migrated: added 'region' column with default 'international'")

    # ── Upsert ────────────────────────────────────────────────────
    def upsert(self, data: dict[str, Any]) -> None:
        """插入或更新一条 Agent 记录（按 name 主键）。

        ``data`` 应为 ``AgentDescriptor.model_dump()`` 的结果（含枚举的
        value 字符串）。列表/字典字段在此处 json.dumps 为文本。
        """
        now = time.time()
        # created_at 仅在首次插入时写入；若记录已存在则保留原值。
        existing = self.load_one(data["name"])
        created_at = existing.get("created_at", now) if existing else now

        row = (
            data["name"],
            data.get("display_name", ""),
            data.get("vendor", ""),
            data.get("version", ""),
            data.get("adapter_type", ""),
            json.dumps(data.get("adapter_config", {}), default=str),
            json.dumps(data.get("capabilities", []), default=str),
            int(data.get("max_context_length", 0)),
            1 if data.get("supports_streaming", False) else 0,
            1 if data.get("supports_tool_call", False) else 0,
            data.get("billing_model", "blackbox"),
            json.dumps(data.get("billing_config", {}), default=str),
            data.get("auth_method", "none"),
            data.get("region", "international"),
            data.get("auth_credentials_ref", ""),
            int(data.get("max_concurrent", 1)),
            int(data.get("rate_limit_per_min", 0)),
            float(data.get("timeout_s", 30.0)),
            int(data.get("retry_count", 3)),
            json.dumps(data.get("fallback_agents", []), default=str),
            1 if data.get("enabled", True) else 0,
            1 if data.get("healthy", True) else 0,
            float(data.get("last_health_check", 0.0)),
            created_at,
            now,
        )
        placeholders = ", ".join("?" for _ in _COLUMNS)
        col_list = ", ".join(_COLUMNS)
        with self._connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO agent_catalog ({col_list}) VALUES ({placeholders})",
                row,
            )

    # ── Read ──────────────────────────────────────────────────────
    def load_all(self) -> list[dict[str, Any]]:
        """全量加载所有 Agent 记录为 dict 列表。"""
        with self._connect() as conn:
            cursor = conn.execute("SELECT * FROM agent_catalog")
            return [self._row_to_dict(r) for r in cursor.fetchall()]

    def load_one(self, name: str) -> dict[str, Any] | None:
        """按 name 加载单条记录，不存在返回 None。"""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM agent_catalog WHERE name = ?", (name,)
            )
            row = cursor.fetchone()
        return self._row_to_dict(row) if row else None

    # ── Delete ────────────────────────────────────────────────────
    def delete(self, name: str) -> bool:
        """按 name 删除一条记录。返回是否实际删除了行。"""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM agent_catalog WHERE name = ?", (name,)
            )
            return cursor.rowcount > 0

    # ── Health update ─────────────────────────────────────────────
    def update_health(self, name: str, healthy: bool, ts: float | None = None) -> None:
        """更新指定 Agent 的健康状态 + 检查时间戳。

        记录不存在时静默忽略（no-op），由上层 AgentCatalog 决定是否告警。
        """
        now = ts if ts is not None else time.time()
        with self._connect() as conn:
            conn.execute(
                """UPDATE agent_catalog
                   SET healthy = ?, last_health_check = ?, updated_at = ?
                   WHERE name = ?""",
                (1 if healthy else 0, now, now, name),
            )

    # ── Row → dict ────────────────────────────────────────────────
    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        """将 sqlite3.Row 转换为 AgentDescriptor 兼容的 dict。

        JSON 文本字段在此处 json.loads 还原为 list/dict；
        布尔字段从 INTEGER 还原为 bool。
        """
        d = dict(row)
        # JSON 字段还原：capabilities / fallback_agents → list, *_config → dict
        for key in ("capabilities", "fallback_agents", "adapter_config", "billing_config"):
            raw = d.get(key)
            if isinstance(raw, str):
                try:
                    d[key] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    d[key] = {} if key.endswith("config") else []
        # 布尔字段还原
        for key in ("supports_streaming", "supports_tool_call", "enabled", "healthy"):
            if key in d:
                d[key] = bool(d[key])
        return d