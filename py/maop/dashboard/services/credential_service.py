"""Credential Vault service layer.

Extracted from the credential_vault router (§ARCH-H2) so the router
layer only does parameter parsing + permission check + service call +
response formatting + error handling.

The service is intentionally framework-agnostic: it does not import
FastAPI and can be unit-tested or invoked without an HTTP context.
Business errors are raised as plain Python exceptions (``KeyError``,
``ValueError``) and the router layer translates them into HTTPException
responses.

Modules covered:
    - credential_vault.py → CredentialVault singleton + CRUD/rotate/audit
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
# CredentialVault singleton (double-checked locking)
# ════════════════════════════════════════════════════════════════════

_vault: Any = None
_vault_lock = threading.Lock()


def get_vault() -> Any:
    """惰性初始化全局 CredentialVault 单例。"""
    global _vault
    if _vault is None:
        with _vault_lock:
            if _vault is None:
                from maop.core.agent.auth.credential_vault import CredentialVault
                _vault = CredentialVault()
    return _vault


def set_vault(vault: Any) -> None:
    """供测试注入自定义 vault（隔离 DB）。"""
    global _vault
    with _vault_lock:
        _vault = vault


# ════════════════════════════════════════════════════════════════════
# Credential operations
# ════════════════════════════════════════════════════════════════════


def list_credentials() -> list:
    """列出所有凭证摘要（掩码预览，不含敏感字段明文）。

    Returns list of model dicts (``mode="json"``).
    """
    vault = get_vault()
    summaries = vault.list_all()
    return [s.model_dump(mode="json") for s in summaries]


def get_credential(credential_id: str, *, actor: str = "api") -> dict:
    """获取单个凭证详情（含解密后的敏感字段）。

    Raises ``KeyError`` if not found.
    """
    vault = get_vault()
    cred = vault.retrieve(credential_id, actor=actor)
    return cred.model_dump(mode="json")


def create_credential(
    *,
    credential_type: str,
    agent_name: str,
    name: str = "",
    api_key: str = "",
    oauth_access_token: str = "",
    oauth_refresh_token: str = "",
    oauth_expires_at: float = 0.0,
    username: str = "",
    password: str = "",
    license_key: str = "",
    cookie_string: str = "",
    bearer_token: str = "",
    created_by: str = "api",
) -> str:
    """创建新凭证（加密后落盘）。Returns the new credential_id.

    Raises ``ValueError`` if credential_type is invalid.
    """
    from maop.core.agent.auth.credential_vault import Credential, CredentialType

    try:
        ct = CredentialType(credential_type)
    except ValueError:
        raise ValueError(f"Invalid credential_type: {credential_type}")

    cred = Credential(
        agent_name=agent_name,
        credential_type=ct,
        api_key=api_key,
        oauth_access_token=oauth_access_token,
        oauth_refresh_token=oauth_refresh_token,
        oauth_expires_at=oauth_expires_at,
        username=username,
        password=password,
        license_key=license_key,
        cookie_string=cookie_string,
        bearer_token=bearer_token,
        extra={"name": name} if name else {},
    )
    vault = get_vault()
    cred_id = vault.store(cred, created_by=created_by)
    logger.info(
        "[credential_service] created credential id=%s agent=%s type=%s",
        cred_id, agent_name, ct.value,
    )
    return cred_id


def delete_credential(credential_id: str, *, actor: str = "api") -> bool:
    """删除凭证。Returns True if deleted, False if not found."""
    vault = get_vault()
    return vault.delete(credential_id, actor=actor)


def rotate_credential(credential_id: str, *, actor: str = "api") -> str:
    """轮换凭证（生成新随机秘密替换原值）。Returns the new credential_id.

    Raises ``KeyError`` if not found.
    """
    vault = get_vault()
    return vault.rotate(credential_id, actor=actor)


def get_credential_audit(
    *,
    credential_id: str = "",
    limit: int = 100,
) -> list:
    """查询凭证操作审计日志（按时间倒序）。"""
    vault = get_vault()
    return vault.get_audit_log(credential_id=credential_id, limit=limit)