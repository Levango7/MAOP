"""企业许可证路由（``/api/licenses``）的响应模型。

镜像 ``maop.enterprise.license_manager.LicenseRecord`` 的 ``model_dump()`` 形状。
企业包在纯净 CI 检出里不存在（ADR-017 双仓），所以模型留在仓内，
``tests/test_enterprise_router_contracts.py`` 在装了企业包的环境里逐字段对账。

条目模型开 ``extra="allow"``：形状持有者在另一个仓库，新增字段必须原样透传给
前端（严格模型会静默丢键）；改名或删必填字段则因缺键变 500，属响亮失败。

信封的 ``status`` 默认值取 ``"error"``（同 ``tenant_schemas``）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LicenseItem(BaseModel):
    """``LicenseRecord`` 的线缆形状（含签名后的 ``license_key``）。"""

    model_config = ConfigDict(extra="allow")

    license_id: str
    customer: str
    edition: str = "enterprise"
    max_users: int | None = None
    fingerprint: str | None = None
    features: list[str] = Field(default_factory=list)
    status: str = "active"
    issued_at: float
    expires_at: float
    revoked_at: float | None = None
    revoked_reason: str = ""
    license_key: str
    issued_by: str = ""
    notes: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    deleted_at: float | None = None


class LicenseListEnvelope(BaseModel):
    status: str = "error"
    licenses: list[LicenseItem] = Field(default_factory=list)
    count: int = 0


class LicenseEnvelope(BaseModel):
    status: str = "error"
    license: LicenseItem


class LicenseAuditItem(BaseModel):
    """``LicenseAuditEntry`` 的线缆形状（详情面板的"操作历史"）。"""

    model_config = ConfigDict(extra="allow")

    log_id: str
    license_id: str
    action: str
    actor: str = ""
    timestamp: float
    detail: str = ""
    ip_address: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class LicenseAuditListEnvelope(BaseModel):
    status: str = "error"
    audit_logs: list[LicenseAuditItem] = Field(default_factory=list)
    count: int = 0
