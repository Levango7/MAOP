"""MAOP Budget Guard — Daily token/cost budget enforcement.

Prevents runaway LLM costs by enforcing daily limits on token usage
and spending. When the budget is exceeded, LLM calls are blocked
and a hook event is triggered.

Usage::

    from maop.core.monitoring.budget_guard import BudgetGuard

    guard = BudgetGuard(root_dir="/path/to/MAOP", daily_token_limit=500000, daily_cost_limit=5.0)

    # Check before LLM call
    if not guard.check_budget():
        return LLMResponse(content="[Budget Exceeded] Daily limit reached")

    # Record usage after LLM call
    guard.record_usage(prompt_tokens=1000, completion_tokens=500, cost_usd=0.02)
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class BudgetStatus(BaseModel):
    """Current budget status."""
    date: str = ""
    tokens_used: int = 0
    tokens_limit: int = 0
    cost_used: float = 0.0
    cost_limit: float = 0.0
    calls_count: int = 0
    budget_exceeded: bool = False
    reason: str = ""


_BUDGET_DDL = """
CREATE TABLE IF NOT EXISTS budget_daily (
    date TEXT PRIMARY KEY,
    tokens_used INTEGER DEFAULT 0,
    cost_used REAL DEFAULT 0.0,
    calls_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS budget_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ── Parallel Implementation Note ──────────────────────────────
# NOTE: BudgetGuard (SQLite-backed, this class) is one of two parallel
# budget implementations. The other is BudgetGuard (JSON-backed) in
# maop/model/budget.py. Both have production callers:
#   - This class (SQLite): used by dashboard/routers/budget.py, dashboard/routers/state.py
#   - model/budget.py BudgetGuard (JSON): used by maop_loop.py, delegate/dispatcher.py
# Future work: consider merging into a single canonical implementation.

class BudgetGuard:
    """Enforce daily token and cost budgets for LLM calls.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    daily_token_limit : int
        Maximum tokens per day. 0 = unlimited.
    daily_cost_limit : float
        Maximum cost (USD) per day. 0.0 = unlimited.
    """

    def __init__(
        self,
        root_dir: str | Path,
        daily_token_limit: int = 0,
        daily_cost_limit: float = 0.0,
    ) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("budget_guard")
        self._daily_token_limit = daily_token_limit
        self._daily_cost_limit = daily_cost_limit
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_BUDGET_DDL)

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def check_budget(self) -> bool:
        """Check if budget allows another LLM call.

        Returns True if within budget, False if exceeded.
        """
        today = self._today()

        with self._connect() as conn:
            row = conn.execute(
                "SELECT tokens_used, cost_used FROM budget_daily WHERE date = ?",
                (today,),
            ).fetchone()

        if row is None:
            return True

        tokens_used, cost_used = row[0], row[1]

        if self._daily_token_limit > 0 and tokens_used >= self._daily_token_limit:
            logger.warning(
                "[budget] Daily token limit exceeded: %d/%d",
                tokens_used, self._daily_token_limit,
            )
            return False

        if self._daily_cost_limit > 0.0 and cost_used >= self._daily_cost_limit:
            logger.warning(
                "[budget] Daily cost limit exceeded: $%.4f/$%.2f",
                cost_used, self._daily_cost_limit,
            )
            return False

        return True

    def record_usage(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> BudgetStatus:
        """Record LLM usage after a call.

        Returns the updated budget status.
        """
        today = self._today()
        total_tokens = prompt_tokens + completion_tokens

        with self._connect() as conn:
            row = conn.execute(
                "SELECT tokens_used, cost_used, calls_count FROM budget_daily WHERE date = ?",
                (today,),
            ).fetchone()

            if row is None:
                conn.execute(
                    "INSERT INTO budget_daily (date, tokens_used, cost_used, calls_count) VALUES (?, ?, ?, ?)",
                    (today, total_tokens, cost_usd, 1),
                )
                tokens_used = total_tokens
                cost_used = cost_usd
                calls_count = 1
            else:
                tokens_used = row[0] + total_tokens
                cost_used = row[1] + cost_usd
                calls_count = row[2] + 1
                conn.execute(
                    "UPDATE budget_daily SET tokens_used = ?, cost_used = ?, calls_count = ? WHERE date = ?",
                    (tokens_used, cost_used, calls_count, today),
                )

        budget_exceeded = False
        reason = ""
        if self._daily_token_limit > 0 and tokens_used >= self._daily_token_limit:
            budget_exceeded = True
            reason = f"Token limit: {tokens_used}/{self._daily_token_limit}"
        if self._daily_cost_limit > 0.0 and cost_used >= self._daily_cost_limit:
            budget_exceeded = True
            reason = f"Cost limit: ${cost_used:.4f}/${self._daily_cost_limit:.2f}"

        if budget_exceeded:
            try:
                import asyncio

                from maop.core.agent.plugins_hooks.hook_manager import HookManager
                hm = HookManager(root_dir=str(self._root))
                with contextlib.suppress(RuntimeError):
                    asyncio.get_running_loop()
                asyncio.run(hm.trigger("on_budget_exceed", {
                    "date": today,
                    "tokens_used": tokens_used,
                    "cost_used": cost_usd,
                    "reason": reason,
                }))
            except Exception:
                logger.debug("Silent exception in core/budget_guard.py:201", exc_info=True)

        return BudgetStatus(
            date=today,
            tokens_used=tokens_used,
            tokens_limit=self._daily_token_limit,
            cost_used=cost_used,
            cost_limit=self._daily_cost_limit,
            calls_count=calls_count,
            budget_exceeded=budget_exceeded,
            reason=reason,
        )

    def get_status(self) -> BudgetStatus:
        """Get current budget status."""
        today = self._today()

        with self._connect() as conn:
            row = conn.execute(
                "SELECT tokens_used, cost_used, calls_count FROM budget_daily WHERE date = ?",
                (today,),
            ).fetchone()

        if row is None:
            return BudgetStatus(
                date=today,
                tokens_limit=self._daily_token_limit,
                cost_limit=self._daily_cost_limit,
            )

        tokens_used, cost_used, calls_count = row
        budget_exceeded = not self.check_budget()

        return BudgetStatus(
            date=today,
            tokens_used=tokens_used,
            tokens_limit=self._daily_token_limit,
            cost_used=cost_used,
            cost_limit=self._daily_cost_limit,
            calls_count=calls_count,
            budget_exceeded=budget_exceeded,
        )

    def reset_daily(self) -> None:
        """Reset today's budget counters (admin action)."""
        today = self._today()
        with self._connect() as conn:
            conn.execute("DELETE FROM budget_daily WHERE date = ?", (today,))
        logger.info("[budget] Daily budget reset for %s", today)
