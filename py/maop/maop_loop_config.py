"""MAOP Loop config/models re-export hub.

Centralizes all dataclass/pydantic models and the LoopConfig configuration
object used by MaopLoop so callers can import them from a single canonical
path. The actual definitions live in:

  - ``maop.core.agent.evolution.phases`` → PhaseContext, PhaseResult
  - ``maop.loop_models``           → LoopConfig, LoopResult, RequirementAnalysis

This module only re-exports; ``maop.maop_loop`` re-exports from here in turn
for backward compatibility with the historical ``from maop.maop_loop import
LoopConfig, ...`` import path.
"""

from __future__ import annotations

# Phase context/result dataclasses — defined in core.agent.evolution.phases
from maop.core.agent.evolution.phases import PhaseContext, PhaseResult

# Pydantic models + LoopConfig — defined in loop_models.py
from maop.loop_models import LoopConfig, LoopResult, RequirementAnalysis

__all__ = [
    "LoopConfig",
    "LoopResult",
    "PhaseContext",
    "PhaseResult",
    "RequirementAnalysis",
]
