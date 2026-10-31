"""SSO 路由（``/api/sso``）的响应模型。

镜像的是 ``maop.enterprise.sso_registry.to_masked_response`` 吐出的形状：企业包
在纯净 CI 检出里不存在（ADR-017 双仓），所以模型必须留在仓内，OpenAPI 才有
可引用的 schema。字段与企业包模型的一致性由
``tests/test_enterprise_router_contracts.py`` 在装了企业包的环境里对账。

**为什么条目模型开 ``extra="allow"``**：这个形状的持有者是另一个仓库，版本可以
独立前进。严格模型遇到企业包新增字段时会**静默丢键**——前端读不到、日志无痕、
所有门禁照样绿，这是本仓最贵的一类静默失效。放开 extra 之后，新增字段原样透传，
而改名/删必填字段会因为必填项缺失变成 500，属于响亮失败。

信封的 ``status`` 默认值取 ``"error"``（同 ``tenant_schemas``）：误漏赋值时宁可
在响应里显出错误，也不要默认报成功。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SSOProviderItem(BaseModel):
    """``SSOProviderResponse`` 脱敏后的线缆形状（``config`` 已遮掉敏感字段）。"""

    model_config = ConfigDict(extra="allow")

    id: int
    name: str
    protocol: str
    tenant_id: str = ""
    enabled: bool = True
    auto_redirect: bool = False
    config: dict[str, Any] = Field(default_factory=dict)
    attribute_mapping: dict[str, Any] = Field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0


class SSOProviderListEnvelope(BaseModel):
    status: str = "error"
    providers: list[SSOProviderItem] = Field(default_factory=list)
    count: int = 0
    total: int = 0


class SSOProviderEnvelope(BaseModel):
    status: str = "error"
    provider: SSOProviderItem


class SSOProviderDeletedEnvelope(BaseModel):
    status: str = "error"
    deleted: bool = False


class SSOProbeDetails(BaseModel):
    """``test_connection`` 的 ``details``：OIDC 与 SAML 各用其中一组键。"""

    model_config = ConfigDict(extra="allow")

    latency_ms: float | None = None
    authorize_url_resolved: bool | None = None
    token_url_resolved: bool | None = None
    userinfo_url_resolved: bool | None = None
    discovery_fetched: bool | None = None
    sso_url_resolved: bool | None = None


class SSOProviderTestEnvelope(BaseModel):
    status: str = "error"
    reachable: bool
    protocol: str
    details: SSOProbeDetails = Field(default_factory=SSOProbeDetails)
    error: str | None = None
