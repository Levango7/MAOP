"""Re-export shim for :mod:`maop.core.cost_tracker`.

P2-1 成本双写统一：canonical implementation lives in
``maop.core.cost_tracker`` (SQLite ``maop.db``). This module re-exports
all public symbols for backward compatibility with callers that still
import from ``maop.core.monitoring.cost_tracker``.
"""
from __future__ import annotations

from maop.core.cost_tracker import (
    DEFAULT_PRICING,
    BudgetStatus,
    CostEntry,
    CostSummary,
    CostTracker,
    ModelPricing,
    get_cost_tracker,
)

__all__ = [
    "BudgetStatus",
    "CostEntry",
    "CostSummary",
    "CostTracker",
    "DEFAULT_PRICING",
    "ModelPricing",
    "get_cost_tracker",
]


def __getattr__(name: str):
    """Proxy attribute access to the canonical module.

    Exposes private module-level state (e.g. ``_cost_tracker_instance``
    singleton) so that ``patch("maop.core.monitoring.cost_tracker.X")``
    and direct attribute reads keep working against the real module.
    """
    import maop.core.cost_tracker as _ct
    try:
        return getattr(_ct, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
