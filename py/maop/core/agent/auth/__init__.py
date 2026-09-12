"""Agent authorization subpackage — credential vault for unified secret management.

为所有 Agent 提供统一的授权凭证管理（API Key / OAuth / 账号密码 / License /
Cookie / Bearer Token），敏感字段 AES 加密存储，审计日志记录每次访问。

Modules:
    credential_vault — CredentialVault 凭证保险库（加密存储 / 审计 / 轮换 / 测试）
"""
from __future__ import annotations

from maop.core.agent.auth.credential_vault import (
    Credential,
    CredentialType,
    CredentialVault,
    CredentialSummary,
)

__all__ = [
    "Credential",
    "CredentialType",
    "CredentialVault",
    "CredentialSummary",
]