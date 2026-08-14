"""Re-export from maop.core.backends.kv_store for backward compatibility.

This module is a thin shim that re-exports all public symbols from
``maop.core.backends.kv_store``. The canonical implementation now lives in
the subpackage; this file exists solely to preserve backward-compatible
import paths (``from maop.core.kv_store import X``).
"""
from __future__ import annotations

from maop.core.backends.kv_store import *  # noqa: F401,F403
