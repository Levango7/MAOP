"""Conftest for contract tests — registers the 'contract' marker and shared fixtures."""

import sys
from pathlib import Path

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "contract: mark a test as a contract test"
    )


def _extract_all_routes(app_or_router) -> set[str]:
    """Recursively extract all route paths, handling FastAPI 0.138+ _IncludedRouter."""
    routes: set[str] = set()
    raw_routes = getattr(app_or_router, "routes", None) or getattr(app_or_router, "router", None)
    if raw_routes is None:
        return routes
    for route in raw_routes:
        cls_name = type(route).__name__
        if hasattr(route, "path"):
            routes.add(route.path)
        if cls_name == "_IncludedRouter" and hasattr(route, "original_router"):
            routes |= _extract_all_routes(route.original_router)
        if hasattr(route, "routes"):
            routes |= _extract_all_routes(route)
    return routes


@pytest.fixture(scope="class")
def server_routes():
    """Extract all registered routes from router modules + server.py."""
    MAOP_ROOT = str(Path(__file__).resolve().parent.parent.parent)
    if MAOP_ROOT not in sys.path:
        sys.path.insert(0, MAOP_ROOT)
    routes: set[str] = set()
    from maop.dashboard.routers import control, data, evolve_insights, memory, model, system
    for mod in (data, control, model, evolve_insights, memory, system):
        r = getattr(mod, "router", None)
        if r:
            routes |= _extract_all_routes(r)
    from maop.dashboard.server import app
    routes |= _extract_all_routes(app)
    return routes
