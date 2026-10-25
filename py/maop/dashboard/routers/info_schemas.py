"""MAOP Dashboard — ``/api/info`` 端点的响应模型.

字段名逐字来自生产者，不另造别名：

- ``EditionInfoPayload`` ← ``maop.config.edition.edition_info()``；
- ``EditionSwitchPayload`` ← ``routers/info/admin.py`` 的切换返回值。

错误路径（400 非法 edition / 403 无 license / 500）都是
``handle_api_errors`` 渲染的 ErrorSchema JSONResponse，绕过响应模型。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class EditionInfoPayload(BaseModel):
    """GET /api/info/edition 响应。

    ``enterprise_available`` 视图不读，但生产者确实发送，保留不删。
    ``features`` 是 ``{FeatureFlag.value: bool}``，``backends`` 是
    ``{backend 名: 实现名}``。
    """

    edition: str = "personal"
    features: dict[str, bool] = Field(default_factory=dict)
    backends: dict[str, str] = Field(default_factory=dict)
    enterprise_available: bool = False
    # 条目由 record_degradation 固定写 5 个 str 键；但 degradation.log 是
    # 落盘 JSONL，旧版本或手工编辑的条目不应让整个响应 500，故只约束值类型。
    degradations: list[dict[str, str]] = Field(default_factory=list)


class EditionSwitchPayload(BaseModel):
    """POST /api/info/edition 的成功响应。"""

    status: str = "error"
    edition: str = ""
    previous: str = ""
    requested: str = ""
    degraded: bool = False
