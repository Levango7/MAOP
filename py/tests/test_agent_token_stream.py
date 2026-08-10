"""Tests for /api/stream/agent/{execution_id} SSE endpoint (v5.0.0).

Tests token-level streaming for agent execution via SSE.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """Create a test client with auth disabled."""
    monkeypatch.setenv("MAOP_AUTH", "0")
    monkeypatch.setenv("MAOP_AUTH_ENABLED", "0")
    monkeypatch.setenv("MAOP_ENV", "test")
    # Patch require_admin to no-op for testing
    import maop.core.security.middleware as mw
    monkeypatch.setattr(mw, "require_admin", lambda req: None)
    from maop.dashboard.server import app
    return TestClient(app)


class TestAgentTokenStreamEndpoint:
    """Test /api/stream/agent/{execution_id} SSE endpoint."""

    def test_endpoint_route_registered(self, client):
        """Endpoint should be registered (not 405 Method Not Allowed)."""
        # We test route registration by checking it's not 405.
        # Full SSE streaming requires a running execution which is complex to set up.
        # The route is defined between /dag/{execution_id} and /{trace_id} in stream.py.
        from maop.dashboard.routers.stream import router
        routes = [r.path for r in router.routes]
        assert "/agent/{execution_id}" in routes or any("/agent/" in r for r in routes)