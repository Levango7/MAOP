"""MAOP Dashboard — 认证 / 用户管理 API 的响应模型.

字段名逐字来自服务层的生产者，不另造别名：

- ``AuthUser`` ← ``auth_service.db_list_users()`` 的每个 item；
- ``AuthRegisterEnvelope`` ← ``auth_service.db_register_user()``；
- ``AuthMutationEnvelope`` ← ``db_update_user()`` / ``db_delete_user()``。

``GET /api/auth/status`` 是唯一没有 ``status`` 信封的端点：它直接返回
``{auth_enabled, has_token}``，App/Agents/Chat/Settings 四个视图也只读
这两项。往模型里补一个 ``status="ok"`` 会让 OpenAPI 承诺一个真实响应里
不存在的键 —— 前端只要有人开始读 ``d.status`` 就会永远读到 ``undefined``。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AuthStatusPayload(BaseModel):
    """GET /api/auth/status 响应（没有 ``status`` 字段，见模块 docstring）。"""

    auth_enabled: bool = False
    has_token: bool = False


class AuthUser(BaseModel):
    """``db_list_users()`` 返回的一行。

    ``created_at`` 是 **epoch 秒**（``users.created_at`` 为 ``REAL``，
    写入用 ``time.time()``）；前端 ``Users.vue`` 按秒×1000 渲染，别改成
    毫秒或 ISO 字符串，否则整列会显示成 1970 年或 Invalid Date。
    """

    username: str = ""
    roles: list[str] = Field(default_factory=list)
    created_at: float = 0.0
    enabled: bool = True
    tenant_id: str = ""


class AuthUsersEnvelope(BaseModel):
    """GET /api/auth/users 响应。"""

    status: str = "error"
    users: list[AuthUser] = Field(default_factory=list)


class AuthRegisterEnvelope(BaseModel):
    """POST /api/auth/register 响应。"""

    status: str = "error"
    username: str = ""
    roles: list[str] = Field(default_factory=list)
    tenant_id: str = ""


class AuthMutationEnvelope(BaseModel):
    """PUT /api/auth/users/{username} 与 DELETE 同名路由的响应。"""

    status: str = "error"
    message: str = ""
