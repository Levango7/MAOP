"""VendorBilling — 厂商统一计费.

同厂商下所有 Agent 的用量汇总为厂商统一账单，支持设置厂商级预算与超支检查。
用量记录持久化到 SQLite，统计查询通过 SQL 聚合完成（避免全表读入）。

设计要点：
    * 用量记录持久化到 SQLite 表 ``vendor_billing_usage``。
    * 厂商预算持久化到 SQLite 表 ``vendor_budget``。
    * 统计查询通过 SQL 聚合（SUM/COUNT）完成，避免全表读入后 Python 分组
      计数（反模式：统计方法全表读入 → 改用 SQL 聚合，来源：
      2026-09-10-python-stats-sql-aggregate-replace-full-table-load）。
    * 周期筛选（``period``）通过 SQL 时间过滤实现，支持 ``month`` / ``day``
      / ``all`` 三种周期。
    * 全程使用 ``threading.RLock`` 保护内存状态。

反模式规避：
    本模块的 ``get_vendor_summary`` / ``get_agent_breakdown`` 仅需聚合结果
    （total_cost / total_tokens / total_calls / per-agent 占比），不需要
    行数据。因此所有聚合指标直接用 SQL 聚合（SUM、COUNT）完成，不在 Python
    中做 ``for r in rows:`` 分组计数。
    （来源：2026-09-10-python-stats-sql-aggregate-replace-full-table-load）
"""

from __future__ import annotations

import logging
import sqlite3  # noqa: F401
import threading
from datetime import datetime, timedelta, timezone  # noqa: F401
from pathlib import Path

from pydantic import BaseModel, Field

from maop.core.agent.vendor.vendor_ecosystem import VendorEcosystem
from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


# ── Pydantic 模型 ────────────────────────────────────────────────
class VendorBillingSummary(BaseModel):
    """厂商统一账单摘要.

    Attributes
    ----------
    vendor_name : str
        厂商名称。
    total_cost : float
        周期内总成本。
    total_tokens : int
        周期内总 token 数。
    total_calls : int
        周期内总调用次数。
    agent_count : int
        厂商下有用量记录的 Agent 数量。
    period : str
        统计周期（``month`` / ``day`` / ``all``）。
    budget : float
        厂商月度预算（0 表示未设置）。
    used : float
        已使用金额（等于 ``total_cost``，冗余字段便于前端展示）。
    """

    vendor_name: str = Field(..., description="厂商名称")
    total_cost: float = Field(default=0.0, description="周期内总成本")
    total_tokens: int = Field(default=0, description="周期内总 token 数")
    total_calls: int = Field(default=0, description="周期内总调用次数")
    agent_count: int = Field(default=0, description="有用量记录的 Agent 数量")
    period: str = Field(default="month", description="统计周期")
    budget: float = Field(default=0.0, description="厂商月度预算")
    used: float = Field(default=0.0, description="已使用金额")


class AgentUsage(BaseModel):
    """厂商下单个 Agent 的用量明细.

    Attributes
    ----------
    agent_name : str
        Agent 名称。
    cost : float
        周期内成本。
    tokens : int
        周期内 token 数。
    calls : int
        周期内调用次数。
    percentage : float
        占厂商总成本的百分比（0-100）。
    """

    agent_name: str = Field(..., description="Agent 名称")
    cost: float = Field(default=0.0, description="成本")
    tokens: int = Field(default=0, description="token 数")
    calls: int = Field(default=0, description="调用次数")
    percentage: float = Field(default=0.0, description="占厂商总成本百分比")


class BudgetStatus(BaseModel):
    """厂商预算状态.

    Attributes
    ----------
    vendor_name : str
        厂商名称。
    budget : float
        月度预算。
    used : float
        已使用金额。
    remaining : float
        剩余预算。
    exceeded : bool
        是否超支。
    """

    vendor_name: str = Field(..., description="厂商名称")
    budget: float = Field(default=0.0, description="月度预算")
    used: float = Field(default=0.0, description="已使用金额")
    remaining: float = Field(default=0.0, description="剩余预算")
    exceeded: bool = Field(default=False, description="是否超支")


