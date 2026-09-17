"""Budget & Cost service layer.

Encapsulates business logic for the Budget Guard (``budget.py``) and
Cost Tracker (``cost.py``) routers. Extracted from the router layer so
routers only do parameter parsing + auth + service call + response
formatting.

The service is intentionally framework-agnostic: it does not import
FastAPI and can be unit-tested or invoked without an HTTP context.
Shared runtime state (``MAOP_ROOT``) is accessed via the
``maop.dashboard.routers.state`` module so the service operates on the
same singletons the dashboard initialised.

Singletons (``_budget_guard``, ``_cost_tracker``) use double-checked
locking, matching the original router style. ``_set_budget_guard`` /
``_set_cost_tracker`` are provided for test injection (isolated DB).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

# Shared runtime state — accessed via the ``state`` module so that tests
# which monkeypatch ``state.MAOP_ROOT`` take effect here too.
from maop.dashboard.routers import state

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# §1  Budget Guard — 预算守护
# ════════════════════════════════════════════════════════════════════════════

_budget_guard: Any = None
_budget_guard_lock = threading.Lock()


def _get_budget_guard() -> Any:
    """惰性初始化 BudgetGuard 单例（双重检查锁定）。"""
    global _budget_guard
    if _budget_guard is not None:
        return _budget_guard
    with _budget_guard_lock:
        if _budget_guard is not None:  # double-checked locking
            return _budget_guard
        from maop.core.budget_guard import BudgetGuard
        _budget_guard = BudgetGuard(root_dir=str(state.MAOP_ROOT))
    return _budget_guard


def _set_budget_guard(guard: Any) -> None:
    """供测试注入自定义 BudgetGuard（隔离 DB）。"""
    global _budget_guard
    with _budget_guard_lock:
        _budget_guard = guard


def get_budget_status() -> dict[str, Any]:
    """返回当前预算使用状态。

    Returns
    -------
    dict
        ``{"budget": {...}}`` — budget 为 BudgetStatus.model_dump()。
    """
    guard = _get_budget_guard()
    status = guard.get_status()
    return {"budget": status.model_dump()}


def reset_budget() -> None:
    """重置预算计数器为零。"""
    guard = _get_budget_guard()
    guard.reset_daily()


def record_budget_usage(
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
) -> dict[str, Any]:
    """记录一条预算使用条目。

    Returns
    -------
    dict
        ``{"budget": {...}}`` — budget 为 BudgetStatus.model_dump()。
    """
    guard = _get_budget_guard()
    result = guard.record_usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
    )
    return {"budget": result.model_dump()}


# ════════════════════════════════════════════════════════════════════════════
# §2  Cost Tracker — 成本追踪
# ════════════════════════════════════════════════════════════════════════════

_cost_tracker: Any = None
_cost_tracker_lock = threading.Lock()


def _get_cost_tracker() -> Any:
    """惰性初始化 CostTracker 进程级单例。

    使用进程级单例，保证读写的预算配置与 llm_provider 的 auto-record
    共享同一份限额/阈值状态（否则每次新建实例会导致配置丢失）。
    """
    global _cost_tracker
    if _cost_tracker is not None:
        return _cost_tracker
    with _cost_tracker_lock:
        if _cost_tracker is not None:  # double-checked locking
            return _cost_tracker
        from maop.core.cost_tracker import get_cost_tracker
        _cost_tracker = get_cost_tracker()
    return _cost_tracker


def _set_cost_tracker(tracker: Any) -> None:
    """供测试注入自定义 CostTracker（隔离 DB）。"""
    global _cost_tracker
    with _cost_tracker_lock:
        _cost_tracker = tracker


def get_cost_entries(
    session_id: str = "",
    agent: str = "",
    model: str = "",
    start_date: str = "",
    end_date: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """查询成本记录条目，返回 model_dump 后的 dict 列表。"""
    tracker = _get_cost_tracker()
    entries = tracker.get_entries(
        session_id=session_id,
        agent=agent,
        model=model,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )
    return [e.model_dump() for e in entries]


def get_cost_summary(
    session_id: str = "",
    agent: str = "",
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """查询成本汇总，返回 model_dump 后的 dict。"""
    tracker = _get_cost_tracker()
    summary = tracker.summary(
        session_id=session_id,
        agent=agent,
        start_date=start_date,
        end_date=end_date,
    )
    return summary.model_dump()


async def get_cost_budget_status() -> dict[str, Any]:
    """查询成本预算状态，返回 model_dump 后的 dict。

    优先使用 ``budget_status_async``（若 tracker 提供），否则降级为
    同步 ``budget_status``。
    """
    tracker = _get_cost_tracker()
    if hasattr(tracker, "budget_status_async"):
        status = await tracker.budget_status_async()
    else:
        status = tracker.budget_status()
    return status.model_dump()


def update_cost_budget(
    daily_limit_usd: float | None = None,
    monthly_limit_usd: float | None = None,
    alert_threshold: float | None = None,
) -> dict[str, Any]:
    """更新成本预算限额与告警阈值。

    未传入的字段保持不变；``0`` 表示无限额。返回更新后的预算状态。
    """
    tracker = _get_cost_tracker()
    status = tracker.set_budget(
        daily_limit_usd=daily_limit_usd,
        monthly_limit_usd=monthly_limit_usd,
        alert_threshold=alert_threshold,
    )
    return status.model_dump()


def get_pricing() -> Any:
    """获取当前定价表。"""
    tracker = _get_cost_tracker()
    return tracker.get_pricing()


def update_pricing(model: str, prompt_per_1m: float, completion_per_1m: float) -> None:
    """更新指定模型的定价。"""
    tracker = _get_cost_tracker()
    tracker.update_pricing(
        model=model,
        prompt_per_1m=prompt_per_1m,
        completion_per_1m=completion_per_1m,
    )


async def record_cost(
    session_id: str = "",
    agent: str = "",
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    latency_ms: float = 0.0,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """记录一条成本条目，返回 model_dump 后的 entry dict。

    优先使用 ``record_async``（若 tracker 提供），否则降级为同步 ``record``。
    """
    tracker = _get_cost_tracker()
    if hasattr(tracker, "record_async"):
        entry = await tracker.record_async(
            session_id=session_id,
            agent=agent,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            metadata=metadata,
        )
    else:
        entry = tracker.record(
            session_id=session_id,
            agent=agent,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            metadata=metadata,
        )
    return entry.model_dump()