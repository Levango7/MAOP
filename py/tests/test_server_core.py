"""Core-path smoke tests for dashboard server creation and routing."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from maop.dashboard.server import app as _app


def test_app_is_valid_fastapi():
    """Module-level app is a valid FastAPI instance."""
    assert _app is not None
    assert hasattr(_app, "router")


def test_routes_registered():
    """At least a handful of routes are registered."""
    paths = [r.path for r in _app.routes if hasattr(r, "path")]
    assert len(paths) > 3, f"Expected >3 routes, got {len(paths)}"


def test_lifespan_defined():
    """Lifespan handler is accessible."""
    assert hasattr(_app.router, "lifespan_context") or hasattr(_app.router, "lifespan")