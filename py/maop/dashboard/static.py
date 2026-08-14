"""Static assets + health/compliance endpoints for the dashboard server.

Extracted from server.py (§2.4). All handlers are mounted on a single
``router`` that server.py includes after the API routers. The SPA
fallback (``/{full_path:path}``) is declared last so it only catches
paths not matched by API routes or static mounts.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from maop import __version__ as MAOP_VERSION
from maop.config.edition import get_edition
from maop.dashboard.routers import state as _state

# _SERVE_DIR is defined in server.py before this module is imported.
from maop.dashboard.server import _SERVE_DIR

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
async def index() -> Any:
    html_path = _SERVE_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(
            html_path.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    return HTMLResponse("<h1>index.html not found</h1>", status_code=404)


@router.get("/style.css")
async def style_css() -> Any:
    css_path = _SERVE_DIR / "style.css"
    if not css_path.exists():
        css_path = _SERVE_DIR / "src" / "style.css"
    if css_path.exists():
        return FileResponse(
            css_path, media_type="text/css",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    return HTMLResponse("/* not found */", status_code=404)


@router.get("/favicon.svg")
async def favicon() -> Any:
    fav = _SERVE_DIR / "favicon.svg"
    if not fav.exists():
        fav = _SERVE_DIR / "public" / "favicon.svg"

    if fav.exists():
        return FileResponse(
            fav, media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    return HTMLResponse("", status_code=404)


# ── Health ─────────────────────────────────────────────────────────
@router.get("/api/health")
async def health() -> Any:
    active_agents = 0
    try:
        _agents = await _state.get_bridge().agent_stats()
        active_agents = len(_agents) if isinstance(_agents, list) else 0
    except Exception:
        logger.debug('swallowed exception', exc_info=True)
        pass
    return {
        "status": "ok",
        "version": MAOP_VERSION,
        "edition": get_edition().value,
        "dashboard": f"MAOP Dashboard v{MAOP_VERSION} (FastAPI)",
        "uptime_ms": round((time.time() - _state.start_time) * 1000),
        "active_agents": active_agents,
        "tls": _state.tls_enabled,
        "auth": _state.auth_enabled,
        "rate_limit": _state.rl_enabled,
    }


# ── CSP Violation Report Endpoint ──────────────────────────────────
_csp_violations: list[dict] = []
_CSP_VIOLATION_MAX = 200  # keep last 200 violations in memory


@router.post("/api/csp-report")
async def csp_report(request: Request) -> Any:
    """Receive CSP violation reports from the browser.

    When CSP is in Report-Only mode (or enforce mode with report-uri),
    the browser POSTs violation details here.  We log them and keep
    a ring buffer for dashboard inspection.
    """
    from fastapi.responses import JSONResponse as _JResp

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


@router.get("/api/csp-violations")
async def csp_violations() -> Any:
    """Return recent CSP violations for dashboard display."""
    return {"violations": list(reversed(_csp_violations)), "count": len(_csp_violations)}


# ── Prometheus metrics endpoint ────────────────────────────────────
@router.get("/api/prometheus")
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


# ── SPA fallback for Vue3 client-side routes ───────────────────────
# Any non-API, non-asset path returns index.html so the Vue router can
# render /monitor, /settings, etc. Declared on a SEPARATE router so
# server.py can include it LAST (after _register_v1_aliases and all API
# routes), otherwise the catch-all would shadow /api/v1/* aliases.
spa_router = APIRouter()


@spa_router.get("/{full_path:path}")
async def spa_fallback(full_path: str) -> Any:
    html_path = _SERVE_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(
            html_path.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    return HTMLResponse("<h1>index.html not found</h1>", status_code=404)


def mount_static_assets(app: Any) -> None:
    """Mount /assets, /src, /public static directories onto the app.

    Called from server.py after app creation. Kept here so all static
    serving logic is co-located.
    """
    # Serve JS/CSS assets from /assets/ (Vite build output) or /src/ (Vite dev)
    for _asset_dir_name in ["assets", "src"]:
        _asset_dir = _SERVE_DIR / _asset_dir_name
        if _asset_dir.exists():
            app.mount(
                f"/{_asset_dir_name}",
                StaticFiles(directory=str(_asset_dir)),
                name=f"static-{_asset_dir_name}",
            )

    # Also serve public/ dir for Vite
    _public_dir = _SERVE_DIR / "public"
    if _public_dir.exists():
        app.mount("/public", StaticFiles(directory=str(_public_dir)), name="static-public")