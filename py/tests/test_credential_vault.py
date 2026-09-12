"""Tests for CredentialVault — 凭证保险库加密存储 / 审计 / 轮换 / 降级。

覆盖：
  * 存储 + 读取往返（各凭证类型）
  * 敏感字段加密验证（密文不含明文）
  * list_all 不泄露敏感字段（掩码预览）
  * 删除
  * 轮换（各类型）
  * test() 测试方法
  * 审计日志记录
  * 优雅降级（cryptography 不可用）
  * 密钥从环境变量读取
  * 不存在凭证的 KeyError
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from maop.core.agent.auth.credential_vault import (
    Credential,
    CredentialSummary,
    CredentialType,
    CredentialVault,
    _CryptoEngine,
)

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def vault(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> CredentialVault:
    """隔离的 CredentialVault（独立 DB + 密钥目录）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MAOP_DATA_DIR", str(data_dir))
    monkeypatch.delenv("MAOP_CREDENTIAL_KEY", raising=False)
    v = CredentialVault(db_path=tmp_path / "vault.db")
    yield v
    v.close()


@pytest.fixture
def vault_with_env_key(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> CredentialVault:
    """使用环境变量密钥的 CredentialVault。"""
    monkeypatch.setenv("MAOP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MAOP_CREDENTIAL_KEY", "test-env-secret-key-1234567890")
    v = CredentialVault(db_path=tmp_path / "vault_env.db")
    yield v
    v.close()


# ── 辅助 ──────────────────────────────────────────────────────────


def _api_key_cred(agent: str = "agent-1", key: str = "sk-abcd1234efgh5678") -> Credential:
    return Credential(
        agent_name=agent,
        credential_type=CredentialType.API_KEY,
        api_key=key,
    )


def _oauth_cred(agent: str = "agent-oauth") -> Credential:
    return Credential(
        agent_name=agent,
        credential_type=CredentialType.OAUTH_TOKEN,
        oauth_access_token="ya29.access-token-value",
        oauth_refresh_token="1//refresh-token-secret",
        oauth_expires_at=time.time() + 3600,
    )


def _user_pass_cred(agent: str = "agent-up") -> Credential:
    return Credential(
        agent_name=agent,
        credential_type=CredentialType.USERNAME_PASSWORD,
        username="admin",
        password="S3cretP@ss!",
    )


# ── 1. 存储 + 读取往返 ────────────────────────────────────────────


class TestStoreRetrieve:
    def test_store_returns_id(self, vault: CredentialVault):
        cred = _api_key_cred()
        cid = vault.store(cred)
        assert isinstance(cid, str) and len(cid) > 0

    def test_retrieve_api_key_roundtrip(self, vault: CredentialVault):
        cred = _api_key_cred(key="sk-test-12345678")
        cid = vault.store(cred)
        got = vault.retrieve(cid)
        assert got.api_key == "sk-test-12345678"
        assert got.agent_name == "agent-1"
        assert got.credential_type == CredentialType.API_KEY

    def test_retrieve_oauth_roundtrip(self, vault: CredentialVault):
        cred = _oauth_cred()
        cid = vault.store(cred)
        got = vault.retrieve(cid)
        assert got.oauth_access_token == "ya29.access-token-value"
        assert got.oauth_refresh_token == "1//refresh-token-secret"
        assert got.oauth_expires_at > 0

    def test_retrieve_username_password_roundtrip(self, vault: CredentialVault):
        cred = _user_pass_cred()
        cid = vault.store(cred)
        got = vault.retrieve(cid)
        assert got.username == "admin"
        assert got.password == "S3cretP@ss!"

    def test_retrieve_nonexistent_raises_keyerror(self, vault: CredentialVault):
        with pytest.raises(KeyError):
            vault.retrieve("nonexistent-id")


# ── 2. 加密验证 ────────────────────────────────────────────────────


class TestEncryption:
    def test_encrypted_data_not_plaintext(self, vault: CredentialVault):
        secret = "sk-super-secret-key-9999"
        cid = vault.store(_api_key_cred(key=secret))
        # 直接查 DB，确认密文不含明文。
        conn = sqlite3.connect(str(vault.db_path))
        row = conn.execute(
            "SELECT encrypted_data FROM credential_vault WHERE id = ?", (cid,)
        ).fetchone()
        conn.close()
        assert row is not None
        encrypted = row[0]
        assert secret not in encrypted

    def test_extra_dict_encrypted(self, vault: CredentialVault):
        cred = Credential(
            agent_name="agent-extra",
            credential_type=CredentialType.API_KEY,
            api_key="sk-key",
            extra={"region": "us-east-1", "project": "secret-project"},
        )
        cid = vault.store(cred)
        got = vault.retrieve(cid)
        assert got.extra["region"] == "us-east-1"
        assert got.extra["project"] == "secret-project"

    def test_env_key_produces_consistent_crypto(self, vault_with_env_key: CredentialVault):
        """同一环境密钥应能解密自己加密的数据。"""
        cred = _api_key_cred(key="sk-env-consistency")
        cid = vault_with_env_key.store(cred)
        got = vault_with_env_key.retrieve(cid)
        assert got.api_key == "sk-env-consistency"


# ── 3. list_all 不泄露敏感字段 ─────────────────────────────────────


class TestListAll:
    def test_list_all_returns_summary(self, vault: CredentialVault):
        vault.store(_api_key_cred(key="sk-listtest-12345678"))
        items = vault.list_all()
        assert len(items) == 1
        assert isinstance(items[0], CredentialSummary)

    def test_list_all_no_sensitive_fields(self, vault: CredentialVault):
        secret = "sk-do-not-leak-1234567890"
        vault.store(_api_key_cred(key=secret))
        vault.store(_user_pass_cred())
        items = vault.list_all()
        # 摘要对象不应有敏感字段属性。
        for item in items:
            assert not hasattr(item, "api_key")
            assert not hasattr(item, "password")
            assert not hasattr(item, "oauth_access_token")
        # 序列化 JSON 也不应包含明文秘密。
        blob = json.dumps([i.model_dump() for i in items])
        assert secret not in blob
        assert "S3cretP@ss!" not in blob

    def test_list_all_preview_is_masked(self, vault: CredentialVault):
        vault.store(_api_key_cred(key="sk-abcd1234efgh5678"))
        items = vault.list_all()
        preview = items[0].preview
        assert "****" in preview
        # 预览不应包含完整密钥。
        assert preview != "sk-abcd1234efgh5678"

    def test_list_all_empty(self, vault: CredentialVault):
        assert vault.list_all() == []


# ── 4. 删除 ────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_existing(self, vault: CredentialVault):
        cid = vault.store(_api_key_cred())
        assert vault.delete(cid) is True
        assert vault.list_all() == []

    def test_delete_nonexistent_returns_false(self, vault: CredentialVault):
        assert vault.delete("no-such-id") is False


# ── 5. 轮换 ────────────────────────────────────────────────────────


class TestRotate:
    def test_rotate_api_key_changes_secret(self, vault: CredentialVault):
        cid = vault.store(_api_key_cred(key="sk-original-12345678"))
        vault.rotate(cid)
        got = vault.retrieve(cid)
        assert got.api_key != "sk-original-12345678"
        assert len(got.api_key) > 0

    def test_rotate_oauth_clears_tokens(self, vault: CredentialVault):
        cid = vault.store(_oauth_cred())
        vault.rotate(cid)
        got = vault.retrieve(cid)
        assert got.oauth_access_token == ""
        assert got.oauth_refresh_token == ""

    def test_rotate_returns_same_id(self, vault: CredentialVault):
        cid = vault.store(_api_key_cred())
        rotated_id = vault.rotate(cid)
        assert rotated_id == cid


# ── 6. test() 方法 ────────────────────────────────────────────────


class TestCredentialTest:
    def test_test_passes_for_valid_credential(self, vault: CredentialVault):
        cid = vault.store(_api_key_cred(key="sk-valid"))
        assert vault.test(cid) is True

    def test_test_updates_last_test_fields(self, vault: CredentialVault):
        cid = vault.store(_api_key_cred(key="sk-valid"))
        vault.test(cid)
        items = vault.list_all()
        assert items[0].last_tested_at > 0
        assert items[0].last_test_result == 1


# ── 7. 审计日志 ────────────────────────────────────────────────────


class TestAuditLog:
    def test_store_logs_audit(self, vault: CredentialVault):
        cid = vault.store(_api_key_cred(), created_by="admin-1")
        logs = vault.get_audit_log(cid)
        assert any(l["action"] == "store" and l["actor"] == "admin-1" for l in logs)

    def test_retrieve_logs_audit(self, vault: CredentialVault):
        cid = vault.store(_api_key_cred())
        vault.retrieve(cid, actor="agent-runner")
        logs = vault.get_audit_log(cid)
        assert any(l["action"] == "retrieve" and l["actor"] == "agent-runner" for l in logs)

    def test_delete_logs_audit(self, vault: CredentialVault):
        cid = vault.store(_api_key_cred())
        vault.delete(cid, actor="admin-2")
        logs = vault.get_audit_log(cid)
        assert any(l["action"] == "delete" and l["actor"] == "admin-2" for l in logs)

    def test_rotate_logs_audit(self, vault: CredentialVault):
        cid = vault.store(_api_key_cred())
        vault.rotate(cid, actor="rotator")
        logs = vault.get_audit_log(cid)
        assert any(l["action"] == "rotate" for l in logs)


# ── 8. 优雅降级 ────────────────────────────────────────────────────


class TestDegradation:
    def test_crypto_engine_degrades_without_cryptography(self):
        """cryptography 导入失败时降级模式仍可加解密往返。"""
        with patch.dict("sys.modules", {"cryptography.fernet": None}):
            # 强制触发 ImportError。
            import builtins
            real_import = builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name.startswith("cryptography"):
                    raise ImportError("simulated absence")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=fake_import):
                engine = _CryptoEngine(b"degrade-test-key")
                assert engine.degraded is True
                token = engine.encrypt("hello-secret")
                assert engine.decrypt(token) == "hello-secret"

    def test_degraded_mode_not_plaintext(self):
        """降级模式密文也不应等于明文。"""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("cryptography"):
                raise ImportError("simulated")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            engine = _CryptoEngine(b"key")
            token = engine.encrypt("my-secret")
            assert token != "my-secret"
            assert token.startswith("DEG::")


# ── 9. 密钥管理 ────────────────────────────────────────────────────


class TestKeyManagement:
    def test_key_file_created_when_no_env(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setenv("MAOP_DATA_DIR", str(data_dir))
        monkeypatch.delenv("MAOP_CREDENTIAL_KEY", raising=False)
        v = CredentialVault(db_path=tmp_path / "k.db")
        v.close()
        assert (data_dir / "credential.key").exists()

    def test_env_key_takes_priority(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MAOP_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MAOP_CREDENTIAL_KEY", "env-priority-key")
        v = CredentialVault(db_path=tmp_path / "env.db")
        v.close()
        # 环境变量存在时不应写密钥文件。
        assert not (tmp_path / "credential.key").exists()