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

from fastapi import FastAPI

from maop.config.edition import FeatureFlag, get_edition, has_feature
from maop.dashboard._register_routes._health import health
from maop.dashboard._register_routes._routers import (
    _register_a2a_router,
    _register_agent_versions_router,
    _register_api_keys_router,
    _register_audit_router,
    _register_core_routers,
    _register_data_routers,
    _register_desktop_dispatch_routers,
    _register_enterprise_routers,
    _register_feedback_router,
    _register_notifications_and_config,
    _register_relay_platform_router,
    _register_scheduling_routers,
    _register_workflow_routers,
    register_routers,
)
from maop.dashboard._register_routes._static import register_static_routes
from maop.dashboard._register_routes._v1_aliases import _register_v1_aliases
from maop.dashboard._register_routes._websocket import register_websocket

logger: logging.Logger = logging.getLogger(__name__)

# Module-level reference to the FastAPI app, set by ``register_routers``.
# ``_register_v1_aliases`` and ``health`` read it so they can stay zero-arg
# (tests call ``_register_v1_aliases()`` and ``asyncio.run(health())`` directly).
_app: FastAPI | None = None

# ── CSP violation ring buffer (module-level so tests can inspect/trim) ──
_csp_violations: list[dict] = []
_CSP_VIOLATION_MAX = 200  # keep last 200 violations in memory

__all__ = [
    "_CSP_VIOLATION_MAX",
    "_csp_violations",
    "_register_v1_aliases",
    "health",
    "register_routers",
    "register_static_routes",
    "register_websocket",
]
