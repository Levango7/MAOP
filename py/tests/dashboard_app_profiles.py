"""FastAPI app objects for the two dashboard profiles, for gate tests.

The response_model ratchet in ``test_frontend_response_models.py`` only sees the
personal app: ``_register_routes._register_enterprise_routers`` is gated by
``has_feature(FeatureFlag.MULTI_USER)``, so with no enterprise license the
tenant / rbac / sso / quotas / n8n / licenses / compliance routers are never
mounted — and every endpoint the dashboard calls on them vanishes from
``app.openapi()``. That is not a passing check, it is a check that never ran.

Here the routers are included directly (they exist in-tree regardless of
license), which lets the same scan judge the enterprise surface too.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

#: Enterprise-only routers, mounted in the dashboard only under MULTI_USER.
ENTERPRISE_ROUTER_MODULES = (
    "tenant",
    "rbac",
    "sso",
    "quotas",
    "n8n",
    "licenses",
    "compliance",
)

_METHODS = ("get", "post", "put", "patch", "delete")


def enterprise_app() -> FastAPI:
    """A fresh app carrying just the enterprise-only routers."""
    app = FastAPI()
    for name in ENTERPRISE_ROUTER_MODULES:
        module = importlib.import_module(f"maop.dashboard.routers.{name}")
        app.include_router(module.router)
    return app


def personal_app() -> FastAPI:
    from maop.dashboard.server import app

    return app


def route_map(app: FastAPI, normalize: Callable[[str], str]) -> dict[tuple[str, str], dict[str, Any]]:
    """``{(METHOD, normalized_path): operation}`` for one profile."""
    routes: dict[tuple[str, str], dict[str, Any]] = {}
    for path, methods in app.openapi().get("paths", {}).items():
        for method, op in methods.items():
            if method in _METHODS:
                routes[(method.upper(), normalize(path))] = op
    return routes


def all_route_map(normalize: Callable[[str], str]) -> dict[tuple[str, str], dict[str, Any]]:
    """Both profiles merged — what a route-referencing fixture must match."""
    routes = route_map(personal_app(), normalize)
    routes.update(route_map(enterprise_app(), normalize))
    return routes
