"""BudgetGuard — Cost tracking and budget enforcement."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from maop.model.schema import BudgetConfig

logger = logging.getLogger(__name__)


class BudgetGuard:
    """Tracks spending and enforces budget limits.

    Usage::

        guard = BudgetGuard(root_dir="/path/to/MAOP")
        if guard.can_spend(estimated_cost=0.005):
            guard.record(model="yi-large", provider="stepfun",
                         cost=0.005, tokens_in=1000, tokens_out=500)
        else:
            # budget exceeded, use cheaper model or reject
    """

    def __init__(self, root_dir: Path | str | None = None,
                 config: BudgetConfig | None = None) -> None:
        self._root = Path(root_dir) if root_dir else Path.cwd()
        self._config = config or BudgetConfig()
        self._daily_spend: float = 0.0
        self._monthly_spend: float = 0.0
        self._ledger: list[dict] = []  # cost records
        self._alerted: bool = False
        self._registry: Any = None  # cached ModelRegistry
        self._load_ledger()

    def _ledger_path(self) -> Path:
        return self._root / "data" / "budget_ledger.json"

    def _load_ledger(self) -> None:
        """Load today's ledger from disk."""
        path = self._ledger_path()
        if not path.exists():
            return
        try:
            import json
            data = json.loads(path.read_text(encoding="utf-8"))
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            this_month = today[:7]
            for entry in data.get("entries", []):
                ts = entry.get("timestamp", "")
                if ts.startswith(today):
                    self._daily_spend += entry.get("cost", 0)
                if ts.startswith(this_month):
                    self._monthly_spend += entry.get("cost", 0)
                self._ledger.append(entry)
        except Exception as exc:
            logger.warning("Failed to load budget ledger: %s", exc)

    def _save_ledger(self) -> None:
        """Persist ledger to disk."""
        path = self._ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import json
            data = {"entries": self._ledger[-1000:]}  # keep last 1000
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to save budget ledger: %s", exc)

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
        """Record an actual cost."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model, "provider": provider,
            "cost": round(cost, 6),
            "tokens_in": tokens_in, "tokens_out": tokens_out,
        }
        self._ledger.append(entry)
        self._daily_spend += cost
        self._monthly_spend += cost
        self._save_ledger()

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
        """Post-execution cost reconciliation.

        Compares estimated cost with actual token usage and records
        the real cost.  Returns a reconciliation summary so the
        caller (e.g. MaopLoop) can log or act on discrepancies.

        Usage::

            # after agent CLI returns
            guard.record_actual_cost(
                trace_id=trace_id,
                model=effective.model_name,
                provider=effective.provider,
                actual_tokens_in=usage["input"],
                actual_tokens_out=usage["output"],
                estimated_cost=effective.cost_estimate,
            )
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
            pass  # fall back to estimated

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
