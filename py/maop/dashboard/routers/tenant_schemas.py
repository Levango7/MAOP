"""Tenant 路由（``/api/tenant``）的响应模型。

为什么不直接复用企业包里的模型：企业包在纯净 CI 检出里不存在（ADR-017 双仓），
而 router 模块本身必须能导入、OpenAPI 才会带上类型。所以这里放一份**只描述线缆
形状**的镜像；字段与企业包模型的一致性由
``tests/test_enterprise_router_contracts.py`` 在装了企业包的环境里逐字段对账，
纯净 CI 下那条对账显式跳过（其余断言照常生效）。

信封的 ``status`` 默认值取 ``"error"``：它是失败路径的默认值，误漏赋值时宁可
在响应里显出错误，也不要默认报成功。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TenantQuota(BaseModel):
    max_api_calls_per_day: int = 10000
    max_storage_mb: int = 5120
    max_agents: int = 50
    max_concurrent_tasks: int = 10
    max_users: int = 20


class TenantUsage(BaseModel):
    api_calls_today: int = 0
    storage_mb: float = 0.0
    active_agents: int = 0
    concurrent_tasks: int = 0
    active_users: int = 0


class TenantItem(BaseModel):
    tenant_id: str
    name: str
    status: str = "trial"
    plan: str = "starter"
    quota: TenantQuota = Field(default_factory=TenantQuota)
    created_at: float = 0.0
    updated_at: float = 0.0
    expires_at: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TenantListItem(TenantItem):
    """列表行比单条多一个 ``usage``（列表服务逐租户补采，失败时为空对象）。"""

    usage: TenantUsage = Field(default_factory=TenantUsage)


class TenantListEnvelope(BaseModel):
    status: str = "error"
    tenants: list[TenantListItem] = Field(default_factory=list)
    count: int = 0


class TenantEnvelope(BaseModel):
    status: str = "error"
    tenant: TenantItem | None = None


class TenantEnvelopeSuspended(BaseModel):
    status: str = "error"
    suspended: bool = False


class TenantEnvelopeActivated(BaseModel):
    status: str = "error"
    activated: bool = False


class TenantEnvelopeDeleted(BaseModel):
    status: str = "error"
    deleted: bool = False
