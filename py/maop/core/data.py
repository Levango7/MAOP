"""Re-export from maop.core.backends.data for backward compatibility.

This module is a thin shim that re-exports all public symbols from
``maop.core.backends.data``. The canonical implementation now lives in the
subpackage; this file exists solely to preserve backward-compatible import
paths (``from maop.core.data import X``).
"""
from __future__ import annotations

from maop.core.backends.data import *  # noqa: F401,F403
