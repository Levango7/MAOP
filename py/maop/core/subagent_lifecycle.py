"""Re-export from maop.core.agent.delegation.subagent_lifecycle for backward compatibility.

This module is a thin shim that re-exports all public symbols from
``maop.core.agent.delegation.subagent_lifecycle``. The canonical
implementation now lives in the subpackage; this file exists solely to
preserve backward-compatible import paths
(``from maop.core.subagent_lifecycle import X``).
"""
from __future__ import annotations

from maop.core.agent.delegation.subagent_lifecycle import *  # noqa: F401,F403
