"""Regression tests for dashboard authentication wiring and password storage.

httpx compatibility note:
  FastAPI's TestClient internally uses httpx.  Between httpx 0.27 and 0.28
  some deprecated Response properties (is_redirect, is_error) were removed.
  These tests only access stable attributes (status_code, json()) and are
  therefore safe across httpx >=0.27.  The pyproject.toml pin
  ``httpx>=0.27.0,<1.0`` ensures we never pull an incompatible major version.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.auth import APIKeyStore, AuthConfig, AuthManager, JWTConfig
from maop.core.middleware import AuthMiddleware
from maop.dashboard.routers import auth as auth_mod

# Defensive: verify the installed httpx version is compatible.
_httpx_version = tuple(int(x) for x in httpx.__version__.split(".")[:2])
assert _httpx_version >= (0, 27), f"requires httpx>=0.27, got {httpx.__version__}"


def test_password_hash_uses_pbkdf2_and_verifies():
    stored = auth_mod._hash_password("correct horse battery staple")

    assert stored.startswith("pbkdf2_sha256$")
    assert "correct horse battery staple" not in stored
    assert auth_mod._verify_password("correct horse battery staple", stored)
    assert not auth_mod._verify_password("wrong", stored)
    assert not auth_mod._password_needs_rehash(stored)


def test_legacy_sha256_password_hash_is_rejected():
    legacy = hashlib.sha256(b"old-password").hexdigest()
    assert not auth_mod._verify_password("old-password", legacy)
    assert not auth_mod._verify_password("wrong", legacy)
    assert auth_mod._password_needs_rehash(legacy)


def test_login_rejects_legacy_password_hash(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    auth_db = data_dir / "auth.db"
    conn = sqlite3.connect(str(auth_db))
    conn.execute(
        """
        CREATE TABLE users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            roles TEXT NOT NULL DEFAULT '["admin"]',
            created_at REAL NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    legacy_hash = hashlib.sha256(b"secret").hexdigest()
    conn.execute(
        "INSERT INTO users (username, password_hash, roles, created_at, enabled) VALUES (?, ?, ?, ?, 1)",
        ("admin", legacy_hash, '["admin"]', time.time()),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(auth_mod, "MAOP_ROOT", tmp_path)
    monkeypatch.setattr(auth_mod, "_auth_mgr", None)

    app = FastAPI()
    app.post("/login")(auth_mod.auth_login)
    with TestClient(app) as client:
        response = client.post("/login", json={"username": "admin", "password": "secret"})

    assert response.json()["status"] == "error"


def test_auth_middleware_uses_auth_manager_for_jwt(tmp_path):
    app = FastAPI()
    store = APIKeyStore(tmp_path / "auth.db")
    manager = AuthManager(
        AuthConfig(enabled=True, jwt=JWTConfig(secret="test-secret")),
        key_store=store,
    )
    app.state.auth_manager = manager
    app.add_middleware(AuthMiddleware, enabled=True, public_paths=[])

    @app.get("/protected")
    async def protected():
        return {"ok": True}

    token = manager.jwt_handler.create_token("alice", roles=["read"])
    with TestClient(app) as client:
        unauthorized = client.get("/protected")
        authorized = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json() == {"ok": True}


def test_auth_middleware_uses_auth_manager_for_api_key(tmp_path):
    app = FastAPI()
    store = APIKeyStore(tmp_path / "auth.db")
    raw_key = store.create_key("service", roles=["execute"])
    manager = AuthManager(AuthConfig(enabled=True), key_store=store)
    app.state.auth_manager = manager
    app.add_middleware(AuthMiddleware, enabled=True, public_paths=[])

    @app.get("/protected")
    async def protected():
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/protected", headers={"X-API-Key": raw_key})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
