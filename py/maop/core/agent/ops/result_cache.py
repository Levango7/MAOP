"""ResultCache — Agent 调用结果缓存模块.

缓存 Agent 对相同任务的调用结果，避免重复计算，降低成本与延迟。

核心机制：
  - **缓存键**：``SHA256(agent_name + task)`` 前 16 字节（32 个十六进制字符）。
  - **TTL 过期**：每条缓存有生存时间，过期后自动失效。默认 3600 秒。
  - **LRU 淘汰**：缓存条目超过 ``max_entries`` 时，淘汰最久未访问的条目。
  - **命中统计**：维护全局命中 / 未命中计数器，计算命中率。

设计要点：
  - **线程安全**：``threading.RLock`` 保护 SQLite 操作与内存计数器。
  - **持久化**：SQLite 表 ``result_cache``（缓存条目）+ ``cache_meta``（全局计数器）。
  - **原子 upsert**：使用 ``ON CONFLICT DO UPDATE`` 保证并发写入一致性。
    （来源：2026-09-12-sqlite-read-modify-write-race-upsert-atomic-fix）

持久化：SQLite 表 ``result_cache`` + ``cache_meta``。
线程安全：``threading.RLock`` 保护内存状态；SQLite 由 WAL + busy_timeout 保证并发。
"""

from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


# ── Pydantic 模型 ────────────────────────────────────────────────
class CacheEntry(BaseModel):
    """单条缓存条目.

    各字段含义：
      - ``task_hash``：缓存键（SHA256 前 16 字节的十六进制表示）。
      - ``result``：缓存的结果内容。
      - ``created_at`` / ``expires_at``：创建 / 过期时间（ISO 8601 UTC）。
      - ``hit_count``：本条缓存的命中次数。
    """

    agent_name: str = Field(description="Agent 名称")
    task_hash: str = Field(description="任务哈希（缓存键）")
    result: str = Field(description="缓存结果")
    created_at: str = Field(description="创建时间（ISO 8601）")
    expires_at: str = Field(description="过期时间（ISO 8601）")
    hit_count: int = Field(default=0, ge=0, description="命中次数")


class CacheStats(BaseModel):
    """缓存统计信息.

    各字段含义：
      - ``total_entries``：当前缓存条目总数。
      - ``total_hits`` / ``total_misses``：全局累计命中 / 未命中次数。
      - ``hit_rate``：命中率（0.0-1.0）。
      - ``expired_entries``：已过期但未清理的条目数。
      - ``size_bytes``：缓存结果占用的字节数。
    """

    total_entries: int = Field(ge=0, description="缓存条目总数")
    total_hits: int = Field(ge=0, description="累计命中次数")
    total_misses: int = Field(ge=0, description="累计未命中次数")
    hit_rate: float = Field(ge=0.0, le=1.0, description="命中率 (0.0-1.0)")
    expired_entries: int = Field(ge=0, description="已过期未清理条目数")
    size_bytes: int = Field(ge=0, description="缓存结果字节数")


