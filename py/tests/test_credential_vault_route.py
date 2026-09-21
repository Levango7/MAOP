"""Credential Vault 路由层测试。

覆盖 credential_vault.py 的 6 个端点的正常路径 + 错误路径。
使用隔离 tmp_path DB + monkeypatch require_admin 为 no-op。

经验来源：
- 2026-09-12-fastapi-require-admin-guard-test-adaptation-by-call-type：
  conftest 设 MAOP_AUTH=0 授予 read-only 角色，需 monkeypatch require_admin
  为 no-op 绕过 admin 守卫。
- 2026-09-12-pydantic-basemodel-migration-422-vs-400-test-compatibility：
  Pydantic v2 BaseModel 校验失败默认返回 422；本测试验证路由层
  自定义 400 错误（invalid credential_type）优先于 Pydantic 校验。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.agent.auth.credential_vault import (
    Credential,
    CredentialType,
    CredentialVault,
)

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def vault(tmp_path: Path) -> CredentialVault:
    """使用隔离 tmp_path DB 的全新 CredentialVault。"""
    return CredentialVault(db_path=tmp_path / "test_credential_vault_route.db")


@pytest.fixture
def app(vault: CredentialVault, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """创建临时 FastAPI app，注入隔离 vault + stub require_admin。"""
    import maop.dashboard.routers.credential_vault as route_mod

    route_mod._set_vault(vault)
    monkeypatch.setattr(route_mod, "require_admin", lambda request: None)

    app = FastAPI()
    app.include_router(route_mod.router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _create_sample_credential(vault: CredentialVault) -> str:
    """创建一个测试用 API Key 凭证，返回 credential_id。"""
    cred = Credential(
        agent_name="claude-code",
        credential_type=CredentialType.API_KEY,
        api_key="sk-test-12345678",
    )
    return vault.store(cred, created_by="test")


# ── 1. GET /api/credential-vault/credentials ─────────────────────


class TestListCredentials:
    """GET /api/credential-vault/credentials 端点测试。"""

    def test_list_empty(self, client: TestClient) -> None:
        """空 vault 返回空列表。"""
        resp = client.get("/api/credential-vault/credentials")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["credentials"] == []
        assert data["count"] == 0

    def test_list_with_credentials(self, client: TestClient, vault: CredentialVault) -> None:
        """列出已有凭证（掩码预览）。"""
        _create_sample_credential(vault)
        resp = client.get("/api/credential-vault/credentials")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        cred = data["credentials"][0]
        assert cred["agent_name"] == "claude-code"
        assert cred["credential_type"] == "api_key"
        # 预览应为掩码，不含完整明文
        assert "sk-test-12345678" not in cred["preview"]
        assert "*" in cred["preview"]


# ── 2. GET /api/credential-vault/credentials/{id} ────────────────


class TestGetCredential:
    """GET /api/credential-vault/credentials/{id} 端点测试。"""

    def test_get_existing(self, client: TestClient, vault: CredentialVault) -> None:
        """获取存在的凭证（含解密敏感字段）。"""
        cred_id = _create_sample_credential(vault)
        resp = client.get(f"/api/credential-vault/credentials/{cred_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["credential"]["agent_name"] == "claude-code"
        assert data["credential"]["api_key"] == "sk-test-12345678"

    def test_get_not_found(self, client: TestClient) -> None:
        """获取不存在的凭证返回 404。"""
        resp = client.get("/api/credential-vault/credentials/nonexistent-id")
        assert resp.status_code == 404
        assert resp.json()["status"] == "error"


# ── 3. POST /api/credential-vault/credentials ────────────────────


class TestCreateCredential:
    """POST /api/credential-vault/credentials 端点测试。"""

    def test_create_api_key(self, client: TestClient) -> None:
        """创建 API Key 凭证。"""
        resp = client.post("/api/credential-vault/credentials", json={
            "credential_type": "api_key",
            "name": "test-key",
            "agent_name": "test-agent",
            "api_key": "sk-new-key-12345",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "credential_id" in data
        assert len(data["credential_id"]) > 0

    def test_create_bearer_token(self, client: TestClient) -> None:
        """创建 Bearer Token 凭证。"""
        resp = client.post("/api/credential-vault/credentials", json={
            "credential_type": "bearer_token",
            "agent_name": "test-agent",
            "bearer_token": "Bearer abc123",
        })
        assert resp.status_code == 200

    def test_create_oauth(self, client: TestClient) -> None:
        """创建 OAuth 凭证。"""
        resp = client.post("/api/credential-vault/credentials", json={
            "credential_type": "oauth_token",
            "agent_name": "test-agent",
            "oauth_access_token": "access-123",
            "oauth_refresh_token": "refresh-456",
            "oauth_expires_at": 9999999999.0,
        })
        assert resp.status_code == 200

    def test_create_username_password(self, client: TestClient) -> None:
        """创建用户名密码凭证。"""
        resp = client.post("/api/credential-vault/credentials", json={
            "credential_type": "username_password",
            "agent_name": "test-agent",
            "username": "admin",
            "password": "secret123",
        })
        assert resp.status_code == 200

    def test_create_invalid_type(self, client: TestClient) -> None:
        """无效凭证类型返回 400。"""
        resp = client.post("/api/credential-vault/credentials", json={
            "credential_type": "invalid_type",
            "agent_name": "test-agent",
        })
        assert resp.status_code == 400
        assert resp.json()["status"] == "error"

    def test_create_missing_agent_name(self, client: TestClient) -> None:
        """缺少 agent_name 返回 422（Pydantic 校验）。"""
        resp = client.post("/api/credential-vault/credentials", json={
            "credential_type": "api_key",
            "api_key": "sk-key",
        })
        assert resp.status_code == 422


# ── 4. DELETE /api/credential-vault/credentials/{id} ─────────────


class TestDeleteCredential:
    """DELETE /api/credential-vault/credentials/{id} 端点测试。"""

    def test_delete_existing(self, client: TestClient, vault: CredentialVault) -> None:
        """删除存在的凭证。"""
        cred_id = _create_sample_credential(vault)
        resp = client.delete(f"/api/credential-vault/credentials/{cred_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == cred_id
        # 确认已删除
        resp2 = client.get("/api/credential-vault/credentials")
        assert resp2.json()["count"] == 0

    def test_delete_not_found(self, client: TestClient) -> None:
        """删除不存在的凭证返回 404。"""
        resp = client.delete("/api/credential-vault/credentials/nonexistent-id")
        assert resp.status_code == 404


# ── 5. POST /api/credential-vault/credentials/{id}/rotate ────────


class TestRotateCredential:
    """POST /api/credential-vault/credentials/{id}/rotate 端点测试。"""

    def test_rotate_api_key(self, client: TestClient, vault: CredentialVault) -> None:
        """轮换 API Key 凭证。"""
        cred_id = _create_sample_credential(vault)
        # 获取原始 api_key
        resp_before = client.get(f"/api/credential-vault/credentials/{cred_id}")
        original_key = resp_before.json()["credential"]["api_key"]

        # 轮换
        resp = client.post(f"/api/credential-vault/credentials/{cred_id}/rotate")
        assert resp.status_code == 200
        assert resp.json()["credential_id"] == cred_id

        # 获取轮换后的 api_key，应不同
        resp_after = client.get(f"/api/credential-vault/credentials/{cred_id}")
        new_key = resp_after.json()["credential"]["api_key"]
        assert new_key != original_key

    def test_rotate_not_found(self, client: TestClient) -> None:
        """轮换不存在的凭证返回 404。"""
        resp = client.post("/api/credential-vault/credentials/nonexistent-id/rotate")
        assert resp.status_code == 404


# ── 6. GET /api/credential-vault/audit ───────────────────────────


class TestAuditLog:
    """GET /api/credential-vault/audit 端点测试。"""

    def test_audit_empty(self, client: TestClient) -> None:
        """空 vault 审计日志为空。"""
        resp = client.get("/api/credential-vault/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["logs"] == []
        assert data["count"] == 0

    def test_audit_after_create(self, client: TestClient, vault: CredentialVault) -> None:
        """创建凭证后审计日志有记录。"""
        _create_sample_credential(vault)
        resp = client.get("/api/credential-vault/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        # 最新一条应为 store 操作
        assert data["logs"][0]["action"] == "store"

    def test_audit_filter_by_credential(self, client: TestClient, vault: CredentialVault) -> None:
        """按 credential_id 过滤审计日志。"""
        cred_id = _create_sample_credential(vault)
        resp = client.get(f"/api/credential-vault/audit?credential_id={cred_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        for log in data["logs"]:
            assert log["credential_id"] == cred_id

    def test_audit_with_limit(self, client: TestClient, vault: CredentialVault) -> None:
        """limit 参数限制返回数量。"""
        _create_sample_credential(vault)
        resp = client.get("/api/credential-vault/audit?limit=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] <= 1