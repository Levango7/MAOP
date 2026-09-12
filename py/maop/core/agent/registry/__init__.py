"""MAOP Agent Registry — 统一 Agent 元数据注册中心。

管理所有 AI Agent 的元数据：能力声明、计费模式、授权方式、运行时配置、降级链。

Usage::

    from maop.core.agent.registry import AgentCatalog, AgentDescriptor

    catalog = AgentCatalog.default()
    catalog.register(AgentDescriptor(name="claude", display_name="Claude", ...))
    agent = catalog.get("claude")
    coders = catalog.search(AgentCapability.CODE_GENERATION)

See:
    - agent_catalog.py      — AgentDescriptor + AgentCatalog 注册中心
    - agent_catalog_store.py — SQLite 持久化层
"""

from __future__ import annotations

from maop.core.agent.registry.agent_catalog import (
    AgentCapability,
    AgentCatalog,
    AgentDescriptor,
    AuthMethod,
    BillingModel,
    get_catalog,
    reset_catalog,
)
from maop.core.agent.registry.agent_catalog_store import AgentCatalogStore

__all__ = [
    "AgentCapability",
    "AgentCatalog",
    "AgentDescriptor",
    "AuthMethod",
    "BillingModel",
    "AgentCatalogStore",
    "get_catalog",
    "reset_catalog",
]