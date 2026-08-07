"""Coverage tests for maop.dashboard.routers.auth — login/logout/register/users CRUD.

Covers POST /api/auth/login (happy + error), POST /api/auth/refresh,
POST /api/auth/logout, POST /api/auth/register (happy + error),
GET /api/auth/users, DELETE /api/auth/users/{username},
PUT /api/auth/users/{username}, GET /api/auth/status.
Uses isolated MAOP_ROOT + real AuthManager with temp DB.
"""
from __future__ import annotations


import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    """Isolate MAOP_ROOT and auth DB to tmp_path."""
    # Create data dir
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("maop.dashboard.routers.auth.MAOP_ROOT", tmp_path)
    monkeypatch.setattr("maop.dashboard.routers.state.MAOP_ROOT", tmp_path)

    # Reset auth manager singleton
    import maop.dashboard.routers.auth as auth_mod
    auth_mod._auth_mgr = None

    # Reset login failures
    auth_mod._login_failures.clear()
    auth_mod._login_failures_by_ip.clear()

    return tmp_path


@pytest.fixture
def client(auth_env, monkeypatch):
    """TestClient with admin role injected and auth router mounted."""
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.auth_identity = "admin"
        return await call_next(request)

    from maop.dashboard.routers.auth import router
    app.include_router(router)
    return TestClient(app)


class TestAuthStatus:
    def test_status_no_token(self, client):
        """GET /api/auth/status without token."""
        resp = client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "auth_enabled" in data


class TestAuthLogin:
    def test_login_missing_fields(self, client):
        """Login with missing fields returns 400."""
        resp = client.post("/api/auth/login", json={})
        assert resp.status_code == 400

    def test_login_missing_password(self, client):
        """Login with missing password returns 400."""
        resp = client.post("/api/auth/login", json={"username": "admin"})
        assert resp.status_code == 400

    def test_login_bad_credentials(self, client):
        """Login with bad credentials returns 401."""
        resp = client.post(
            "/api/auth/login",
            json={"username": "nobody", "password": "wrong-password-123"},
        )
        assert resp.status_code == 401

    def test_login_admin_with_env_password(self, auth_env, client, monkeypatch):
        """Login as admin with MAOP_ADMIN_PASSWORD set succeeds."""
        # Set admin password env before first login
        monkeypatch.setenv("MAOP_ADMIN_PASSWORD", "test-admin-pwd-123")
        # Reset auth manager so default user is created with our password
        import maop.dashboard.routers.auth as auth_mod
        auth_mod._auth_mgr = None

        resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "test-admin-pwd-123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "token" in data
        assert data["username"] == "admin"

    def test_login_lockout_after_failures(self, client):
        """After 5 failed logins, account is locked (429)."""
        for _ in range(5):
            client.post(
                "/api/auth/login",
                json={"username": "lockme", "password": "wrong-pwd-12345"},
            )
        # 6th attempt should be locked
        resp = client.post(
            "/api/auth/login",
            json={"username": "lockme", "password": "wrong-pwd-12345"},
        )
        assert resp.status_code == 429


