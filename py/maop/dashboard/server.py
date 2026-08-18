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
TLS:    MAOP_TLS=1 MAOP_TLS_CERT=cert.pem MAOP_TLS_KEY=key.pem python -m maop.dashboard.server
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import sys
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

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
from maop.config.edition import FeatureFlag, get_edition, has_feature
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

# ── Auth module (imported early for lifespan & WS) ─────────────────
from maop.dashboard.routers import auth as _auth_mod
from maop.dashboard.routers import state as _state
from maop.dashboard.routers.routing_preview import router as routing_preview_router

# ── WebSocket real-time push ───────────────────────────────────────
_ws_clients: set[WebSocket] = set()
_ws_lock = asyncio.Lock()
_ws_snapshot_cache: dict | None = None
_ws_snapshot_ts: float = 0.0
_WS_SNAPSHOT_TTL = 5.0  # seconds — cache snapshot to avoid redundant DB queries
_ws_push_task: asyncio.Task | None = None


_WS_SEND_TIMEOUT = 5.0  # OPS-3 fix: cap per-client send time


async def _ws_broadcast(msg: dict) -> Any:
    # OPS-3 fix: snapshot clients under the lock, but send OUTSIDE the lock
    # (concurrently, with a per-client timeout) so one slow client can no
    # longer block all broadcasts and new connections.
    async with _ws_lock:
        clients = list(_ws_clients)
    if not clients:
        return

    async def _send(ws: WebSocket) -> WebSocket | None:
        try:
            await asyncio.wait_for(ws.send_json(msg), timeout=_WS_SEND_TIMEOUT)
            return None
        except Exception:
            return ws

    results = await asyncio.gather(*(_send(ws) for ws in clients))
    dead = {ws for ws in results if ws is not None}
    if dead:
        async with _ws_lock:
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
            logger.warning("WS push error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    global _ws_push_task
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
        _ws_push_task = asyncio.create_task(_ws_push_loop())

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
        if _ws_push_task is not None:
            _ws_push_task.cancel()
            with suppress(asyncio.CancelledError):
                await _ws_push_task
            _ws_push_task = None
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

# ── Global Exception Handler ──────────────────────────────────────
# Catches unhandled exceptions: logs full details server-side,
# returns generic message to client (no internal info leakage).
from fastapi import Request as _Req
from fastapi.responses import JSONResponse as _JResp


@app.exception_handler(Exception)
async def _global_exception_handler(request: _Req, exc: Exception) -> Any:
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc)
    return _JResp(status_code=500, content={"status": "error", "error": "Internal server error"})

# ── CORS ───────────────────────────────────────────────────────────
from fastapi.middleware.cors import CORSMiddleware

_cors_origins = os.environ.get("MAOP_CORS_ORIGINS", "").split(",")
_cors_origins = [o.strip() for o in _cors_origins if o.strip()]
if not _cors_origins:
    _cors_origins = ["http://localhost:9079", "http://127.0.0.1:9079", "http://localhost:8080"]
# Fail-closed: reject wildcard origins. With allow_credentials=True a "*"
# origin lets any site make authenticated cross-origin requests, so in production
# we refuse and fall back to an empty allow-list.
if "*" in _cors_origins:
    if _is_prod_env:
        logger.warning("CORS allow_origins is wildcard '*' in production — rejecting; falling back to empty allow-list")
        _cors_origins = []
    else:
        logger.warning("CORS allow_origins contains '*' — any origin will be allowed (non-production only)")
# #9 fix: CORS narrowed — explicit methods/headers instead of wildcard
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins, allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"], allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-Trace-Id", "X-Request-Id"])

# ── Rate Limit + Auth + CSP Middleware ─────────────────────────────
from maop.core.security.middleware import AuthMiddleware, CSPMiddleware, RateLimitMiddleware