# ── VendorBilling ───────────────────────────────────────────────
class VendorBilling:
    """厂商统一计费管理器.

    记录 Agent 用量，汇总为厂商统一账单，支持厂商级预算管理。

    Parameters
    ----------
    ecosystem : VendorEcosystem
        厂商生态管理器，用于查询 Agent→厂商映射。
    db_path : Path | None
        SQLite 持久化路径。``None`` 时由 ``get_db_path("vendor_billing")`` 解析。
    """

    def __init__(
        self,
        ecosystem: VendorEcosystem,
        db_path: Path | None = None,
    ) -> None:
        self._ecosystem = ecosystem
        self._db_path: Path = (
            Path(db_path) if db_path else get_db_path("vendor_billing")
        )
        self._lock = threading.RLock()
        self._init_db()

    # ── 初始化 ──────────────────────────────────────────────────
    def _init_db(self) -> None:
        """初始化用量表与预算表（幂等）。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            # 用量记录表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vendor_billing_usage (
                    id TEXT PRIMARY KEY,
                    agent_name TEXT NOT NULL,
                    vendor_name TEXT NOT NULL,
                    cost REAL DEFAULT 0.0,
                    tokens INTEGER DEFAULT 0,
                    calls INTEGER DEFAULT 0,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_vendor "
                "ON vendor_billing_usage(vendor_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_agent "
                "ON vendor_billing_usage(agent_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_timestamp "
                "ON vendor_billing_usage(timestamp)"
            )
            # 厂商预算表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vendor_budget (
                    vendor_name TEXT PRIMARY KEY,
                    monthly_budget REAL DEFAULT 0.0
                )
            """)

    # ── 用量记录 ────────────────────────────────────────────────
    def record_usage(
        self,
        agent_name: str,
        cost: float,
        tokens: int = 0,
        calls: int = 1,
    ) -> None:
        """记录 Agent 使用量.

        自动通过 ecosystem 查询 Agent 所属厂商。Agent 未归属任何厂商时
        记录到 ``"unknown"`` 厂商并记录警告。

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        cost : float
            本次成本。
        tokens : int
            本次 token 数。
        calls : int
            本次调用次数。
        """
        with self._lock:
            vendor_name = self._ecosystem.get_vendor_of_agent(agent_name)
            if vendor_name is None:
                vendor_name = "unknown"
                logger.warning(
                    "[vendor_billing] Agent %s 未归属任何厂商，记录到 unknown",
                    agent_name,
                )

            now_iso = datetime.now(timezone.utc).isoformat()
            record_id = f"vbu-{agent_name}-{now_iso}"
            with sqlite_connect(self._db_path) as conn:
                conn.execute(
                    """INSERT INTO vendor_billing_usage
                       (id, agent_name, vendor_name, cost, tokens, calls, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (record_id, agent_name, vendor_name, cost, tokens, calls, now_iso),
                )
        logger.debug(
            "[vendor_billing] 记录 Agent %s 用量: cost=%.4f tokens=%d calls=%d",
            agent_name, cost, tokens, calls,
        )

    # ── 账单查询 ────────────────────────────────────────────────
    def get_vendor_summary(
        self,
        vendor_name: str,
        period: str = "month",
    ) -> VendorBillingSummary:
        """获取厂商统一账单.

        通过 SQL 聚合计算厂商在指定周期内的总成本、总 token、总调用次数与
        有用量记录的 Agent 数量。不在 Python 中全表读入分组计数。

        Parameters
        ----------
        vendor_name : str
            厂商名称。
        period : str
            统计周期：``month``（本月）/ ``day``（本日）/ ``all``（全部）。

        Returns
        -------
        VendorBillingSummary
            厂商账单摘要。
        """
        since = self._period_start(period)
        budget = self._get_budget(vendor_name)

        with sqlite_connect(self._db_path) as conn:
            # SQL 聚合：一次查询拿到 total_cost / total_tokens / total_calls /
            # agent_count，避免全表读入后 Python 分组计数。
            row = conn.execute(
                """SELECT
                       COALESCE(SUM(cost), 0.0) AS total_cost,
                       COALESCE(SUM(tokens), 0) AS total_tokens,
                       COALESCE(SUM(calls), 0) AS total_calls,
                       COUNT(DISTINCT agent_name) AS agent_count
                   FROM vendor_billing_usage
                   WHERE vendor_name = ? AND timestamp >= ?""",
                (vendor_name, since),
            ).fetchone()

        total_cost = float(row["total_cost"]) if row else 0.0
        total_tokens = int(row["total_tokens"]) if row else 0
        total_calls = int(row["total_calls"]) if row else 0
        agent_count = int(row["agent_count"]) if row else 0

        return VendorBillingSummary(
            vendor_name=vendor_name,
            total_cost=round(total_cost, 6),
            total_tokens=total_tokens,
            total_calls=total_calls,
            agent_count=agent_count,
            period=period,
            budget=budget,
            used=round(total_cost, 6),
        )

    def get_agent_breakdown(
        self,
        vendor_name: str,
        period: str = "month",
    ) -> list[AgentUsage]:
        """获取厂商下各 Agent 用量明细.

        通过 SQL 聚合按 Agent 分组，一次查询拿到各 Agent 的 cost/tokens/calls
        与厂商总额，在 Python 中仅做百分比除法（非分组计数）。

        Parameters
        ----------
        vendor_name : str
            厂商名称。
        period : str
            统计周期：``month`` / ``day`` / ``all``。

        Returns
        -------
        list[AgentUsage]
            各 Agent 用量明细，按成本降序排列。
        """
        since = self._period_start(period)

        with sqlite_connect(self._db_path) as conn:
            # SQL 聚合：按 agent_name GROUP BY，一次拿到各 Agent 聚合结果。
            rows = conn.execute(
                """SELECT
                       agent_name,
                       COALESCE(SUM(cost), 0.0) AS cost,
                       COALESCE(SUM(tokens), 0) AS tokens,
                       COALESCE(SUM(calls), 0) AS total_calls
                   FROM vendor_billing_usage
                   WHERE vendor_name = ? AND timestamp >= ?
                   GROUP BY agent_name
                   ORDER BY cost DESC""",
                (vendor_name, since),
            ).fetchall()

        if not rows:
            return []

        # 计算厂商总额用于百分比（仅做除法，非分组计数）
        total_cost = sum(float(r["cost"]) for r in rows)
        result: list[AgentUsage] = []
        for r in rows:
            cost = float(r["cost"])
            percentage = (cost / total_cost * 100.0) if total_cost > 0 else 0.0
            result.append(
                AgentUsage(
                    agent_name=r["agent_name"],
                    cost=round(cost, 6),
                    tokens=int(r["tokens"]),
                    calls=int(r["total_calls"]),
                    percentage=round(percentage, 4),
                )
            )
        return result

    # ── 预算管理 ────────────────────────────────────────────────
    def set_vendor_budget(
        self,
        vendor_name: str,
        monthly_budget: float,
    ) -> None:
        """设置厂商统一月度预算.

        Parameters
        ----------
        vendor_name : str
            厂商名称。
        monthly_budget : float
            月度预算金额（≥ 0）。
        """
        if monthly_budget < 0:
            raise ValueError("月度预算不能为负数")
        with self._lock, sqlite_connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO vendor_budget (vendor_name, monthly_budget)
                       VALUES (?, ?)
                       ON CONFLICT(vendor_name) DO UPDATE SET
                         monthly_budget=excluded.monthly_budget""",
                (vendor_name, monthly_budget),
            )
        logger.debug(
            "[vendor_billing] 厂商 %s 月度预算设为 %.2f",
            vendor_name, monthly_budget,
        )

    def check_vendor_budget(self, vendor_name: str) -> BudgetStatus:
        """检查厂商预算状态.

        统计本月已使用金额，与预算对比。

        Parameters
        ----------
        vendor_name : str
            厂商名称。

        Returns
        -------
        BudgetStatus
            预算状态。未设置预算时 ``budget=0``，``exceeded=False``。
        """
        budget = self._get_budget(vendor_name)
        # 复用 get_vendor_summary 的 SQL 聚合，避免重复实现
        summary = self.get_vendor_summary(vendor_name, period="month")
        used = summary.total_cost
        remaining = budget - used if budget > 0 else 0.0
        exceeded = budget > 0 and used > budget
        return BudgetStatus(
            vendor_name=vendor_name,
            budget=budget,
            used=round(used, 6),
            remaining=round(remaining, 6),
            exceeded=exceeded,
        )

    # ── 内部工具 ────────────────────────────────────────────────
    @staticmethod
    def _period_start(period: str) -> str:
        """计算周期的起始时间（ISO 8601 UTC）.

        Parameters
        ----------
        period : str
            ``month`` / ``day`` / ``all``。

        Returns
        -------
        str
            起始时间 ISO 字符串。``all`` 返回 Unix 纪元。
        """
        now = datetime.now(timezone.utc)
        if period == "month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif period == "day":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "all":
            return "1970-01-01T00:00:00+00:00"
        else:
            # 未知周期默认按月
            logger.warning("[vendor_billing] 未知周期 %r，按月处理", period)
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start.isoformat()

    def _get_budget(self, vendor_name: str) -> float:
        """查询厂商月度预算."""
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT monthly_budget FROM vendor_budget WHERE vendor_name = ?",
                (vendor_name,),
            ).fetchone()
        return float(row["monthly_budget"]) if row else 0.0