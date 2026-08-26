"""Route registration for the MAOP dashboard server.

Extracted from ``server.py``.  Holds:
  - ``register_routers(app)`` — all ``app.include_router()`` calls
  - ``register_static_routes(app, serve_dir)`` — index/style/favicon/health/
    CSP/Prometheus/v1-version/SPA-fallback
  - ``register_websocket(app)`` — WebSocket endpoint
  - ``_register_v1_aliases()`` — module-level (tests import it directly)
  - ``health()`` — module-level (contract tests import it directly)

Re-exported by ``server.py`` for backward compatibility.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect
from fastapi import Request as _Req
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.responses import JSONResponse as _JResp
from fastapi.staticfiles import StaticFiles

from maop import __version__ as MAOP_VERSION
from maop.config.edition import FeatureFlag, get_edition, has_feature

# WebSocket pool / lock live in _ws_manager; import them so register_websocket
# can use the same singletons.
from maop.dashboard._ws_manager import _ws_clients, _ws_lock
from maop.dashboard.routers import auth as _auth_mod
from maop.dashboard.routers import state as _state
from maop.dashboard.routers.routing_preview import router as routing_preview_router

logger = logging.getLogger(__name__)

# Module-level reference to the FastAPI app, set by ``register_routers``.
# ``_register_v1_aliases`` and ``health`` read it so they can stay zero-arg
# (tests call ``_register_v1_aliases()`` and ``asyncio.run(health())`` directly).
_app: FastAPI | None = None

# ── CSP violation ring buffer (module-level so tests can inspect/trim) ──
_csp_violations: list[dict] = []
_CSP_VIOLATION_MAX = 200  # keep last 200 violations in memory


# ── Include routers ────────────────────────────────────────────────
def _register_enterprise_routers(app: FastAPI) -> None:
    """Register enterprise-only routers (tenant, rbac, sso, quotas, n8n, licenses).

    Each router is gated by a feature flag and wrapped in try/except ImportError
    so that personal edition (which lacks the router module) degrades gracefully
    with a warning instead of crashing on startup.
    """
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
    if has_feature(FeatureFlag.N8N_INTEGRATION):
        try:
            from maop.dashboard.routers import n8n as n8n_router
            app.include_router(n8n_router.router)
            logger.info("[server] Enterprise router: n8n enabled")
        except ImportError as _e:
            logger.warning(
                "[server] Enterprise router MISSING: n8n (import error: %s).",
                _e,
            )

    # ── License management router (Enterprise only, gated by FeatureFlag.LICENSE_MANAGEMENT) ──
    # Implements PRD docs/prd-license-management.md: CRUD + lifecycle (validate,
    # revoke, renew) + audit log for issued MAOP Enterprise licenses.
    if has_feature(FeatureFlag.LICENSE_MANAGEMENT):
        try:
            from maop.dashboard.routers import licenses as licenses_router
            app.include_router(licenses_router.router)
            logger.info("[server] Enterprise router: licenses enabled")
        except ImportError as _e:
            logger.warning(
                "[server] Enterprise router MISSING: licenses (import error: %s).",
                _e,
            )


def _register_core_routers(app: FastAPI) -> None:
    """Register core dashboard routers (data / control / model / memory / system / auth)."""
    from maop.dashboard.routers import control, data, evolve_insights, memory, model, system

    app.include_router(data.router)
    app.include_router(control.router)
    app.include_router(model.router)
    app.include_router(evolve_insights.router)
    app.include_router(memory.router)
    app.include_router(system.router)
    app.include_router(_auth_mod.router)


def _register_api_keys_router(app: FastAPI) -> None:
    """Register the structured API Key management router (optional)."""
    try:
        from maop.dashboard.routers import api_keys as _api_keys_router
        app.include_router(_api_keys_router.router)
        logger.info("[server] Router: api-keys enabled")
    except ImportError as _e:
        logger.warning("[server] Router MISSING: api_keys (import error: %s)", _e)


def _register_workflow_routers(app: FastAPI) -> None:
    """Register workflow / session / plugin / stream routers."""
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


def _register_data_routers(app: FastAPI) -> None:
    """Register data / cost / agents / chat / knowledge / observability routers."""
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

    # t194 (2026-08-14): LLM 智能任务拆分 — 自然语言 → 子任务 DAG。
    from maop.dashboard.routers import dag as dag_router

    app.include_router(dag_router.router)


def _register_scheduling_routers(app: FastAPI) -> None:
    """Register scheduling / supervisor / debate routers (all optional, guarded)."""
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

    # Supervisor (proactive multi-agent supervision): patrol / alert /
    # replace / degrade / terminate / upgrade. Mounted unconditionally —
    # endpoints return 404 when no Supervisor has been configured
    # (passive-only mode), so the router is always importable.
    try:
        from maop.dashboard.routers import supervisor as supervisor_router

        app.include_router(supervisor_router.router)
        logger.info("[server] Router: supervisor enabled")
    except ImportError as _e:
        logger.warning("[server] Router MISSING: supervisor (import error: %s)", _e)

    # Debate (adversarial multi-agent debate): start / get / verdict /
    # config / history. Mounted unconditionally — endpoints return 404
    # when no DebateDispatcher has been configured, so the router is
    # always importable.
    try:
        from maop.dashboard.routers import debate as debate_router

        app.include_router(debate_router.router)
        logger.info("[server] Router: debate enabled")
    except ImportError as _e:
        logger.warning("[server] Router MISSING: debate (import error: %s)", _e)


def _register_a2a_router(app: FastAPI) -> None:
    """Mount the A2A protocol endpoint (JSON-RPC /a2a) if available.

    F6b (2026-07-22, Phase F): mount the A2A protocol so external agents
    (Google ADK / LangGraph / CrewAI / any A2A-compliant system) can
    discover MAOP agents and dispatch tasks to them. The A2AManager is
    assembled by ServiceContainer with the WorkerPool injected (F6a):
    every ``tasks/send`` is forwarded via ``WorkerPool.submit(agent_name=...)``
    to ``MaopLoop.run(agent=...)`` so the caller-pinned agent actually
    executes the task. See ADR-013.
    """
    from maop.core.agent.delegation.a2a import create_a2a_router as _create_a2a_router
    from maop.core.reliability.services import ServiceContainer as _A2AContainer

    # MAOP_ROOT is needed for the A2A container; import lazily to avoid a
    # circular import at module load time (server.py sets MAOP_ROOT).
    from maop.dashboard.server import MAOP_ROOT

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


def _register_audit_router(app: FastAPI) -> None:
    """Register the audit router (unified for both editions).

    audit.py handles enterprise (EnterpriseAuditLogger) and personal
    (control.audit.AuditLog) editions with FeatureFlag gating internally.
    """
    try:
        from maop.dashboard.routers import audit as audit_router
        app.include_router(audit_router.router)
        logger.info("[server] Router: audit enabled (edition=%s)", get_edition().value)
    except ImportError as _e:
        logger.warning("[server] Router MISSING: audit (import error: %s)", _e)


def _register_notifications_and_config(app: FastAPI) -> None:
    """Register notifications / config-history / blackboard routers."""
    # ── Notification Center router (available in both editions) ──
    # Implements PRD docs/prd-notification-center.md: channels (Email/Webhook/InApp),
    # rules, templates, event bus, async delivery with retry + dead-letter queue,
    # and WebSocket real-time push at /api/notifications/ws.
    try:
        from maop.dashboard.routers import notifications as notifications_router
        app.include_router(notifications_router.router)
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

    # ── Blackboard architecture router ──────────────────────────────────
    # P2: 经典黑板架构 — 结构化共享知识库 + 知识源 + 控制器。
    # 读操作开放访问；写操作（write/clear）需 admin 鉴权。
    # 黑板默认不接入 EventBus（向后兼容），需显式 enable_event_bus() 启用。
    try:
        from maop.dashboard.routers import blackboard as blackboard_router
        app.include_router(blackboard_router.router)
        logger.info("[server] Router: blackboard enabled")
    except ImportError as _e:
        logger.warning("[server] Router MISSING: blackboard (import error: %s)", _e)


def register_routers(app: FastAPI) -> None:
    """Register all API routers (core + enterprise + optional)."""
    global _app
    _app = app

    _register_core_routers(app)
    _register_api_keys_router(app)
    _register_workflow_routers(app)
    _register_data_routers(app)
    _register_scheduling_routers(app)
    _register_a2a_router(app)

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
    _register_audit_router(app)
    _register_enterprise_routers(app)
    _register_notifications_and_config(app)


# ── Health ─────────────────────────────────────────────────────────
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


# ── Static files + health + CSP + Prometheus + v1-version + SPA fallback ──

def _serve_index_html(serve_dir: Path) -> Any:
    """Return index.html from ``serve_dir`` (or 404 fallback)."""
    html_path = serve_dir / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"), headers={"Cache-Control":"no-cache, no-store, must-revalidate"})
    return HTMLResponse("<h1>index.html not found</h1>", status_code=404)


def _serve_style_css(serve_dir: Path) -> Any:
    """Return style.css from ``serve_dir`` (or 404 fallback)."""
    css_path = serve_dir / "style.css"
    if not css_path.exists():
        css_path = serve_dir / "src" / "style.css"
    if css_path.exists():
        return FileResponse(css_path, media_type="text/css", headers={"Cache-Control":"public, max-age=3600"})
    return HTMLResponse("/* not found */", status_code=404)


def _serve_favicon(serve_dir: Path) -> Any:
    """Return favicon.svg from ``serve_dir`` (or 404 fallback)."""
    fav = serve_dir / "favicon.svg"
    if not fav.exists():
        fav = serve_dir / "public" / "favicon.svg"
    if fav.exists():
        return FileResponse(fav, media_type="image/svg+xml", headers={"Cache-Control":"public, max-age=86400"})
    return HTMLResponse("", status_code=404)


def _mount_asset_dirs(app: FastAPI, serve_dir: Path) -> None:
    """Mount Vite asset directories (``assets`` / ``src`` / ``public``) on ``app``."""
    for _asset_dir_name in ["assets", "src"]:
        _asset_dir = serve_dir / _asset_dir_name
        if _asset_dir.exists():
            app.mount(f"/{_asset_dir_name}", StaticFiles(directory=str(_asset_dir)), name=f"static-{_asset_dir_name}")
    _public_dir = serve_dir / "public"
    if _public_dir.exists():
        app.mount("/public", StaticFiles(directory=str(_public_dir)), name="static-public")


def _build_csp_entry(body: dict[str, Any]) -> dict[str, Any]:
    """Extract a CSP violation entry from the report body."""
    report = body.get("csp-report", {})
    return {
        "ts": time.time(),
        "document_uri": report.get("document-uri", ""),
        "violated_directive": report.get("violated-directive", ""),
        "blocked_uri": report.get("blocked-uri", ""),
        "source_file": report.get("source-file", ""),
        "line_number": report.get("line-number", ""),
    }


def register_static_routes(app: FastAPI, serve_dir: Path) -> None:
    """Register index/style/favicon/asset mounts/health/CSP/Prometheus/
    v1-version/SPA-fallback routes on ``app``."""
    @app.get("/")
    async def index() -> Any:
        return _serve_index_html(serve_dir)

    @app.get("/style.css")
    async def style_css() -> Any:
        return _serve_style_css(serve_dir)

    @app.get("/favicon.svg")
    async def favicon() -> Any:
        return _serve_favicon(serve_dir)

    _mount_asset_dirs(app, serve_dir)

    @app.get("/api/health")
    async def health_endpoint() -> Any:
        return await health()

    # ── CSP Violation Report Endpoint ──────────────────────────────────
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
        entry = _build_csp_entry(body)
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
        return _serve_index_html(serve_dir)


# ── WebSocket endpoint ─────────────────────────────────────────────
def register_websocket(app: FastAPI) -> None:
    """Register the /ws WebSocket endpoint on ``app``."""
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
    if _app is None:
        raise RuntimeError("_register_v1_aliases called before register_routers")
    EXEMPT = {
        "/api/health",
        "/api/stream",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/refresh",
    }
    v1 = APIRouter()
    existing = {getattr(r, "path", None) for r in _app.routes}
    for route in _app.routes:
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
    _app.include_router(v1)
    logger.info("[server] Registered %d /api/v1/* alias routes", len(v1.routes))