"""Re-export from maop.core.memory.three_layer_memory for backward compatibility.

This module is a thin shim that re-exports all public symbols from
``maop.core.memory.three_layer_memory``. The canonical implementation now
lives in the subpackage; this file exists solely to preserve
backward-compatible import paths (``from maop.core.three_layer_memory import X``).
"""
from __future__ import annotations

from maop.core.memory.three_layer_memory import *  # noqa: F401,F403
