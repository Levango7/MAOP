"""RBAC 路由（``/api/rbac``）的响应模型。

企业包在纯净 CI 检出里不存在（ADR-017 双仓），而 router 要能导入、OpenAPI 才会
带类型，故这里只镜像线缆形状；字段一致性由
``tests/test_enterprise_router_contracts.py`` 在装包环境下对账。

``role`` 在企业包里是 ``str, Enum``；镜像用 ``str`` —— 序列化结果相同，但不需要
在个人版进程里引入那个枚举。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RoleGrantItem(BaseModel):
    user_id: str
    role: str
    tenant_id: str = ""
    granted_by: str = ""
    granted_at: float = 0.0
    expires_at: float | None = None


class GrantListEnvelope(BaseModel):
    status: str = "error"
    grants: list[RoleGrantItem] = Field(default_factory=list)
    count: int = 0


class GrantEnvelope(BaseModel):
    status: str = "error"
    grant: RoleGrantItem | None = None


class RevokeEnvelope(BaseModel):
    status: str = "error"
    revoked: bool = False


class RoleItem(BaseModel):
    role: str
    permissions: list[str] = Field(default_factory=list)
    permission_count: int = 0


class RoleListEnvelope(BaseModel):
    status: str = "error"
    roles: list[RoleItem] = Field(default_factory=list)


class PermissionItem(BaseModel):
    value: str
    name: str


class PermissionListEnvelope(BaseModel):
    status: str = "error"
    permissions: list[PermissionItem] = Field(default_factory=list)
    current_user: str = ""
    current_roles: list[str] = Field(default_factory=list)
    current_permissions: list[str] = Field(default_factory=list)
