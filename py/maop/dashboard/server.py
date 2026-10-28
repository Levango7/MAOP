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
import threading
from contextlib import asynccontextmanager
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
# One resolver for source checkouts, wheel installs and containers.  The
# old __file__-based derivation pointed at site-packages after pip install.
from maop.config.home import dashboard_dir as _resolve_dashboard_dir
from maop.config.home import resolve_maop_root as _resolve_maop_root

MAOP_ROOT = _resolve_maop_root()
DASH_DIR = MAOP_ROOT / "dashboard"
DASH_VUE3_DIST_DIR = MAOP_ROOT / "dashboard" / "dist-enterprise"
DASH_VUE3_SRC_DIR = MAOP_ROOT / "dashboard-enterprise"

# Unified Vue3 serve directory (both personal & enterprise):
#   dist-enterprise (Vite build) > packaged wheel asset > dev source.
#   (Legacy native JS dashboard was removed in v4; see git history.)
from maop.config.settings import get_settings as _get_settings

_edition_cfg = _get_settings()
_resolved_dashboard = _resolve_dashboard_dir(MAOP_ROOT)
if _resolved_dashboard is not None and _resolved_dashboard.name == "dist-enterprise":
    _SERVE_DIR = _resolved_dashboard
elif _resolved_dashboard is not None:
    # P2-18 fix: dev source dir is not buildable — warn clearly
    _SERVE_DIR = _resolved_dashboard
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
async def drain_cancelled(*tasks: asyncio.Task | None) -> int:
    """Cancel the given still-pending tasks and wait for the cancellation.

    Returns how many tasks were cancelled. ``None`` entries and already-done
    tasks are skipped, so callers can pass optional handles directly.

    This is a function rather than inline ``with suppress(...)`` blocks for
    two reasons: the shutdown path is otherwise untested, and every extra
    suppress site adds to the ``check_silent_exceptions`` baseline, a ratchet
    that may only shrink.
    """
    pending = [t for t in tasks if t is not None and not t.done()]
    for task in pending:
        task.cancel()
    if pending:
        try:
            await asyncio.gather(*pending)
        except asyncio.CancelledError:  # silent-ok: interrupt — 取消由本函数自己发起，取消即结果
            pass
    return len(pending)


# ── 分层服务起停（PLAN_SCHEDULING_LAYERING.md 阶段1/阶段3 收口）─────
# 拆成独立函数（而非内联进 lifespan）的两个理由与 drain_cancelled 相同：
# 起停路径要能被测试直接驱动（不起整套 lifespan 副作用），以及每条
# try/except 都让"吞异常"基线的可读性更差——这里集中一次讲清。