# ── ResultCache ─────────────────────────────────────────────────
class ResultCache:
    """Agent 调用结果缓存管理器.

    缓存 Agent 对相同任务的调用结果，支持 TTL 过期与 LRU 淘汰。

    Parameters
    ----------
    db_path : Path | None
        SQLite 持久化路径。``None`` 时由 ``get_db_path("result_cache")`` 解析。
    default_ttl_s : int
        默认 TTL（秒），默认 3600。
    max_entries : int
        最大缓存条目数，超过时 LRU 淘汰，默认 10000。
    """

    def __init__(
        self,
        db_path: Path | None = None,
        default_ttl_s: int = 3600,
        max_entries: int = 10000,
    ) -> None:
        if default_ttl_s <= 0:
            raise ValueError(f"default_ttl_s 必须为正整数，收到 {default_ttl_s}")
        if max_entries <= 0:
            raise ValueError(f"max_entries 必须为正整数，收到 {max_entries}")

        self._db_path: Path = Path(db_path) if db_path else get_db_path("result_cache")
        self._default_ttl_s: int = default_ttl_s
        self._max_entries: int = max_entries
        self._lock = threading.RLock()
        self._init_db()

    # ── 初始化 ──────────────────────────────────────────────────
    def _init_db(self) -> None:
        """初始化 SQLite 表（幂等）。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS result_cache (
                    task_hash TEXT PRIMARY KEY,
                    agent_name TEXT NOT NULL,
                    task TEXT NOT NULL,
                    result TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    last_accessed TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL DEFAULT 0
                )
            """)
            # 按 agent_name 建索引，加速 invalidate
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cache_agent "
                "ON result_cache(agent_name)",
            )
            # 按 last_accessed 建索引，加速 LRU 淘汰
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cache_lru "
                "ON result_cache(last_accessed)",
            )
            # 全局计数器表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_meta (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0
                )
            """)
            # 初始化计数器（幂等）
            conn.execute(
                "INSERT OR IGNORE INTO cache_meta (key, value) VALUES ('total_hits', 0)",
            )
            conn.execute(
                "INSERT OR IGNORE INTO cache_meta (key, value) VALUES ('total_misses', 0)",
            )

    # ── 缓存键 ──────────────────────────────────────────────────
    @staticmethod
    def _compute_hash(agent_name: str, task: str) -> str:
        """计算缓存键：SHA256(agent_name + task) 前 16 字节的十六进制表示.

        Returns
        -------
        str
            32 字符的十六进制字符串。
        """
        raw = (agent_name + task).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:32]

    # ── 获取缓存 ────────────────────────────────────────────────
    def get(self, agent_name: str, task: str) -> CacheEntry | None:
        """获取缓存结果.

        若缓存存在且未过期，更新命中次数与最后访问时间，返回缓存条目。
        若缓存不存在或已过期，记录未命中并返回 None。

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        task : str
            任务内容。

        Returns
        -------
        CacheEntry | None
            缓存条目；不存在或已过期返回 None。
        """
        task_hash = self._compute_hash(agent_name, task)
        now_iso = datetime.now(timezone.utc).isoformat()

        with self._lock, sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT agent_name, task_hash, result, created_at, expires_at, hit_count "
                "FROM result_cache WHERE task_hash = ?",
                (task_hash,),
            ).fetchone()

            if row is None:
                # 未命中
                self._increment_counter(conn, "total_misses")
                return None

            # 检查是否过期
            expires_at = row["expires_at"]
            if now_iso >= expires_at:
                # 已过期，删除并记录未命中
                conn.execute(
                    "DELETE FROM result_cache WHERE task_hash = ?",
                    (task_hash,),
                )
                self._increment_counter(conn, "total_misses")
                logger.debug("[result_cache] expired miss for %s/%s", agent_name, task_hash)
                return None

            # 命中：更新 hit_count 和 last_accessed
            new_hit_count = int(row["hit_count"]) + 1
            conn.execute(
                "UPDATE result_cache SET hit_count = ?, last_accessed = ? "
                "WHERE task_hash = ?",
                (new_hit_count, now_iso, task_hash),
            )
            self._increment_counter(conn, "total_hits")

            return CacheEntry(
                agent_name=row["agent_name"],
                task_hash=row["task_hash"],
                result=row["result"],
                created_at=row["created_at"],
                expires_at=row["expires_at"],
                hit_count=new_hit_count,
            )

    # ── 存入缓存 ────────────────────────────────────────────────
    def put(
        self,
        agent_name: str,
        task: str,
        result: str,
        ttl_s: int = 0,
    ) -> None:
        """存入缓存.

        若缓存键已存在，覆盖旧值（upsert）。存入后检查是否超过
        ``max_entries``，超过则 LRU 淘汰最久未访问的条目。

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        task : str
            任务内容。
        result : str
            要缓存的结果。
        ttl_s : int
            TTL（秒）。``0`` 时使用默认 TTL。
        """
        effective_ttl = ttl_s if ttl_s > 0 else self._default_ttl_s
        task_hash = self._compute_hash(agent_name, task)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        expires_iso = (now + timedelta(seconds=effective_ttl)).isoformat()
        size_bytes = len(result.encode("utf-8"))

        with self._lock, sqlite_connect(self._db_path) as conn:
            # 原子 upsert：ON CONFLICT 依赖 PRIMARY KEY 约束
            # （来源：2026-09-12-sqlite-read-modify-write-race-upsert-atomic-fix）
            conn.execute(
                """INSERT INTO result_cache
                   (task_hash, agent_name, task, result, created_at, expires_at,
                    hit_count, last_accessed, size_bytes)
                   VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                   ON CONFLICT(task_hash) DO UPDATE SET
                       agent_name = excluded.agent_name,
                       task = excluded.task,
                       result = excluded.result,
                       created_at = excluded.created_at,
                       expires_at = excluded.expires_at,
                       hit_count = 0,
                       last_accessed = excluded.last_accessed,
                       size_bytes = excluded.size_bytes""",
                (
                    task_hash,
                    agent_name,
                    task,
                    result,
                    now_iso,
                    expires_iso,
                    now_iso,
                    size_bytes,
                ),
            )

            # LRU 淘汰：超过 max_entries 时删除最久未访问的
            count = conn.execute(
                "SELECT COUNT(*) AS cnt FROM result_cache",
            ).fetchone()["cnt"]
            if count > self._max_entries:
                to_evict = count - self._max_entries
                conn.execute(
                    "DELETE FROM result_cache WHERE task_hash IN ("
                    "  SELECT task_hash FROM result_cache "
                    "  ORDER BY last_accessed ASC LIMIT ?"
                    ")",
                    (to_evict,),
                )
                logger.debug("[result_cache] LRU evicted %d entries", to_evict)

        logger.debug(
            "[result_cache] put %s/%s (ttl=%ds, size=%d bytes)",
            agent_name, task_hash, effective_ttl, size_bytes,
        )

    # ── 失效缓存 ────────────────────────────────────────────────
    def invalidate(self, agent_name: str, task: str = "") -> int:
        """失效缓存.

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        task : str
            任务内容。为空时失效该 Agent 的所有缓存。

        Returns
        -------
        int
            实际失效的条目数。
        """
        with self._lock, sqlite_connect(self._db_path) as conn:
            if task:
                task_hash = self._compute_hash(agent_name, task)
                cursor = conn.execute(
                    "DELETE FROM result_cache WHERE task_hash = ?",
                    (task_hash,),
                )
            else:
                cursor = conn.execute(
                    "DELETE FROM result_cache WHERE agent_name = ?",
                    (agent_name,),
                )
            count = cursor.rowcount
        logger.debug(
            "[result_cache] invalidated %d entries for %s (task=%r)",
            count, agent_name, task,
        )
        return max(count, 0)

    # ── 清理过期 ────────────────────────────────────────────────
    def cleanup_expired(self) -> int:
        """清理所有已过期的缓存条目.

        Returns
        -------
        int
            清理的条目数。
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock, sqlite_connect(self._db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM result_cache WHERE expires_at < ?",
                (now_iso,),
            )
            count = cursor.rowcount
        if count > 0:
            logger.debug("[result_cache] cleaned up %d expired entries", count)
        return max(count, 0)

    # ── 缓存统计 ────────────────────────────────────────────────
    def get_stats(self) -> CacheStats:
        """获取缓存统计信息.

        Returns
        -------
        CacheStats
            缓存统计结果。
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock, sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total, "
                "COALESCE(SUM(size_bytes), 0) AS size_bytes "
                "FROM result_cache",
            ).fetchone()
            total_entries = int(row["total"])
            size_bytes = int(row["size_bytes"])

            expired = conn.execute(
                "SELECT COUNT(*) AS cnt FROM result_cache WHERE expires_at < ?",
                (now_iso,),
            ).fetchone()["cnt"]

            total_hits = self._get_counter(conn, "total_hits")
            total_misses = self._get_counter(conn, "total_misses")

        total_accesses = total_hits + total_misses
        hit_rate = total_hits / total_accesses if total_accesses > 0 else 0.0

        return CacheStats(
            total_entries=total_entries,
            total_hits=total_hits,
            total_misses=total_misses,
            hit_rate=hit_rate,
            expired_entries=int(expired),
            size_bytes=size_bytes,
        )

    # ── 清空所有 ────────────────────────────────────────────────
    def clear_all(self) -> None:
        """清空所有缓存条目（不影响计数器）。"""
        with self._lock, sqlite_connect(self._db_path) as conn:
            conn.execute("DELETE FROM result_cache")
        logger.debug("[result_cache] cleared all entries")

    # ── 内部工具 ────────────────────────────────────────────────
    @staticmethod
    def _increment_counter(conn, key: str) -> None:
        """递增全局计数器（在当前连接事务内）."""
        conn.execute(
            "INSERT INTO cache_meta (key, value) VALUES (?, 1) "
            "ON CONFLICT(key) DO UPDATE SET value = value + 1",
            (key,),
        )

    @staticmethod
    def _get_counter(conn, key: str) -> int:
        """获取全局计数器值."""
        row = conn.execute(
            "SELECT value FROM cache_meta WHERE key = ?",
            (key,),
        ).fetchone()
        return int(row["value"]) if row else 0