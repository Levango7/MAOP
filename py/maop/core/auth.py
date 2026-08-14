"""Re-export from maop.core.security.auth for backward compatibility.

This module is a thin shim that re-exports all public symbols from
``maop.core.security.auth``. The canonical implementation now lives in the
subpackage; this file exists solely to preserve backward-compatible import
paths (``from maop.core.auth import X``).
"""
from __future__ import annotations

from maop.core.security.auth import *  # noqa: F401,F403
