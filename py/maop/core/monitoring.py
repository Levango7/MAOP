"""Re-export from maop.core.monitoring.monitoring for backward compatibility.

This module is a thin shim that re-exports all public symbols from
``maop.core.monitoring.monitoring``. The canonical implementation now lives
in the subpackage; this file exists solely to preserve backward-compatible
import paths (``from maop.core.monitoring import X``).
"""
from __future__ import annotations

from maop.core.monitoring.monitoring import *  # noqa: F401,F403