_rl_enabled = os.environ.get("MAOP_RATE_LIMIT", os.environ.get("MAOP_RATE_LIMIT_ENABLED", "1")) == "1"
_rl_rate = float(os.environ.get("MAOP_RATE_LIMIT_RPS", "30"))
_rl_burst = int(os.environ.get("MAOP_RATE_LIMIT_BURST", "60"))
app.add_middleware(RateLimitMiddleware, rate=_rl_rate, burst=_rl_burst, enabled=_rl_enabled)
# ── Quota Middleware (Enterprise only, lazy-loaded) ───────────────
# 注册顺序: 在 Auth 之前注册,使执行顺序为 CSP → Auth → Quota → RateLimit → CORS.
# 这样 Auth 先注入 request.state.tenant_id,Quota 才能拿到租户上下文.
# 中间件内部通过 has_feature(FeatureFlag.TENANT_ISOLATION) 守卫,Personal 版 no-op.
# quota_manager=None 触发惰性加载(首次请求时从 routers.quotas 获取单例).
try:
    from maop.enterprise.quota_middleware import QuotaMiddleware
    _quota_mw_enabled = os.environ.get("MAOP_QUOTA_MIDDLEWARE", "1") == "1"
    app.add_middleware(QuotaMiddleware, quota_manager=None, enabled=_quota_mw_enabled)
    logger.info("[server] QuotaMiddleware registered (enabled=%s)", _quota_mw_enabled)
except ImportError as _e:
    logger.warning("[server] QuotaMiddleware not registered (import error: %s)", _e)
# C-P0-1 fix: production defaults to auth enabled for safety
# 配置统一由 settings.py 的 MAOPSettings.auth_enabled 提供（支持 MAOP_AUTH_ENABLED
# 和 MAOP_AUTH 两个 env alias），消除 server.py 直接读 os.getenv 的双真相源问题。
_env_is_prod = os.environ.get("MAOP_ENV", "").strip().lower() == "production"
_auth_enabled = _get_settings().auth_enabled
if _env_is_prod and not _auth_enabled:
    logger.warning("[security] MAOP_AUTH=0 in production — all write endpoints exposed!")
