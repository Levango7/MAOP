"""
MAOP Dashboard - FastAPI replacement for dashboard/server-v2.ps1.
Async, non-blocking, with pure-Python DataProxy (replaces PS subprocess).

Production features:
  - TLS/HTTPS support via MAOP.core.tls
  - Auth middleware via MAOP.core.middleware
  - Rate limiting via MAOP.core.middleware
  - Configurable CORS (no wildcard in production)
  - Graceful shutdown on SIGTERM/SIGINT

Architecture: routes split into routers/ subpackage by domain.
  - routers/data.py:    query/read endpoints
  - routers/control.py: action endpoints
  - routers/model.py:   model management
  - routers/evolve_insights.py:  self-evolution
  - routers/memory.py:  memory + neural mechanisms
  - routers/system.py:  framework/audit/config/overview

Start:  python -m maop.dashboard.server
TLS:    MAOP_TLS_ENABLED=1 MAOP_TLS_CERT=cert.pem MAOP_TLS_KEY=key.pem python -m maop.dashboard.server
        （MAOP_TLS 旧名仍向后兼容，会触发 DeprecationWarning）
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI

logger = logging.getLogger(__name__)

# ── v5.0.0: DeprecationWarning for short-name env vars ────────────
# Short names still work but emit a warning. Will be removed in v6.0.0.
# Canonical names: MAOP_DASH_PORT, MAOP_DASH_WORKERS, MAOP_TLS_ENABLED, MAOP_AUTH_ENABLED.
import warnings as _warnings


def _warn_deprecated_env_aliases() -> None:
    """Emit DeprecationWarning for short-name env vars that have canonical long names."""
    _aliases: list[tuple[str, str]] = [
        ("MAOP_WORKERS", "MAOP_DASH_WORKERS"),
        ("MAOP_TLS", "MAOP_TLS_ENABLED"),
        ("MAOP_AUTH", "MAOP_AUTH_ENABLED"),
    ]
    for short, canonical in _aliases:
        if short in os.environ and canonical not in os.environ:
            _warnings.warn(
                f"{short} is deprecated since v5.0.0; use {canonical} instead. "
                f"Short name will be removed in v6.0.0.",
                DeprecationWarning,
                stacklevel=2,
            )
            logger.warning(
                "[config] %s is deprecated; use %s instead (will be removed in v6.0.0).",
                short, canonical,
            )


_warn_deprecated_env_aliases()

# ── Paths ──────────────────────────────────────────────────────────
MAOP_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DASH_DIR = MAOP_ROOT / "dashboard"
DASH_VUE3_DIST_DIR = MAOP_ROOT / "dashboard" / "dist-enterprise"
DASH_VUE3_SRC_DIR = MAOP_ROOT / "dashboard-enterprise"

# Unified Vue3 serve directory (both personal & enterprise):
#   dist-enterprise (Vite build) > dashboard-enterprise (dev source)
#   Legacy native JS dashboard archived to archive/js-dashboard/
from maop.config.settings import get_settings as _get_settings

_edition_cfg = _get_settings()
if DASH_VUE3_DIST_DIR.exists():
    _SERVE_DIR = DASH_VUE3_DIST_DIR
elif DASH_VUE3_SRC_DIR.exists():
    # P2-18 fix: dev source dir is not buildable — warn clearly
    _SERVE_DIR = DASH_VUE3_SRC_DIR
    logger.warning(
        "[server] Using dashboard-enterprise/ source dir (dev mode). "
        "Vue .vue files cannot be served as static assets — run "
        "'cd dashboard-enterprise && npm run build' for production."
    )
else:
    _SERVE_DIR = DASH_DIR
    logger.warning("[server] Vue3 dashboard not found, falling back to legacy dashboard/")

# ── Shared state (imported by routers) ─────────────────────────────
from maop import __version__ as MAOP_VERSION

# ── WebSocket real-time push (extracted to _ws_manager) ────────────
from maop.dashboard import _ws_manager

# ── Auth module (imported early for lifespan & WS) ─────────────────
from maop.dashboard.routers import auth as _auth_mod


# ── Lifespan ───────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    if _auth_mod._auth_enabled:
        app.state.auth_manager = _auth_mod.get_auth_mgr()
        # Structured API Key manager (scopes / IP allow-list / rate limit / usage).
        try:
            from maop.core.security.api_key_manager import get_api_key_manager
            app.state.api_key_manager = get_api_key_manager()
        except Exception as _exc:
            logger.warning("[lifespan] ApiKeyManager init failed: %s", _exc)

    # ── Background tasks gate (multi-worker safe) ──────────────
    # Each uvicorn worker runs its own lifespan; without a gate the backup
    # scheduler, log-rotate scheduler and WS push task would start in every
    # worker, causing duplicate backups and duplicate pushes. main() sets
    # MAOP_BACKGROUND_TASKS=0 automatically when workers>1; override with
    # MAOP_BACKGROUND_TASKS=1 to force-enable (e.g. a single dedicated worker).
    _bg_enabled = os.environ.get("MAOP_BACKGROUND_TASKS", "1") == "1"
    if _bg_enabled:
        _ws_manager._ws_push_task = asyncio.create_task(_ws_manager._ws_push_loop())

    # ── Initialize OTel tracing (if enabled) ────────────────────
    try:
        from maop.core.monitoring.otel import setup_provider
        setup_provider()
    except Exception as exc:
        logger.debug("[lifespan] OTel setup skipped: %s", exc)

    # ── Auto-start backup & log-rotation schedulers ────────────
    _backup_scheduler = None
    _log_rotate_scheduler = None
    _sched_enabled = os.environ.get("MAOP_AUTO_SCHED", "1") == "1" and _bg_enabled
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
        if _ws_manager._ws_push_task is not None:
            _ws_manager._ws_push_task.cancel()
            with suppress(asyncio.CancelledError):
                await _ws_manager._ws_push_task
            _ws_manager._ws_push_task = None
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


# ── App ────────────────────────────────────────────────────────────
_is_prod_env = os.environ.get("MAOP_ENV", "").lower() == "production"
# Fail-closed docs exposure: API docs (Swagger / ReDoc / OpenAPI) are hidden in
# production unless MAOP_EXPOSE_DOCS=1 is set explicitly. Non-production keeps
# docs on by default. Never expose docs in production without a clear reason.
_expose_docs = os.environ.get("MAOP_EXPOSE_DOCS", "0") == "1"
_docs_enabled = (not _is_prod_env) or _expose_docs
if _is_prod_env and _expose_docs:
    logger.warning("[security] MAOP_EXPOSE_DOCS=1 in production — Swagger/OpenAPI endpoints are publicly exposed")
app = FastAPI(
    title="MAOP Dashboard",
    version=MAOP_VERSION,
    docs_url="/api/docs" if _docs_enabled else None,
    redoc_url="/api/redoc" if _docs_enabled else None,
    openapi_url="/api/openapi.json" if _docs_enabled else None,
    lifespan=lifespan,
)

# ── Middleware stack (extracted to _middleware_stack) ──────────────
from maop.dashboard._middleware_stack import (
    register_exception_handler,
    setup_cors,
    setup_enterprise_guard,
    setup_security_middleware,
)

# ── Global Exception Handler ──────────────────────────────────────
# Catches unhandled exceptions: logs full details server-side,
# returns generic message to client (no internal info leakage).
register_exception_handler(app)

# ── CORS ───────────────────────────────────────────────────────────
_cors_origins = setup_cors(app, _is_prod_env)

# ── Rate Limit + Auth + CSP Middleware ─────────────────────────────
_sec_cfg = setup_security_middleware(app)
# C-P0-1 fix: production defaults to auth enabled for safety
# 配置统一由 settings.py 的 MAOPSettings.auth_enabled 提供（支持 MAOP_AUTH_ENABLED
# 和 MAOP_AUTH 两个 env alias），消除 server.py 直接读 os.getenv 的双真相源问题。
_auth_enabled = _sec_cfg["auth_enabled"]
_rl_enabled = _sec_cfg["rl_enabled"]
_rl_rate = _sec_cfg["rl_rate"]
_rl_burst = _sec_cfg["rl_burst"]
_csp_enabled = _sec_cfg["csp_enabled"]
_csp_report_only = _sec_cfg["csp_report_only"]
_csp_connect_src = _sec_cfg["csp_connect_src"]

# ── Include routers (extracted to _register_routes) ────────────────
from maop.dashboard._register_routes import (
    register_routers,
    register_static_routes,
    register_websocket,
)

register_routers(app)

# ── Enterprise API 404 Guard ────────────────────────────────────────
# In personal edition, enterprise-only API paths return 404 instead of
# leaking route existence or causing confusing 500 errors.
setup_enterprise_guard(app)

# ── WebSocket endpoint ─────────────────────────────────────────────
register_websocket(app)

# --- API v1 aliases (backward-compatible versioning) -------------------
# Register /api/v1/* aliases for every /api/* route so the frontend can
# migrate incrementally to a versioned API. Old /api/xxx paths remain
# fully functional (no breaking change). Exempt endpoints - health
# (K8s/Docker probes), stream (SSE token-via-query), auth (login/logout/
# refresh) - stay unversioned for infrastructure compatibility.
from maop.dashboard._register_routes import _register_v1_aliases

_register_v1_aliases()

# ── Static files + health + CSP + Prometheus + v1-version + SPA fallback ──
register_static_routes(app, _SERVE_DIR)

# ── Graceful Shutdown ──────────────────────────────────────────────
# OPS-1 fix: do NOT raise SystemExit(0) from signal context — that bypasses
# uvicorn's graceful shutdown (in-flight requests dropped, lifespan shutdown
# skipped). Instead, log and CHAIN to the previously installed handler
# (uvicorn's, or Python's default which raises KeyboardInterrupt for SIGINT —
# both trigger uvicorn's graceful shutdown path).
_shutting_down = False
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

if sys.platform != "win32":
    _prev_handlers[signal.SIGTERM] = signal.signal(signal.SIGTERM, _signal_handler)
_prev_handlers[signal.SIGINT] = signal.signal(signal.SIGINT, _signal_handler)

# ── Start ──────────────────────────────────────────────────────────
# M2 修复：统一读取 MAOP_TLS_ENABLED（兼容旧名 MAOP_TLS，触发 DeprecationWarning）
from maop.config.env import get_tls_enabled as _get_tls_enabled

_tls_enabled = _get_tls_enabled()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("MAOP_DASH_PORT", sys.argv[1] if len(sys.argv) > 1 else "9079"))
    host = os.environ.get("MAOP_DASH_HOST", "0.0.0.0")

    ssl_kwargs: dict[str, Any] = {}
    if _tls_enabled:
        from maop.core.security.tls import TLSSettings, create_ssl_context
        cert_file = os.environ.get("MAOP_TLS_CERT", "")
        key_file = os.environ.get("MAOP_TLS_KEY", "")
        if cert_file and key_file:
            ssl_ctx = create_ssl_context(TLSSettings(enabled=True, cert_file=cert_file, key_file=key_file,
                min_version=os.environ.get("MAOP_TLS_MIN_VERSION", "TLSv1_2")))
            if ssl_ctx:
                ssl_kwargs["ssl"] = ssl_ctx
                logger.info("TLS enabled: cert=%s", cert_file)
        else:
            logger.warning("MAOP_TLS=1 but MAOP_TLS_CERT/MAOP_TLS_KEY not set, starting without TLS")

    proto = "https" if ssl_kwargs else "http"
    workers = int(os.environ.get("MAOP_DASH_WORKERS", os.environ.get("MAOP_WORKERS", "1")))
    logger.info(f'MAOP Dashboard v{MAOP_VERSION} (FastAPI) -> {proto}://{host}:{port}')
    logger.info(f'  CORS origins: {_cors_origins}')
    logger.info(f"  Auth: {('enabled' if _auth_enabled else 'disabled')}")
    logger.info(f"  Rate limit: {('enabled' if _rl_enabled else 'disabled')} ({_rl_rate} rps, burst={_rl_burst})")
    logger.info(f"  TLS: {('enabled' if ssl_kwargs else 'disabled')}")
    logger.info(f"  CSP: {('enabled' if _csp_enabled else 'disabled')}{' (report-only)' if _csp_report_only else ''} connect-src={_csp_connect_src}")
    logger.info(f"  Workers: {workers}")

    if workers > 1:
        # Multi-worker mode imports the app by string path; an SSLContext
        # object cannot be pickled across worker processes (spawn on Win32),
        # so pass cert/key file paths via ssl_certfile/ssl_keyfile instead.
        multi_ssl_kwargs: dict[str, Any] = {}
        if _tls_enabled:
            _mw_cert = os.environ.get("MAOP_TLS_CERT", "")
            _mw_key = os.environ.get("MAOP_TLS_KEY", "")
            if _mw_cert and _mw_key:
                multi_ssl_kwargs["ssl_certfile"] = _mw_cert
                multi_ssl_kwargs["ssl_keyfile"] = _mw_key
            else:
                logger.warning("Multi-worker + TLS requested but MAOP_TLS_CERT/MAOP_TLS_KEY not set; starting without TLS")
        # Disable per-worker background tasks (backup/log-rotate/WS push) to
        # avoid duplicate backups and duplicate pushes. Override by exporting
        # MAOP_BACKGROUND_TASKS=1 before launch.
        if os.environ.get("MAOP_BACKGROUND_TASKS") is None:
            os.environ["MAOP_BACKGROUND_TASKS"] = "0"
            logger.warning(
                "Multi-worker mode: background tasks (backup, log-rotate, WS push) "
                "disabled to avoid duplicates. Set MAOP_BACKGROUND_TASKS=1 to force-enable."
            )
        uvicorn.run(
            "maop.dashboard.server:app",
            host=host, port=port, workers=workers, log_level="info",
            **multi_ssl_kwargs,
        )
    else:
        uvicorn.run(app, host=host, port=port, log_level="info", **ssl_kwargs)


# Re-export for backward compatibility (tests and other modules import these from server)
from maop.dashboard._middleware_stack import (  # noqa: F401
    _global_exception_handler,
    _normalize_api_path,
)
from maop.dashboard._register_routes import (  # noqa: F401
    _CSP_VIOLATION_MAX,
    _csp_violations,
    health,
)
from maop.dashboard._ws_manager import (  # noqa: F401
    _ws_broadcast,
    _ws_clients,
    _ws_lock,
    _ws_push_loop,
)
