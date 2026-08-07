"""Application lifespan (startup/shutdown) and signal handling.

Extracted from server.py (§2.4). The ``lifespan`` async context manager
is passed to ``FastAPI(lifespan=...)``; ``_signal_handler`` is installed
by server.py after app creation for graceful SIGTERM/SIGINT shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from contextlib import suppress
from typing import Any

from fastapi import FastAPI

from maop.dashboard.routers import auth as _auth_mod

from . import ws_state
from .ws_broadcast import _ws_push_loop

logger = logging.getLogger(__name__)


async def lifespan(app: FastAPI) -> Any:
    """FastAPI lifespan: start background tasks on startup, cancel on shutdown."""
    # MAOP_ROOT imported lazily to avoid circular import at module load time
    # (server.py defines MAOP_ROOT before importing this module, but the
    # explicit import keeps the dependency visible).
    from maop.dashboard.server import MAOP_ROOT

    if _auth_mod._auth_enabled:
        app.state.auth_manager = _auth_mod.get_auth_mgr()
    ws_state._ws_push_task = asyncio.create_task(_ws_push_loop())

    # ── Initialize OTel tracing (if enabled) ────────────────────
    try:
        from maop.core.monitoring.otel import setup_provider

        setup_provider()
    except Exception as exc:
        logger.debug("[lifespan] OTel setup skipped: %s", exc)

    # ── Auto-start backup & log-rotation schedulers ────────────
    _backup_scheduler = None
    _log_rotate_scheduler = None
    _sched_enabled = os.environ.get("MAOP_AUTO_SCHED", "1") == "1"
    if _sched_enabled:
        try:
            from maop.core.backends.db_backup import DbBackup

            _backup_scheduler = DbBackup(root_dir=str(MAOP_ROOT))
            _backup_scheduler.start_scheduler(
                interval_s=float(os.environ.get("MAOP_BACKUP_INTERVAL", "3600"))
            )
            logger.info("[lifespan] Backup scheduler auto-started")
        except Exception as exc:
            logger.warning("[lifespan] Failed to start backup scheduler: %s", exc)
        try:
            from maop.core.reliability.log_rotate import LogRotateScheduler

            _log_rotate_scheduler = LogRotateScheduler(
                interval_s=float(os.environ.get("MAOP_LOGROTATE_INTERVAL", "600")),
                log_dir=str(MAOP_ROOT / "logs"),
                data_dir=str(MAOP_ROOT / "data"),
            )
            _log_rotate_scheduler.start()
            logger.info("[lifespan] Log-rotate scheduler auto-started")
        except Exception as exc:
            logger.warning("[lifespan] Failed to start log-rotate scheduler: %s", exc)

    try:
        yield
    finally:
        if ws_state._ws_push_task is not None:
            ws_state._ws_push_task.cancel()
            with suppress(asyncio.CancelledError):
                await ws_state._ws_push_task
            ws_state._ws_push_task = None
        # ── Stop schedulers on shutdown ────────────────────────
        if _backup_scheduler is not None:
            try:
                _backup_scheduler.stop_scheduler()
            except Exception as exc:
                logger.warning("[shutdown] Backup scheduler stop failed: %s", exc)
        if _log_rotate_scheduler is not None:
            try:
                _log_rotate_scheduler.stop()
            except Exception as exc:
                logger.warning("[shutdown] Log-rotate scheduler stop failed: %s", exc)


# ── Graceful Shutdown ──────────────────────────────────────────────
# OPS-1 fix: do NOT raise SystemExit(0) from signal context — that bypasses
# uvicorn's graceful shutdown (in-flight requests dropped, lifespan shutdown
# skipped). Instead, log and CHAIN to the previously installed handler
# (uvicorn's, or Python's default which raises KeyboardInterrupt for SIGINT —
# both trigger uvicorn's graceful shutdown path).
_shutting_down: bool = False
_prev_handlers: dict[int, Any] = {}


def _signal_handler(signum: int, frame: Any) -> None:
    global _shutting_down
    if not _shutting_down:
        _shutting_down = True
        sig_name = signal.Signals(signum).name
        logger.info("Received %s, initiating graceful shutdown...", sig_name)
    prev = _prev_handlers.get(signum)
    if callable(prev):
        prev(signum, frame)
    elif prev == signal.SIG_DFL:
        # Restore default and re-send so default semantics apply
        signal.signal(signum, signal.SIG_DFL)
        signal.raise_signal(signum)
    # SIG_IGN or None: nothing else to do


def install_signal_handlers() -> None:
    """Install SIGINT/SIGTERM handlers for graceful shutdown.

    Called from server.py after app creation. Kept here so all signal
    logic is co-located with lifespan.
    """
    if sys.platform != "win32":
        _prev_handlers[signal.SIGTERM] = signal.signal(signal.SIGTERM, _signal_handler)
    _prev_handlers[signal.SIGINT] = signal.signal(signal.SIGINT, _signal_handler)