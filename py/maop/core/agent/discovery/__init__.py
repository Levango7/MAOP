"""MAOP Agent Discovery — 自动发现系统中已安装的 AI Agent 工具。

.. warning::
   **本包未接入主流程**（2026-09-21 核实）。

   全仓库**无任何生产代码 import 本包**（既非显式 import，也不在惰性映射 /
   入口点 / 配置中）；仅由测试文件直接引用。两个模块共约 774 行：
   ``agent_discovery``（795 行的跨平台扫描实现）与 ``config_template``。

   来源：``423b969``（企业级Agent调度平台10个模块 — 厂商生态+运维+执行安全）
   一次性创建，此后从未被接线；CHANGELOG 亦未记载本包。


跨平台扫描（Windows / Linux / Mac），覆盖四类来源：
  - 桌面应用（Windows 注册表 + 开始菜单 / Linux .desktop / Mac /Applications）
  - PATH 中的 CLI 工具
  - VS Code 扩展目录
  - JetBrains 插件目录

配合 :mod:`maop.core.agent.discovery.config_template` 可一键生成适配器配置，
并自动注册到 :class:`maop.core.agent.registry.agent_catalog.AgentCatalog`。

Usage::

    from maop.core.agent.discovery.agent_discovery import AgentDiscovery

    discovery = AgentDiscovery()
    discovered = discovery.discover_all()
    n = discovery.auto_register(catalog, discovered)

See:
    - agent_discovery.py  — AgentDiscovery + DiscoveredAgent
    - config_template.py  — ConfigTemplateManager + ConfigTemplate
"""

from __future__ import annotations

from maop.core.agent.discovery.agent_discovery import (
    SCAN_TARGETS,
    AgentDiscovery,
    DiscoveredAgent,
)
from maop.core.agent.discovery.config_template import (
    ConfigTemplate,
    ConfigTemplateManager,
)

__all__ = [
    "AgentDiscovery",
    "DiscoveredAgent",
    "SCAN_TARGETS",
    "ConfigTemplate",
    "ConfigTemplateManager",
]