async def start_layering_services(app: FastAPI, *, bg_enabled: bool) -> None:
    """启动阶段性服务并把句柄挂到 ``app.state``（幂等性由服务自身保证）。

    - **阶段1 HealthService**：探测循环读 AgentCatalog 并回写健康标志。
      与 backup/logrotate 同门控（``bg_enabled``）——多 worker 下 N 份
      探测循环只是放大对被探测端点的压力，不增加信息量。
    - **阶段3 A2AGateway**：``MAOP_A2A_GATEWAY=1`` 才构造（默认关——门户
      HTTP 传输层对外监听属部署决策）。开启时门户对象开始生命周期管理；
      传输层/黑板桥的注入由部署侧组合（本接线只保证 manager 就绪与
      start/stop 对称）。关：``app.state.a2a_gateway`` 保持 None。
    - **阶段6 协作层问题#9 BlackboardWSBridge**：订阅黑板写入事件并实时推
      ``/ws``（事件驱动路径，与 ``_ws_push_loop`` 的周期快照互补）。与
      WS 推送同门控（``bg_enabled``）——多 worker 下每条桥会把自己 worker
      的连接池广播一遍，N 份桥没有新信息量。

    失败一律降级为 warning：这些服务是增强面，不是仪表板启动依赖。
    """
    app.state.health_service = None
    app.state.a2a_gateway = None
    app.state.ws_blackboard_bridge = None

    if bg_enabled:
        try:
            from maop.core.scheduling.health_service import HealthService

            _health_service = HealthService()
            await _health_service.start()
            app.state.health_service = _health_service
            logger.info("[lifespan] Health service auto-started")
        except Exception as exc:
            logger.warning("[lifespan] Health service start failed: %s", exc)

    from maop.config.settings import a2a_gateway_enabled

    if a2a_gateway_enabled():
        try:
            from maop.core.agent.delegation.a2a import A2AManager
            from maop.core.coordination.a2a_gateway import A2AGateway

            _gateway = A2AGateway(manager=A2AManager(root_dir=str(MAOP_ROOT)))
            await _gateway.start()
            app.state.a2a_gateway = _gateway
            logger.info("[lifespan] A2A gateway started (MAOP_A2A_GATEWAY=1)")
        except Exception as exc:
            logger.warning("[lifespan] A2A gateway start failed: %s", exc)

    if bg_enabled:
        try:
            from maop.core.reliability.blackboard import get_blackboard
            from maop.dashboard.ws_blackboard_bridge import BlackboardWSBridge

            _bb_bridge = BlackboardWSBridge(get_blackboard())
            await _bb_bridge.start()
            app.state.ws_blackboard_bridge = _bb_bridge
            logger.info("[lifespan] Blackboard WS bridge started")
        except Exception as exc:
            logger.warning("[lifespan] Blackboard WS bridge start failed: %s", exc)


