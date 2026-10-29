"""MAOP Dashboard — Credential Vault API 的响应模型.

凭证摘要直接复用领域模型 ``CredentialSummary``：``credential_service.list_credentials()``
返回的就是它的 ``model_dump(mode="json")``（``CredentialType`` 是 str-Enum，落 JSON 后是
普通字符串），另写一份 11 字段的镜像只会制造第二份真相——加字段时漏改一处就静默丢数据。

这四条路由都带 ``error_value=``：非预期异常时那个降级 dict 同样会过 response_model
（只有 HTTPException 分支返回 JSONResponse 而绕过它），所以成功侧的键全部给默认值、
``error`` 显式声明，否则兜底分支自己会变成 500。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from maop.core.agent.auth.credential_vault import CredentialSummary


class CredentialListEnvelope(BaseModel):
    """GET /api/credential-vault/credentials 响应（掩码摘要，不含明文）。"""

    status: str = "error"
    credentials: list[CredentialSummary] = Field(default_factory=list)
    count: int = 0
    error: str = ""


class CredentialCreatedEnvelope(BaseModel):
    """POST /api/credential-vault/credentials 响应。"""

    status: str = "error"
    credential_id: str = ""
    error: str = ""


class CredentialDeletedEnvelope(BaseModel):
    """DELETE /api/credential-vault/credentials/{credential_id} 响应。"""

    status: str = "error"
    deleted: str = ""
    error: str = ""


class CredentialRotatedEnvelope(BaseModel):
    """POST /api/credential-vault/credentials/{credential_id}/rotate 响应。

    ``rotate`` 原地替换秘密并返回同一个 id，所以这里与创建侧的 ``credential_id``
    语义不同：轮换不会产生新 id。
    """

    status: str = "error"
    credential_id: str = ""
    error: str = ""


__all__ = [
    "CredentialCreatedEnvelope",
    "CredentialDeletedEnvelope",
    "CredentialListEnvelope",
    "CredentialRotatedEnvelope",
]
