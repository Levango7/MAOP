"""Agent 管理子包 — 从 ``agent_service`` 拆分（file-size ratchet）.

包含：

- ``catalog`` — Agent Catalog 注册中心管理（list/get/register/update/delete/search/stats）
- ``gateway`` — Agent Proxy Bridge + Gateway（规则/审计/预算）

两个子模块的 public 名称由父 ``agent_service`` 转发 import，
``__all__`` 不变，调用方和测试无需改动。
"""

from __future__ import annotations

from maop.dashboard.services.agent.catalog import (
    catalog_stats,
    delete_agent,
    get_agent,
    list_agents,
    register_agent,
    search_agents,
    update_agent,
    update_agent_health,
)
from maop.dashboard.services.agent.gateway import (
    add_proxy_rule,
    bridge_adapters,
    bridge_call,
    bridge_health,
    bridge_sync_config,
    check_budget,
    get_budget_summary,
    list_proxy_rules,
    query_audit,
    remove_proxy_rule,
    set_department_budget,
)

__all__ = [
    "add_proxy_rule",
    "bridge_adapters",
    "bridge_call",
    "bridge_health",
    "bridge_sync_config",
    "catalog_stats",
    "check_budget",
    "delete_agent",
    "get_agent",
    "get_budget_summary",
    "list_agents",
    "list_proxy_rules",
    "query_audit",
    "register_agent",
    "remove_proxy_rule",
    "search_agents",
    "set_department_budget",
    "update_agent",
    "update_agent_health",
]
