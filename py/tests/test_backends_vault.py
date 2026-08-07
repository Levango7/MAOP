"""Tests for MAOP HashiCorp Vault Secret Backend (backends_vault.py).

Uses unittest.mock to simulate the hvac client — no real Vault server required.

Covers:
  - Import and construction with mocked hvac
  - Environment variable configuration (MAOP_VAULT_ADDR/TOKEN/MOUNT/PATH)
  - Authentication failure handling
  - get/set/delete/list secret operations
  - KV v2 mount auto-creation
  - Path construction
  - Client close
  - Factory degradation to LocalSecretBackend when hvac is unavailable
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# ── Helpers ───────────────────────────────────────────────────────

VAULT_ENV_VARS = (
    "MAOP_VAULT_ADDR",
    "MAOP_VAULT_TOKEN",
    "MAOP_VAULT_MOUNT",
    "MAOP_VAULT_PATH",
)


def _install_mock_hvac(monkeypatch, *, authenticated=True, mounts=None):
    """Install a mock ``hvac`` module into sys.modules and return the mock client.

    The returned client is a ``MagicMock`` whose ``secrets.kv.v2.*`` methods
    can be configured per-test. ``is_authenticated`` and
    ``sys.list_mounted_secrets_engines`` are pre-stubbed so that
    ``VaultSecretBackend.__init__`` completes without error.
    """
    mock_hvac = MagicMock(name="hvac")
    mock_client = MagicMock(name="hvac.Client")
    mock_client.is_authenticated.return_value = authenticated
    if mounts is None:
        mounts = {"secret/": {"type": "kv"}}
    mock_client.sys.list_mounted_secrets_engines.return_value = mounts
    mock_hvac.Client.return_value = mock_client
    monkeypatch.setitem(sys.modules, "hvac", mock_hvac)
    return mock_client


@pytest.fixture(autouse=True)
def _clean_vault_env(monkeypatch):
    """Remove vault env vars before each test so defaults are deterministic."""
    for var in VAULT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ── Import / construction ─────────────────────────────────────────

class TestVaultImport:
    def test_vault_backend_import(self, monkeypatch):
        """VaultSecretBackend imports and constructs without error when hvac is mocked."""
        _install_mock_hvac(monkeypatch)
        from maop.core.backends.backends_vault import VaultSecretBackend
        backend = VaultSecretBackend()
        assert backend is not None
        assert hasattr(backend, "get_secret")


# ── Init / configuration ──────────────────────────────────────────

class TestVaultInit:
    def test_vault_init_with_env_vars(self, monkeypatch):
        """MAOP_VAULT_ADDR/TOKEN/MOUNT/PATH env vars are read into instance attrs."""
        monkeypatch.setenv("MAOP_VAULT_ADDR", "http://vault:8200")
        monkeypatch.setenv("MAOP_VAULT_TOKEN", "my-token")
        monkeypatch.setenv("MAOP_VAULT_MOUNT", "kv")
        monkeypatch.setenv("MAOP_VAULT_PATH", "myapp")
        _install_mock_hvac(monkeypatch)
        from maop.core.backends.backends_vault import VaultSecretBackend
        backend = VaultSecretBackend()
        assert backend._addr == "http://vault:8200"
        assert backend._token == "my-token"
        assert backend._mount == "kv"
        assert backend._base_path == "myapp"

    def test_vault_init_not_authenticated_raises(self, monkeypatch):
        """When is_authenticated() returns False, RuntimeError is raised."""
        _install_mock_hvac(monkeypatch, authenticated=False)
        from maop.core.backends.backends_vault import VaultSecretBackend
        with pytest.raises(RuntimeError, match="Vault authentication failed"):
            VaultSecretBackend()


# ── get_secret ────────────────────────────────────────────────────

class TestVaultGetSecret:
    def test_vault_get_secret(self, monkeypatch):
        """get_secret returns the 'value' field from the KV v2 response."""
        mock_client = _install_mock_hvac(monkeypatch)
        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "sk-test-123"}}
        }
        from maop.core.backends.backends_vault import VaultSecretBackend
        backend = VaultSecretBackend()
        assert backend.get_secret("openai") == "sk-test-123"
        mock_client.secrets.kv.v2.read_secret_version.assert_called_once_with(
            path="maop/openai", mount_point="secret",
        )

    def test_vault_get_secret_not_found(self, monkeypatch):
        """get_secret returns None when the secret does not exist (read raises)."""
        mock_client = _install_mock_hvac(monkeypatch)
        mock_client.secrets.kv.v2.read_secret_version.side_effect = Exception("not found")
        from maop.core.backends.backends_vault import VaultSecretBackend
        backend = VaultSecretBackend()
        assert backend.get_secret("missing") is None


# ── set_secret ────────────────────────────────────────────────────

class TestVaultSetSecret:
    def test_vault_set_secret(self, monkeypatch):
        """set_secret writes {value: ...} to the KV v2 path."""
        mock_client = _install_mock_hvac(monkeypatch)
        from maop.core.backends.backends_vault import VaultSecretBackend
        backend = VaultSecretBackend()
        backend.set_secret("openai", "sk-new")
        mock_client.secrets.kv.v2.create_or_update_secret.assert_called_once_with(
            path="maop/openai", mount_point="secret", secret={"value": "sk-new"},
        )


# ── delete_secret ─────────────────────────────────────────────────

class TestVaultDeleteSecret:
    def test_vault_delete_secret(self, monkeypatch):
        """delete_secret returns True and calls delete_metadata_and_all_versions."""
        mock_client = _install_mock_hvac(monkeypatch)
        from maop.core.backends.backends_vault import VaultSecretBackend
        backend = VaultSecretBackend()
        assert backend.delete_secret("openai") is True
        mock_client.secrets.kv.v2.delete_metadata_and_all_versions.assert_called_once_with(
            path="maop/openai", mount_point="secret",
        )

    def test_vault_delete_secret_not_found(self, monkeypatch):
        """delete_secret returns False when the secret does not exist."""
        mock_client = _install_mock_hvac(monkeypatch)
        mock_client.secrets.kv.v2.delete_metadata_and_all_versions.side_effect = Exception("not found")
        from maop.core.backends.backends_vault import VaultSecretBackend
        backend = VaultSecretBackend()
        assert backend.delete_secret("missing") is False


# ── list_secrets ──────────────────────────────────────────────────

class TestVaultListSecrets:
    def test_vault_list_secrets(self, monkeypatch):
        """list_secrets returns the keys from the KV v2 list response."""
        mock_client = _install_mock_hvac(monkeypatch)
        mock_client.secrets.kv.v2.list_secrets.return_value = {
            "data": {"keys": ["openai", "anthropic"]}
        }
        from maop.core.backends.backends_vault import VaultSecretBackend
        backend = VaultSecretBackend()
        assert backend.list_secrets() == ["openai", "anthropic"]

    def test_vault_list_secrets_strips_slash(self, monkeypatch):
        """Directory entries (trailing /) have the slash stripped."""
        mock_client = _install_mock_hvac(monkeypatch)
        mock_client.secrets.kv.v2.list_secrets.return_value = {
            "data": {"keys": ["openai", "subdir/"]}
        }
        from maop.core.backends.backends_vault import VaultSecretBackend
        backend = VaultSecretBackend()
        result = backend.list_secrets()
        assert "subdir" in result
        assert "subdir/" not in result

    def test_vault_list_secrets_empty(self, monkeypatch):
        """An empty keys list returns []."""
        mock_client = _install_mock_hvac(monkeypatch)
        mock_client.secrets.kv.v2.list_secrets.return_value = {"data": {"keys": []}}
        from maop.core.backends.backends_vault import VaultSecretBackend
        backend = VaultSecretBackend()
        assert backend.list_secrets() == []


# ── _ensure_mount ─────────────────────────────────────────────────

class TestVaultEnsureMount:
    def test_vault_ensure_mount_existing(self, monkeypatch):
        """When the KV mount already exists, enable_secrets_engine is NOT called."""
        mock_client = _install_mock_hvac(
            monkeypatch, mounts={"secret/": {"type": "kv"}}
        )
        from maop.core.backends.backends_vault import VaultSecretBackend
        VaultSecretBackend()
        mock_client.sys.enable_secrets_engine.assert_not_called()

    def test_vault_ensure_mount_creates_new(self, monkeypatch):
        """When the KV mount is missing, enable_secrets_engine is called with kv v2."""
        mock_client = _install_mock_hvac(monkeypatch, mounts={})
        from maop.core.backends.backends_vault import VaultSecretBackend
        VaultSecretBackend()
        mock_client.sys.enable_secrets_engine.assert_called_once_with(
            backend_type="kv", path="secret", options={"version": "2"},
        )


# ── _full_path ────────────────────────────────────────────────────

class TestVaultFullPath:
    def test_vault_full_path(self, monkeypatch):
        """_full_path joins base_path and key with a single slash."""
        _install_mock_hvac(monkeypatch)
        from maop.core.backends.backends_vault import VaultSecretBackend
        backend = VaultSecretBackend()
        assert backend._full_path("openai") == "maop/openai"


# ── close ─────────────────────────────────────────────────────────

class TestVaultClose:
    def test_vault_close(self, monkeypatch):
        """close() delegates to the hvac adapter's close()."""
        mock_client = _install_mock_hvac(monkeypatch)
        from maop.core.backends.backends_vault import VaultSecretBackend
        backend = VaultSecretBackend()
        backend.close()
        mock_client.adapter.close.assert_called_once()


# ── Factory degradation ───────────────────────────────────────────

class TestVaultDegradation:
    def test_get_secret_backend_vault_degrades(self, monkeypatch):
        """When hvac cannot be imported, get_secret_backend() falls back to LocalSecretBackend."""
        from maop.core.backends.backends import LocalSecretBackend, get_secret_backend, reset_backends
        # Ensure hvac is NOT importable (real state: hvac not installed)
        monkeypatch.delitem(sys.modules, "hvac", raising=False)
        monkeypatch.delitem(sys.modules, "maop.core.backends.backends_vault", raising=False)
        monkeypatch.setenv("MAOP_SECRET_BACKEND", "vault")
        reset_backends()
        try:
            backend = get_secret_backend()
            assert isinstance(backend, LocalSecretBackend)
        finally:
            reset_backends()
