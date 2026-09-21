"""BillingEngine — 计费抽象引擎.

根据 Agent 的 ``BillingModel`` 计算消耗单位与成本，调用 ``QuotaBucket`` 扣减
额度，与 ``CostTracker`` 集成（可选）。

计费模式（``BillingModel``）：
  - ``FREE``         — 不计费，quota_units=0, cost=0
  - ``SUBSCRIPTION`` — 包月已付费，quota_units=0, cost=0
  - ``PER_TOKEN``    — 按 token 计费，quota_units=tokens, cost=tokens * price_per_token
  - ``PER_CALL``     — 按调用次数计费，quota_units=calls, cost=calls * price_per_call
  - ``CREDIT``       — 积分制，quota_units=calls, cost=0（用 QuotaBucket 管理）
  - ``BLACKBOX``     — 黑盒估算，quota_units=calls * blackbox_estimate_per_call, cost=0

持久化：SQLite 表 ``billing_records``，主键 ``id``（UUID）。
线程安全：``threading.RLock`` 保护内存状态；SQLite 由 WAL + busy_timeout 保证并发。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.agent.billing.quota_bucket import ConsumeResult, QuotaBucket, QuotaEntry  # noqa: F401
from maop.core.agent.registry.agent_catalog import (
    AgentCatalog,
    AgentDescriptor,
    BillingModel,
)
from maop.core.backends.db_utils import get_db_path, sqlite_connect

# CostTracker 可选依赖：仅在传入实例时才使用，避免硬耦合
try:
    from maop.core.cost_tracker import CostTracker
except ImportError:  # pragma: no cover - CostTracker 应始终可用
    CostTracker = None  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)


# ── Pydantic 模型 ────────────────────────────────────────────────
class BillingRecord(BaseModel):
    """单次计费记录."""

    agent_name: str = Field(..., description="Agent 名称")
    billing_model: str = Field(..., description="BillingModel value")
    tokens_consumed: int = Field(default=0, ge=0, description="消耗的 token 数")
    calls_made: int = Field(default=0, ge=0, description="调用次数")
    cost_usd: float = Field(default=0.0, ge=0.0, description="成本（美元）")
    quota_consumed: int = Field(default=0, ge=0, description="消耗的额度单位")
    quota_source: str = Field(default="", description="free/prepaid/postpaid 或组合")
    timestamp: str = Field(default="", description="ISO 时间戳")


class BillingResult(BaseModel):
    """计费结果."""

    success: bool = Field(..., description="是否成功")
    record: BillingRecord | None = Field(default=None, description="计费记录")
    error: str = Field(default="", description="错误信息")


# ── BillingEngine ───────────────────────────────────────────────
class BillingEngine:
    """计费抽象引擎.

    根据 Agent 的 ``billing_model`` 计算消耗，调用 ``QuotaBucket.consume`` 扣减
    额度，可选调用 ``CostTracker.record`` 记录成本明细。

    Parameters
    ----------
    catalog : AgentCatalog | None
        Agent 元数据注册中心。``None`` 时使用 ``AgentCatalog.default()``。
    quota_bucket : QuotaBucket | None
        额度分桶管理器。``None`` 时使用默认 DB 路径构造。
    cost_tracker : CostTracker | None
        成本追踪器。``None`` 时不记录到 CostTracker（仅记录到 billing_records）。
    db_path : str | Path | None
        billing_records 表的 SQLite 路径。``None`` 时由 ``get_db_path("billing")`` 解析。
    """

    def __init__(
        self,
        catalog: AgentCatalog | None = None,
        quota_bucket: QuotaBucket | None = None,
        cost_tracker: CostTracker | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self._catalog: AgentCatalog = catalog if catalog is not None else AgentCatalog.default()
        self._db_path: Path = Path(db_path) if db_path else get_db_path("billing")
        self._quota: QuotaBucket = quota_bucket if quota_bucket is not None else QuotaBucket(db_path=self._db_path)
        self._cost_tracker: CostTracker | None = cost_tracker
        self._lock = threading.RLock()
        self._init_db()

    # ── 初始化 ──────────────────────────────────────────────────
    def _init_db(self) -> None:
        """初始化计费记录表（幂等）。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS billing_records (
                    id TEXT PRIMARY KEY,
                    agent_name TEXT NOT NULL,
                    billing_model TEXT NOT NULL,
                    tokens_consumed INTEGER DEFAULT 0,
                    calls_made INTEGER DEFAULT 0,
                    cost_usd REAL DEFAULT 0.0,
                    quota_consumed INTEGER DEFAULT 0,
                    quota_source TEXT DEFAULT '',
                    session_id TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_billing_agent ON billing_records(agent_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_billing_timestamp ON billing_records(timestamp)"
            )

    # ── 计费 ────────────────────────────────────────────────────
    def charge(
        self,
        agent_name: str,
        *,
        tokens: int = 0,
        calls: int = 1,
        session_id: str = "",
        model: str = "",
    ) -> BillingResult:
        """对 Agent 调用进行计费.

        Parameters
        ----------
        agent_name : str
            Agent 名称。必须在 catalog 中注册。
        tokens : int
            本次调用消耗的 token 数（PER_TOKEN 模式下作为 quota_units）。
        calls : int
            本次调用次数（PER_CALL / CREDIT / BLACKBOX 模式下作为 quota_units 基数）。
        session_id : str
            会话 ID（透传给 CostTracker）。
        model : str
            模型名称（透传给 CostTracker 用于定价）。

        Returns
        -------
        BillingResult
            计费结果。失败时 ``record=None``，``error`` 描述原因。
        """
        with self._lock:
            descriptor = self._catalog.get(agent_name)
            if descriptor is None:
                return BillingResult(
                    success=False,
                    error=f"agent {agent_name!r} not found in catalog",
                )

            billing_model = descriptor.billing_model
            quota_units, cost_usd = self._calculate_charge(descriptor, tokens, calls)

            # 扣减额度
            quota_source = ""
            if quota_units > 0:
                consume_result = self._quota.consume(agent_name, quota_units)
                if not consume_result.success:
                    return BillingResult(
                        success=False,
                        error=f"quota consume failed: {consume_result.reason}",
                    )
                quota_source = self._format_quota_source(consume_result)

            # CostTracker 集成（可选）
            if self._cost_tracker is not None:
                try:
                    self._cost_tracker.record(
                        session_id=session_id,
                        agent=agent_name,
                        model=model,
                        prompt_tokens=tokens,
                        total_tokens=tokens,
                    )
                except Exception as exc:
                    # CostTracker 失败不应影响计费主流程
                    logger.warning(
                        "[billing] CostTracker.record failed for %s: %s",
                        agent_name, exc,
                    )

            # 记录 BillingRecord
            now_iso = datetime.now(timezone.utc).isoformat()
            record = BillingRecord(
                agent_name=agent_name,
                billing_model=billing_model.value,
                tokens_consumed=tokens,
                calls_made=calls,
                cost_usd=round(cost_usd, 6),
                quota_consumed=quota_units,
                quota_source=quota_source,
                timestamp=now_iso,
            )
            self._persist_record(record, session_id=session_id, model=model)

            logger.debug(
                "[billing] charge %s: model=%s tokens=%d calls=%d quota=%d cost=%.6f",
                agent_name, billing_model.value, tokens, calls, quota_units, cost_usd,
            )
            return BillingResult(success=True, record=record)

    def estimate_cost(self, agent_name: str, tokens: int = 0, calls: int = 1) -> float:
        """估算成本（不实际扣减）.

        Returns
        -------
        float
            预估成本（美元）。Agent 不存在时返回 0.0。
        """
        descriptor = self._catalog.get(agent_name)
        if descriptor is None:
            return 0.0
        _, cost_usd = self._calculate_charge(descriptor, tokens, calls)
        return round(cost_usd, 6)

    # ── 查询 ────────────────────────────────────────────────────
    def get_agent_billing_summary(self, agent_name: str) -> dict[str, Any]:
        """获取 Agent 计费摘要.

        Returns
        -------
        dict[str, Any]
            包含 total_calls / total_tokens / total_cost / total_quota_consumed /
            quota_remaining / billing_model 等字段。
        """
        descriptor = self._catalog.get(agent_name)
        billing_model = descriptor.billing_model.value if descriptor else "unknown"

        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                """SELECT
                    COUNT(*) as total_calls,
                    COALESCE(SUM(tokens_consumed), 0) as total_tokens,
                    COALESCE(SUM(cost_usd), 0.0) as total_cost,
                    COALESCE(SUM(quota_consumed), 0) as total_quota
                   FROM billing_records WHERE agent_name = ?""",
                (agent_name,),
            ).fetchone()

        total_calls = int(row["total_calls"]) if row else 0
        total_tokens = int(row["total_tokens"]) if row else 0
        total_cost = float(row["total_cost"]) if row else 0.0
        total_quota = int(row["total_quota"]) if row else 0

        quota_remaining = self._quota.get_remaining(agent_name)

        return {
            "agent_name": agent_name,
            "billing_model": billing_model,
            "total_calls": total_calls,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "total_quota_consumed": total_quota,
            "quota_remaining": quota_remaining,
        }

    def get_billing_records(
        self,
        agent_name: str = "",
        limit: int = 100,
    ) -> list[BillingRecord]:
        """获取计费记录.

        Parameters
        ----------
        agent_name : str
            筛选指定 Agent。空字符串表示不筛选（返回所有）。
        limit : int
            返回记录数上限。

        Returns
        -------
        list[BillingRecord]
            按时间倒序排列。
        """
        with sqlite_connect(self._db_path) as conn:
            if agent_name:
                rows = conn.execute(
                    """SELECT * FROM billing_records
                       WHERE agent_name = ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (agent_name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM billing_records
                       ORDER BY timestamp DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        return [self._row_to_record(r) for r in rows]

    # ── 内部工具 ────────────────────────────────────────────────
    @staticmethod
    def _calculate_charge(
        descriptor: AgentDescriptor,
        tokens: int,
        calls: int,
    ) -> tuple[int, float]:
        """根据计费模式计算消耗单位和成本.

        Returns
        -------
        tuple[int, float]
            (quota_units, cost_usd)。
        """
        model = descriptor.billing_model
        config = descriptor.billing_config

        if model == BillingModel.FREE:
            return (0, 0.0)

        if model == BillingModel.SUBSCRIPTION:
            # 包月已付费：不消耗额度，不产生额外成本
            return (0, 0.0)

        if model == BillingModel.PER_TOKEN:
            price_per_token = float(config.get("price_per_token", 0.0))
            quota_units = max(0, tokens)
            cost = quota_units * price_per_token
            return (quota_units, cost)

        if model == BillingModel.PER_CALL:
            price_per_call = float(config.get("price_per_call", 0.0))
            quota_units = max(0, calls)
            cost = quota_units * price_per_call
            return (quota_units, cost)

        if model == BillingModel.CREDIT:
            # 积分制：消耗 calls 个额度单位，不产生美元成本
            quota_units = max(0, calls)
            return (quota_units, 0.0)

        if model == BillingModel.BLACKBOX:
            # 黑盒估算：每次调用消耗 blackbox_estimate_per_call 个额度单位
            per_call = int(config.get("blackbox_estimate_per_call", 1))
            quota_units = max(0, calls) * max(0, per_call)
            return (quota_units, 0.0)

        # 未知模式：保守不计费
        logger.warning("[billing] unknown billing_model %r for %s", model, descriptor.name)
        return (0, 0.0)

    @staticmethod
    def _format_quota_source(consume_result: ConsumeResult) -> str:
        """格式化消耗来源标签.

        格式：``free:100,prepaid:50,postpaid:0``（仅记录非零桶）。
        """
        parts: list[str] = []
        if consume_result.from_free > 0:
            parts.append(f"free:{consume_result.from_free}")
        if consume_result.from_prepaid > 0:
            parts.append(f"prepaid:{consume_result.from_prepaid}")
        if consume_result.from_postpaid > 0:
            parts.append(f"postpaid:{consume_result.from_postpaid}")
        return ",".join(parts) if parts else "none"

    def _persist_record(
        self,
        record: BillingRecord,
        *,
        session_id: str = "",
        model: str = "",
    ) -> None:
        """持久化 BillingRecord 到 SQLite."""
        record_id = f"bill-{uuid.uuid4().hex[:8]}"
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO billing_records
                   (id, agent_name, billing_model, tokens_consumed, calls_made,
                    cost_usd, quota_consumed, quota_source, session_id, model, timestamp)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (record_id, record.agent_name, record.billing_model,
                 record.tokens_consumed, record.calls_made, record.cost_usd,
                 record.quota_consumed, record.quota_source,
                 session_id, model, record.timestamp),
            )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> BillingRecord:
        """sqlite3.Row → BillingRecord."""
        return BillingRecord(
            agent_name=row["agent_name"],
            billing_model=row["billing_model"],
            tokens_consumed=row["tokens_consumed"],
            calls_made=row["calls_made"],
            cost_usd=row["cost_usd"],
            quota_consumed=row["quota_consumed"],
            quota_source=row["quota_source"],
            timestamp=row["timestamp"],
        )