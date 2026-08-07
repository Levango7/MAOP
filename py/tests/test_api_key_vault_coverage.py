"""Coverage tests for maop.core.api_key_vault — store/retrieve/delete/rotate."""
from __future__ import annotations


import pytest

from maop.core.security.api_key_vault import ApiKeyVault


def _has_crypto():
    try:
        import cryptography  # noqa: F401
        return True
    except ImportError:
        return False


skip_no_crypto = pytest.mark.skipif(not _has_crypto(), reason="cryptography not installed")


@skip_no_crypto
class TestApiKeyVault:
    def test_store_and_retrieve(self, tmp_path):
        vault = ApiKeyVault(root_dir=tmp_path)
        vault.store("openai", "sk-test-123")
        assert vault.retrieve("openai") == "sk-test-123"

    def test_retrieve_nonexistent_returns_none(self, tmp_path):
        vault = ApiKeyVault(root_dir=tmp_path)
        assert vault.retrieve("nope") is None

    def test_store_overwrite(self, tmp_path):
        vault = ApiKeyVault(root_dir=tmp_path)
        vault.store("openai", "sk-old")
        vault.store("openai", "sk-new")
        assert vault.retrieve("openai") == "sk-new"

    def test_delete_existing(self, tmp_path):
        vault = ApiKeyVault(root_dir=tmp_path)
        vault.store("openai", "sk-test")
        assert vault.delete("openai") is True
        assert vault.retrieve("openai") is None

    def test_delete_nonexistent_returns_false(self, tmp_path):
        vault = ApiKeyVault(root_dir=tmp_path)
        assert vault.delete("nope") is False

    def test_list_providers_empty(self, tmp_path):
        vault = ApiKeyVault(root_dir=tmp_path)
        assert vault.list_providers() == []

    def test_list_providers_sorted(self, tmp_path):
        vault = ApiKeyVault(root_dir=tmp_path)
        vault.store("zoo", "k1")
        vault.store("alpha", "k2")
        vault.store("mid", "k3")
        assert vault.list_providers() == ["alpha", "mid", "zoo"]

    def test_rotate_master_key_auto_generate(self, tmp_path):
        vault = ApiKeyVault(root_dir=tmp_path)
        vault.store("openai", "sk-test")
        assert vault.rotate_master_key() is True
        # Key should still be retrievable after rotation
        assert vault.retrieve("openai") == "sk-test"

    def test_rotate_master_key_with_explicit_key(self, tmp_path):
        from cryptography.fernet import Fernet
        vault = ApiKeyVault(root_dir=tmp_path)
        vault.store("openai", "sk-test")
        new_key = Fernet.generate_key()
        assert vault.rotate_master_key(new_key=new_key) is True
        assert vault.retrieve("openai") == "sk-test"

    def test_rotate_master_key_with_string_key(self, tmp_path):
        from cryptography.fernet import Fernet
        vault = ApiKeyVault(root_dir=tmp_path)
        vault.store("openai", "sk-test")
        new_key = Fernet.generate_key()
        assert vault.rotate_master_key(new_key=new_key.decode()) is True
        assert vault.retrieve("openai") == "sk-test"

    def test_rotate_master_key_empty_vault(self, tmp_path):
        vault = ApiKeyVault(root_dir=tmp_path)
        assert vault.rotate_master_key() is True

    def test_rotate_master_key_invalid_key_returns_false(self, tmp_path):
        vault = ApiKeyVault(root_dir=tmp_path)
        vault.store("openai", "sk-test")
        assert vault.rotate_master_key(new_key=b"invalid") is False

    def test_maop_key_env_var(self, tmp_path, monkeypatch):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        monkeypatch.setenv("MAOP_KEY", key.decode())
        vault = ApiKeyVault(root_dir=tmp_path)
        vault.store("openai", "sk-test")
        assert vault.retrieve("openai") == "sk-test"

    def test_maop_key_file_env_var(self, tmp_path, monkeypatch):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        key_file = tmp_path / "custom_key"
        key_file.write_bytes(key)
        monkeypatch.setenv("MAOP_KEY_FILE", str(key_file))
        vault = ApiKeyVault(root_dir=tmp_path)
        vault.store("openai", "sk-test")
        assert vault.retrieve("openai") == "sk-test"


class TestApiKeyVaultNoCrypto:
    """Test behavior when cryptography is not available (mocked out)."""

    def test_encrypt_without_crypto_raises(self, tmp_path, monkeypatch):
        # Force ImportError for cryptography.fernet
        original_import = __import__

        def _fake_import(name, *args, **kwargs):
            if name == "cryptography.fernet" or name == "cryptography":
                raise ImportError("no crypto")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", _fake_import)
        monkeypatch.delenv("MAOP_KEY", raising=False)

        vault = ApiKeyVault(root_dir=tmp_path)
        assert vault._fernet is None
        with pytest.raises(RuntimeError, match="cryptography library required"):
            vault.store("openai", "sk-test")

    def test_rotate_without_crypto_returns_false(self, tmp_path, monkeypatch):
        original_import = __import__

        def _fake_import(name, *args, **kwargs):
            if name == "cryptography.fernet" or name == "cryptography":
                raise ImportError("no crypto")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", _fake_import)
        monkeypatch.delenv("MAOP_KEY", raising=False)

        vault = ApiKeyVault(root_dir=tmp_path)
        assert vault.rotate_master_key() is False