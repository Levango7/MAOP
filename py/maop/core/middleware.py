"""Re-export from maop.core.security.middleware for backward compatibility.

This module is a thin shim that re-exports all public symbols from
``maop.core.security.middleware``. The canonical implementation now lives
in the subpackage; this file exists solely to preserve backward-compatible
import paths (``from maop.core.middleware import X``).
"""
from __future__ import annotations

from maop.core.security.middleware import *  # noqa: F401,F403
