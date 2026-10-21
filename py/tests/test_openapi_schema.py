"""The whole app must be able to generate its OpenAPI schema.

FastAPI aborts generation for every route as soon as one route has no
response class, so a single ``response_class=None`` silently breaks
``/api/docs`` and every OpenAPI consumer. The bb-m3 metrics route did exactly
that; this test exercises the full mounted app rather than a sub-app.
"""

from __future__ import annotations


def test_full_app_openapi_schema_generates():
    from maop.dashboard.server import app

    schema = app.openapi()
    assert "/api/runs" in schema["paths"]


def test_plain_text_metrics_route_is_documented():
    from maop.dashboard.server import app

    operation = app.openapi()["paths"]["/api/bb-m3/metrics"]["get"]
    assert "text/plain" in operation["responses"]["200"]["content"]
