"""MAOP Agent Identity — 联邦身份映射表.

打通四套独立的 Agent ID 体系：

* ``AgentCatalog`` (SQLite, name 主键) — 元数据编目
* ``AgentRegistry`` (SQLite, name 主键) — CLI 发现 + 健康探测
* ``RedisAgentRegistry`` (Redis, card_id 主键) — 分布式 A2A 发现
* ``WorkerRegistry`` (Redis, worker_id 主键) — 分布式执行节点

本模块维护 ``agent_identity`` 表，字段 ``agent_name`` 与 ``card_id``
/ ``worker_id`` 做双向映射，使健康状态、能力、下线事件能在四套
体系间传播，避免同一个 agent 在不同子系统中被当作两个不同实体。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


_AGENT_IDENTITY_DDL = """
CREATE TABLE IF NOT EXISTS agent_identity (
    agent_name    TEXT NOT NULL,
    card_id       TEXT,
    worker_id     TEXT,
    source        TEXT DEFAULT '',
    registered_at REAL NOT NULL,
    updated_at    REAL DEFAULT 0.0,
    PRIMARY KEY (agent_name, card_id, worker_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_identity_card_id
    ON agent_identity(card_id);

CREATE INDEX IF NOT EXISTS idx_agent_identity_worker_id
    ON agent_identity(worker_id);

CREATE INDEX IF NOT EXISTS idx_agent_identity_name
    ON agent_identity(agent_name);
"""

# 注：查询/删除的 SQL 一律以内联字面量 + `?` 参数绑定的形态写在
# execute 调用处（无模块级 SQL 变量、无字符串拼接，静态扫描可直读）。


class AgentIdentity:
    """Agent 联邦身份映射表.

    提供 ``agent_name ↔ card_id ↔ worker_id`` 的双向查询，供四套
    注册体系在注册/下线/健康变更时相互关联。
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path: Path = Path(db_path) if db_path else get_db_path("agent_identity")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_AGENT_IDENTITY_DDL)

    # ── 写入映射 ──────────────────────────────────────────────────

    def register_mapping(
        self,
        agent_name: str,
        *,
        card_id: str | None = None,
        worker_id: str | None = None,
        source: str = "",
    ) -> None:
        """为 agent_name 注册/更新 card_id / worker_id 映射。

        若同 (agent_name, card_id, worker_id) 已存在，仅更新
        ``updated_at``；若 card_id/worker_id 仅有其一变化，则插入新行，
        旧行保留（历史映射不删除，便于审计）。
        """
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO agent_identity
                    (agent_name, card_id, worker_id, source, registered_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_name,
                    card_id,
                    worker_id,
                    source,
                    now,
                    now,
                ),
            )
        logger.debug(
            "[agent-identity] mapping registered: name=%s card_id=%s worker_id=%s",
            agent_name, card_id, worker_id,
        )

    # ── 查询映射 ──────────────────────────────────────────────────

    def get_by_card_id(self, card_id: str) -> list[dict[str, Any]]:
        """按 card_id 查询所有关联的 (agent_name, worker_id)。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT agent_name, card_id, worker_id, source, registered_at,"
                " updated_at FROM agent_identity WHERE card_id = ?",
                (card_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_by_worker_id(self, worker_id: str) -> list[dict[str, Any]]:
        """按 worker_id 查询所有关联的 (agent_name, card_id)。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT agent_name, card_id, worker_id, source, registered_at,"
                " updated_at FROM agent_identity WHERE worker_id = ?",
                (worker_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_by_agent_name(self, agent_name: str) -> list[dict[str, Any]]:
        """按 agent_name 查询所有关联的 (card_id, worker_id)。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT agent_name, card_id, worker_id, source, registered_at,"
                " updated_at FROM agent_identity WHERE agent_name = ?",
                (agent_name,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── 移除映射 ──────────────────────────────────────────────────

    def remove_mapping(
        self,
        agent_name: str,
        *,
        card_id: str | None = None,
        worker_id: str | None = None,
    ) -> int:
        """移除映射，返回实际删除的行数。

        若 card_id/worker_id 均为空，则移除该 agent_name 下所有映射。
        """
        with self._connect() as conn:
            if card_id is not None and worker_id is not None:
                cursor = conn.execute(
                    "DELETE FROM agent_identity WHERE agent_name = ?"
                    " AND card_id = ? AND worker_id = ?",
                    (agent_name, card_id, worker_id),
                )
            elif card_id is not None:
                cursor = conn.execute(
                    "DELETE FROM agent_identity WHERE agent_name = ? AND card_id = ?",
                    (agent_name, card_id),
                )
            elif worker_id is not None:
                cursor = conn.execute(
                    "DELETE FROM agent_identity WHERE agent_name = ? AND worker_id = ?",
                    (agent_name, worker_id),
                )
            else:
                cursor = conn.execute(
                    "DELETE FROM agent_identity WHERE agent_name = ?",
                    (agent_name,),
                )
            return cursor.rowcount or 0

    # ── 便捷：单值解析 ────────────────────────────────────────────

    def resolve_agent_name(self, *, card_id: str | None = None,
                           worker_id: str | None = None) -> str | None:
        """由 card_id 或 worker_id 反解 agent_name，找不到返回 None。

        优先匹配 card_id，其次 worker_id。
        """
        if card_id is not None:
            rows = self.get_by_card_id(card_id)
            if rows:
                return str(rows[0]["agent_name"])
        if worker_id is not None:
            rows = self.get_by_worker_id(worker_id)
            if rows:
                return str(rows[0]["agent_name"])
        return None


# ── 进程级单例（双重检查锁定）─────────────────────────────────────

_identity_instance: AgentIdentity | None = None
_identity_lock = __import__("threading").Lock()


def get_agent_identity() -> AgentIdentity:
    """获取进程级 AgentIdentity 单例。"""
    global _identity_instance
    if _identity_instance is None:
        with _identity_lock:
            if _identity_instance is None:
                _identity_instance = AgentIdentity()
    return _identity_instance


def reset_agent_identity() -> None:
    """重置单例（供测试隔离使用）。"""
    global _identity_instance
    with _identity_lock:
        _identity_instance = None
