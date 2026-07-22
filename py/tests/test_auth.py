"""Tests for MAOP.core.auth — APIKeyStore, JWTHandler, AuthManager."""

from __future__ import annotations

import time

import pytest

from maop.core.auth import (
    APIKey,
    APIKeyStore,
    AuthConfig,
    AuthManager,
    AuthResult,
    JWTConfig,
    JWTHandler,
)


# ── Models ────────────────────────────────────────────────────────


class TestAuthResult:
    def test_defaults(self):
        r = AuthResult()
        assert r.authenticated is False
        assert r.identity == ""
        assert r.roles == []
        assert r.error == ""


class TestAPIKey:
    def test_defaults(self):
        k = APIKey()
        assert k.key_hash == ""
        assert k.enabled is True
        assert k.rate_limit == 0
        assert k.expires_at is None


class TestJWTConfig:
    def test_defaults(self):
        c = JWTConfig()
        assert c.algorithm == "HS256"
        assert c.issuer == "MAOP"
        assert c.default_ttl_s == 3600.0


# ── APIKeyStore ───────────────────────────────────────────────────


@pytest.fixture
def key_store(tmp_path):
    store = APIKeyStore(db_path=tmp_path / "auth.db")
    yield store
    store.close()


class TestAPIKeyStore:
    def test_init_creates_db(self, tmp_path):
        store = APIKeyStore(db_path=tmp_path / "auth.db")
        assert (tmp_path / "auth.db").exists()
        store.close()

    def test_create_key_returns_plaintext(self, key_store):
        raw = key_store.create_key("my-service", roles=["read"])
        assert isinstance(raw, str)
        assert len(raw) > 0

    def test_validate_created_key(self, key_store):
        raw = key_store.create_key("svc", roles=["read", "write"])
        result = key_store.validate_key(raw)
        assert result.authenticated is True
        assert result.identity == "svc"
        assert result.roles == ["read", "write"]

    def test_validate_invalid_key(self, key_store):
        result = key_store.validate_key("nonexistent-key")
        assert result.authenticated is False
        assert "Invalid" in result.error

    def test_revoke_key(self, key_store):
        raw = key_store.create_key("svc")
        assert key_store.revoke_key("svc") is True
        result = key_store.validate_key(raw)
        assert result.authenticated is False
        assert "disabled" in result.error

    def test_revoke_nonexistent(self, key_store):
        assert key_store.revoke_key("nope") is False

    def test_list_keys(self, key_store):
        key_store.create_key("svc1", roles=["read"])
        key_store.create_key("svc2", roles=["write"])
        keys = key_store.list_keys()
        assert len(keys) == 2
        names = [k["name"] for k in keys]
        assert "svc1" in names
        assert "svc2" in names

    def test_list_keys_no_plaintext(self, key_store):
        raw = key_store.create_key("svc")
        keys = key_store.list_keys()
        for k in keys:
            assert "key" not in k or k.get("key") != raw

    def test_expired_key(self, key_store):
        raw = key_store.create_key("svc", ttl_s=0.01)
        time.sleep(0.05)
        result = key_store.validate_key(raw)
        assert result.authenticated is False
        assert "expired" in result.error

    def test_key_with_rate_limit(self, key_store):
        raw = key_store.create_key("svc", rate_limit=100)
        result = key_store.validate_key(raw)
        assert result.authenticated is True
        keys = key_store.list_keys()
        assert any(k["rate_limit"] == 100 for k in keys)

    def test_hash_key_is_sha256(self):
        h = APIKeyStore._hash_key("testkey")
        assert len(h) == 64  # SHA256 hex digest
        assert h != "testkey"


# ── JWTHandler ────────────────────────────────────────────────────


