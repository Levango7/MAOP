"""MAOP Agent Discovery — 自动发现系统中已安装的 AI Agent 工具。

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