app.add_middleware(
    AuthMiddleware,
    enabled=_auth_enabled,
    public_paths=[
        "/", "/api/health", "/api/prometheus",
        "/style.css", "/favicon.svg",
        "/api/docs", "/openapi.json",
        "/api/auth/status", "/api/auth/login",
        "/api/csp-report",
        "/api/alerts/webhook",  # Alertmanager webhook receiver (server-to-server, no auth)
        "/api/stream",  # P0 fix: SSE token validated via _check_sse_token in handler
        # SSO 公开端点（PRD 4.1）：登录跳转/回调/metadata/enabled 需在未认证下可访问
        "/api/sso/enabled",
        "/api/sso/oidc",  # /api/sso/oidc/{id}/login + /callback
        "/api/sso/saml",  # /api/sso/saml/{id}/login + /acs
        "/api/sso/providers",  # GET /providers/{id}/metadata 公开；CRUD 由 require_admin 守卫
        "/api/v1/sso/enabled",
        "/api/v1/sso/oidc",
        "/api/v1/sso/saml",
        "/api/v1/sso/providers",
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
from maop.dashboard.routers import control, data, evolve_insights, memory, model, system

app.include_router(data.router)
app.include_router(control.router)
app.include_router(model.router)
app.include_router(evolve_insights.router)
app.include_router(memory.router)
app.include_router(system.router)
app.include_router(_auth_mod.router)

# API Key management router (structured keys with scopes, IP allow-list, rate limit, usage stats).
try:
    from maop.dashboard.routers import api_keys as _api_keys_router
    app.include_router(_api_keys_router.router)
    logger.info("[server] Router: api-keys enabled")
except ImportError as _e:
    logger.warning("[server] Router MISSING: api_keys (import error: %s)", _e)

from maop.dashboard.routers import protocol, subagent, worktree

app.include_router(subagent.router)
app.include_router(worktree.router)
app.include_router(protocol.router)

from maop.dashboard.routers import hook as hook_router

app.include_router(hook_router.router)

# 任务199: Hook 可视化配置 CRUD API（/api/hooks 复数路径，与 /api/hook/* 互补）
from maop.dashboard.routers import hooks as hooks_router

app.include_router(hooks_router.router)

from maop.dashboard.routers import stream as stream_router

app.include_router(stream_router.router)

from maop.dashboard.routers import permission as perm_router

app.include_router(perm_router.router)

from maop.dashboard.routers import mcp as mcp_router

app.include_router(mcp_router.router)

from maop.dashboard.routers import session as session_router

app.include_router(session_router.router)
# P1-3: 任务历史页 — /api/sessions (列表+分页) + /api/sessions/{id}/rerun
app.include_router(session_router.tasks_router)

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

from maop.dashboard.routers import agent_proxy as agent_proxy_router

app.include_router(agent_proxy_router.router)
app.include_router(routing_preview_router)

# Phase γ-4: scheduling decision trace API (read-only GET endpoints).
from maop.dashboard.routers import routing as routing_router

app.include_router(routing_router.router)

# F2-01: Agent 自演化闭环 — PerformanceEvaluator / ABTest(SPRT) / AutoDeployer.
from maop.dashboard.routers import evolution_experiment as evolution_router

app.include_router(evolution_router.router)

# F1-04: Observability stack — tracing/metrics/logging status + Prometheus JSON.
from maop.dashboard.routers import observability as observability_router

app.include_router(observability_router.router)

# Alertmanager webhook receiver (POST /api/alerts/webhook). Minimal sink so the
# Prometheus → Alertmanager → dashboard notification chain is fully wired.
from maop.dashboard.routers import alerts as alerts_router

app.include_router(alerts_router.router)

# F1-02 (异常自适应调度): scheduling failure-detector stats endpoint
# (GET /api/scheduling/failure-stats + POST .../reset). Mounted
# unconditionally — the detector is process-wide and available in both
# personal and enterprise editions.
try:
    from maop.dashboard.routers import scheduling as scheduling_router

    app.include_router(scheduling_router.router)
    logger.info("[server] Router: scheduling enabled")
except ImportError as _e:
    logger.warning("[server] Router MISSING: scheduling (import error: %s)", _e)

# t194 (2026-08-14): LLM 智能任务拆分 — 自然语言 → 子任务 DAG。
from maop.dashboard.routers import dag as dag_router

app.include_router(dag_router.router)

# ── A2A protocol endpoint (JSON-RPC /a2a) ─────────────────────────
# F6b (2026-07-22, Phase F): mount the A2A protocol so external agents
# (Google ADK / LangGraph / CrewAI / any A2A-compliant system) can
# discover MAOP agents and dispatch tasks to them. The A2AManager is
# assembled by ServiceContainer with the WorkerPool injected (F6a):
# every ``tasks/send`` is forwarded via ``WorkerPool.submit(agent_name=...)``
# to ``MaopLoop.run(agent=...)`` so the caller-pinned agent actually
# executes the task. See ADR-013.
from maop.core.agent.delegation.a2a import create_a2a_router as _create_a2a_router
from maop.core.reliability.services import ServiceContainer as _A2AContainer

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
# (which used `# type: ignore` to mask missing modules) with
# explicit `logger.warning` + a `has_<name>_router` flag. The previous
# behavior silently degraded ENTERPRISE mode to "router enabled but 404
# on every call" because the missing module was treated as a non-event.
# Enterprise routers (files already exist in routers/) (routers/{rbac,tenant,audit,sso}.py)
# and flip these flags to True.
_edition_settings = _get_settings()
has_tenant_router: bool = False
has_audit_router: bool = False
has_rbac_router: bool = False
has_sso_router: bool = False

# ── Audit router (always registered — unified for both editions) ──
# audit.py handles enterprise (EnterpriseAuditLogger) and personal
# (control.audit.AuditLog) editions with FeatureFlag gating internally.
try:
    from maop.dashboard.routers import audit as audit_router
    app.include_router(audit_router.router)
    has_audit_router = True
    logger.info("[server] Router: audit enabled (edition=%s)", get_edition().value)
except ImportError as _e:
    logger.warning("[server] Router MISSING: audit (import error: %s)", _e)

if has_feature(FeatureFlag.MULTI_USER):
    try:
        # `# type: ignore` is required because mypy cannot
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
    try:
        # Multi-tenant resource quotas router — bridges QuotaManager to
        # frontend. Implements PRD docs/prd-tenant-quota.md.
        from maop.dashboard.routers import quotas as quotas_router
        app.include_router(quotas_router.router)
        logger.info("[server] Enterprise router: quotas enabled")
    except ImportError as _e:
        logger.warning(
            "[server] Enterprise router MISSING: quotas (import error: %s). "
            "ENTERPRISE mode will return 404 on /api/quotas/*.",
            _e,
        )

# ── n8n integration router (Enterprise only, gated by FeatureFlag.N8N_INTEGRATION) ──
has_n8n_router: bool = False
if has_feature(FeatureFlag.N8N_INTEGRATION):
    try:
        from maop.dashboard.routers import n8n as n8n_router
        app.include_router(n8n_router.router)
        has_n8n_router = True
        logger.info("[server] Enterprise router: n8n enabled")
    except ImportError as _e:
        logger.warning(
            "[server] Enterprise router MISSING: n8n (import error: %s).",
            _e,
        )

# ── License management router (Enterprise only, gated by FeatureFlag.LICENSE_MANAGEMENT) ──
# Implements PRD docs/prd-license-management.md: CRUD + lifecycle (validate,
# revoke, renew) + audit log for issued MAOP Enterprise licenses.
has_licenses_router: bool = False
if has_feature(FeatureFlag.LICENSE_MANAGEMENT):
    try:
        from maop.dashboard.routers import licenses as licenses_router
        app.include_router(licenses_router.router)
        has_licenses_router = True
        logger.info("[server] Enterprise router: licenses enabled")
    except ImportError as _e:
        logger.warning(
            "[server] Enterprise router MISSING: licenses (import error: %s).",
            _e,
        )

# ── Notification Center router (available in both editions) ──
# Implements PRD docs/prd-notification-center.md: channels (Email/Webhook/InApp),
# rules, templates, event bus, async delivery with retry + dead-letter queue,
# and WebSocket real-time push at /api/notifications/ws.
has_notifications_router: bool = False
try:
    from maop.dashboard.routers import notifications as notifications_router
    app.include_router(notifications_router.router)
    has_notifications_router = True
    # Wire the notification manager's broadcaster to the router's WS pool
    # so InApp notifications are pushed to connected clients in real time.
    try:
        notifications_router.wire_broadcaster()
    except Exception as _notif_wire_exc:
        logger.warning("[server] Notification broadcaster wire failed: %s", _notif_wire_exc)
    logger.info("[server] Router: notifications enabled (edition=%s)", get_edition().value)
except ImportError as _e:
    logger.warning("[server] Router MISSING: notifications (import error: %s)", _e)

# ── Config history & rollback router ────────────────────────────────
# Provides /api/config/history + /api/config/rollback/{version} so operators
# can inspect the configuration change timeline and restore a known-good
# snapshot. All endpoints are admin-guarded inside the router.
try:
    from maop.dashboard.routers import config as config_router
    app.include_router(config_router.router)
    logger.info("[server] Router: config-history enabled")
except ImportError as _e:
    logger.warning("[server] Router MISSING: config-history (import error: %s)", _e)

# ── Enterprise API 404 Guard ────────────────────────────────────────
# In personal edition, enterprise-only API paths return 404 instead of
# leaking route existence or causing confusing 500 errors.
_ENTERPRISE_API_PREFIXES = (
    "/api/tenant",
    "/api/sso",
    "/api/rbac",
    "/api/n8n",
    "/api/licenses",
    "/api/quotas",
)
# OPS-7 fix: a version-prefixed path such as /api/v1/tenant/... previously
# bypassed the guard because it does not start with "/api/tenant". Normalize
# away an optional /vN segment so both forms are treated identically.
_API_VERSION_RE = re.compile(r"^/api/v\d+/")
def _normalize_api_path(path: str) -> str:
    return _API_VERSION_RE.sub("/api/", path, count=1)

if not has_feature(FeatureFlag.MULTI_USER):
    @app.middleware("http")
    async def enterprise_api_guard(request: _Req, call_next: Any) -> Any:
        path = _normalize_api_path(request.url.path)
        # 判定是否为 enterprise-only API 路径（含 version-prefixed 变体）。
        is_grants = path.startswith("/api/rbac/grants")
        is_tenant_list = path.startswith("/api/tenant/list")
        is_enterprise_api = (
            is_grants
            or is_tenant_list
            or any(path.startswith(p) for p in _ENTERPRISE_API_PREFIXES)
        )
        if not is_enterprise_api:
            return await call_next(request)
        # 先放行让 AuthMiddleware 处理认证；未认证请求由 AuthMiddleware
        # 返回 401（而非此处 404），避免泄露端点存在性并符合 RBAC 语义。
        response = await call_next(request)
        if response.status_code == 401:
            return response
        # 认证通过后，personal edition 对 enterprise API 返回软降级响应。
        if is_grants:
            return _JResp(content={"grants": [], "hint": "Enterprise only"})
        if is_tenant_list:
            return _JResp(content={"tenants": [], "hint": "Enterprise only"})
        return _JResp(status_code=404, content={"status": "error", "error": "Not found", "hint": "This endpoint requires MAOP Enterprise edition"})

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
    # Minimal critical-dependency probe: the metadata storage (SQLite/PostgreSQL)
    # that backs the dashboard. A lightweight `SELECT 1` forces a real round-trip
    # without scanning tables, so the endpoint stays cheap and is safe to call
    # from Docker/K8s probes. If storage is unreachable the process is unhealthy
    # and we return 503 so orchestrators stop routing traffic / alert.
    storage_ok = True
    try:
        from maop.core.backends.db_utils import get_db_path, get_pool
        pool = get_pool(get_db_path())
        conn = pool.acquire()
        try:
            conn.execute("SELECT 1")
        finally:
            pool.release(conn)
    except Exception as exc:
        storage_ok = False
        logger.warning("[health] storage dependency check failed: %s", exc)

    active_agents = 0
    try:
        _agents = await _state.get_bridge().agent_stats()
        active_agents = len(_agents) if isinstance(_agents, list) else 0
    except Exception:
        logger.debug('swallowed exception', exc_info=True)

    if not storage_ok:
        return _JResp(
            status_code=503,
            content={
                "status": "error",
                "reason": "storage_unavailable",
                "version": MAOP_VERSION,
                "edition": get_edition().value,
            },
        )
    return {"status": "ok", "version": MAOP_VERSION, "edition": get_edition().value,
            "dashboard": f"MAOP Dashboard v{MAOP_VERSION} (FastAPI)",
            "uptime_ms": round((time.time() - _state.start_time) * 1000),
            "active_agents": active_agents,
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
    from fastapi import Response

    from maop.core.monitoring.monitoring import metrics as _metrics
    text = _metrics.to_prometheus()
    return Response(content=text, media_type="text/plain; version=0.0.4; charset=utf-8")

# ── WebSocket endpoint ─────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> Any:
    # Auth must be validated BEFORE accept() — BaseHTTPMiddleware cannot
    # intercept WebSocket scope, so we enforce it here directly.
    if _auth_mod._auth_enabled:
        # #5 fix: token via Sec-WebSocket-Protocol subprotocol (not URL query)
        # Browser: new WebSocket(url, [token]). Avoids URL/access-log exposure.
        # Fallback: query param kept for non-browser clients (curl, CLI).
        token = ws.query_params.get("token", "")
        if not token:
            # Try Sec-WebSocket-Protocol header (browser subprotocol)
            protocols = ws.headers.get("sec-websocket-protocol", "")
            if protocols:
                # Format: "token, <actual_token>" or just "<actual_token>"
                parts = [p.strip() for p in protocols.split(",") if p.strip()]
                if parts:
                    token = parts[-1]  # last protocol identifier
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

# --- API v1 aliases (backward-compatible versioning) -------------------
# Register /api/v1/* aliases for every /api/* route so the frontend can
# migrate incrementally to a versioned API. Old /api/xxx paths remain
# fully functional (no breaking change). Exempt endpoints - health
# (K8s/Docker probes), stream (SSE token-via-query), auth (login/logout/
# refresh) - stay unversioned for infrastructure compatibility.
def _register_v1_aliases() -> None:
    """Register /api/v1/* aliases for all /api/* routes (except auth/stream/health)."""
    EXEMPT = {
        "/api/health",
        "/api/stream",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/refresh",
    }
    v1 = APIRouter()
    existing = {getattr(r, "path", None) for r in app.routes}
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path in EXEMPT:
            continue
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        v1_path = "/api/v1" + path[4:]
        if v1_path in existing:
            continue
        methods = getattr(route, "methods", None) or {"GET"}
        v1.add_api_route(v1_path, endpoint, methods=methods)
    app.include_router(v1)
    logger.info("[server] Registered %d /api/v1/* alias routes", len(v1.routes))


_register_v1_aliases()


@app.get("/api/v1/version")
async def api_v1_version() -> Any:
    """Return current API version."""
    return {"version": MAOP_VERSION, "api_version": "v1", "status": "stable"}

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
_tls_enabled = os.environ.get("MAOP_TLS", "0") == "1"

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
