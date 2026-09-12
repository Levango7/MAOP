"""SLAManager — SLA 监控与告警模块.

在 ``HealthScorer`` 之上提供 SLA（Service Level Agreement）监控：
为每个 Agent 定义 SLA 指标阈值（响应时间、成功率、可用性、成本），
定期检查是否满足，违反时触发已注册的告警回调。

设计要点：
  - **不修改 HealthScorer**：仅通过其公共接口 ``get_score`` 查询评分（只读）。
  - **线程安全**：``threading.RLock`` 保护 SLA 定义、回调列表与 SQLite 操作。
  - **回调异常安全**：单个回调抛异常不影响其他回调与其他 Agent 的检查。
  - **可用性定义**：当前用成功率近似可用性（无独立的心跳/探活机制时）。

持久化：SQLite 表 ``sla_definitions``，主键 ``agent_name``。
线程安全：``threading.RLock`` 保护内存状态；SQLite 由 WAL + busy_timeout 保证并发。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect
from maop.core.agent.ops.health_scorer import HealthScorer

logger = logging.getLogger(__name__)


# ── Pydantic 模型 ────────────────────────────────────────────────
class SLADefinition(BaseModel):
    """单个 Agent 的 SLA 定义.

    各阈值语义：
      - ``max_response_time_s``：平均响应时间不得超过此值（秒）。
      - ``min_success_rate``：成功率不得低于此值（0.0-1.0）。
      - ``min_availability``：可用性不得低于此值（0.0-1.0）。
      - ``max_cost_per_call``：单次调用成本不得超过此值（预留字段，当前不检查）。
      - ``enabled``：是否启用此 SLA 检查。
    """

    agent_name: str = Field(description="Agent 名称")
    max_response_time_s: float = Field(
        default=10.0, gt=0,
        description="最大平均响应时间（秒）",
    )
    min_success_rate: float = Field(
        default=0.95, ge=0.0, le=1.0,
        description="最低成功率 (0.0-1.0)",
    )
    min_availability: float = Field(
        default=0.99, ge=0.0, le=1.0,
        description="最低可用性 (0.0-1.0)",
    )
    max_cost_per_call: float = Field(
        default=1.0, ge=0.0,
        description="最大单次调用成本（预留字段）",
    )
    enabled: bool = Field(default=True, description="是否启用此 SLA 检查")


class SLAStatus(BaseModel):
    """单个 Agent 的 SLA 检查结果.

    ``compliant`` 为 True 表示满足所有 SLA 指标；
    为 False 时 ``violations`` 列出所有违反项。
    """

    agent_name: str = Field(description="Agent 名称")
    compliant: bool = Field(description="是否满足 SLA")
    violations: list[str] = Field(
        default_factory=list,
        description="违反项描述列表",
    )
    current_response_time_s: float = Field(
        ge=0.0, description="当前平均响应时间（秒）",
    )
    current_success_rate: float = Field(
        ge=0.0, le=1.0, description="当前成功率 (0.0-1.0)",
    )
    current_availability: float = Field(
        ge=0.0, le=1.0, description="当前可用性 (0.0-1.0)",
    )
    timestamp: str = Field(description="检查时间戳（ISO 8601）")


# ── SLAManager ───────────────────────────────────────────────────
class SLAManager:
    """SLA 监控与告警管理器.

    管理每个 Agent 的 SLA 定义，检查是否满足指标，
    违反时触发已注册的告警回调。

    Parameters
    ----------
    health_scorer : HealthScorer
        HealthScorer 实例，用于查询 Agent 评分（只读访问，不修改其状态）。
    db_path : Path | None
        SLA 定义持久化路径。``None`` 时由 ``get_db_path("sla_manager")`` 解析。
    """

    def __init__(
        self,
        health_scorer: HealthScorer,
        db_path: Path | None = None,
    ) -> None:
        self._health_scorer: HealthScorer = health_scorer
        self._db_path: Path = Path(db_path) if db_path else get_db_path("sla_manager")
        self._lock = threading.RLock()
        self._callbacks: list[Callable[[SLAStatus], None]] = []
        self._init_db()
        self._load_from_db()

    # ── 初始化 ──────────────────────────────────────────────────
    def _init_db(self) -> None:
        """初始化 SQLite 表（幂等）。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sla_definitions (
                    agent_name TEXT PRIMARY KEY,
                    max_response_time_s REAL NOT NULL DEFAULT 10.0,
                    min_success_rate REAL NOT NULL DEFAULT 0.95,
                    min_availability REAL NOT NULL DEFAULT 0.99,
                    max_cost_per_call REAL NOT NULL DEFAULT 1.0,
                    enabled INTEGER NOT NULL DEFAULT 1
                )
            """)

    def _load_from_db(self) -> None:
        """从 DB 加载 SLA 定义到内存缓存.

        内存缓存避免每次 ``check_sla`` 都查 DB，同时 DB 仍为持久化源。
        """
        self._sla_cache: dict[str, SLADefinition] = {}
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute("SELECT * FROM sla_definitions").fetchall()
            for row in rows:
                # r[0] 索引访问兼容 tuple 和 sqlite3.Row
                # （来源：2026-09-10-python-sqlite-batch-fetchmany-init-memory-peak）
                sla = SLADefinition(
                    agent_name=row["agent_name"],
                    max_response_time_s=row["max_response_time_s"],
                    min_success_rate=row["min_success_rate"],
                    min_availability=row["min_availability"],
                    max_cost_per_call=row["max_cost_per_call"],
                    enabled=bool(row["enabled"]),
                )
                self._sla_cache[sla.agent_name] = sla

    # ── SLA 定义 CRUD ───────────────────────────────────────────
    def define_sla(self, agent_name: str, sla: SLADefinition) -> None:
        """定义 / 更新 Agent 的 SLA（upsert）.

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        sla : SLADefinition
            SLA 定义。``sla.agent_name`` 会被强制对齐为 ``agent_name``。
        """
        # 强制对齐 agent_name，确保定义与键一致
        sla = sla.model_copy(update={"agent_name": agent_name})
        with self._lock, sqlite_connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO sla_definitions
                   (agent_name, max_response_time_s, min_success_rate,
                    min_availability, max_cost_per_call, enabled)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(agent_name) DO UPDATE SET
                       max_response_time_s = excluded.max_response_time_s,
                       min_success_rate = excluded.min_success_rate,
                       min_availability = excluded.min_availability,
                       max_cost_per_call = excluded.max_cost_per_call,
                       enabled = excluded.enabled""",
                (
                    agent_name,
                    sla.max_response_time_s,
                    sla.min_success_rate,
                    sla.min_availability,
                    sla.max_cost_per_call,
                    1 if sla.enabled else 0,
                ),
            )
            self._sla_cache[agent_name] = sla
        logger.debug(
            "[sla_manager] defined SLA for %s: max_rt=%.2fs min_sr=%.2f min_avail=%.2f",
            agent_name,
            sla.max_response_time_s,
            sla.min_success_rate,
            sla.min_availability,
        )

    def get_sla(self, agent_name: str) -> SLADefinition | None:
        """获取 Agent 的 SLA 定义.

        Returns
        -------
        SLADefinition | None
            不存在则返回 None。
        """
        with self._lock:
            return self._sla_cache.get(agent_name)

    # ── SLA 检查 ────────────────────────────────────────────────
    def check_sla(self, agent_name: str) -> SLAStatus:
        """检查指定 Agent 的 SLA 是否满足.

        从 ``HealthScorer`` 获取当前指标，与 SLA 定义逐项对比。

        Parameters
        ----------
        agent_name : str
            Agent 名称。

        Returns
        -------
        SLAStatus
            SLA 检查结果。若 Agent 无 SLA 定义或无调用记录，
            返回 ``compliant=True`` 且 ``violations=[]``（无定义即无违反）。
        """
        with self._lock:
            sla = self._sla_cache.get(agent_name)
            if sla is None:
                # 无 SLA 定义，视为合规
                return SLAStatus(
                    agent_name=agent_name,
                    compliant=True,
                    violations=[],
                    current_response_time_s=0.0,
                    current_success_rate=0.0,
                    current_availability=0.0,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
            if not sla.enabled:
                # SLA 未启用，视为合规
                return SLAStatus(
                    agent_name=agent_name,
                    compliant=True,
                    violations=[],
                    current_response_time_s=0.0,
                    current_success_rate=0.0,
                    current_availability=0.0,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )

            # 从 HealthScorer 获取当前评分（只读）
            health = self._health_scorer.get_score(agent_name)

            # 可用性：当前用成功率近似（无独立心跳机制）
            current_availability = health.success_rate
            current_response_time = health.avg_response_time_s
            current_success_rate = health.success_rate

            violations: list[str] = []

            # 检查响应时间
            if current_response_time > sla.max_response_time_s:
                violations.append(
                    f"响应时间 {current_response_time:.2f}s 超过 SLA 上限 "
                    f"{sla.max_response_time_s:.2f}s",
                )

            # 检查成功率
            if current_success_rate < sla.min_success_rate:
                violations.append(
                    f"成功率 {current_success_rate:.2%} 低于 SLA 下限 "
                    f"{sla.min_success_rate:.2%}",
                )

            # 检查可用性
            if current_availability < sla.min_availability:
                violations.append(
                    f"可用性 {current_availability:.2%} 低于 SLA 下限 "
                    f"{sla.min_availability:.2%}",
                )

            return SLAStatus(
                agent_name=agent_name,
                compliant=len(violations) == 0,
                violations=violations,
                current_response_time_s=current_response_time,
                current_success_rate=current_success_rate,
                current_availability=current_availability,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

    def check_all_sla(self) -> list[SLAStatus]:
        """检查所有已定义 SLA 的 Agent.

        Returns
        -------
        list[SLAStatus]
            所有已定义 SLA 的 Agent 的检查结果列表。
        """
        with self._lock:
            agent_names = list(self._sla_cache.keys())
        return [self.check_sla(name) for name in agent_names]

    # ── 告警回调 ────────────────────────────────────────────────
    def register_alert_callback(
        self,
        callback: Callable[[SLAStatus], None],
    ) -> None:
        """注册告警回调.

        回调签名：``callback(status: SLAStatus) -> None``。
        可注册多个回调；``check_and_alert`` 会依次调用。
        回调抛异常不会影响其他回调与其他 Agent 的检查。

        Parameters
        ----------
        callback : Callable[[SLAStatus], None]
            回调函数，接收 SLA 检查结果。
        """
        with self._lock:
            self._callbacks.append(callback)
        logger.debug("[sla_manager] alert callback registered: %r", callback)

    def check_and_alert(self) -> list[SLAStatus]:
        """检查所有 SLA 并触发告警回调.

        集成 :meth:`check_all_sla` 与回调通知：
          1. 调用 ``check_all_sla`` 获取所有检查结果；
          2. 对 **不合规** 的结果（``compliant=False``）依次调用所有注册回调；
          3. 单个回调异常被捕获并记录，不影响后续回调与其他 Agent。

        Returns
        -------
        list[SLAStatus]
            所有已定义 SLA 的 Agent 的检查结果（含合规与不合规）。
        """
        statuses = self.check_all_sla()

        # 深拷贝回调列表快照，避免遍历时被其他线程修改
        with self._lock:
            callbacks = list(self._callbacks)

        for status in statuses:
            if status.compliant:
                continue  # 合规的不告警
            for callback in callbacks:
                try:
                    callback(status)
                except Exception:
                    # 回调异常安全：记录但不传播，不影响其他回调
                    logger.exception(
                        "[sla_manager] alert callback %r raised on %s",
                        callback, status.agent_name,
                    )
        return statuses

    # ── 内部工具 ────────────────────────────────────────────────
    def _get_all_sla_agents(self) -> list[str]:
        """获取所有已定义 SLA 的 Agent 名称（从内存缓存）."""
        with self._lock:
            return list(self._sla_cache.keys())