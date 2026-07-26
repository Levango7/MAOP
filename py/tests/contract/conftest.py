"""Conftest for contract tests — registers the 'contract' marker and shared fixtures."""

import sys
from pathlib import Path

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "contract: mark a test as a contract test"
    )


@pytest.fixture(scope="class")
def server_routes():
    """Extract all registered routes from router modules + server.py."""
    MAOP_ROOT = str(Path(__file__).resolve().parent.parent.parent)
    if MAOP_ROOT not in sys.path:
        sys.path.insert(0, MAOP_ROOT)
    routes = set()
    from maop.dashboard.routers import control, data, evolve, memory, model, system
    for mod in (data, control, model, evolve, memory, system):
        r = getattr(mod, "router", None)
        if r:
            for route in r.routes:
                if hasattr(route, "path"):
                    routes.add(route.path)
    from maop.dashboard.server import app
    for route in app.routes:
        if hasattr(route, "path"):
            routes.add(route.path)
    return routes
