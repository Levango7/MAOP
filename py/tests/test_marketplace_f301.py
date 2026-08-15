"""Tests for marketplace signing (G-01 Ed25519) and sandbox (G-02 whitelist env).

F-301 Marketplace security tests:
  - Ed25519 sign/verify round-trip
  - Tampered signature rejected
  - Wrong key rejected
  - Malformed signature rejected
  - Sandbox env whitelist strips secrets (JWT_SECRET, DB_PASSWORD, API_KEY)
  - Sandbox env whitelist forwards MAOP_SANDBOX_* variables
  - Sandbox env whitelist forwards safe variables (PATH, HOME, …)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from maop.core.marketplace.sandbox import (
    _BLOCKED_ENV_VARS,
    _SAFE_ENV_VARS,
    _SANDBOX_ENV_PREFIX,
    SandboxManager,
    build_sandbox_env,
)
from maop.core.marketplace.signing import (
    PackageVerifier,
    SignatureError,
    generate_key_pair,
    load_public_key,
    private_key_to_pem,
    public_key_to_pem,
    sign_payload,
    verify,
)

# ── G-01: Ed25519 signing tests ─────────────────────────────────────


class TestEd25519Signing:
    """Test Ed25519 asymmetric signature verification (G-01)."""

    def test_sign_verify_roundtrip(self):
        """A valid signature verifies successfully."""
        private_key, public_key = generate_key_pair()
        payload = {"name": "test-plugin", "version": "1.0.0", "author": "tester"}
        signature_hex = sign_payload(payload, private_key)
        assert verify(payload, signature_hex, public_key) is True

    def test_tampered_payload_rejected(self):
        """Tampering with the payload after signing fails verification."""
        private_key, public_key = generate_key_pair()
        payload = {"name": "test-plugin", "version": "1.0.0"}
        signature_hex = sign_payload(payload, private_key)
        tampered = {"name": "evil-plugin", "version": "1.0.0"}
        with pytest.raises(SignatureError, match="verification failed"):
            verify(tampered, signature_hex, public_key)

    def test_tampered_signature_rejected(self):
        """A tampered signature fails verification."""
        private_key, public_key = generate_key_pair()
        payload = {"name": "test-plugin", "version": "1.0.0"}
        signature_hex = sign_payload(payload, private_key)
        # Flip a hex char.
        tampered_sig = ("0" if signature_hex[0] != "0" else "1") + signature_hex[1:]
        with pytest.raises(SignatureError):
            verify(payload, tampered_sig, public_key)

    def test_wrong_key_rejected(self):
        """A signature verified with the wrong public key fails."""
        priv_a, _ = generate_key_pair()
        _, pub_b = generate_key_pair()
        payload = {"name": "test-plugin", "version": "1.0.0"}
        signature_hex = sign_payload(payload, priv_a)
        with pytest.raises(SignatureError, match="verification failed"):
            verify(payload, signature_hex, pub_b)

    def test_empty_signature_rejected(self):
        """An empty signature string is rejected."""
        _, public_key = generate_key_pair()
        with pytest.raises(SignatureError, match="empty"):
            verify({"a": 1}, "", public_key)

    def test_malformed_hex_rejected(self):
        """A non-hex signature string is rejected."""
        _, public_key = generate_key_pair()
        with pytest.raises(SignatureError, match="not valid hex"):
            verify({"a": 1}, "not-hex-at-all!!!", public_key)

    def test_wrong_length_signature_rejected(self):
        """A signature with wrong byte length is rejected."""
        _, public_key = generate_key_pair()
        # 32 bytes instead of 64.
        short_sig = os.urandom(32).hex()
        with pytest.raises(SignatureError, match="64 bytes"):
            verify({"a": 1}, short_sig, public_key)

    def test_key_serialization_roundtrip(self, tmp_path: Path):
        """Keys can be serialised to PEM and loaded back."""
        private_key, public_key = generate_key_pair()
        pem_bytes = public_key_to_pem(public_key)
        pem_path = tmp_path / "pub.pem"
        pem_path.write_bytes(pem_bytes)
        loaded = load_public_key(pem_path)
        # Verify a signature with the loaded key.
        payload = {"test": True}
        sig = sign_payload(payload, private_key)
        assert verify(payload, sig, loaded) is True

    def test_private_key_pem_serializable(self):
        """Private key PEM serialisation works."""
        private_key, _ = generate_key_pair()
        pem = private_key_to_pem(private_key)
        assert b"BEGIN PRIVATE KEY" in pem


class TestPackageVerifier:
    """Test the PackageVerifier convenience class."""

    def test_verify_package_success(self):
        """A complete signed package verifies."""
        private_key, public_key = generate_key_pair()
        verifier = PackageVerifier()
        verifier.set_public_key(public_key)
        payload = {"name": "plugin", "version": "2.0"}
        sig = sign_payload(payload, private_key)
        package = {**payload, "signature_hex": sig}
        assert verifier.verify_package(package) is True

    def test_verify_package_missing_signature(self):
        """A package without signature_hex raises."""
        _, public_key = generate_key_pair()
        verifier = PackageVerifier()
        verifier.set_public_key(public_key)
        with pytest.raises(SignatureError, match="missing"):
            verifier.verify_package({"name": "plugin"})

    def test_verify_package_bad_signature(self):
        """A package with bad signature fails."""
        _, public_key = generate_key_pair()
        verifier = PackageVerifier()
        verifier.set_public_key(public_key)
        package = {"name": "plugin", "signature_hex": "00" * 64}
        with pytest.raises(SignatureError):
            verifier.verify_package(package)

    def test_no_public_key_raises(self):
        """Calling verify without a key raises."""
        verifier = PackageVerifier()
        with pytest.raises(SignatureError, match="No public key"):
            verifier.verify({"a": 1}, "00" * 64)


# ── G-02: Sandbox env whitelist tests ───────────────────────────────


class TestSandboxEnvWhitelist:
    """Test the G-02 whitelist environment policy."""

    def test_blocked_vars_stripped(self):
        """JWT_SECRET, DB_PASSWORD, API_KEY are never forwarded."""
        env = {
            "JWT_SECRET": "super-secret-jwt",
            "DB_PASSWORD": "db-pass-123",
            "API_KEY": "api-key-456",
            "SECRET_KEY": "secret-789",
            "PATH": "/usr/bin:/bin",
        }
        result = build_sandbox_env(env)
        assert "JWT_SECRET" not in result
        assert "DB_PASSWORD" not in result
        assert "API_KEY" not in result
        assert "SECRET_KEY" not in result

    def test_safe_vars_forwarded(self):
        """Safe variables (PATH, HOME, LANG) are forwarded."""
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/home/user",
            "LANG": "en_US.UTF-8",
            "JWT_SECRET": "should-be-stripped",
        }
        result = build_sandbox_env(env)
        assert result.get("PATH") == "/usr/bin:/bin"
        assert result.get("HOME") == "/home/user"
        assert result.get("LANG") == "en_US.UTF-8"

    def test_maop_sandbox_vars_forwarded(self):
        """MAOP_SANDBOX_* variables are forwarded."""
        env = {
            "MAOP_SANDBOX_CONFIG_PATH": "/etc/sandbox/config",
            "MAOP_SANDBOX_DEBUG": "1",
            "MAOP_SANDBOX_RESOURCE_LIMIT": "100",
        }
        result = build_sandbox_env(env)
        assert result.get("MAOP_SANDBOX_CONFIG_PATH") == "/etc/sandbox/config"
        assert result.get("MAOP_SANDBOX_DEBUG") == "1"
        assert result.get("MAOP_SANDBOX_RESOURCE_LIMIT") == "100"

    def test_maop_secrets_not_forwarded(self):
        """MAOP_JWT_SECRET, MAOP_DB_PASSWORD etc. are not forwarded."""
        env = {
            "MAOP_JWT_SECRET": "jwt-secret",
            "MAOP_DB_PASSWORD": "db-pass",
            "MAOP_API_KEY": "api-key",
            "MAOP_SECRET_KEY": "secret",
            "MAOP_DATABASE_URL": "postgres://...",
            "MAOP_REDIS_URL": "redis://...",
        }
        result = build_sandbox_env(env)
        for key in env:
            assert key not in result, f"{key} should have been stripped"

    def test_arbitrary_vars_stripped(self):
        """Arbitrary non-whitelisted variables are stripped."""
        env = {
            "RANDOM_VAR": "value",
            "ANOTHER_VAR": "another",
            "PATH": "/usr/bin",
        }
        result = build_sandbox_env(env)
        assert "RANDOM_VAR" not in result
        assert "ANOTHER_VAR" not in result
        assert "PATH" in result

    def test_empty_env(self):
        """An empty source env produces an empty result."""
        result = build_sandbox_env({})
        assert result == {}

    def test_blocked_list_covers_common_secrets(self):
        """The blocked list covers all common secret variable names."""
        expected_blocked = {
            "JWT_SECRET", "DB_PASSWORD", "API_KEY", "SECRET_KEY",
            "MAOP_JWT_SECRET", "MAOP_DB_PASSWORD", "MAOP_API_KEY",
            "MAOP_SECRET_KEY", "DATABASE_URL", "REDIS_URL",
            "MAOP_DATABASE_URL", "MAOP_REDIS_URL",
        }
        assert expected_blocked <= _BLOCKED_ENV_VARS

    def test_safe_list_includes_path_and_home(self):
        """The safe list includes PATH and HOME."""
        assert "PATH" in _SAFE_ENV_VARS
        assert "HOME" in _SAFE_ENV_VARS

    def test_sandbox_prefix_constant(self):
        """The sandbox prefix is MAOP_SANDBOX_."""
        assert _SANDBOX_ENV_PREFIX == "MAOP_SANDBOX_"

    def test_extra_safe_vars_merged(self):
        """Extra safe variables are merged with the default safe set."""
        env = {"CUSTOM_VAR": "value", "PATH": "/usr/bin"}
        result = build_sandbox_env(env, extra_safe=frozenset({"CUSTOM_VAR"}))
        assert result.get("CUSTOM_VAR") == "value"
        assert result.get("PATH") == "/usr/bin"


class TestSandboxManager:
    """Test the SandboxManager with whitelist env."""

    def test_create_and_get_sandbox(self, tmp_path: Path):
        """Create a sandbox and retrieve it."""
        mgr = SandboxManager(tmp_path)
        sb = mgr.create()
        assert sb.id.startswith("sb-")
        assert sb.status == "active"
        retrieved = mgr.get(sb.id)
        assert retrieved is not None
        assert retrieved.id == sb.id

    def test_run_sandbox_with_whitelist_env(self, tmp_path: Path):
        """Running a sandbox uses the whitelist env (G-02)."""
        mgr = SandboxManager(tmp_path)
        sb = mgr.create()
        # Set a secret in the environment — it should NOT be forwarded.
        old_env = os.environ.get("JWT_SECRET")
        os.environ["JWT_SECRET"] = "test-secret-that-should-not-leak"
        try:
            result = mgr.run(sb.id, command="echo hello")
            assert result.ok is True
            assert result.exit_code == 0
        finally:
            if old_env is None:
                del os.environ["JWT_SECRET"]
            else:
                os.environ["JWT_SECRET"] = old_env

    def test_cleanup_sandbox(self, tmp_path: Path):
        """Cleanup removes the sandbox."""
        mgr = SandboxManager(tmp_path)
        sb = mgr.create()
        assert mgr.cleanup(sb.id) is True
        retrieved = mgr.get(sb.id)
        assert retrieved is not None
        assert retrieved.status == "cleaned"

    def test_run_nonexistent_sandbox(self, tmp_path: Path):
        """Running a non-existent sandbox returns an error."""
        mgr = SandboxManager(tmp_path)
        result = mgr.run("nonexistent", command="echo hello")
        assert result.ok is False
        assert "not found" in result.error

    def test_list_sandboxes(self, tmp_path: Path):
        """List sandboxes returns created sandboxes."""
        mgr = SandboxManager(tmp_path)
        mgr.create()
        mgr.create()
        sandboxes = mgr.list_all()
        assert len(sandboxes) == 2

    def test_stats(self, tmp_path: Path):
        """Stats returns sandbox counts by status."""
        mgr = SandboxManager(tmp_path)
        mgr.create()
        mgr.create()
        stats = mgr.stats()
        assert stats.get("active", 0) == 2