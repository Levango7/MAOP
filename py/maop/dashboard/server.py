"""
MAOP Dashboard - FastAPI replacement for dashboard/server-v2.ps1.
Async, non-blocking, with pure-Python DataBridge (replaces PS subprocess).

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
  - routers/evolve.py:  self-evolution
  - routers/memory.py:  memory + neural mechanisms
  - routers/system.py:  framework/audit/config/overview

Start:  python -m maop.dashboard.server
TLS:    MAOP_TLS=1 MAOP_TLS_CERT=cert.pem MAOP_TLS_KEY=key.pem python -m maop.dashboard.server
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────
MAOP_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DASH_DIR = MAOP_ROOT / "dashboard"
DASH_VUE3_DIST_DIR = MAOP_ROOT / "dashboard" / "dist-enterprise"
DASH_VUE3_SRC_DIR = MAOP_ROOT / "dashboard-enterprise"

# Unified Vue3 serve directory (both personal & enterprise):
#   dist-enterprise (Vite build) > dashboard-enterprise (dev source)
#   Legacy native JS dashboard archived to archive/js-dashboard/
from maop.config.settings import get_settings as _get_settings
from maop.config.edition import has_feature, FeatureFlag, get_edition
_edition_cfg = _get_settings()
if DASH_VUE3_DIST_DIR.exists():
    _SERVE_DIR = DASH_VUE3_DIST_DIR
elif DASH_VUE3_SRC_DIR.exists():
    _SERVE_DIR = DASH_VUE3_SRC_DIR
else:
    _SERVE_DIR = DASH_DIR
    logger.warning("[server] Vue3 dashboard not found, falling back to legacy dashboard/")

# ── Shared state (imported by routers) ─────────────────────────────
from maop.dashboard.routers import state as _state
from maop.dashboard.routers.routing_preview import router as routing_preview_router

from maop import __version__ as MAOP_VERSION

# ── Auth module (imported early for lifespan & WS) ─────────────────
from maop.dashboard.routers import auth as _auth_mod

# ── WebSocket real-time push ───────────────────────────────────────
_ws_clients: set[WebSocket] = set()
_ws_lock = asyncio.Lock()
_ws_snapshot_cache: dict | None = None
_ws_snapshot_ts: float = 0.0
_WS_SNAPSHOT_TTL = 5.0  # seconds — cache snapshot to avoid redundant DB queries
_ws_push_task: asyncio.Task | None = None


async def _ws_broadcast(msg: dict) -> Any:
    dead = set()
    async with _ws_lock:
        for ws in _ws_clients:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        _ws_clients.difference_update(dead)


async def _ws_push_loop() -> Any:
    global _ws_snapshot_cache, _ws_snapshot_ts
    while True:
        await asyncio.sleep(15)
        if not _ws_clients:
            continue
        try:
            now = time.time()
            # Reuse cached snapshot if within TTL
            if _ws_snapshot_cache and (now - _ws_snapshot_ts) < _WS_SNAPSHOT_TTL:
                snapshot = _ws_snapshot_cache
            else:
                bridge = _state.get_bridge()
                live = await bridge.live()
                report = await bridge.report(hours=48)
                ts = await bridge.timeseries(hours=168)
                snapshot = {"type": "snapshot", "ts": now, "live": live, "report": report, "timeseries": ts}
                _ws_snapshot_cache = snapshot
                _ws_snapshot_ts = now
            await _ws_broadcast(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.warning("WS push error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    global _ws_push_task
    if _auth_mod._auth_enabled:
        app.state.auth_manager = _auth_mod.get_auth_mgr()
    _ws_push_task = asyncio.create_task(_ws_push_loop())

    # ── Initialize OTel tracing (if enabled) ────────────────────
    try:
        from maop.core.otel import setup_provider
        setup_provider()
    except Exception as exc:
        logger.debug("[lifespan] OTel setup skipped: %s", exc)

    # ── Auto-start backup & log-rotation schedulers ────────────
    _backup_scheduler = None
    _log_rotate_scheduler = None
    _sched_enabled = os.environ.get("MAOP_AUTO_SCHED", "1") == "1"
    if _sched_enabled:
        try:
            from maop.core.db_backup import DbBackup
            _backup_scheduler = DbBackup(root_dir=str(MAOP_ROOT))
            _backup_scheduler.start_scheduler(
                interval_s=float(os.environ.get("MAOP_BACKUP_INTERVAL", "3600"))
            )
            logger.info("[lifespan] Backup scheduler auto-started")
        except Exception as exc:
            logger.warning("[lifespan] Failed to start backup scheduler: %s", exc)
        try:
            from maop.core.log_rotate import LogRotateScheduler
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
        if _ws_push_task is not None:
            _ws_push_task.cancel()
            with suppress(asyncio.CancelledError):
                await _ws_push_task
            _ws_push_task = None
        # ── Stop schedulers on shutdown ────────────────────────
        if _backup_scheduler is not None:
            try:
                _backup_scheduler.stop_scheduler()
            except Exception:
                pass
        if _log_rotate_scheduler is not None:
            try:
                _log_rotate_scheduler.stop()
            except Exception:
                pass


# ── App ────────────────────────────────────────────────────────────
app = FastAPI(title="MAOP Dashboard", version=MAOP_VERSION, docs_url="/api/docs", lifespan=lifespan)

# ── Global Exception Handler ──────────────────────────────────────
# Catches unhandled exceptions: logs full details server-side,
# returns generic message to client (no internal info leakage).
from fastapi import Request as _Req
from fastapi.responses import JSONResponse as _JResp

@app.exception_handler(Exception)
async def _global_exception_handler(request: _Req, exc: Exception) -> Any:
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return _JResp(status_code=500, content={"status": "error", "error": "Internal server error"})

# ── CORS ───────────────────────────────────────────────────────────
from fastapi.middleware.cors import CORSMiddleware
_cors_origins = os.environ.get("MAOP_CORS_ORIGINS", "").split(",")
_cors_origins = [o.strip() for o in _cors_origins if o.strip()]
if not _cors_origins:
    _cors_origins = ["http://localhost:9079", "http://127.0.0.1:9079", "http://localhost:8080"]
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ── Rate Limit + Auth + CSP Middleware ─────────────────────────────
from maop.core.middleware import RateLimitMiddleware, AuthMiddleware, CSPMiddleware
_rl_enabled = os.environ.get("MAOP_RATE_LIMIT", "1") == "1"
_rl_rate = float(os.environ.get("MAOP_RATE_LIMIT_RPS", "30"))
_rl_burst = int(os.environ.get("MAOP_RATE_LIMIT_BURST", "60"))
app.add_middleware(RateLimitMiddleware, rate=_rl_rate, burst=_rl_burst, enabled=_rl_enabled)
_auth_enabled = os.environ.get("MAOP_AUTH", "0") == "1"
app.add_middleware(
    AuthMiddleware,
    enabled=_auth_enabled,
    public_paths=[
        "/", "/api/health", "/api/prometheus",
        "/style.css", "/favicon.svg",
        "/api/docs", "/openapi.json",
        "/api/auth/status", "/api/auth/login",
        "/api/csp-report",
    ],
)
# CSP & security headers (Content-Security-Policy, X-Frame-Options, etc.)
_csp_enabled = os.environ.get("MAOP_CSP", "1") == "1"
_csp_report_only = os.environ.get("MAOP_CSP_REPORT_ONLY", "0") == "1"
_csp_report_uri = os.environ.get("MAOP_CSP_REPORT_URI", "") or None
_csp_connect_src = os.environ.get("MAOP_CSP_CONNECT_SRC", "") or "'self'"
app.add_middleware(
    CSPMiddleware,
    enabled=_csp_enabled,
    report_only=_csp_report_only,
    report_uri=_csp_report_uri,
    connect_src=_csp_connect_src,
)

# ── Include routers ────────────────────────────────────────────────
from maop.dashboard.routers import data, control, model, evolve, memory, system
app.include_router(data.router)
app.include_router(control.router)
app.include_router(model.router)
app.include_router(evolve.router)
app.include_router(memory.router)
app.include_router(system.router)
app.include_router(_auth_mod.router)

from maop.dashboard.routers import subagent, worktree, protocol
app.include_router(subagent.router)
app.include_router(worktree.router)
app.include_router(protocol.router)

from maop.dashboard.routers import hook as hook_router
app.include_router(hook_router.router)

from maop.dashboard.routers import stream as stream_router
app.include_router(stream_router.router)

from maop.dashboard.routers import permission as perm_router
app.include_router(perm_router.router)

from maop.dashboard.routers import mcp as mcp_router
app.include_router(mcp_router.router)

from maop.dashboard.routers import session as session_router
app.include_router(session_router.router)

from maop.dashboard.routers import react as react_router
app.include_router(react_router.router)

from maop.dashboard.routers import plugin as plugin_router
app.include_router(plugin_router.router)

from maop.dashboard.routers import cost as cost_router
app.include_router(cost_router.router)

from maop.dashboard.routers import agents as agents_router
app.include_router(agents_router.router)

from maop.dashboard.routers import chat as chat_router
app.include_router(chat_router.router)

from maop.dashboard.routers import knowledge as knowledge_router
app.include_router(knowledge_router.router)

from maop.dashboard.routers import info as info_router
app.include_router(info_router.router)

from maop.dashboard.routers import budget as budget_router
app.include_router(budget_router.router)

from maop.dashboard.routers import tool_audit as tool_audit_router
app.include_router(tool_audit_router.router)

from maop.dashboard.routers import agent_bridge as agent_bridge_router
app.include_router(agent_bridge_router.router)
app.include_router(routing_preview_router)

# ── A2A protocol endpoint (JSON-RPC /a2a) ─────────────────────────
# F6b (2026-07-22, Phase F): mount the A2A protocol so external agents
# (Google ADK / LangGraph / CrewAI / any A2A-compliant system) can
# discover MAOP agents and dispatch tasks to them. The A2AManager is
# assembled by ServiceContainer with the WorkerPool injected (F6a):
# every ``tasks/send`` is forwarded via ``WorkerPool.submit(agent_name=...)``
# to ``MaopLoop.run(agent=...)`` so the caller-pinned agent actually
# executes the task. See ADR-013.
from maop.core.services import ServiceContainer as _A2AContainer
from maop.core.a2a import create_a2a_router as _create_a2a_router
try:
    _a2a_container = _A2AContainer(root_dir=MAOP_ROOT)
    _a2a_manager = _a2a_container.get("a2a_manager", raise_on_failure=False)
    if _a2a_manager is not None:
        app.include_router(_create_a2a_router(_a2a_manager))
        logger.info("[server] A2A router mounted at /a2a (agent_name routing enabled)")
    else:
        logger.warning("[server] A2AManager unavailable — /a2a endpoint not mounted")
except Exception as _a2a_exc:
    logger.warning("[server] A2A router mount failed: %s", _a2a_exc)

# ── Enterprise-only routers ────────────────────────────────────────
# t19 (2026-07-21): removed redundant `from maop.config.edition import
# has_feature, FeatureFlag` — these symbols were already imported at line 52
# and the duplicate import triggered ruff F811.
#
# B1 (2026-07-22): replaced silent `except ImportError: logger.debug(...)`
# (which used `# type: ignore[attr-defined]` to mask missing modules) with
# explicit `logger.warning` + a `has_<name>_router` flag. The previous
# behavior silently degraded ENTERPRISE mode to "router enabled but 404
# on every call" because the missing module was treated as a non-event.
# Phase C will create the missing files (routers/{rbac,tenant,audit,sso}.py)
# and flip these flags to True.
_edition_settings = _get_settings()
has_tenant_router: bool = False
has_audit_router: bool = False
has_rbac_router: bool = False
has_sso_router: bool = False
if has_feature(FeatureFlag.MULTI_USER):
    try:
        # `# type: ignore[attr-defined]` is required because mypy cannot
        # statically verify that routers/tenant.py exists — it is created
        # in Phase C. The runtime `except ImportError` below emits a
        # warning if the file is missing, so the silent-swallow problem
        # from B1 is fixed at runtime; the type-ignore only silences the
        # static checker.
        from maop.dashboard.routers import tenant as tenant_router
        app.include_router(tenant_router.router)
        has_tenant_router = True
        logger.info("[server] Enterprise router: tenant enabled")
    except ImportError as _e:
        logger.warning(
            "[server] Enterprise router MISSING: tenant (import error: %s). "
            "ENTERPRISE mode will return 404 on /api/tenant/* — Phase C will "
            "add routers/tenant.py to fix this.",
            _e,
        )
    try:
        # See note on tenant_router import above re: type: ignore[attr-defined].
        from maop.dashboard.routers import audit as audit_router
        app.include_router(audit_router.router)
        has_audit_router = True
        logger.info("[server] Enterprise router: audit enabled")
    except ImportError as _e:
        logger.warning(
            "[server] Enterprise router MISSING: audit (import error: %s). "
            "ENTERPRISE mode will return 404 on /api/audit/* — Phase C will "
            "add routers/audit.py to fix this.",
            _e,
        )
    try:
        # C1 (2026-07-22): RBAC router — bridges RBACManager to frontend.
        from maop.dashboard.routers import rbac as rbac_router
        app.include_router(rbac_router.router)
        has_rbac_router = True
        logger.info("[server] Enterprise router: rbac enabled")
    except ImportError as _e:
        logger.warning(
            "[server] Enterprise router MISSING: rbac (import error: %s). "
            "ENTERPRISE mode will return 404 on /api/rbac/* — Phase C will "
            "add routers/rbac.py to fix this.",
            _e,
        )
    try:
        # C4 (2026-07-22): SSO router — bridges SSOManager to frontend.
        from maop.dashboard.routers import sso as sso_router
        app.include_router(sso_router.router)
        has_sso_router = True
        logger.info("[server] Enterprise router: sso enabled")
    except ImportError as _e:
        logger.warning(
            "[server] Enterprise router MISSING: sso (import error: %s). "
            "ENTERPRISE mode will return 404 on /api/sso/* — Phase C will "
            "add routers/sso.py to fix this.",
            _e,
        )

# ── Enterprise API 404 Guard ────────────────────────────────────────
# In personal edition, enterprise-only API paths return 404 instead of
# leaking route existence or causing confusing 500 errors.
_ENTERPRISE_API_PREFIXES = (
    "/api/tenant",
    "/api/audit",
    "/api/sso",
    "/api/rbac",
)

if not has_feature(FeatureFlag.MULTI_USER):
    @app.middleware("http")
    async def enterprise_api_guard(request: _Req, call_next: Any) -> Any:
        path = request.url.path
        if path.startswith("/api/rbac/grants"):
            return _JResp(content={"grants": [], "hint": "Enterprise only"})
        if path.startswith("/api/tenant/list"):
            return _JResp(content={"tenants": [], "hint": "Enterprise only"})
        if any(path.startswith(p) for p in _ENTERPRISE_API_PREFIXES):
            return _JResp(status_code=404, content={"status": "error", "error": "Not found", "hint": "This endpoint requires MAOP Enterprise edition"})
        return await call_next(request)

# ── Static files ───────────────────────────────────────────────────
@app.get("/")
async def index() -> Any:
    html_path = _SERVE_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"), headers={"Cache-Control":"no-cache, no-store, must-revalidate"})
    return HTMLResponse("<h1>index.html not found</h1>", status_code=404)

@app.get("/style.css")
async def style_css() -> Any:
    css_path = _SERVE_DIR / "style.css"
    if not css_path.exists():
        css_path = _SERVE_DIR / "src" / "style.css"
    if css_path.exists():
        return FileResponse(css_path, media_type="text/css", headers={"Cache-Control":"public, max-age=3600"})
    return HTMLResponse("/* not found */", status_code=404)

@app.get("/favicon.svg")
async def favicon() -> Any:
    fav = _SERVE_DIR / "favicon.svg"
    if not fav.exists():
        fav = _SERVE_DIR / "public" / "favicon.svg"

    if fav.exists():
        return FileResponse(fav, media_type="image/svg+xml", headers={"Cache-Control":"public, max-age=86400"})
    return HTMLResponse("", status_code=404)

# Serve JS/CSS assets from /assets/ (Vite build output) or /src/ (Vite dev)
for _asset_dir_name in ["assets", "src"]:
    _asset_dir = _SERVE_DIR / _asset_dir_name
    if _asset_dir.exists():
        app.mount(f"/{_asset_dir_name}", StaticFiles(directory=str(_asset_dir)), name=f"static-{_asset_dir_name}")

# Also serve public/ dir for Vite
_public_dir = _SERVE_DIR / "public"
if _public_dir.exists():
    app.mount("/public", StaticFiles(directory=str(_public_dir)), name="static-public")

# ── Health ─────────────────────────────────────────────────────────
@app.get("/api/health")
async def health() -> Any:
    return {"status": "ok", "version": MAOP_VERSION, "edition": get_edition().value,
            "dashboard": f"MAOP Dashboard v{MAOP_VERSION} (FastAPI)",
            "uptime_ms": round((time.time() - _state.start_time) * 1000),
            "tls": _state.tls_enabled, "auth": _state.auth_enabled, "rate_limit": _state.rl_enabled}

# ── CSP Violation Report Endpoint ──────────────────────────────────
_csp_violations: list[dict] = []
_CSP_VIOLATION_MAX = 200  # keep last 200 violations in memory

@app.post("/api/csp-report")
async def csp_report(request: _Req) -> Any:
    """Receive CSP violation reports from the browser.

    When CSP is in Report-Only mode (or enforce mode with report-uri),
    the browser POSTs violation details here.  We log them and keep
    a ring buffer for dashboard inspection.
    """
    try:
        body = await request.json()
    except Exception:
        return _JResp(status_code=400, content={"error": "Invalid JSON"})
    entry = {
        "ts": time.time(),
        "document_uri": body.get("csp-report", {}).get("document-uri", ""),
        "violated_directive": body.get("csp-report", {}).get("violated-directive", ""),
        "blocked_uri": body.get("csp-report", {}).get("blocked-uri", ""),
        "source_file": body.get("csp-report", {}).get("source-file", ""),
        "line_number": body.get("csp-report", {}).get("line-number", ""),
    }
    logger.warning(
        "CSP violation: directive=%s blocked=%s uri=%s source=%s:%s",
        entry["violated_directive"], entry["blocked_uri"],
        entry["document_uri"], entry["source_file"], entry["line_number"],
    )
    _csp_violations.append(entry)
    if len(_csp_violations) > _CSP_VIOLATION_MAX:
        _csp_violations.pop(0)
    return {"status": "ok"}

@app.get("/api/csp-violations")
async def csp_violations() -> Any:
    """Return recent CSP violations for dashboard display."""
    return {"violations": list(reversed(_csp_violations)), "count": len(_csp_violations)}

# ── Prometheus metrics endpoint ────────────────────────────────────
@app.get("/api/prometheus")
async def prometheus_metrics() -> Any:
    """Prometheus text-format metrics exposition endpoint.

    Returns all registered metrics (counters, gauges, histograms) in
    Prometheus text exposition format.  Scrape with:
        scrape_configs:
          - job_name: 'maop'
            metrics_path: /api/prometheus
            static_configs:
              - targets: ['localhost:9079']
    """
    from maop.core.monitoring import metrics as _metrics
    from fastapi import Response
    text = _metrics.to_prometheus()
    return Response(content=text, media_type="text/plain; version=0.0.4; charset=utf-8")

# ── WebSocket endpoint ─────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> Any:
    # Auth must be validated BEFORE accept() — BaseHTTPMiddleware cannot
    # intercept WebSocket scope, so we enforce it here directly.
    if _auth_mod._auth_enabled:
        token = ws.query_params.get("token", "")
        if not token:
            await ws.close(code=4401, reason="Authentication required")
            return
        try:
            mgr = _auth_mod.get_auth_mgr()
            payload = mgr.jwt_handler.validate_token(token)
            # validate_token always returns an AuthResult (never None).
            # Must check .authenticated flag — otherwise forged/expired
            # tokens bypass WebSocket auth entirely (P1-1 fix).
            if not payload or not getattr(payload, "authenticated", False):
                await ws.close(code=4401, reason="Invalid token")
                return
        except Exception:
            await ws.close(code=4401, reason="Authentication failed")
            return
    await ws.accept()
    async with _ws_lock:
        _ws_clients.add(ws)
    try:
        await ws.send_json({"type": "hello", "msg": "MAOP Dashboard WebSocket connected", "ts": time.time()})
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_json({"type": "pong", "ts": time.time()})
    except WebSocketDisconnect:
        pass
    finally:
        async with _ws_lock:
            _ws_clients.discard(ws)

# ── SPA fallback for Vue3 client-side routes ───────────────────────
# Any non-API, non-asset path returns index.html so the Vue router can
# render /monitor, /settings, etc. Mounts and explicit routes above are
# matched first, so /api/*, /assets/*, /style.css, /favicon.svg and /ws
# are not affected.
@app.get("/{full_path:path}")
async def spa_fallback(full_path: str) -> Any:
    html_path = _SERVE_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"), headers={"Cache-Control":"no-cache, no-store, must-revalidate"})
    return HTMLResponse("<h1>index.html not found</h1>", status_code=404)

# ── Graceful Shutdown ──────────────────────────────────────────────
_shutting_down = False

def _signal_handler(signum: int, frame: Any) -> None:
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True
    sig_name = signal.Signals(signum).name
    logger.info("Received %s, initiating graceful shutdown...", sig_name)
    raise SystemExit(0)

if sys.platform != "win32":
    signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)

# ── Start ──────────────────────────────────────────────────────────
_tls_enabled = os.environ.get("MAOP_TLS", "0") == "1"

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("MAOP_DASH_PORT", sys.argv[1] if len(sys.argv) > 1 else "9079"))
    host = os.environ.get("MAOP_DASH_HOST", "0.0.0.0")

    ssl_kwargs: dict[str, Any] = {}
    if _tls_enabled:
        from maop.core.tls import TLSSettings, create_ssl_context
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
    logger.info(f'MAOP Dashboard v{MAOP_VERSION} (FastAPI) -> {proto}://{host}:{port}')
    logger.info(f'  CORS origins: {_cors_origins}')
    logger.info(f"  Auth: {('enabled' if _auth_enabled else 'disabled')}")
    logger.info(f"  Rate limit: {('enabled' if _rl_enabled else 'disabled')} ({_rl_rate} rps, burst={_rl_burst})")
    logger.info(f"  TLS: {('enabled' if ssl_kwargs else 'disabled')}")
    logger.info(f"  CSP: {('enabled' if _csp_enabled else 'disabled')}{' (report-only)' if _csp_report_only else ''} connect-src={_csp_connect_src}")

    uvicorn.run(app, host=host, port=port, log_level="info", **ssl_kwargs)