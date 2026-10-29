"""MAOP Dashboard — Agent Version API 的响应模型.

版本行与灰度行的键由 ``agent_versions_helpers`` 的 ``_AGENT_VERSION_COLUMNS`` /
``_CANARY_COLUMNS`` 与 ``_version_row_to_dict`` / ``_canary_row_to_dict`` 决定，
这里逐列照写：可空列（``activated_at`` / ``retired_at`` / ``duration_seconds`` /
``ended_at``）留 ``| None``，其余 TEXT 列在建表时都是 ``NOT NULL`` 带默认值，
所以不放宽成 Optional——放宽了就等于不判。

``config_snapshot`` / ``target_user_groups`` 在行转换里已被 ``json.loads`` 解析成
dict / list（解析失败回落 ``{}`` / ``[]``），因此模型接的是解析后的形状，而不是
SQLite 里的原始 TEXT。

这几条路由用的是不带 ``error_value=`` 的 ``@handle_api_errors``：异常分支返回
JSONResponse（ErrorSchema），不经过 response_model，所以模型只描述成功形状。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class VersionItem(BaseModel):
    """``agent_versions`` 一行在 API 上的形状。"""

    version_id: str = ""
    agent_id: str = ""
    version_number: str = ""
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    changelog: str = ""
    status: str = ""
    created_by: str = ""
    tenant_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    activated_at: float | None = None
    retired_at: float | None = None


class PageMeta(BaseModel):
    """``list_versions`` 的分页元信息。"""

    total: int = 0
    page: int = 1
    page_size: int = 20


class VersionListEnvelope(BaseModel):
    """GET /api/agent-versions 响应。"""

    status: str = "ok"
    data: list[VersionItem] = Field(default_factory=list)
    meta: PageMeta = Field(default_factory=PageMeta)


class VersionActivateEnvelope(BaseModel):
    """POST /api/agent-versions/{version_id}/activate 响应。"""

    status: str = "ok"
    data: VersionItem


class RollbackMeta(BaseModel):
    """回滚把「从哪个版本、到哪个版本、为什么」原样回给调用方。"""

    rolled_back_from: str = ""
    rolled_back_to: str = ""
    reason: str = ""


class VersionRollbackEnvelope(BaseModel):
    """POST /api/agent-versions/{version_id}/rollback 响应。"""

    status: str = "ok"
    data: VersionItem
    meta: RollbackMeta


class CanaryItem(BaseModel):
    """``agent_version_canaries`` 一行在 API 上的形状。"""

    canary_id: str = ""
    version_id: str = ""
    percentage: int = 0
    target_user_groups: list[str] = Field(default_factory=list)
    strategy: str = ""
    status: str = ""
    duration_seconds: int | None = None
    started_at: float = 0.0
    ended_at: float | None = None
    created_by: str = ""


class CanaryConfigEnvelope(BaseModel):
    """POST /api/agent-versions/{version_id}/canary 响应。"""

    status: str = "ok"
    data: CanaryItem


__all__ = [
    "CanaryConfigEnvelope",
    "CanaryItem",
    "PageMeta",
    "RollbackMeta",
    "VersionActivateEnvelope",
    "VersionItem",
    "VersionListEnvelope",
    "VersionRollbackEnvelope",
]