class TestJWTHandler:
    def test_init_auto_generates_secret(self):
        handler = JWTHandler()
        assert len(handler.config.secret) > 0

    def test_init_with_custom_secret(self):
        cfg = JWTConfig(secret="my-secret")
        handler = JWTHandler(config=cfg)
        assert handler.config.secret == "my-secret"

    def test_create_token_format(self):
        handler = JWTHandler(config=JWTConfig(secret="test"))
        token = handler.create_token("user1")
        parts = token.split(".")
        assert len(parts) == 3

    def test_validate_valid_token(self):
        handler = JWTHandler(config=JWTConfig(secret="test"))
        token = handler.create_token("user1", roles=["admin"])
        result = handler.validate_token(token)
        assert result.authenticated is True
        assert result.identity == "user1"
        assert result.roles == ["admin"]

    def test_validate_expired_token(self):
        handler = JWTHandler(config=JWTConfig(secret="test", default_ttl_s=0.01))
        token = handler.create_token("user1")
        time.sleep(0.05)
        result = handler.validate_token(token)
        assert result.authenticated is False
        assert "expired" in result.error

    def test_validate_invalid_signature(self):
        handler1 = JWTHandler(config=JWTConfig(secret="secret1"))
        handler2 = JWTHandler(config=JWTConfig(secret="secret2"))
        token = handler1.create_token("user1")
        result = handler2.validate_token(token)
        assert result.authenticated is False
        assert "signature" in result.error

    def test_validate_malformed_token(self):
        handler = JWTHandler(config=JWTConfig(secret="test"))
        result = handler.validate_token("not.a.valid.token.format")
        assert result.authenticated is False
        assert "format" in result.error

    def test_validate_wrong_issuer(self):
        handler1 = JWTHandler(config=JWTConfig(secret="test", issuer="maop1"))
        handler2 = JWTHandler(config=JWTConfig(secret="test", issuer="maop2"))
        token = handler1.create_token("user1")
        result = handler2.validate_token(token)
        assert result.authenticated is False
        assert "issuer" in result.error

    def test_b64url_encode_decode_roundtrip(self):
        handler = JWTHandler()
        data = b'{"test": true}'
        encoded = handler._b64url_encode(data)
        decoded = handler._b64url_decode(encoded)
        assert decoded == data

    def test_create_token_with_custom_ttl(self):
        handler = JWTHandler(config=JWTConfig(secret="test"))
        token = handler.create_token("user1", ttl_s=7200)
        result = handler.validate_token(token)
        assert result.authenticated is True
        assert result.expires_at > time.time() + 3600


# ── AuthManager ───────────────────────────────────────────────────


class TestAuthManager:
    def test_init_defaults(self, tmp_path):
        store = APIKeyStore(db_path=tmp_path / "auth.db")
        mgr = AuthManager(key_store=store)
        assert mgr.config.enabled is True
        store.close()

    def test_authenticate_with_api_key(self, tmp_path):
        store = APIKeyStore(db_path=tmp_path / "auth.db")
        raw = store.create_key("svc", roles=["read"])
        mgr = AuthManager(key_store=store)
        result = mgr.authenticate(api_key=raw)
        assert result.authenticated is True
        assert result.identity == "svc"
        store.close()

    def test_authenticate_with_jwt(self):
        mgr = AuthManager()
        token = mgr.jwt_handler.create_token("user1", roles=["admin"])
        result = mgr.authenticate(bearer_token=token)
        assert result.authenticated is True
        assert result.identity == "user1"

    def test_authenticate_with_bearer_prefix(self):
        mgr = AuthManager()
        token = mgr.jwt_handler.create_token("user1")
        result = mgr.authenticate(bearer_token=f"Bearer {token}")
        assert result.authenticated is True

    def test_authenticate_no_credentials(self):
        mgr = AuthManager()
        result = mgr.authenticate()
        assert result.authenticated is False
        assert "No credentials" in result.error

    def test_authenticate_invalid_api_key(self):
        mgr = AuthManager()
        result = mgr.authenticate(api_key="invalid")
        assert result.authenticated is False

    def test_authenticate_invalid_jwt(self):
        mgr = AuthManager()
        result = mgr.authenticate(bearer_token="bad.token.here")
        assert result.authenticated is False

    def test_authenticate_disabled_returns_anonymous(self, tmp_path):
        store = APIKeyStore(db_path=tmp_path / "auth.db")
        mgr = AuthManager(config=AuthConfig(enabled=False), key_store=store)
        result = mgr.authenticate()
        assert result.authenticated is True
        assert result.identity == "anonymous"
        assert "guest" in result.roles  # disabled auth grants guest, not admin (security)
        store.close()

    def test_authenticate_api_key_takes_priority(self, tmp_path):
        store = APIKeyStore(db_path=tmp_path / "auth.db")
        raw = store.create_key("svc")
        mgr = AuthManager(key_store=store)
        # Provide both — API key should win
        jwt_token = mgr.jwt_handler.create_token("jwt-user")
        result = mgr.authenticate(api_key=raw, bearer_token=jwt_token)
        assert result.authenticated is True
        assert result.identity == "svc"
        store.close()

    def test_authenticate_falls_back_to_jwt(self, tmp_path):
        store = APIKeyStore(db_path=tmp_path / "auth.db")
        mgr = AuthManager(key_store=store)
        jwt_token = mgr.jwt_handler.create_token("jwt-user", roles=["user"])
        # Invalid API key, valid JWT
        result = mgr.authenticate(api_key="invalid-key", bearer_token=jwt_token)
        assert result.authenticated is True
        assert result.identity == "jwt-user"
        store.close()
