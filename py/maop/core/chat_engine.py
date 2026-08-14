"""Re-export from maop.core.agent.llm_chat.chat_engine for backward compatibility.

This module is a thin shim that re-exports all public symbols from
``maop.core.agent.llm_chat.chat_engine``. The canonical implementation now
lives in the subpackage; this file exists solely to preserve
backward-compatible import paths (``from maop.core.chat_engine import X``).
"""
from __future__ import annotations

from maop.core.agent.llm_chat.chat_engine import *  # noqa: F401,F403
