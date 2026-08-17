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
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


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
    ) -> CostEntry:
        """Record a single LLM call's token usage and calculate cost."""
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens

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

        import json
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
                f"SELECT * FROM cost_entries {where} ORDER BY created_at DESC LIMIT ?",
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

        for e in entries:
            by_model.setdefault(e.model, {"tokens": 0, "cost": 0.0, "calls": 0})
            by_model[e.model]["tokens"] += e.total_tokens
            by_model[e.model]["cost"] += e.cost_usd
            by_model[e.model]["calls"] += 1

            by_agent.setdefault(e.agent, {"tokens": 0, "cost": 0.0, "calls": 0})
            by_agent[e.agent]["tokens"] += e.total_tokens
            by_agent[e.agent]["cost"] += e.cost_usd
            by_agent[e.agent]["calls"] += 1

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
    ) -> dict[str, Any]:
        """Post-execution cost reconciliation.

        Records actual token usage via :meth:`record` and returns a
        reconciliation summary comparing estimated vs actual cost.

        This method provides API compatibility with the legacy
        ``maop.model.budget.BudgetGuard.record_actual_cost`` so that
        ``maop_loop`` can use ``CostTracker`` as the single source of
        truth for cost data (P2-1: 成本双写统一).
        """
        # Calculate actual cost from the pricing table; fall back to
        # the caller's estimate when the model is not in the table.
        actual_cost = self._calculate_cost(model, actual_tokens_in, actual_tokens_out)
        if actual_cost == 0.0:
            actual_cost = estimated_cost

        self.record(
            model=model,
            prompt_tokens=actual_tokens_in,
            completion_tokens=actual_tokens_out,
            metadata={
                "trace_id": trace_id,
                "provider": provider,
                "estimated_cost": estimated_cost,
            },
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
        }

    # ── Internal ────────────────────────────────────────────────

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> CostEntry:
        import json
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


def get_cost_tracker() -> CostTracker:
    """Return a process-wide CostTracker singleton (lazy).

    Lazily initialized on first call. Used by llm_provider to auto-record
    token usage without creating a new tracker per call.

    预算限额与告警阈值从 ``MAOPSettings`` 读取（env: ``MAOP_BUDGET_*``），
    使成本告警无需改代码即可配置；运行时可通过 :meth:`CostTracker.set_budget`
    （Dashboard ``PUT /api/cost/budget``）覆盖。
    """
    global _cost_tracker_instance
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
