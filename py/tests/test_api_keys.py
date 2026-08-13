"""Tests for API Key management — ApiKeyManager + router + middleware integration.

Covers:
  * Key generation format (maop_{key_id}_{secret})
  * SHA-256 hash storage (plaintext never persisted)
  * Validation (valid / invalid / expired / revoked / wrong IP / missing scope)
  * Scopes check
  * Sliding-window rate limit
  * IP allow-list (plain IP + CIDR)
  * Usage recording & paginated stats
  * CRUD (create / list / get / revoke / delete)
  * FastAPI router endpoints (TestClient)
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from maop.core.security.api_key_manager import (
    ApiKeyCreate,
    ApiKeyManager,
    ApiKeyValidationResult,

    reset_api_key_manager,
)


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def manager(tmp_path: Any) -> ApiKeyManager:
    """Fresh ApiKeyManager backed by an isolated temp DB."""
    reset_api_key_manager()
    mgr = ApiKeyManager(db_path=tmp_path / "auth.db", rate_window_s=60)
    yield mgr
    mgr.close()
    reset_api_key_manager()


@pytest.fixture
def app_with_manager(manager: ApiKeyManager) -> FastAPI:
    """Minimal FastAPI app with the api_keys router + manager on app.state."""
    from maop.dashboard.routers.api_keys import router as api_keys_router

    app = FastAPI()
    app.state.api_key_manager = manager
    # Stub auth state so require_admin passes: tests set auth_roles=["admin"].
    @app.middleware("http")
    async def _stub_auth(request: Request, call_next):
        # Default to admin for router tests; individual tests can override.
        if not hasattr(request.state, "auth_roles"):
            request.state.auth_roles = ["admin"]
            request.state.auth_identity = "test-admin"
        return await call_next(request)

    app.include_router(api_keys_router)
    return app


@pytest.fixture
def client(app_with_manager: FastAPI) -> TestClient:
    return TestClient(app_with_manager)


# ── Key generation ────────────────────────────────────────────────


class TestKeyGeneration:
    def test_key_format(self, manager: ApiKeyManager):
        req = ApiKeyCreate(name="test-svc")
        result = manager.create_key(req)
        assert result.plaintext_key.startswith("maop_")
        parts = result.plaintext_key.split("_")
        # maop_{key_id}_{secret}
        assert len(parts) == 3
        assert parts[0] == "maop"
        assert parts[1] == result.key_id
        assert len(parts[1]) == 8   # key_id length
        assert len(parts[2]) == 32  # secret length

    def test_key_id_is_unique(self, manager: ApiKeyManager):
        r1 = manager.create_key(ApiKeyCreate(name="a"))
        r2 = manager.create_key(ApiKeyCreate(name="b"))
        assert r1.key_id != r2.key_id
        assert r1.plaintext_key != r2.plaintext_key

    def test_plaintext_not_stored(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="secret-check"))
        # The plaintext must not appear in the DB file bytes.
        db_bytes = manager.db_path.read_bytes()
        assert result.plaintext_key.encode() not in db_bytes


# ── Validation ────────────────────────────────────────────────────


class TestValidation:
    def test_valid_key(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc", scopes=["read", "write"]))
        v = manager.validate_key(result.plaintext_key)
        assert v.valid is True
        assert v.key_id == result.key_id
        assert v.name == "svc"
        assert "read" in v.scopes

    def test_invalid_format(self, manager: ApiKeyManager):
        v = manager.validate_key("not-a-maop-key")
        assert v.valid is False
        assert "format" in v.error.lower()

    def test_unknown_key(self, manager: ApiKeyManager):
        v = manager.validate_key("maop_aaaaaaaa_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        assert v.valid is False
        assert "invalid" in v.error.lower()

    def test_expired_key(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="exp", ttl_s=1))
        # Manually backdate expiry to the past.
        import sqlite3
        conn = sqlite3.connect(str(manager.db_path))
        conn.execute(
            "UPDATE api_keys SET expires_at = ? WHERE key_id = ?",
            (time.time() - 10, result.key_id),
        )
        conn.commit()
        conn.close()
        v = manager.validate_key(result.plaintext_key)
        assert v.valid is False
        assert "expired" in v.error.lower()

    def test_revoked_key(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="rev"))
        assert manager.revoke_key(result.key_id) is True
        v = manager.validate_key(result.plaintext_key)
        assert v.valid is False
        assert "revoked" in v.error.lower()

    def test_revoke_already_revoked_returns_false(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="rev2"))
        assert manager.revoke_key(result.key_id) is True
        assert manager.revoke_key(result.key_id) is False

    def test_revoke_unknown_key_returns_false(self, manager: ApiKeyManager):
        assert manager.revoke_key("nope-id") is False


# ── Scopes ────────────────────────────────────────────────────────


class TestScopes:
    def test_scope_granted(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc", scopes=["read", "write"]))
        v = manager.validate_key(result.plaintext_key, required_scope="read")
        assert v.valid is True

    def test_scope_missing(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc", scopes=["read"]))
        v = manager.validate_key(result.plaintext_key, required_scope="write")
        assert v.valid is False
        assert "scope" in v.error.lower()

    def test_wildcard_scope(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc", scopes=["*"]))
        v = manager.validate_key(result.plaintext_key, required_scope="anything")
        assert v.valid is True

    def test_check_scope_static(self):
        assert ApiKeyManager.check_scope(["read"], "read") is True
        assert ApiKeyManager.check_scope(["read"], "write") is False
        assert ApiKeyManager.check_scope(["*"], "write") is True
        assert ApiKeyManager.check_scope([], "") is True


# ── IP allow-list ─────────────────────────────────────────────────


class TestIpAllowList:
    def test_no_allowlist_allows_all(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc", ip_whitelist=[]))
        v = manager.validate_key(result.plaintext_key, client_ip="1.2.3.4")
        assert v.valid is True

    def test_ip_allowed(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc", ip_whitelist=["10.0.0.1"]))
        v = manager.validate_key(result.plaintext_key, client_ip="10.0.0.1")
        assert v.valid is True

    def test_ip_blocked(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc", ip_whitelist=["10.0.0.1"]))
        v = manager.validate_key(result.plaintext_key, client_ip="8.8.8.8")
        assert v.valid is False
        assert "ip" in v.error.lower()

    def test_cidr_allowed(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc", ip_whitelist=["10.0.0.0/24"]))
        v = manager.validate_key(result.plaintext_key, client_ip="10.0.0.50")
        assert v.valid is True

    def test_cidr_blocked(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc", ip_whitelist=["10.0.0.0/24"]))
        v = manager.validate_key(result.plaintext_key, client_ip="10.0.1.50")
        assert v.valid is False

    def test_invalid_client_ip_rejected(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc", ip_whitelist=["10.0.0.1"]))
        v = manager.validate_key(result.plaintext_key, client_ip="not-an-ip")
        assert v.valid is False


# ── Rate limiting ─────────────────────────────────────────────────


class TestRateLimit:
    def test_within_limit(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc", rate_limit=5))
        for _ in range(4):
            manager.record_usage(
                result.key_id, endpoint="/t", method="GET",
                ip_address="1.1.1.1", status_code=200, latency_ms=1.0,
            )
        v = manager.validate_key(result.plaintext_key)
        assert v.valid is True

    def test_exceeds_limit(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc", rate_limit=3))
        for _ in range(3):
            manager.record_usage(
                result.key_id, endpoint="/t", method="GET",
                ip_address="1.1.1.1", status_code=200, latency_ms=1.0,
            )
        v = manager.validate_key(result.plaintext_key)
        assert v.valid is False
        assert v.rate_limit_exceeded is True
        assert "rate" in v.error.lower()

    def test_zero_limit_is_unlimited(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc", rate_limit=0))
        for _ in range(100):
            manager.record_usage(
                result.key_id, endpoint="/t", method="GET",
                ip_address="1.1.1.1", status_code=200, latency_ms=1.0,
            )
        v = manager.validate_key(result.plaintext_key)
        assert v.valid is True

    def test_window_expires(self, tmp_path: Any):
        """Requests outside the window should not count."""
        reset_api_key_manager()
        mgr = ApiKeyManager(db_path=tmp_path / "auth.db", rate_window_s=1)
        try:
            result = mgr.create_key(ApiKeyCreate(name="svc", rate_limit=2))
            mgr.record_usage(
                result.key_id, endpoint="/t", method="GET",
                ip_address="1.1.1.1", status_code=200, latency_ms=1.0,
            )
            mgr.record_usage(
                result.key_id, endpoint="/t", method="GET",
                ip_address="1.1.1.1", status_code=200, latency_ms=1.0,
            )
            # Wait for the 1s window to expire.
            time.sleep(1.1)
            v = mgr.validate_key(result.plaintext_key)
            assert v.valid is True
        finally:
            mgr.close()
            reset_api_key_manager()


# ── Usage stats ───────────────────────────────────────────────────


class TestUsageStats:
    def test_empty_usage(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc"))
        usage = manager.get_usage(result.key_id)
        assert usage.total == 0
        assert usage.records == []
        assert usage.requests_in_window == 0

    def test_records_usage(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc"))
        for i in range(5):
            manager.record_usage(
                result.key_id, endpoint=f"/e{i}", method="GET",
                ip_address="1.1.1.1", status_code=200, latency_ms=float(i),
            )
        usage = manager.get_usage(result.key_id)
        assert usage.total == 5
        assert len(usage.records) == 5
        assert usage.requests_in_window == 5
        # Records are ordered by timestamp DESC.
        assert usage.records[0].endpoint == "/e4"

    def test_pagination(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc"))
        for i in range(10):
            manager.record_usage(
                result.key_id, endpoint=f"/e{i}", method="GET",
                ip_address="1.1.1.1", status_code=200, latency_ms=1.0,
            )
        page1 = manager.get_usage(result.key_id, limit=3, offset=0)
        page2 = manager.get_usage(result.key_id, limit=3, offset=3)
        assert len(page1.records) == 3
        assert len(page2.records) == 3
        # Different pages should have different records.
        ids1 = {r.id for r in page1.records}
        ids2 = {r.id for r in page2.records}
        assert ids1.isdisjoint(ids2)

    def test_last_used_at_updated(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc"))
        assert result.key.last_used_at is None
        manager.record_usage(
            result.key_id, endpoint="/t", method="GET",
            ip_address="1.1.1.1", status_code=200, latency_ms=1.0,
        )
        key = manager.get_key(result.key_id)
        assert key is not None
        assert key.last_used_at is not None


# ── CRUD ──────────────────────────────────────────────────────────


class TestCrud:
    def test_list_keys(self, manager: ApiKeyManager):
        manager.create_key(ApiKeyCreate(name="a"))
        manager.create_key(ApiKeyCreate(name="b"))
        keys = manager.list_keys()
        assert len(keys) == 2
        # Ordered by created_at DESC.
        assert keys[0].name == "b"

    def test_list_keys_by_tenant(self, manager: ApiKeyManager):
        manager.create_key(ApiKeyCreate(name="a", tenant_id="t1"))
        manager.create_key(ApiKeyCreate(name="b", tenant_id="t2"))
        keys = manager.list_keys(tenant_id="t1")
        assert len(keys) == 1
        assert keys[0].name == "a"

    def test_get_key(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc", scopes=["read"], description="desc"))
        key = manager.get_key(result.key_id)
        assert key is not None
        assert key.name == "svc"
        assert key.scopes == ["read"]
        assert key.description == "desc"

    def test_get_unknown_key(self, manager: ApiKeyManager):
        assert manager.get_key("nope") is None

    def test_delete_key(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc"))
        manager.record_usage(
            result.key_id, endpoint="/t", method="GET",
            ip_address="1.1.1.1", status_code=200, latency_ms=1.0,
        )
        assert manager.delete_key(result.key_id) is True
        assert manager.get_key(result.key_id) is None
        # Usage rows also deleted.
        usage = manager.get_usage(result.key_id)
        assert usage.total == 0

    def test_delete_unknown_key(self, manager: ApiKeyManager):
        assert manager.delete_key("nope") is False


# ── Router endpoints ──────────────────────────────────────────────


class TestRouter:
    def test_create_key_endpoint(self, client: TestClient):
        resp = client.post("/api/api-keys", json={"name": "svc1", "scopes": ["read"]})
        assert resp.status_code == 201
        data = resp.json()
        assert data["plaintext_key"].startswith("maop_")
        assert data["key"]["name"] == "svc1"
        assert data["key"]["scopes"] == ["read"]

    def test_list_keys_endpoint(self, client: TestClient):
        client.post("/api/api-keys", json={"name": "a"})
        client.post("/api/api-keys", json={"name": "b"})
        resp = client.get("/api/api-keys")
        assert resp.status_code == 200
        keys = resp.json()
        assert len(keys) == 2

    def test_get_key_endpoint(self, client: TestClient):
        create = client.post("/api/api-keys", json={"name": "svc"}).json()
        key_id = create["key_id"]
        resp = client.get(f"/api/api-keys/{key_id}")
        assert resp.status_code == 200
        assert resp.json()["key_id"] == key_id

    def test_get_unknown_key_404(self, client: TestClient):
        resp = client.get("/api/api-keys/nope")
        assert resp.status_code == 404

    def test_revoke_key_endpoint(self, client: TestClient):
        create = client.post("/api/api-keys", json={"name": "svc"}).json()
        key_id = create["key_id"]
        resp = client.post(f"/api/api-keys/{key_id}/revoke")
        assert resp.status_code == 200
        # Subsequent validation should fail.
        key = client.get(f"/api/api-keys/{key_id}").json()
        assert key["enabled"] is False

    def test_revoke_unknown_key_404(self, client: TestClient):
        resp = client.post("/api/api-keys/nope/revoke")
        assert resp.status_code == 404

    def test_delete_key_endpoint(self, client: TestClient):
        create = client.post("/api/api-keys", json={"name": "svc"}).json()
        key_id = create["key_id"]
        resp = client.delete(f"/api/api-keys/{key_id}")
        assert resp.status_code == 200
        assert client.get(f"/api/api-keys/{key_id}").status_code == 404

    def test_usage_endpoint(self, client: TestClient, manager: ApiKeyManager):
        create = client.post("/api/api-keys", json={"name": "svc"}).json()
        key_id = create["key_id"]
        manager.record_usage(
            key_id, endpoint="/t", method="GET",
            ip_address="1.1.1.1", status_code=200, latency_ms=1.0,
        )
        resp = client.get(f"/api/api-keys/{key_id}/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["records"][0]["endpoint"] == "/t"

    def test_validate_endpoint(self, client: TestClient):
        create = client.post(
            "/api/api-keys",
            json={"name": "svc", "scopes": ["read"]},
        ).json()
        plaintext = create["plaintext_key"]
        resp = client.post(
            "/api/api-keys/validate",
            json={"key": plaintext, "scope": "read"},
        )
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    def test_create_with_all_fields(self, client: TestClient):
        resp = client.post(
            "/api/api-keys",
            json={
                "name": "full",
                "scopes": ["read", "write"],
                "roles": ["admin"],
                "rate_limit": 100,
                "ip_whitelist": ["10.0.0.0/24"],
                "ttl_s": 3600,
                "description": "full key",
                "tenant_id": "t1",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["key"]["scopes"] == ["read", "write"]
        assert data["key"]["rate_limit"] == 100
        assert data["key"]["ip_whitelist"] == ["10.0.0.0/24"]
        assert data["key"]["tenant_id"] == "t1"
        assert data["key"]["expires_at"] is not None


# ── Pydantic models ───────────────────────────────────────────────


class TestPydanticModels:
    def test_apikey_create_defaults(self):
        req = ApiKeyCreate(name="x")
        assert req.scopes == []
        assert req.roles == []
        assert req.rate_limit == 0
        assert req.ip_whitelist == []
        assert req.ttl_s is None
        assert req.description == ""

    def test_apikey_create_validation(self):
        with pytest.raises(ValueError):
            ApiKeyCreate(name="")  # min_length=1
        with pytest.raises(ValueError):
            ApiKeyCreate(name="x", rate_limit=-1)  # ge=0

    def test_validation_result_defaults(self):
        r = ApiKeyValidationResult()
        assert r.valid is False
        assert r.scopes == []
        assert r.rate_limit_exceeded is False


# ── Prune ─────────────────────────────────────────────────────────


class TestPrune:
    def test_prune_old_usage(self, manager: ApiKeyManager):
        result = manager.create_key(ApiKeyCreate(name="svc"))
        for i in range(20):
            manager.record_usage(
                result.key_id, endpoint=f"/e{i}", method="GET",
                ip_address="1.1.1.1", status_code=200, latency_ms=1.0,
            )
        deleted = manager.prune_usage(keep_last_n=5)
        assert deleted == 15
        usage = manager.get_usage(result.key_id)
        assert usage.total == 5