"""Re-export shim for :mod:`maop.core.budget_guard`.

P2-1 成本双写统一：canonical implementation lives in
``maop.core.budget_guard`` (SQLite ``maop.db``). This module re-exports
all public symbols for backward compatibility with callers that still
import from ``maop.core.monitoring.budget_guard``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from maop.core.budget_guard import (
    BudgetGuard,
    BudgetStatus,
    _BUDGET_DDL,  # noqa: F401  — re-exported for monitoring/__init__ lazy lookup
)

__all__ = [
    "BudgetGuard",
    "BudgetStatus",
]

if TYPE_CHECKING:
    # Expose private symbol for static analysis.
    pass
