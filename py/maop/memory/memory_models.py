"""MAOP Three-Layer Memory — Pydantic models & config.

Extracted from ``maop.memory.manager`` to keep each module focused and
under 500 lines. These models are re-exported from ``manager.py`` for
backward compatibility::

    from maop.memory.manager import MemoryManagerConfig  # still works
    from maop.memory.memory_models import MemoryManagerConfig  # canonical
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MemoryLayer(str):
    """Memory layer identifiers (Working / Short-term / Long-term)."""

    WORKING = "working"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"


class MemoryContext(BaseModel):
    """Aggregated context built from all three memory layers."""

    working_context: list[dict[str, Any]] = Field(default_factory=list)
    short_term_results: list[dict[str, Any]] = Field(default_factory=list)
    long_term_results: list[dict[str, Any]] = Field(default_factory=list)
    injected_summary: str = ""
    total_tokens_estimate: int = 0


class ConsolidationTrigger(BaseModel):
    """Thresholds & flags controlling automatic L2 → L3 consolidation."""

    entry_threshold: int = 100
    days_since_last: int = 7
    auto_trigger: bool = True


class MemoryManagerConfig(BaseModel):
    """Top-level configuration for :class:`MemoryManager`."""

    max_working_tokens: int = 4000
    short_term_ttl_days: int = 30
    long_term_min_group_size: int = 3
    consolidation: ConsolidationTrigger = Field(default_factory=ConsolidationTrigger)
    inject_max_results: int = 5
    inject_max_tokens: int = 800