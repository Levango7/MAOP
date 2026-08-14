"""Re-export from maop.core.evolution.ab_test for backward compatibility.

This module is a thin shim that re-exports all public symbols from
``maop.core.evolution.ab_test``. The canonical implementation now lives in
the subpackage; this file exists solely to preserve backward-compatible
import paths (``from maop.core.ab_test import X``).
"""
from __future__ import annotations

from maop.core.evolution.ab_test import *  # noqa: F401,F403