class TestAuthRefresh:
    def test_refresh_no_auth_header(self, client):
        """Refresh without Authorization header returns 401."""
        resp = client.post("/api/auth/refresh", json={})
        assert resp.status_code == 401

    def test_refresh_invalid_token(self, client):
        """Refresh with invalid token returns 401."""
        resp = client.post(
            "/api/auth/refresh",
            json={},
            headers={"Authorization": "Bearer invalid-token-xyz"},
        )
        assert resp.status_code == 401

    def test_refresh_valid_token(self, auth_env, client, monkeypatch):
        """Refresh with valid token returns new token."""
        monkeypatch.setenv("MAOP_ADMIN_PASSWORD", "test-admin-pwd-123")
        import maop.dashboard.routers.auth as auth_mod
        auth_mod._auth_mgr = None

        # Login to get token
        login_resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "test-admin-pwd-123"},
        )
        token = login_resp.json()["token"]

        # Refresh
        resp = client.post(
            "/api/auth/refresh",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "token" in data


class TestAuthLogout:
    def test_logout_no_token(self, client):
        """Logout without token returns ok (best-effort)."""
        resp = client.post("/api/auth/logout", json={})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_logout_with_invalid_token(self, client):
        """Logout with invalid token still returns ok (best-effort)."""
        resp = client.post(
            "/api/auth/logout",
            json={},
            headers={"Authorization": "Bearer invalid-xyz"},
        )
        assert resp.status_code == 200

    def test_logout_with_valid_token(self, auth_env, client, monkeypatch):
        """Logout with valid token revokes it."""
        monkeypatch.setenv("MAOP_ADMIN_PASSWORD", "test-admin-pwd-123")
        import maop.dashboard.routers.auth as auth_mod
        auth_mod._auth_mgr = None

        login_resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "test-admin-pwd-123"},
        )
        token = login_resp.json()["token"]

        resp = client.post(
            "/api/auth/logout",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


class TestAuthRegister:
    def test_register_missing_fields(self, client):
        """Register with missing fields returns 400."""
        resp = client.post("/api/auth/register", json={})
        assert resp.status_code == 400

    def test_register_short_password(self, client):
        """Register with short password returns 400."""
        resp = client.post(
            "/api/auth/register",
            json={"username": "u1", "password": "short", "roles": ["read"]},
        )
        assert resp.status_code == 400

    def test_register_happy(self, client):
        """Register a new user successfully."""
        resp = client.post(
            "/api/auth/register",
            json={"username": "newuser1", "password": "valid-pwd-123", "roles": ["read"]},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_register_duplicate(self, client):
        """Register duplicate user returns 409 or 400 (wrapped error)."""
        client.post(
            "/api/auth/register",
            json={"username": "dupuser", "password": "valid-pwd-123", "roles": ["read"]},
        )
        resp = client.post(
            "/api/auth/register",
            json={"username": "dupuser", "password": "valid-pwd-123", "roles": ["read"]},
        )
        # _db_register_user returns JSONResponse(409) but auth_register wraps
        # it in try/except which may convert to 400. Both indicate the dup
        # was rejected — accept either.
        assert resp.status_code in (400, 409)


class TestAuthUsers:
    def test_list_users_empty(self, client):
        """List users when no users exist."""
        resp = client.get("/api/auth/users")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "users" in data

    def test_list_users_after_register(self, client):
        """List users after registering one."""
        client.post(
            "/api/auth/register",
            json={"username": "listuser", "password": "valid-pwd-123", "roles": ["read"]},
        )
        resp = client.get("/api/auth/users")
        assert resp.status_code == 200
        users = resp.json()["users"]
        assert any(u["username"] == "listuser" for u in users)

    def test_delete_admin_forbidden(self, client):
        """Cannot delete admin user."""
        resp = client.delete("/api/auth/users/admin")
        assert resp.status_code == 403

    def test_delete_nonexistent(self, client):
        """Delete nonexistent user returns 404 or 500 (db not initialized)."""
        # Initialize db by registering a user first (creates the users table)
        client.post(
            "/api/auth/register",
            json={"username": "inituser", "password": "valid-pwd-123", "roles": ["read"]},
        )
        resp = client.delete("/api/auth/users/nobody_xyz_123")
        assert resp.status_code == 404

    def test_delete_user_happy(self, client):
        """Delete an existing user succeeds."""
        client.post(
            "/api/auth/register",
            json={"username": "deluser", "password": "valid-pwd-123", "roles": ["read"]},
        )
        resp = client.delete("/api/auth/users/deluser")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_update_user_roles(self, client):
        """Update user roles succeeds."""
        client.post(
            "/api/auth/register",
            json={"username": "upduser", "password": "valid-pwd-123", "roles": ["read"]},
        )
        resp = client.put(
            "/api/auth/users/upduser",
            json={"roles": ["read", "write"]},
        )
        assert resp.status_code == 200

    def test_update_user_enabled(self, client):
        """Update user enabled status succeeds."""
        client.post(
            "/api/auth/register",
            json={"username": "enauser", "password": "valid-pwd-123", "roles": ["read"]},
        )
        resp = client.put(
            "/api/auth/users/enauser",
            json={"enabled": False},
        )
        assert resp.status_code == 200

    def test_update_user_password(self, client):
        """Update user password succeeds."""
        client.post(
            "/api/auth/register",
            json={"username": "pwduser", "password": "valid-pwd-123", "roles": ["read"]},
        )
        resp = client.put(
            "/api/auth/users/pwduser",
            json={"password": "new-valid-pwd-456"},
        )
        assert resp.status_code == 200

    def test_update_user_short_password(self, client):
        """Update user with short password returns 400."""
        client.post(
            "/api/auth/register",
            json={"username": "shortpwd", "password": "valid-pwd-123", "roles": ["read"]},
        )
        resp = client.put(
            "/api/auth/users/shortpwd",
            json={"password": "short"},
        )
        assert resp.status_code == 400

    def test_update_nonexistent_user(self, client):
        """Update nonexistent user returns 404 or 500 (db not initialized)."""
        # Initialize db by registering a user first
        client.post(
            "/api/auth/register",
            json={"username": "inituser2", "password": "valid-pwd-123", "roles": ["read"]},
        )
        resp = client.put(
            "/api/auth/users/nobody_xyz_123",
            json={"roles": ["read"]},
        )
        assert resp.status_code == 404