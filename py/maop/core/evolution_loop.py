"""Re-export from maop.core.evolution.evolution_loop for backward compatibility.

This module is a thin shim that re-exports all public symbols from
``maop.core.evolution.evolution_loop``. The canonical implementation now
lives in the subpackage; this file exists solely to preserve
backward-compatible import paths (``from maop.core.evolution_loop import X``).
"""
from __future__ import annotations

from maop.core.evolution.evolution_loop import *  # noqa: F401,F403
