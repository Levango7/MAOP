"""BudgetGuard — Cost tracking and budget enforcement (deprecated shim).

.. deprecated:: P2-1
    This JSON-backed ``BudgetGuard`` is superseded by the SQLite-backed
    :class:`maop.core.cost_tracker.CostTracker` which is now the single
    source of truth for cost data. New code should import ``CostTracker``
    from :mod:`maop.core.cost_tracker` (or via the re-export below:
    ``from maop.model.budget import CostTracker``).

    The legacy ``BudgetGuard`` class is retained here as a **deprecated
    in-memory shim** so that existing callers (``delegate/dispatcher.py``
    read-only ``can_spend``, ``dashboard/routers/model.py`` read-only
    ``stats``) and the test suite (``tests/test_budget.py``) continue to
    work without changes. The JSON ``budget_ledger.json`` read/write path
    has been **removed** — the main loop (``maop_loop.py``) now writes to
    ``CostTracker`` directly, and the dispatcher reads budget status from
    ``CostTracker`` as well. This shim only keeps in-memory accumulators
    for backward-compatible ``can_spend`` / ``record`` / ``stats`` semantics.

    For SQLite-backed daily token/cost enforcement (with hook events), use
    :class:`maop.core.budget_guard.BudgetGuard` instead.
"""

from __future__ import annotations

import logging
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Re-export CostTracker as the canonical cost source (P2-1).
from maop.core.cost_tracker import CostTracker  # noqa: F401  — re-exported for callers
from maop.model.schema import BudgetConfig

logger = logging.getLogger(__name__)

# Emit a DeprecationWarning at import time so callers know to migrate.
warnings.warn(
    "maop.model.budget is deprecated since P2-1; use maop.core.cost_tracker.CostTracker "
    "or maop.core.budget_guard.BudgetGuard instead. The JSON budget_ledger.json path "
    "has been removed; this module now only provides an in-memory BudgetGuard shim.",
    DeprecationWarning,
    stacklevel=2,
)


class BudgetGuard:
    """Deprecated in-memory budget guard shim.

    Tracks spending and enforces budget limits **in memory only**.
    The former JSON ``budget_ledger.json`` persistence has been removed
    (P2-1 成本双写统一) — callers needing persistence should use
    :class:`maop.core.cost_tracker.CostTracker` instead.

    Usage (deprecated)::

        guard = BudgetGuard(root_dir="/path/to/MAOP")
        if guard.can_spend(estimated_cost=0.005):
            guard.record(model="yi-large", provider="stepfun",
                         cost=0.005, tokens_in=1000, tokens_out=500)
        else:
            # budget exceeded, use cheaper model or reject
    """

    def __init__(self, root_dir: Path | str | None = None,
                 config: BudgetConfig | None = None) -> None:
        warnings.warn(
            "maop.model.budget.BudgetGuard is deprecated; use "
            "maop.core.cost_tracker.CostTracker or "
            "maop.core.budget_guard.BudgetGuard instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._root = Path(root_dir) if root_dir else Path.cwd()
        self._config = config or BudgetConfig()
        self._daily_spend: float = 0.0
        self._monthly_spend: float = 0.0
        self._ledger: list[dict] = []  # in-memory cost records (no longer persisted to JSON)
        self._alerted: bool = False
        self._registry: Any = None  # cached ModelRegistry

    def can_spend(self, estimated_cost: float = 0.0) -> bool:
        """Check if spending estimated_cost is within budget."""
        if not self._config.hard_stop:
            return True
        if self._daily_spend + estimated_cost > self._config.daily_limit:
            logger.warning(
                "Daily budget exceeded: $%.4f + $%.4f > $%.4f",
                self._daily_spend, estimated_cost, self._config.daily_limit,
            )
            return False
        if self._monthly_spend + estimated_cost > self._config.monthly_limit:
            logger.warning(
                "Monthly budget exceeded: $%.4f + $%.4f > $%.4f",
                self._monthly_spend, estimated_cost, self._config.monthly_limit,
            )
            return False
        return True

    def record(self, model: str, provider: str, cost: float,
               tokens_in: int = 0, tokens_out: int = 0) -> None:
        """Record an actual cost (in-memory only; no JSON persistence)."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model, "provider": provider,
            "cost": round(cost, 6),
            "tokens_in": tokens_in, "tokens_out": tokens_out,
        }
        self._ledger.append(entry)
        self._daily_spend += cost
        self._monthly_spend += cost

        # Check alert threshold
        if not self._alerted and self._daily_spend >= self._config.daily_limit * self._config.alert_threshold:
            logger.warning(
                "Budget alert: daily spend $%.4f >= %.0f%% of $%.4f",
                self._daily_spend, self._config.alert_threshold * 100,
                self._config.daily_limit,
            )
            self._alerted = True

    def stats(self) -> dict:
        """Return budget statistics for Dashboard."""
        return {
            "daily_spend": round(self._daily_spend, 4),
            "daily_limit": self._config.daily_limit,
            "monthly_spend": round(self._monthly_spend, 4),
            "monthly_limit": self._config.monthly_limit,
            "alert_threshold": self._config.alert_threshold,
            "hard_stop": self._config.hard_stop,
            "daily_remaining": round(max(0, self._config.daily_limit - self._daily_spend), 4),
            "daily_utilization": round(
                self._daily_spend / self._config.daily_limit if self._config.daily_limit > 0 else 0, 4
            ),
            "alerted": self._alerted,
        }

    def reset_alert(self) -> None:
        self._alerted = False

    def record_actual_cost(
        self,
        trace_id: str = "",
        model: str = "",
        provider: str = "",
        actual_tokens_in: int = 0,
        actual_tokens_out: int = 0,
        estimated_cost: float = 0.0,
    ) -> dict:
        """Post-execution cost reconciliation (in-memory; deprecated).

        Compares estimated cost with actual token usage and records
        the real cost.  Returns a reconciliation summary so the
        caller (e.g. MaopLoop) can log or act on discrepancies.

        .. deprecated::
            Use :meth:`maop.core.cost_tracker.CostTracker.record_actual_cost`
            instead — that persists to SQLite and is the canonical path.
        """
        # Look up model pricing for actual cost calculation
        actual_cost = estimated_cost
        try:
            if self._registry is None:
                from maop.model.registry import ModelRegistry
                self._registry = ModelRegistry(project_root=self._root)
            m = self._registry.get_model(model)
            if m:
                actual_cost = (
                    m.cost_per_1k_input * actual_tokens_in / 1000
                    + m.cost_per_1k_output * actual_tokens_out / 1000
                )
        except Exception:
            logger.debug('swallowed exception', exc_info=True)
            # fall back to estimated

        self.record(
            model=model, provider=provider, cost=actual_cost,
            tokens_in=actual_tokens_in, tokens_out=actual_tokens_out,
        )

        discrepancy = round(actual_cost - estimated_cost, 6)
        if abs(discrepancy) > estimated_cost * 0.5:  # >50% deviation
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