async def shutdown_layering_services(app: FastAPI) -> None:
    """与 ``start_layering_services`` 对称的关停（逆序停止，各自幂等）。"""
    _bb_bridge = getattr(app.state, "ws_blackboard_bridge", None)
    if _bb_bridge is not None:
        try:
            await _bb_bridge.stop()
        except Exception as exc:
            logger.warning("[shutdown] Blackboard WS bridge stop failed: %s", exc)
    _gateway = getattr(app.state, "a2a_gateway", None)
    if _gateway is not None:
        try:
            await _gateway.stop()
        except Exception as exc:
            logger.warning("[shutdown] A2A gateway stop failed: %s", exc)
    _health_service = getattr(app.state, "health_service", None)
    if _health_service is not None:
        try:
            await _health_service.stop()
        except Exception as exc:
            logger.warning("[shutdown] Health service stop failed: %s", exc)


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

    # ── Warm the multimodal provider (M2.2 B2 收尾) ─────────────
    # Resolving the provider here means the first image upload does not pay
    # the CLIP model load (~600 MB) inline on a request path. Gated by the
    # same background-tasks flag as the schedulers: with workers>1 every
    # worker would otherwise load its own copy of the weights.
    #
    # Runs on a daemon thread on purpose: provider resolution may download
    # weights, and uvicorn binds its socket only after lifespan startup. A
    # synchronous warm-up therefore leaves the dashboard unreachable for
    # minutes on a fresh/offline machine.
    if _bg_enabled:
        def _warm_multimodal() -> None:
            try:
                from maop.memory.multimodal import warm_multimodal_space
                logger.info(
                    "[lifespan] multimodal space: %s", warm_multimodal_space()
                )
            except Exception as exc:
                logger.warning("[lifespan] multimodal provider warm-up failed: %s", exc)

        threading.Thread(
            target=_warm_multimodal, name="maop-multimodal-warmup", daemon=True
        ).start()

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

    # ── 从 config/mcp_servers.yaml 播种 MCP 服务器 ──────────────
    # 2026-09-24: 此前该配置文件**静默失效** —— MCPDiscovery 写好了却零调用方，
    # 也没有任何启动播种路径，写在里面的服务器从未被连接。
    #
    # 门控在 _bg_enabled 下：seed_from_config 是「先查 name 再 add」，
    # 多 worker 并发执行会出现「都查到不存在 → 都 INSERT」的竞态，
    # 而 add_server 是 DELETE+INSERT，交错执行会留下同名重复行。
    # 与 backup/logrotate 的多 worker 处理一致，只让单 worker 做。
    if _bg_enabled:
        try:
            from maop.dashboard.services import plugin_service
            _seeded = plugin_service.sync_mcp_config()
            if _seeded.get("added") or _seeded.get("errors"):
                logger.info(
                    "[lifespan] MCP config seeded: added=%s errors=%s",
                    _seeded.get("added"), _seeded.get("errors"),
                )
        except Exception as exc:
            # 播种失败不应阻塞启动 —— 它只是配置同步，不是服务依赖
            logger.warning("[lifespan] MCP config seed failed: %s", exc)

        # ── 自动连接标了 auto_connect 的 MCP 服务器 ───────────────
        # 后台任务，不 await：一台不可达的 MCP 服务不该拖慢或卡住仪表板启动。
        async def _mcp_auto_connect() -> None:
            try:
                from maop.dashboard.services import plugin_service as _ps
                res = await _ps.auto_connect_servers()
                if res["connected"] or res["failed"]:
                    logger.info(
                        "[lifespan] MCP auto-connect: connected=%s failed=%s",
                        res["connected"], res["failed"],
                    )
            except Exception as exc:
                logger.warning("[lifespan] MCP auto-connect failed: %s", exc)

        app.state.mcp_autoconnect_task = asyncio.create_task(_mcp_auto_connect())

    # ── 分层服务（阶段1 HealthService / 阶段3 A2AGateway）──────
    # 放在最后：它们只依赖 app.state 与自身单例，不与其他启动段耦合。
    await start_layering_services(app, bg_enabled=_bg_enabled)

    try:
        yield
    finally:
        # 先停分层服务（对称起停），再走既有的任务/调度器清理序。
        await shutdown_layering_services(app)
        # 关停期要取消的后台任务统一交给 drain_cancelled：每多一处
        # ``with suppress(asyncio.CancelledError)`` 都是给"吞异常"基线加一笔，
        # 而这个 ratchet 只允许减少。
        _autoconnect = getattr(app.state, "mcp_autoconnect_task", None)
        await drain_cancelled(_autoconnect, _ws_manager._ws_push_task)
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
        # ── Flush the multimodal ingest queue (M2.2 B2 收尾) ─────
        # close(drain=True), not drain(): the worker may be holding a partial
        # batch that drain() cannot see — without this every graceful
        # shutdown loses up to flush_interval_s of uploads.
        try:
            from maop.memory.multimodal import drain_multimodal_ingest
            drain_multimodal_ingest(timeout=3.0)
        except Exception as exc:
            logger.warning("[shutdown] Multimodal ingest drain failed: %s", exc)


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
    # P0 安全默认: 仅监听本机回环地址。历史默认 0.0.0.0 会把未加防护的
    # Dashboard 直接暴露到所有网络接口。需要外部/容器访问时通过
    # MAOP_DASH_HOST 显式设置（Dockerfile 与 docker-compose 已内置
    # MAOP_DASH_HOST=0.0.0.0，容器部署行为不变）。
    host = os.environ.get("MAOP_DASH_HOST", "127.0.0.1")

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
from maop.dashboard._middleware_stack import (
    _global_exception_handler,  # noqa: F401
    _normalize_api_path,  # noqa: F401
)
from maop.dashboard._register_routes import (
    _CSP_VIOLATION_MAX,  # noqa: F401
    _csp_violations,  # noqa: F401
    health,  # noqa: F401
)
from maop.dashboard._ws_manager import (
    _ws_broadcast,  # noqa: F401
    _ws_clients,  # noqa: F401
    _ws_lock,  # noqa: F401
    _ws_push_loop,  # noqa: F401
)
