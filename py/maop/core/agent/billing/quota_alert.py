"""QuotaAlertManager — 额度低阈值预警机制.

在 QuotaBucket 之上提供低额度阈值预警：当某 Agent 的某桶剩余额度
降至预设阈值以下时，生成 AlertEvent 并触发已注册的回调（如 webhook、
邮件、日志告警）。

设计要点：
  - **不修改 QuotaBucket**：仅通过其公共接口 ``get_remaining`` 查询额度。
  - **线程安全**：``threading.RLock`` 保护回调列表与 SQLite 操作。
  - **回调异常安全**：单个回调抛异常不影响其他回调与其他 Agent 的检查。
  - **阈值语义**：``threshold > 0`` 时才检查对应桶；``threshold == 0``
    表示"不监控此桶"，避免无额度 Agent 误报。

持久化：SQLite 表 ``quota_alerts``，主键 ``agent_name``。
线程安全：``threading.RLock`` 保护内存状态；SQLite 由 WAL + busy_timeout 保证并发。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

if TYPE_CHECKING:
    # 仅用于类型注解，运行时不导入（避免循环依赖）
    from maop.core.agent.billing.quota_bucket import QuotaBucket

logger = logging.getLogger(__name__)


# ── Pydantic 模型 ────────────────────────────────────────────────
class QuotaThreshold(BaseModel):
    """单个 Agent 的额度预警阈值配置.

    每个桶的阈值语义：**剩余额度 <= 阈值时触发预警**。
    ``threshold == 0`` 表示不监控该桶（避免无额度 Agent 误报）。
    """

    agent_name: str = Field(description="Agent 名称")
    free_threshold: int = Field(
        default=0, ge=0,
        description="免费桶预警阈值（剩余<=此值触发；0=不监控）",
    )
    prepaid_threshold: int = Field(
        default=0, ge=0,
        description="预付桶预警阈值（剩余<=此值触发；0=不监控）",
    )
    postpaid_threshold: int = Field(
        default=0, ge=0,
        description="后付桶预警阈值（剩余<=此值触发；0=不监控）",
    )
    total_threshold: int = Field(
        default=0, ge=0,
        description="总额度预警阈值（剩余<=此值触发；0=不监控）",
    )
    enabled: bool = Field(default=True, description="是否启用此预警")


class AlertEvent(BaseModel):
    """单次预警事件.

    当某桶剩余额度降至阈值以下时生成。
    """

    agent_name: str = Field(description="Agent 名称")
    alert_type: str = Field(
        description="预警类型: free_low/prepaid_low/postpaid_low/total_low",
    )
    current_value: int = Field(description="当前剩余额度")
    threshold: int = Field(description="触发阈值")
    timestamp: str = Field(description="ISO 8601 时间戳")
    message: str = Field(description="人类可读的预警消息")


# ── QuotaAlertManager ─────────────────────────────────────────────
class QuotaAlertManager:
    """额度预警管理器.

    管理每个 Agent 的预警阈值配置，定期（或按需）检查各桶剩余额度，
    触发预警事件并通知已注册的回调。

    Parameters
    ----------
    quota_bucket : QuotaBucket
        QuotaBucket 实例，用于查询剩余额度（只读访问，不修改其状态）。
    db_path : str | Path | None
        预警配置持久化路径。``None`` 时由 ``get_db_path("quota_alert")`` 解析。
    """

    def __init__(
        self,
        quota_bucket: QuotaBucket,
        db_path: str | Path | None = None,
    ) -> None:
        self._quota_bucket: QuotaBucket = quota_bucket
        self._db_path: Path = Path(db_path) if db_path else get_db_path("quota_alert")
        self._lock = threading.RLock()
        self._callbacks: list[Callable[[AlertEvent], None]] = []
        self._init_db()

    # ── 初始化 ──────────────────────────────────────────────────
    def _init_db(self) -> None:
        """初始化 SQLite 表（幂等）。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS quota_alerts (
                    agent_name TEXT PRIMARY KEY,
                    free_threshold INTEGER DEFAULT 0,
                    prepaid_threshold INTEGER DEFAULT 0,
                    postpaid_threshold INTEGER DEFAULT 0,
                    total_threshold INTEGER DEFAULT 0,
                    enabled INTEGER DEFAULT 1
                )
            """)

    # ── CRUD ────────────────────────────────────────────────────
    def set_alert(self, agent_name: str, threshold: QuotaThreshold) -> None:
        """设置 / 更新 Agent 的预警阈值（upsert）.

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        threshold : QuotaThreshold
            阈值配置。``threshold.agent_name`` 会被强制对齐为 ``agent_name``。
        """
        # 强制对齐 agent_name，确保配置与键一致
        threshold = threshold.model_copy(update={"agent_name": agent_name})
        with self._lock, sqlite_connect(self._db_path) as conn:
            # ON CONFLICT(agent_name) 依赖 PRIMARY KEY 约束
            # （来源：2026-09-12-sqlite-read-modify-write-race-upsert-atomic-fix）
            conn.execute(
                """INSERT INTO quota_alerts
                   (agent_name, free_threshold, prepaid_threshold,
                    postpaid_threshold, total_threshold, enabled)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(agent_name) DO UPDATE SET
                       free_threshold = excluded.free_threshold,
                       prepaid_threshold = excluded.prepaid_threshold,
                       postpaid_threshold = excluded.postpaid_threshold,
                       total_threshold = excluded.total_threshold,
                       enabled = excluded.enabled""",
                (
                    agent_name,
                    threshold.free_threshold,
                    threshold.prepaid_threshold,
                    threshold.postpaid_threshold,
                    threshold.total_threshold,
                    1 if threshold.enabled else 0,
                ),
            )
        logger.debug(
            "[quota_alert] set_alert for %s: free=%d prepaid=%d postpaid=%d total=%d enabled=%s",
            agent_name,
            threshold.free_threshold,
            threshold.prepaid_threshold,
            threshold.postpaid_threshold,
            threshold.total_threshold,
            threshold.enabled,
        )

    def remove_alert(self, agent_name: str) -> None:
        """移除 Agent 的预警配置.

        不存在则静默忽略。
        """
        with self._lock, sqlite_connect(self._db_path) as conn:
            conn.execute(
                "DELETE FROM quota_alerts WHERE agent_name = ?",
                (agent_name,),
            )
        logger.debug("[quota_alert] remove_alert for %s", agent_name)

    def get_alert(self, agent_name: str) -> QuotaThreshold | None:
        """获取 Agent 的预警配置.

        Returns
        -------
        QuotaThreshold | None
            不存在则返回 None。
        """
        with self._lock, sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM quota_alerts WHERE agent_name = ?",
                (agent_name,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_threshold(row)

    # ── 检查 ────────────────────────────────────────────────────
    def check_alerts(self) -> list[AlertEvent]:
        """检查所有 Agent 的额度，触发预警.

        遍历所有已配置预警的 Agent，查询其各桶剩余额度，
        对每个低于阈值的桶生成一个 AlertEvent。

        Returns
        -------
        list[AlertEvent]
            本次检查产生的所有预警事件（可能为空）。
        """
        events: list[AlertEvent] = []
        with self._lock:
            thresholds = self._get_all_alerts()
            for threshold in thresholds:
                if not threshold.enabled:
                    continue
                events.extend(self._check_one(threshold))
        return events

    def _check_one(self, threshold: QuotaThreshold) -> list[AlertEvent]:
        """检查单个 Agent 的额度，生成预警事件.

        通过 QuotaBucket.get_remaining 公共接口查询，不修改 QuotaBucket 状态。
        """
        events: list[AlertEvent] = []
        agent_name = threshold.agent_name
        # 通过 QuotaBucket 公共接口查询剩余额度（只读）
        remaining = self._quota_bucket.get_remaining(agent_name)
        free_rem = remaining["free"]
        prepaid_rem = remaining["prepaid"]
        postpaid_rem = remaining["postpaid"]
        total_rem = free_rem + prepaid_rem + postpaid_rem

        now_iso = datetime.now(timezone.utc).isoformat()

        # 免费桶：threshold > 0 时才检查，0 表示不监控
        if threshold.free_threshold > 0 and free_rem <= threshold.free_threshold:
            events.append(AlertEvent(
                agent_name=agent_name,
                alert_type="free_low",
                current_value=free_rem,
                threshold=threshold.free_threshold,
                timestamp=now_iso,
                message=(
                    f"Agent {agent_name!r} 免费桶剩余 {free_rem}，"
                    f"已低于预警阈值 {threshold.free_threshold}"
                ),
            ))
        # 预付桶
        if threshold.prepaid_threshold > 0 and prepaid_rem <= threshold.prepaid_threshold:
            events.append(AlertEvent(
                agent_name=agent_name,
                alert_type="prepaid_low",
                current_value=prepaid_rem,
                threshold=threshold.prepaid_threshold,
                timestamp=now_iso,
                message=(
                    f"Agent {agent_name!r} 预付桶剩余 {prepaid_rem}，"
                    f"已低于预警阈值 {threshold.prepaid_threshold}"
                ),
            ))
        # 后付桶
        if threshold.postpaid_threshold > 0 and postpaid_rem <= threshold.postpaid_threshold:
            events.append(AlertEvent(
                agent_name=agent_name,
                alert_type="postpaid_low",
                current_value=postpaid_rem,
                threshold=threshold.postpaid_threshold,
                timestamp=now_iso,
                message=(
                    f"Agent {agent_name!r} 后付桶剩余 {postpaid_rem}，"
                    f"已低于预警阈值 {threshold.postpaid_threshold}"
                ),
            ))
        # 总额度
        if threshold.total_threshold > 0 and total_rem <= threshold.total_threshold:
            events.append(AlertEvent(
                agent_name=agent_name,
                alert_type="total_low",
                current_value=total_rem,
                threshold=threshold.total_threshold,
                timestamp=now_iso,
                message=(
                    f"Agent {agent_name!r} 总额度剩余 {total_rem}，"
                    f"已低于预警阈值 {threshold.total_threshold}"
                ),
            ))
        return events

    # ── 回调 ────────────────────────────────────────────────────
    def register_callback(self, callback: Callable[[AlertEvent], None]) -> None:
        """注册预警回调.

        回调签名：``callback(event: AlertEvent) -> None``。
        可注册多个回调；``check_and_notify`` 会依次调用。
        回调抛异常不会影响其他回调与其他 Agent 的检查。

        Parameters
        ----------
        callback : Callable[[AlertEvent], None]
            回调函数。
        """
        with self._lock:
            self._callbacks.append(callback)
        logger.debug("[quota_alert] callback registered: %r", callback)

    def check_and_notify(self) -> list[AlertEvent]:
        """检查额度并通知所有已注册回调.

        集成 :meth:`check_alerts` 与回调通知：
          1. 调用 ``check_alerts`` 获取所有预警事件；
          2. 对每个事件依次调用所有注册回调；
          3. 单个回调异常被捕获并记录，不影响后续回调与其他事件。

        Returns
        -------
        list[AlertEvent]
            本次检查产生的所有预警事件。
        """
        events = self.check_alerts()
        # 拷贝回调列表快照，避免遍历时被其他线程修改
        with self._lock:
            callbacks = list(self._callbacks)

        for event in events:
            for callback in callbacks:
                try:
                    callback(event)
                except Exception:
                    # 回调异常安全：记录但不传播，不影响其他回调
                    logger.exception(
                        "[quota_alert] callback %r raised on event %s/%s",
                        callback, event.agent_name, event.alert_type,
                    )
        return events

    # ── 内部工具 ────────────────────────────────────────────────
    def _get_all_alerts(self) -> list[QuotaThreshold]:
        """从 DB 读取所有预警配置."""
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute("SELECT * FROM quota_alerts").fetchall()
        # r[0] 索引访问兼容 tuple 和 sqlite3.Row
        # （来源：2026-09-10-python-sqlite-batch-fetchmany-init-memory-peak）
        return [self._row_to_threshold(row) for row in rows]

    @staticmethod
    def _row_to_threshold(row: sqlite3.Row) -> QuotaThreshold:
        """sqlite3.Row → QuotaThreshold."""
        return QuotaThreshold(
            agent_name=row["agent_name"],
            free_threshold=row["free_threshold"],
            prepaid_threshold=row["prepaid_threshold"],
            postpaid_threshold=row["postpaid_threshold"],
            total_threshold=row["total_threshold"],
            enabled=bool(row["enabled"]),
        )