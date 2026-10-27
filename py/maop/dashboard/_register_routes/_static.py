# _register_routes._static: static file routes + health + CSP + Prometheus + v1-version + SPA fallback.
# Split from _register_routes.py into _register_routes/_static.py.

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi import Request as _Req
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.responses import JSONResponse as _JResp
from fastapi.staticfiles import StaticFiles

from maop import __version__ as MAOP_VERSION

logger: logging.Logger = logging.getLogger(__name__)

__all__: list[str] = []


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
    # 从包的 __dict__ 获取 _csp_violations / _CSP_VIOLATION_MAX，
    # 使 CSP ring buffer 与包级状态保持同一引用。
    import maop.dashboard._register_routes as _rr
    from maop.dashboard._register_routes import health

    _csp_buf = _rr._csp_violations
    _csp_max = _rr._CSP_VIOLATION_MAX

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
        _csp_buf.append(entry)
        if len(_csp_buf) > _csp_max:
            _csp_buf.pop(0)
        return {"status": "ok"}

    @app.get("/api/csp-violations")
    async def csp_violations() -> Any:
        """Return recent CSP violations for dashboard display."""
        return {"violations": list(reversed(_csp_buf)), "count": len(_csp_buf)}

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
    # render /monitor, /settings, etc.
    #
    # 2026-09-23 修复：原实现**没有排除 /api/**，与上方注释的承诺不符。
    # "已注册的路由先匹配"只对**存在**的 API 路径成立；未注册的 /api/* 会落到
    # 这里返回 200 + index.html，前端 res.json() 抛
    #   Unexpected token '<', "<!DOCTYPE "... is not valid JSON
    # 把"端点不存在"伪装成解析错误，且掩盖真实缺口。本项目已因此踩坑三次
    # （kg_router、compliance.router 定义未挂载；本次 alerts/audit/mcp 端点
    # 缺失），每次都靠人肉发现。
    #
    # 现在对 /api/ 前缀显式返回 404 JSON，让契约缺口在第一次调用就暴露。
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> Any:
        if full_path == "api" or full_path.startswith("api/"):
            return _JResp(
                status_code=404,
                content={
                    "status": "error",
                    "error": "Not Found",
                    "code": "HTTP_404",
                    "detail": f"/{full_path}",
                },
            )
        return _serve_index_html(serve_dir)
