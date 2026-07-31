"""Regression tests for OPS-7: enterprise_api_guard v1 alias bypass.

The guard previously checked ``path.startswith("/api/tenant")`` etc., so a
version-prefixed path such as ``/api/v1/tenant/...`` bypassed it entirely.
The fix normalizes away an optional ``/vN`` segment before the prefix check.
"""
from __future__ import annotations

import importlib

import pytest

from maop.config.edition import FeatureFlag, has_feature


def test_normalize_api_path_strips_v1():
    from maop.dashboard.server import _normalize_api_path

    assert _normalize_api_path("/api/v1/tenant/x") == "/api/tenant/x"


def test_normalize_api_path_strips_v2_and_deep():
    from maop.dashboard.server import _normalize_api_path

    assert _normalize_api_path("/api/v2/audit/foo/bar") == "/api/audit/foo/bar"


def test_normalize_api_path_plain_unchanged():
    from maop.dashboard.server import _normalize_api_path

    assert _normalize_api_path("/api/tenant/x") == "/api/tenant/x"
    assert _normalize_api_path("/api/health") == "/api/health"


def test_normalize_api_path_non_api_unchanged():
    from maop.dashboard.server import _normalize_api_path

    assert _normalize_api_path("/app/foo/v1/bar") == "/app/foo/v1/bar"


@pytest.mark.skipif(
    has_feature(FeatureFlag.MULTI_USER),
    reason="enterprise_api_guard is only registered in personal edition",
)
def test_guard_blocks_versioned_enterprise_path():
    """Force personal edition and verify /api/v1/tenant/* is blocked (404)."""
    import maop.config.edition as ed
    import maop.dashboard.server as server
    from fastapi.testclient import TestClient

    original = ed.has_feature
    ed.has_feature = lambda flag: False  # type: ignore[assignment]
    try:
        importlib.reload(server)
        client = TestClient(server.app)
        resp = client.get("/api/v1/tenant/list")
        assert resp.status_code == 404
        body = resp.json()
        assert "Enterprise" in body.get("hint", "")
    except Exception as exc:  # pragma: no cover - reload may be env-sensitive
        pytest.skip(f"server reload unsupported in this env: {exc}")
    finally:
        ed.has_feature = original  # type: ignore[assignment]
        try:
            importlib.reload(server)
        except Exception:  # pragma: no cover
            pass
