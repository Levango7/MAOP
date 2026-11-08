"""MAOP Cost Tracker — Real-time token usage and cost tracking.

Provides:
  - CostTracker: record/query token usage per session/agent/model
  - CostEntry: individual usage record
  - CostSummary: aggregated usage stats
  - Budget alerts via HookManager integration

Integration points:
  - maop_execute.py calls tracker.record() after each LLM call
  - Dashboard /api/cost/* endpoints for visualization
  - HookManager: COST_BUDGET_EXCEEDED event when over budget
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


def _entry_unpriced(entry: CostEntry) -> bool:
    """这条成本条目**无法计价**吗（2026-11-08）。

    判据顺序：
    1. ``metadata["priced"]`` 显式为 False → 无法计价（新写入的条目都带这个键，
       来自 ``maop_loop_phases`` 的 ``bool(ModelRegistry.get_model(model))``）；
    2. 没有 ``priced`` 键（2026-11-08 之前写入的历史行）→ 按 ``provider`` 是否为空
       兜底：provider 空 = models.yaml 没收这个模型 = 无从取价。

    为什么要单独判这一步：无法计价的条目 ``cost_usd`` 必然是 0.0，与"这次真免费"
    在账面上完全同形。成本页必须能把两者分开，否则运营读到的"总成本 $0.0000"
    是错的（实测 15 条 cost_entries 里 14 条属于这种）。
    """
    meta = entry.metadata or {}
    if "priced" in meta:
        return not bool(meta["priced"])
    return not str(meta.get("provider") or "").strip()


class CostEntry(BaseModel):
    id: str = ""
    session_id: str = ""
    agent: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class CostSummary(BaseModel):
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_calls: int = 0
    avg_latency_ms: float = 0.0
    by_model: dict[str, dict[str, Any]] = Field(default_factory=dict)
    by_agent: dict[str, dict[str, Any]] = Field(default_factory=dict)
    by_session: dict[str, dict[str, Any]] = Field(default_factory=dict)
    #: 无法计价的条目数（2026-11-08）：metadata.priced=false 或旧行无 priced 键
    #: 且 provider 为空。totals 里的金额是"有价部分的合计"，不等于真实总花费。
    unpriced_entries: int = 0


class BudgetStatus(BaseModel):
    daily_limit_usd: float = 0.0
    monthly_limit_usd: float = 0.0
    alert_threshold: float = 0.8
    daily_spent_usd: float = 0.0
    monthly_spent_usd: float = 0.0
    daily_remaining_usd: float = 0.0
    monthly_remaining_usd: float = 0.0
    daily_over_budget: bool = False
    monthly_over_budget: bool = False
    daily_warned: bool = False
    monthly_warned: bool = False


class ModelPricing(BaseModel):
    prompt_per_1m: float = 0.0
    completion_per_1m: float = 0.0


DEFAULT_PRICING: dict[str, ModelPricing] = {
    "gpt-4o": ModelPricing(prompt_per_1m=2.50, completion_per_1m=10.00),
    "gpt-4o-mini": ModelPricing(prompt_per_1m=0.15, completion_per_1m=0.60),
    "gpt-4-turbo": ModelPricing(prompt_per_1m=10.00, completion_per_1m=30.00),
    "claude-3.5-sonnet": ModelPricing(prompt_per_1m=3.00, completion_per_1m=15.00),
    "claude-3-haiku": ModelPricing(prompt_per_1m=0.25, completion_per_1m=1.25),
    "deepseek-chat": ModelPricing(prompt_per_1m=0.14, completion_per_1m=0.28),
    "deepseek-reasoner": ModelPricing(prompt_per_1m=0.55, completion_per_1m=2.19),
}


class CostTracker:
    """Real-time token usage and cost tracking with budget alerts.

    Features:
      - Record per-call token usage with automatic cost calculation
      - Query by session/agent/model/time range
      - Aggregated summaries with breakdowns
      - Budget monitoring with daily/monthly limits
      - HookManager integration for budget alerts
      - SQLite persistence
    """

    def __init__(
        self,
        root_dir: str | Path | None = "data",
        hook_manager: Any = None,
        daily_limit_usd: float = 0.0,
        monthly_limit_usd: float = 0.0,
        alert_threshold: float = 0.8,
        pricing: dict[str, ModelPricing] | None = None,
    ) -> None:
        # root_dir 兼容 None（dispatcher 等调用方可能不传）；db 路径实际由
        # get_db_path("cost_tracker") 决定，_root 仅作参数占位。
        self._root = Path(root_dir) if root_dir else Path("data")
        self._db_path = get_db_path("cost_tracker")
        self._hook_manager = hook_manager
        self._daily_limit = daily_limit_usd
        self._monthly_limit = monthly_limit_usd
        self._alert_threshold = alert_threshold
        self._pricing = pricing or DEFAULT_PRICING
        self._budget_alerted_daily = False
        self._budget_alerted_monthly = False
        self._budget_warned_daily = False
        self._budget_warned_monthly = False
        self._init_db()

    @property
    def db_path(self) -> Path:
        """M4 修复：公共访问器，暴露 SQLite 数据库路径。

        替代直接访问私有属性 ``_db_path``，供 PersonalCostGuard 等外部
        模块安全地获取数据库路径以查询累计花费。
        """
        return self._db_path

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cost_entries (
                    id TEXT PRIMARY KEY,
                    session_id TEXT DEFAULT '',
                    agent TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    cost_usd REAL DEFAULT 0.0,
                    latency_ms INTEGER DEFAULT 0,
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_session ON cost_entries(session_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_agent ON cost_entries(agent, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_model ON cost_entries(model, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_time ON cost_entries(created_at)")

    # ── Record ──────────────────────────────────────────────────

    def record(
        self,
        *,
        session_id: str = "",
        agent: str = "",
        model: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: int = 0,
        metadata: dict[str, Any] | None = None,
        cost_usd: float | None = None,
    ) -> CostEntry:
        """Record a single LLM call's token usage and calculate cost.

        ``cost_usd``（可选）：调用方已算好金额时显式传入，不再按
        ``self._pricing`` 重算。此前 :meth:`record_actual_cost` 的
        "pricing 未收录则退回 estimated_cost" 分支是**死代码**——它算出
        的金额被这里按 ``_calculate_cost`` 重算覆盖，导致 `models.yaml`
        有价、`DEFAULT_PRICING` 无价的模型（如 ``agnes-2.5-pro``）
        台账恒记 ``cost_usd=0.0``，而同一笔的 ``run.cost`` 事件写
        ``estimated_cost=0.00098``。
        """
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens

        if cost_usd is None:
            cost_usd = self._calculate_cost(model, prompt_tokens, completion_tokens)

        entry = CostEntry(
            id=f"cost-{uuid.uuid4().hex[:8]}",
            session_id=session_id,
            agent=agent,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            metadata=metadata or {},
            created_at=datetime.now(timezone.utc).isoformat(),
        )


        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO cost_entries
                   (id, session_id, agent, model, prompt_tokens, completion_tokens,
                    total_tokens, cost_usd, latency_ms, metadata, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (entry.id, entry.session_id, entry.agent, entry.model,
                 entry.prompt_tokens, entry.completion_tokens, entry.total_tokens,
                 entry.cost_usd, entry.latency_ms, json.dumps(entry.metadata), entry.created_at),
            )

        self._check_budget()
        return entry

    async def record_async(
        self,
        *,
        session_id: str = "",
        agent: str = "",
        model: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> CostEntry:
        """Async version of record() — wraps sync sqlite3 via asyncio.to_thread.

        P2-P3 fix (M5): 避免在 async 路径中阻塞事件循环。
        在 async 上下文中调用此方法而非 record()。
        """
        import asyncio
        return await asyncio.to_thread(
            self.record,
            session_id=session_id,
            agent=agent,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            metadata=metadata,
        )

    def _calculate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        pricing = self._pricing.get(model)
        if pricing is None:
            return 0.0
        prompt_cost = (prompt_tokens / 1_000_000) * pricing.prompt_per_1m
        completion_cost = (completion_tokens / 1_000_000) * pricing.completion_per_1m
        return round(prompt_cost + completion_cost, 6)

    # ── Budget ──────────────────────────────────────────────────

    def _check_budget(self) -> None:
        if self._daily_limit <= 0 and self._monthly_limit <= 0:
            return
        status = self.budget_status()
        # 软阈值预警（达到 alert_threshold 比例、尚未超限）
        if status.daily_warned and not status.daily_over_budget and not self._budget_warned_daily:
            self._budget_warned_daily = True
            self._fire_budget_alert(
                "daily", status.daily_spent_usd, self._daily_limit,
                event="cost.budget_warning",
            )
        if status.monthly_warned and not status.monthly_over_budget and not self._budget_warned_monthly:
            self._budget_warned_monthly = True
            self._fire_budget_alert(
                "monthly", status.monthly_spent_usd, self._monthly_limit,
                event="cost.budget_warning",
            )
        # 超限告警（100%）
        if status.daily_over_budget and not self._budget_alerted_daily:
            self._budget_alerted_daily = True
            self._fire_budget_alert("daily", status.daily_spent_usd, self._daily_limit)
        if status.monthly_over_budget and not self._budget_alerted_monthly:
            self._budget_alerted_monthly = True
            self._fire_budget_alert("monthly", status.monthly_spent_usd, self._monthly_limit)

    def _fire_budget_alert(self, period: str, spent: float, limit: float, event: str = "cost.budget_exceeded") -> None:
        level = "exceeded" if event == "cost.budget_exceeded" else "warning"
        logger.warning("[cost] %s budget %s: $%.4f / $%.4f", period.capitalize(), level, spent, limit)
        if self._hook_manager:
            try:
                import asyncio
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._hook_manager.trigger(event, {
                        "period": period, "spent_usd": spent, "limit_usd": limit,
                    })
                )
            except RuntimeError:
                logger.debug("[cost] No running event loop; skipping async budget alert for %s", period)

    def budget_status(self) -> BudgetStatus:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

        with sqlite_connect(self._db_path) as conn:
            daily_row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) as total FROM cost_entries WHERE created_at >= ?",
                (today_start,),
            ).fetchone()
            monthly_row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) as total FROM cost_entries WHERE created_at >= ?",
                (month_start,),
            ).fetchone()

        daily_spent = float(daily_row["total"]) if daily_row else 0.0
        monthly_spent = float(monthly_row["total"]) if monthly_row else 0.0

        return BudgetStatus(
            daily_limit_usd=self._daily_limit,
            monthly_limit_usd=self._monthly_limit,
            alert_threshold=self._alert_threshold,
            daily_spent_usd=round(daily_spent, 4),
            monthly_spent_usd=round(monthly_spent, 4),
            daily_remaining_usd=round(max(0, self._daily_limit - daily_spent), 4),
            monthly_remaining_usd=round(max(0, self._monthly_limit - monthly_spent), 4),
            daily_over_budget=self._daily_limit > 0 and daily_spent > self._daily_limit,
            monthly_over_budget=self._monthly_limit > 0 and monthly_spent > self._monthly_limit,
            daily_warned=self._daily_limit > 0 and daily_spent >= self._daily_limit * self._alert_threshold,
            monthly_warned=self._monthly_limit > 0 and monthly_spent >= self._monthly_limit * self._alert_threshold,
        )

    async def budget_status_async(self) -> BudgetStatus:
        """Async version of budget_status() — wraps sync sqlite3 via asyncio.to_thread.

        P2-P3 fix (M5): 避免在 async 路径中阻塞事件循环。
        """
        import asyncio
        return await asyncio.to_thread(self.budget_status)

    def set_budget(
        self,
        daily_limit_usd: float | None = None,
        monthly_limit_usd: float | None = None,
        alert_threshold: float | None = None,
    ) -> BudgetStatus:
        """更新预算限额与告警阈值并返回最新状态。

        未显式传入的字段保持不变（``None`` 表示"不修改"）。限额与阈值
        被钳制到合法范围（限额 >= 0，阈值 [0, 1]）。更新后重置告警状态，
        使新配置下的预算评估立即生效。这是成本告警配置通道的运行时入口，
        供 Dashboard ``PUT /api/cost/budget`` 调用。
        """
        if daily_limit_usd is not None:
            self._daily_limit = max(0.0, float(daily_limit_usd))
        if monthly_limit_usd is not None:
            self._monthly_limit = max(0.0, float(monthly_limit_usd))
        if alert_threshold is not None:
            self._alert_threshold = max(0.0, min(1.0, float(alert_threshold)))
        self._budget_alerted_daily = False
        self._budget_alerted_monthly = False
        self._budget_warned_daily = False
        self._budget_warned_monthly = False
        return self.budget_status()

    # ── Query ───────────────────────────────────────────────────

    def get_entries(
        self,
        *,
        session_id: str = "",
        agent: str = "",
        model: str = "",
        start_date: str = "",
        end_date: str = "",
        limit: int = 100,
    ) -> list[CostEntry]:
        clauses = []
        params: list[Any] = []
        if session_id:
            clauses.append("session_id=?")
            params.append(session_id)
        if agent:
            clauses.append("agent=?")
            params.append(agent)
        if model:
            clauses.append("model=?")
            params.append(model)
        if start_date:
            clauses.append("created_at >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("created_at <= ?")
            params.append(end_date)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)

        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM cost_entries {where} ORDER BY created_at DESC LIMIT ?",  # nosec B608 — 子句构造器只含固定子句与 ? 占位（已人工核）
                params,
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def summary(
        self,
        *,
        session_id: str = "",
        agent: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> CostSummary:
        entries = self.get_entries(
            session_id=session_id, agent=agent,
            start_date=start_date, end_date=end_date, limit=10000,
        )
        if not entries:
            return CostSummary()

        total_prompt = sum(e.prompt_tokens for e in entries)
        total_completion = sum(e.completion_tokens for e in entries)
        total_tokens = sum(e.total_tokens for e in entries)
        total_cost = sum(e.cost_usd for e in entries)
        total_calls = len(entries)
        avg_latency = sum(e.latency_ms for e in entries) / total_calls if total_calls else 0.0

        by_model: dict[str, dict[str, Any]] = {}
        by_agent: dict[str, dict[str, Any]] = {}
        by_session: dict[str, dict[str, Any]] = {}
        unpriced = 0

        for e in entries:
            # 无法计价 = models.yaml 未收录该模型（provider 为空）。旧行没有 priced
            # 键，所以这里不依赖新字段，按 provider 兜底判断，历史数据同样算得准。
            entry_unpriced = _entry_unpriced(e)
            unpriced += 1 if entry_unpriced else 0
            by_model.setdefault(e.model, {"tokens": 0, "cost": 0.0, "calls": 0, "unpriced": 0})
            by_model[e.model]["tokens"] += e.total_tokens
            by_model[e.model]["cost"] += e.cost_usd
            by_model[e.model]["calls"] += 1
            by_model[e.model]["unpriced"] += 1 if entry_unpriced else 0

            by_agent.setdefault(e.agent, {"tokens": 0, "cost": 0.0, "calls": 0, "unpriced": 0})
            by_agent[e.agent]["tokens"] += e.total_tokens
            by_agent[e.agent]["cost"] += e.cost_usd
            by_agent[e.agent]["calls"] += 1
            by_agent[e.agent]["unpriced"] += 1 if entry_unpriced else 0

            by_session.setdefault(e.session_id, {"tokens": 0, "cost": 0.0, "calls": 0})
            by_session[e.session_id]["tokens"] += e.total_tokens
            by_session[e.session_id]["cost"] += e.cost_usd
            by_session[e.session_id]["calls"] += 1

        return CostSummary(
            total_prompt_tokens=total_prompt,
            total_completion_tokens=total_completion,
            total_tokens=total_tokens,
            total_cost_usd=round(total_cost, 4),
            total_calls=total_calls,
            avg_latency_ms=round(avg_latency, 1),
            by_model=by_model,
            by_agent=by_agent,
            by_session=by_session,
            unpriced_entries=unpriced,
        )

    def get_pricing(self) -> dict[str, dict[str, float]]:
        return {k: {"prompt_per_1m": v.prompt_per_1m, "completion_per_1m": v.completion_per_1m} for k, v in self._pricing.items()}

    def update_pricing(self, model: str, prompt_per_1m: float, completion_per_1m: float) -> None:
        self._pricing[model] = ModelPricing(prompt_per_1m=prompt_per_1m, completion_per_1m=completion_per_1m)

    # ── Cost Reconciliation (compat with model.budget.BudgetGuard) ──

    def record_actual_cost(
        self,
        trace_id: str = "",
        model: str = "",
        provider: str = "",
        actual_tokens_in: int = 0,
        actual_tokens_out: int = 0,
        estimated_cost: float = 0.0,
        agent: str = "",
        phase: str = "",
        tokens_source: str = "",
        priced: bool | None = None,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> dict[str, Any]:
        """Post-execution cost reconciliation.

        Records actual token usage via :meth:`record` and returns a
        reconciliation summary comparing estimated vs actual cost.

        This method provides API compatibility with the legacy
        ``maop.model.budget.BudgetGuard.record_actual_cost`` so that
        ``maop_loop`` can use ``CostTracker`` as the single source of
        truth for cost data (P2-1: 成本双写统一)。

        ``agent`` / ``phase``（2026-10-29，M-X3 归属）：写入归属维度，供
        ``core.cost_attribution`` 按 agent/phase 聚合；空值不写 phase 元数据
        （聚合侧记 unattributed），旧调用方零改动。

        ``tokens_source``（2026-11-08）：用量**来源标记**。外部 CLI 路径拿不到
        真实 usage 时，台账里的 token 数是启发式估算，必须与实测值可区分，
        否则"按 token 计量省钱"无从验证。取值：``measured`` / ``estimated``
        / ``mixed``。空串 = 调用方未声明，不写。
        """
        # Calculate actual cost from the pricing table; fall back to
        # the caller's estimate when the model is not in the table.
        actual_cost = self._calculate_cost(model, actual_tokens_in, actual_tokens_out)
        if actual_cost == 0.0:
            actual_cost = estimated_cost

        metadata: dict[str, Any] = {
            "trace_id": trace_id,
            "provider": provider,
            "estimated_cost": estimated_cost,
        }
        if phase:
            metadata["phase"] = phase
        if tokens_source:
            metadata["tokens_source"] = tokens_source
        # priced（2026-11-08）：models.yaml 未收录该模型时，provider/金额都只能是
        # 空/0.0。此前这个判据只存在于 maop_loop_phases 的 run event 里，台账本身
        # 没有，于是成本页把「无法计价」显示成「$0.0000」=「免费」——实测 15 条
        # cost_entries 里 14 条如此（agents.yaml 声明的 14 个模型有 12 个不在
        # models.yaml）。None = 调用方未声明，不写（旧调用方零改动）。
        if priced is not None:
            metadata["priced"] = bool(priced)
        # 缓存命中明细（2026-11-08）：外部 CLI 的提示侧消耗里 cache_read 常占
        # 九成以上（copilot 实测 19328/19853），它是「token 节省」唯一可验证的
        # 指标。只在非零时写，避免给未启用缓存的 agent 增加噪声字段。
        if cache_read_tokens:
            metadata["cache_read_tokens"] = cache_read_tokens
        if cache_write_tokens:
            metadata["cache_write_tokens"] = cache_write_tokens

        self.record(
            agent=agent,
            model=model,
            prompt_tokens=actual_tokens_in,
            completion_tokens=actual_tokens_out,
            metadata=metadata,
            # 显式传入，避免 record() 按 DEFAULT_PRICING 重算把上面的
            # estimated_cost 兜底覆盖成 0.0。
            cost_usd=actual_cost,
        )

        discrepancy = round(actual_cost - estimated_cost, 6)
        if estimated_cost > 0 and abs(discrepancy) > estimated_cost * 0.5:
            logger.warning(
                "Cost discrepancy for trace %s: estimated $%.6f, actual $%.6f (delta %+.6f)",
                trace_id, estimated_cost, actual_cost, discrepancy,
            )

        return {
            "trace_id": trace_id,
            "estimated_cost": round(estimated_cost, 6),
            "actual_cost": round(actual_cost, 6),
            "discrepancy": discrepancy,
            "tokens_in": actual_tokens_in,
            "tokens_out": actual_tokens_out,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
        }

    # ── Internal ────────────────────────────────────────────────

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> CostEntry:

        metadata: dict[str, Any] = {}
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        return CostEntry(
            id=row["id"], session_id=row["session_id"], agent=row["agent"],
            model=row["model"], prompt_tokens=row["prompt_tokens"],
            completion_tokens=row["completion_tokens"], total_tokens=row["total_tokens"],
            cost_usd=row["cost_usd"], latency_ms=row["latency_ms"],
            metadata=metadata, created_at=row["created_at"],
        )

# ── Singleton accessor ───────────────────────────────────────

_cost_tracker_instance: CostTracker | None = None
# P1-1 fix: 保护单例创建的锁，避免多线程并发时创建多个实例。
# 参考 settings.py:get_settings() 的双检锁实现。
_cost_tracker_lock = threading.Lock()


def get_cost_tracker() -> CostTracker:
    """Return a process-wide CostTracker singleton (lazy).

    Lazily initialized on first call. Used by llm_provider to auto-record
    token usage without creating a new tracker per call.

    预算限额与告警阈值从 ``MAOPSettings`` 读取（env: ``MAOP_BUDGET_*``），
    使成本告警无需改代码即可配置；运行时可通过 :meth:`CostTracker.set_budget`
    （Dashboard ``PUT /api/cost/budget``）覆盖。

    P1-1 fix: 使用 threading.Lock + 双检锁保护单例创建，
    避免多线程并发调用时创建多个 CostTracker 实例。
    """
    global _cost_tracker_instance
    if _cost_tracker_instance is None:
        with _cost_tracker_lock:
            # 双检锁：持锁后再次检查，防止等待期间已被其他线程初始化
            if _cost_tracker_instance is None:
                daily = monthly = 0.0
                threshold = 0.8
                try:
                    from maop.config.settings import get_settings
                    settings = get_settings()
                    daily = settings.budget_daily_limit_usd
                    monthly = settings.budget_monthly_limit_usd
                    threshold = settings.budget_alert_threshold
                except Exception as exc:  # pragma: no cover - settings 加载失败时回退默认
                    logger.warning("[cost] Failed to load budget settings, using defaults: %s", exc)
                _cost_tracker_instance = CostTracker(
                    daily_limit_usd=daily,
                    monthly_limit_usd=monthly,
                    alert_threshold=threshold,
                )
    return _cost_tracker_instance
