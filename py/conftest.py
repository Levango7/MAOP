"""Root conftest.py — ensures pydantic_settings is importable.

On some Windows environments the isolation_venv's pydantic_settings directory
gets locked by running processes, resulting in a broken import (the package
directory exists but its files are inaccessible). This conftest inserts the
ps_fix fallback path at sys.path[0] so the working pydantic_settings takes
priority over the locked one.

This file is loaded by pytest before any test collection, making the
PYTHONPATH workaround automatic and permanent.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

# Always insert ps_fix at front of sys.path so it shadows any broken
# pydantic_settings in the main site-packages.
_venv_ps_fix = Path(sys.prefix) / "Lib" / "site-packages" / "ps_fix"
if _venv_ps_fix.is_dir():
    _p = str(_venv_ps_fix)
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Also check project-relative ps_fix
_proj_ps_fix = Path(__file__).resolve().parent.parent / "ps_fix"
if _proj_ps_fix.is_dir():
    _p = str(_proj_ps_fix)
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Force-reimport pydantic_settings if it was already loaded from a broken path
if "pydantic_settings" in sys.modules:
    try:
        from pydantic_settings import BaseSettings  # noqa: F401
    except ImportError:
        del sys.modules["pydantic_settings"]
        importlib.import_module("pydantic_settings")


# ── Session-scoped cleanup for async webhook resources ─────────────
# Tests that import pipeline_core.event_hook leave an aiohttp ClientSession
# and a background _async_webhook_worker task alive. Without explicit
# shutdown, pytest's teardown logs "Unclosed client session" and
# "Task was destroyed but it is pending!". This fixture ensures
# shutdown_webhook() runs once at session end.

import pytest


@pytest.fixture(scope="session", autouse=True)
def _cleanup_webhook_session():
    yield
    try:
        import sys as _sys
        for _p in list(_sys.path):
            if "doc-pipeline" in _p:
                from pipeline_core.event_hook import shutdown_webhook
                shutdown_webhook(timeout_s=3.0)
                break
    except Exception:
        pass
