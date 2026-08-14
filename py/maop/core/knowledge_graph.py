"""Re-export from maop.core.memory.knowledge_graph for backward compatibility.

This module is a thin shim that re-exports all public symbols from
``maop.core.memory.knowledge_graph``. The canonical implementation now lives
in the subpackage; this file exists solely to preserve backward-compatible
import paths (``from maop.core.knowledge_graph import X``).
"""
from __future__ import annotations

from maop.core.memory.knowledge_graph import *  # noqa: F401,F403
