"""MAOP 路由决策审计日志 — 记录 Agent 路由的关键决策事件.

将路由过程中的关键决策（选中/失败/降级切换/并发拒绝/限流触发）
持久化到 SQLite，供事后审计、排障与运营分析。

核心组件：
  - ``RoutingAuditEvent``  — 审计事件（Pydantic 模型）
  - ``RoutingAuditLogger`` — 审计日志记录器（线程安全，SQLite 持久化）

Usage::

    from maop.core.agent.router.routing_audit import (
        RoutingAuditEvent, RoutingAuditLogger,
    )

    logger = RoutingAuditLogger(db_path=Path("/tmp/audit.db"))
    logger.log(RoutingAuditEvent(
        event_type="route_selected",
        agent_name="claude-code",
        strategy="cost_optimized",
        reason="lowest cost",
        context={"candidates": 3},
    ))
    events = logger.query(agent_name="claude-code", limit=50)

表结构 ``routing_audit_logs``：
    id           TEXT PRIMARY KEY  — 事件唯一 ID（uuid4）
    timestamp    TEXT              — ISO8601 时间戳
    event_type   TEXT              — 事件类型
    agent_name   TEXT              — Agent 名称
    strategy     TEXT              — 路由策略
    reason       TEXT              — 决策原因
    context_json TEXT              — 上下文 JSON
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)

# 合法的事件类型（供校验参考，不强制限制以保持扩展性）
_VALID_EVENT_TYPES: frozenset[str] = frozenset({
    "route_selected",
    "route_failed",
    "fallback_switched",
    "concurrent_rejected",
    "rate_limited",
})


# ── Pydantic 模型 ─────────────────────────────────────────────────
class RoutingAuditEvent(BaseModel):
    """路由审计事件.

    Attributes
    ----------
    event_type : str
        事件类型："route_selected" / "route_failed" / "fallback_switched"
        / "concurrent_rejected" / "rate_limited"。
    agent_name : str
        相关 Agent 名称（失败/无候选时可为空）。
    strategy : str
        路由策略（如 "cost_optimized"）。
    reason : str
        决策原因（供排障/审计阅读）。
    context : dict
        附加上下文（候选数、排除列表等），序列化为 JSON 存储。
    """

    event_type: str = Field(..., description="事件类型")
    agent_name: str = Field(default="", description="Agent 名称")
    strategy: str = Field(default="", description="路由策略")
    reason: str = Field(default="", description="决策原因")
    context: dict[str, object] = Field(
        default_factory=dict, description="附加上下文",
    )
    # 以下字段由 logger.log 自动填充，构造时一般不传
    id: str = Field(default="", description="事件唯一 ID")
    timestamp: str = Field(default="", description="ISO8601 时间戳")


# ── RoutingAuditLogger ────────────────────────────────────────────
class RoutingAuditLogger:
    """路由审计日志记录器（线程安全，SQLite 持久化）.

    Parameters
    ----------
    db_path : Path | None
        SQLite 数据库路径。``None`` 时使用 ``get_db_path()`` 默认路径。
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path: Path = Path(db_path) if db_path is not None else get_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # 可重入锁：保护并发写入（SQLite 自身有 busy_timeout，RLock 避免竞争）
        self._lock: threading.RLock = threading.RLock()
        self._init_db()

    # ── 内部：建表 ────────────────────────────────────────────────
    def _init_db(self) -> None:
        """初始化 SQLite 表 ``routing_audit_logs``."""
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS routing_audit_logs (
                    id           TEXT PRIMARY KEY,
                    timestamp    TEXT NOT NULL,
                    event_type   TEXT NOT NULL,
                    agent_name   TEXT DEFAULT '',
                    strategy     TEXT DEFAULT '',
                    reason       TEXT DEFAULT '',
                    context_json TEXT DEFAULT '{}'
                )
            """)
            # 按 agent_name 查询的辅助索引
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_routing_audit_agent "
                "ON routing_audit_logs(agent_name)"
            )
            # 按时间倒序查询的辅助索引
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_routing_audit_ts "
                "ON routing_audit_logs(timestamp DESC)"
            )

    # ── 公共接口 ──────────────────────────────────────────────────
    def log(self, event: RoutingAuditEvent) -> None:
        """记录一条审计事件.

        自动填充 ``id``（uuid4）和 ``timestamp``（ISO8601 UTC），
        若事件已带 id/timestamp 则保留原值（支持测试注入）。

        Parameters
        ----------
        event : RoutingAuditEvent
            待记录的审计事件。
        """
        # 填充 id / timestamp（若未提供）
        if not event.id:
            event.id = str(uuid.uuid4())
        if not event.timestamp:
            event.timestamp = datetime.now(timezone.utc).isoformat()

        context_json = json.dumps(event.context, ensure_ascii=False, default=str)
        with self._lock:
            with sqlite_connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO routing_audit_logs "
                    "(id, timestamp, event_type, agent_name, strategy, reason, context_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.id,
                        event.timestamp,
                        event.event_type,
                        event.agent_name,
                        event.strategy,
                        event.reason,
                        context_json,
                    ),
                )
        logger.debug(
            "[routing_audit] 记录事件: type=%s agent=%s strategy=%s",
            event.event_type, event.agent_name, event.strategy,
        )

    def query(
        self,
        agent_name: str = "",
        limit: int = 100,
    ) -> list[RoutingAuditEvent]:
        """查询审计日志.

        按 ``timestamp`` 倒序返回。``agent_name`` 非空时仅返回该 Agent 的事件。

        Parameters
        ----------
        agent_name : str
            过滤 Agent 名称。空字符串表示不过滤（返回所有）。
        limit : int
            最大返回条数，默认 100。

        Returns
        -------
        list[RoutingAuditEvent]
            审计事件列表（时间倒序）。
        """
        if limit <= 0:
            return []

        with self._lock:
            with sqlite_connect(self._db_path) as conn:
                if agent_name:
                    rows = conn.execute(
                        "SELECT id, timestamp, event_type, agent_name, "
                        "strategy, reason, context_json "
                        "FROM routing_audit_logs "
                        "WHERE agent_name = ? "
                        "ORDER BY timestamp DESC LIMIT ?",
                        (agent_name, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, timestamp, event_type, agent_name, "
                        "strategy, reason, context_json "
                        "FROM routing_audit_logs "
                        "ORDER BY timestamp DESC LIMIT ?",
                        (limit,),
                    ).fetchall()

        events: list[RoutingAuditEvent] = []
        for row in rows:
            try:
                ctx = json.loads(row[6]) if row[6] else {}
            except (json.JSONDecodeError, TypeError):
                ctx = {}
            events.append(RoutingAuditEvent(
                id=row[0],
                timestamp=row[1],
                event_type=row[2],
                agent_name=row[3],
                strategy=row[4],
                reason=row[5],
                context=ctx,
            ))
        return events

    def count(self, agent_name: str = "") -> int:
        """统计审计日志条数.

        Parameters
        ----------
        agent_name : str
            过滤 Agent 名称。空字符串表示统计全部。

        Returns
        -------
        int
            日志条数。
        """
        with self._lock:
            with sqlite_connect(self._db_path) as conn:
                if agent_name:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM routing_audit_logs WHERE agent_name = ?",
                        (agent_name,),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM routing_audit_logs"
                    ).fetchone()
                return int(row[0]) if row else 0