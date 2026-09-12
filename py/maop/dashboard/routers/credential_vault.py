"""MAOP Dashboard — Credential Vault API endpoints.

暴露 ``CredentialVault`` 凭证保险库 via REST API，供桌面应用调度层
管理 Agent 授权凭证（创建、查询、删除、轮换、审计日志）。

所有端点要求 admin 角色（via ``require_admin`` 守卫）。
敏感字段绝不通过 API 返回明文——list 端点仅返回掩码预览。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 ──────────────────────────────────────────────
class CreateCredentialRequest(BaseModel):
    """POST /api/credential-vault/credentials 请求体。

    支持五种凭证类型 + Bearer Token，敏感字段经 CredentialVault 加密后落盘。
    ``name`` 为凭证显示名称（存储在 extra 中）。
    """

    credential_type: str = Field(..., description="凭证类型")
    name: str = Field(default="", max_length=256, description="凭证显示名称")
    agent_name: str = Field(..., min_length=1, max_length=200, description="关联 Agent 名")
    # 可选敏感字段
    api_key: str = Field(default="", description="API Key")
    oauth_access_token: str = Field(default="", description="OAuth Access Token")
    oauth_refresh_token: str = Field(default="", description="OAuth Refresh Token")
    oauth_expires_at: float = Field(default=0.0, description="OAuth 过期时间戳")
    username: str = Field(default="", description="用户名")
    password: str = Field(default="", description="密码")
    license_key: str = Field(default="", description="License Key")
    cookie_string: str = Field(default="", description="Cookie 字符串")
    bearer_token: str = Field(default="", description="Bearer Token")


# ── 单例（双重检查锁定）────────────────────────────────────────────
_vault: Any = None
_vault_lock = threading.Lock()


def _get_vault() -> Any:
    """惰性初始化全局 CredentialVault 单例。"""
    global _vault
    if _vault is None:
        with _vault_lock:
            if _vault is None:
                from maop.core.agent.auth.credential_vault import CredentialVault
                _vault = CredentialVault()
    return _vault


def _set_vault(vault: Any) -> None:
    """供测试注入自定义 vault（隔离 DB）。"""
    global _vault
    with _vault_lock:
        _vault = vault


# ── 端点 ──────────────────────────────────────────────────────────
@router.get("/api/credential-vault/credentials")
@handle_api_errors(
    "List credentials",
    error_value={"status": "error", "credentials": [], "count": 0, "error": "List failed"},
)
async def api_list_credentials(request: Request) -> dict[str, Any]:
    """列出所有凭证摘要（掩码预览，不含敏感字段明文）。"""
    require_admin(request)
    vault = _get_vault()
    summaries = vault.list_all()
    return {
        "status": "ok",
        "credentials": [s.model_dump(mode="json") for s in summaries],
        "count": len(summaries),
    }


@router.get("/api/credential-vault/credentials/{credential_id}")
@handle_api_errors(
    "Get credential",
    error_value={"status": "error", "credential": None, "error": "Not found"},
)
async def api_get_credential(credential_id: str, request: Request) -> dict[str, Any]:
    """获取单个凭证详情（含解密后的敏感字段，仅 admin 可访问）。"""
    require_admin(request)
    vault = _get_vault()
    try:
        cred = vault.retrieve(credential_id, actor="api")
    except KeyError:
        raise HTTPException(404, f"Credential not found: {credential_id}")
    return {"status": "ok", "credential": cred.model_dump(mode="json")}


@router.post("/api/credential-vault/credentials")
@handle_api_errors(
    "Create credential",
    error_value={"status": "error", "error": "Create failed"},
)
async def api_create_credential(
    body: CreateCredentialRequest, request: Request
) -> dict[str, Any]:
    """创建新凭证（加密后落盘）。"""
    require_admin(request)
    from maop.core.agent.auth.credential_vault import Credential, CredentialType

    try:
        ct = CredentialType(body.credential_type)
    except ValueError:
        raise HTTPException(400, f"Invalid credential_type: {body.credential_type}")

    cred = Credential(
        agent_name=body.agent_name,
        credential_type=ct,
        api_key=body.api_key,
        oauth_access_token=body.oauth_access_token,
        oauth_refresh_token=body.oauth_refresh_token,
        oauth_expires_at=body.oauth_expires_at,
        username=body.username,
        password=body.password,
        license_key=body.license_key,
        cookie_string=body.cookie_string,
        bearer_token=body.bearer_token,
        extra={"name": body.name} if body.name else {},
    )
    vault = _get_vault()
    cred_id = vault.store(cred, created_by="api")
    logger.info(
        "[credential_vault_route] created credential id=%s agent=%s type=%s",
        cred_id, body.agent_name, ct.value,
    )
    return {"status": "ok", "credential_id": cred_id}


@router.delete("/api/credential-vault/credentials/{credential_id}")
@handle_api_errors(
    "Delete credential",
    error_value={"status": "error", "error": "Delete failed"},
)
async def api_delete_credential(credential_id: str, request: Request) -> dict[str, Any]:
    """删除凭证。"""
    require_admin(request)
    vault = _get_vault()
    deleted = vault.delete(credential_id, actor="api")
    if not deleted:
        raise HTTPException(404, f"Credential not found: {credential_id}")
    return {"status": "ok", "deleted": credential_id}


@router.post("/api/credential-vault/credentials/{credential_id}/rotate")
@handle_api_errors(
    "Rotate credential",
    error_value={"status": "error", "error": "Rotate failed"},
)
async def api_rotate_credential(credential_id: str, request: Request) -> dict[str, Any]:
    """轮换凭证（生成新随机秘密替换原值）。"""
    require_admin(request)
    vault = _get_vault()
    try:
        new_id = vault.rotate(credential_id, actor="api")
    except KeyError:
        raise HTTPException(404, f"Credential not found: {credential_id}")
    return {"status": "ok", "credential_id": new_id}


@router.get("/api/credential-vault/audit")
@handle_api_errors(
    "Credential audit log",
    error_value={"status": "error", "logs": [], "count": 0, "error": "Query failed"},
)
async def api_credential_audit(
    request: Request,
    credential_id: str = "",  # noqa: B008
    limit: int = 100,  # noqa: B008
) -> dict[str, Any]:
    """查询凭证操作审计日志（按时间倒序）。"""
    require_admin(request)
    vault = _get_vault()
    capped_limit = max(1, min(int(limit), 1000))
    logs = vault.get_audit_log(credential_id=credential_id, limit=capped_limit)
    return {"status": "ok", "logs": logs, "count": len(logs)}