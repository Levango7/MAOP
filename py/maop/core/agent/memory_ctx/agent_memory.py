"""MAOP Agent Memory — Agent 记忆存储与检索。

存储 agent 的交互历史、学习偏好、错误模式和性能指标，
为自进化模块提供数据基础。

记忆类型：
  - interaction: 交互记录（用户请求 + agent 响应）
  - preference: 学习到的用户偏好
  - error_pattern: 错误模式与解决方案
  - performance: 性能指标快照
  - lesson: 经验教训

Usage::

    from maop.core.agent.memory_ctx.agent_memory import AgentMemory

    memory = AgentMemory(root_dir="/path/to/MAOP")
    memory.store("claude", "interaction", {"prompt": "...", "response": "..."})
    records = memory.retrieve("claude", memory_type="error_pattern", limit=10)
    summary = memory.summarize("claude")
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)

# 记忆类型枚举
MEMORY_TYPES = {"interaction", "preference", "error_pattern", "performance", "lesson"}

# 自动清理策略：每种类型的最大保留条数
MAX_RECORDS_PER_TYPE = {
    "interaction": 500,
    "preference": 100,
    "error_pattern": 200,
    "performance": 1000,
    "lesson": 100,
}


class AgentMemory:
    """Agent 记忆存储引擎，使用 SQLite 持久化。"""

    def __init__(self, root_dir: str | Path = "data") -> None:
        self._root = Path(root_dir)
        self._db_path = get_db_path("agent_memory")
        self._init_db()

    def _init_db(self) -> None:
        """初始化记忆数据库表。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_name TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    importance REAL DEFAULT 0.5,
                    created_at TEXT NOT NULL,
                    expires_at TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_agent_type
                ON agent_memory(agent_name, memory_type)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_created
                ON agent_memory(created_at DESC)
            """)
            # 进化历史表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_evolution_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_name TEXT NOT NULL,
                    evolution_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    changes TEXT NOT NULL,
                    success BOOLEAN DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_evolution_agent
                ON agent_evolution_history(agent_name, created_at DESC)
            """)

    def store(
        self,
        agent_name: str,
        memory_type: str,
        content: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
    ) -> int:
        """存储一条记忆。

        Args:
            agent_name: agent 名称
            memory_type: 记忆类型 (interaction/preference/error_pattern/performance/lesson)
            content: 记忆内容（JSON 可序列化）
            metadata: 可选的元数据
            importance: 重要性 0.0-1.0，影响保留优先级

        Returns:
            记忆记录的 ID
        """
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"Invalid memory_type: {memory_type}. Must be one of {MEMORY_TYPES}")

        now = datetime.now(timezone.utc).isoformat()
        content_str = json.dumps(content, ensure_ascii=False, default=str)
        meta_str = json.dumps(metadata or {}, ensure_ascii=False, default=str)

        with sqlite_connect(self._db_path) as conn:
            cursor = conn.execute(
                """INSERT INTO agent_memory
                   (agent_name, memory_type, content, metadata, importance, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (agent_name, memory_type, content_str, meta_str, importance, now),
            )
            record_id = cursor.lastrowid

        # 自动清理：超过上限时删除最旧、最不重要的记录
        self._auto_cleanup(agent_name, memory_type)

        logger.debug(
            "[agent_memory] Stored %s memory #%d for agent '%s'",
            memory_type, record_id, agent_name,
        )
        return record_id or 0

    def retrieve(
        self,
        agent_name: str,
        memory_type: str | None = None,
        limit: int = 50,
        min_importance: float = 0.0,
    ) -> list[dict[str, Any]]:
        """检索 agent 的记忆。

        Args:
            agent_name: agent 名称
            memory_type: 可选，筛选特定类型
            limit: 最多返回条数
            min_importance: 最低重要性阈值

        Returns:
            记忆记录列表，每条含 id/type/content/metadata/importance/created_at
        """
        query = "SELECT * FROM agent_memory WHERE agent_name = ?"
        params: list[Any] = [agent_name]

        if memory_type:
            query += " AND memory_type = ?"
            params.append(memory_type)
        if min_importance > 0:
            query += " AND importance >= ?"
            params.append(min_importance)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(query, params).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def forget(self, agent_name: str, memory_id: int | None = None) -> int:
        """遗忘（删除）记忆。

        Args:
            agent_name: agent 名称
            memory_id: 可选，指定删除某条记忆；不指定则删除该 agent 的全部记忆

        Returns:
            删除的条数
        """
        with sqlite_connect(self._db_path) as conn:
            if memory_id is not None:
                cursor = conn.execute(
                    "DELETE FROM agent_memory WHERE id = ? AND agent_name = ?",
                    (memory_id, agent_name),
                )
            else:
                cursor = conn.execute(
                    "DELETE FROM agent_memory WHERE agent_name = ?",
                    (agent_name,),
                )
            deleted = cursor.rowcount

        logger.info(
            "[agent_memory] Forgot %d memories for agent '%s'",
            deleted, agent_name,
        )
        return deleted

    def forget_type(self, agent_name: str, memory_type: str) -> int:
        """删除指定类型的全部记忆。"""
        with sqlite_connect(self._db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM agent_memory WHERE agent_name = ? AND memory_type = ?",
                (agent_name, memory_type),
            )
            return cursor.rowcount

    def summarize(self, agent_name: str) -> dict[str, Any]:
        """总结 agent 的记忆状态。

        返回各类型的记忆数量、最近活动时间、平均重要性等统计信息。
        """
        with sqlite_connect(self._db_path) as conn:
            # 各类型计数
            type_counts: dict[str, int] = {}
            for mtype in MEMORY_TYPES:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM agent_memory WHERE agent_name = ? AND memory_type = ?",
                    (agent_name, mtype),
                ).fetchone()
                type_counts[mtype] = row["cnt"] if row else 0

            # 最近活动
            recent = conn.execute(
                "SELECT created_at FROM agent_memory WHERE agent_name = ? ORDER BY created_at DESC LIMIT 1",
                (agent_name,),
            ).fetchone()
            last_activity = recent["created_at"] if recent else ""

            # 平均重要性
            avg_row = conn.execute(
                "SELECT AVG(importance) as avg_imp FROM agent_memory WHERE agent_name = ?",
                (agent_name,),
            ).fetchone()
            avg_importance = round(avg_row["avg_imp"] or 0.0, 3) if avg_row else 0.0

            # 最常见的错误模式
            error_patterns: list[dict[str, Any]] = []
            error_rows = conn.execute(
                """SELECT content, COUNT(*) as freq FROM agent_memory
                   WHERE agent_name = ? AND memory_type = 'error_pattern'
                   GROUP BY content ORDER BY freq DESC LIMIT 5""",
                (agent_name,),
            ).fetchall()
            for row in error_rows:
                try:
                    content = json.loads(row["content"])
                    error_patterns.append({"pattern": content, "frequency": row["freq"]})
                except Exception as e:
                    logger.debug("ignored: %s", e, exc_info=True)

            # 进化历史计数
            evo_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM agent_evolution_history WHERE agent_name = ?",
                (agent_name,),
            ).fetchone()
            evolution_count = evo_row["cnt"] if evo_row else 0

        total = sum(type_counts.values())
        return {
            "agent_name": agent_name,
            "total_memories": total,
            "by_type": type_counts,
            "last_activity": last_activity,
            "avg_importance": avg_importance,
            "top_error_patterns": error_patterns,
            "evolution_count": evolution_count,
        }

    def record_evolution(
        self,
        agent_name: str,
        evolution_type: str,
        description: str,
        changes: dict[str, Any],
        success: bool = True,
    ) -> None:
        """记录一次自进化事件。"""
        now = datetime.now(timezone.utc).isoformat()
        changes_str = json.dumps(changes, ensure_ascii=False, default=str)
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO agent_evolution_history
                   (agent_name, evolution_type, description, changes, success, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (agent_name, evolution_type, description, changes_str, success, now),
            )

    def get_evolution_history(self, agent_name: str, limit: int = 20) -> list[dict[str, Any]]:
        """获取 agent 的自进化历史。"""
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM agent_evolution_history
                   WHERE agent_name = ? ORDER BY created_at DESC LIMIT ?""",
                (agent_name, limit),
            ).fetchall()
        result = []
        for row in rows:
            try:
                changes = json.loads(row["changes"])
            except Exception:
                changes = {}
            result.append({
                "id": row["id"],
                "evolution_type": row["evolution_type"],
                "description": row["description"],
                "changes": changes,
                "success": bool(row["success"]),
                "created_at": row["created_at"],
            })
        return result

    def _auto_cleanup(self, agent_name: str, memory_type: str) -> None:
        """自动清理超出上限的旧记忆。"""
        max_count = MAX_RECORDS_PER_TYPE.get(memory_type, 200)
        with sqlite_connect(self._db_path) as conn:
            count_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM agent_memory WHERE agent_name = ? AND memory_type = ?",
                (agent_name, memory_type),
            ).fetchone()
            count = count_row["cnt"] if count_row else 0
            if count > max_count:
                # 删除最旧、最不重要的记录
                conn.execute(
                    """DELETE FROM agent_memory WHERE id IN (
                        SELECT id FROM agent_memory
                        WHERE agent_name = ? AND memory_type = ?
                        ORDER BY importance ASC, created_at ASC
                        LIMIT ?
                    )""",
                    (agent_name, memory_type, count - max_count),
                )
                logger.debug(
                    "[agent_memory] Auto-cleaned %d old %s memories for '%s'",
                    count - max_count, memory_type, agent_name,
                )

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        """将数据库行转换为字典。"""
        try:
            content = json.loads(row["content"])
        except Exception:
            content = {"raw": row["content"]}
        try:
            metadata = json.loads(row["metadata"])
        except Exception:
            metadata = {}
        return {
            "id": row["id"],
            "agent_name": row["agent_name"],
            "memory_type": row["memory_type"],
            "content": content,
            "metadata": metadata,
            "importance": row["importance"],
            "created_at": row["created_at"],
        }
