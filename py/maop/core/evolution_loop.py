"""Re-export from maop.core.evolution.evolution_loop for backward compatibility.

This module is a thin shim that re-exports all public symbols from
``maop.core.evolution.evolution_loop``. The canonical implementation now
lives in the subpackage; this file exists solely to preserve
backward-compatible import paths (``from maop.core.evolution_loop import X``).
"""
from __future__ import annotations

from maop.core.evolution.evolution_loop import *  # re-export shim（star import）

# T2 拆分后 perf_loop 独立成模块，star import 带不出 re-export 符号，
# 此处显式补充，保持 `from maop.core.evolution_loop import X` 兼容。
from maop.core.evolution.evolution_perf_loop import (  # noqa: F401
    EvolutionCycleReport,
    PerformanceEvolutionLoop,
)
