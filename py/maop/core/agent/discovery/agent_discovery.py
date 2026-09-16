"""Agent 自动发现模块 — 扫描系统中已安装的 AI Agent 工具。

跨平台支持（Windows / Linux / Mac），扫描来源：
  - 桌面应用（Windows: 注册表 + 开始菜单 / Linux: /usr/share/applications /
    Mac: /Applications）
  - PATH 中的 CLI 工具（shutil.which）
  - VS Code 扩展目录（~/.vscode/extensions）
  - JetBrains 插件目录（~/.<IDE>/plugins）

设计要点：
  - **线程安全**：所有扫描方法用 ``threading.RLock`` 保护，支持并发调用。
  - **跨平台**：根据 ``sys.platform`` 分派不同的扫描策略，不支持的
    平台返回空列表而非报错。
  - **容错**：单个扫描目标失败不影响整体，逐项 try/except 容错。
  - **幂等**：``discover_all`` 对同名 Agent 去重，保留首个发现。
  - **不实际安装**：测试中 mock 扫描函数，不依赖真实安装环境。

模块拆分结构：
  - ``discovery_models.py`` — 数据模型与常量（DiscoveredAgent / SCAN_TARGETS /
    _VENDOR_MAP / _get_display_info）
  - ``discovery_mixin.py`` — 扫描方法 Mixin（AgentDiscoveryMixin）
  - ``agent_discovery.py`` — AgentDiscovery 主类（继承 Mixin，补充
    auto_register / _check_process）+ 向后兼容 re-export

Usage::

    from maop.core.agent.discovery.agent_discovery import AgentDiscovery

    discovery = AgentDiscovery()
    discovered = discovery.discover_all()
    for agent in discovered:
        print(f"{agent.name} ({agent.source}): {agent.path}")
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Any

# ── 向后兼容 re-export ──────────────────────────────────────────────
# 以下符号从 discovery_models re-export，保持所有
# ``from maop.core.agent.discovery.agent_discovery import ...`` 调用不变。
from maop.core.agent.discovery.discovery_models import (
    DiscoveredAgent,
    SCAN_TARGETS,
    _VENDOR_MAP,
    _get_display_info,
)
from maop.core.agent.discovery.discovery_mixin import AgentDiscoveryMixin

__all__ = [
    "AgentDiscovery",
    "DiscoveredAgent",
    "SCAN_TARGETS",
]

logger = logging.getLogger(__name__)


# ── AgentDiscovery 发现器 ───────────────────────────────────────────
class AgentDiscovery(AgentDiscoveryMixin):
    """Agent 自动发现器 — 扫描系统中已安装的 AI Agent 工具.

    线程安全：所有公共方法用 ``threading.RLock`` 保护，可并发调用。
    跨平台：根据 ``sys.platform`` 分派不同的扫描策略。

    扫描逻辑继承自 :class:`AgentDiscoveryMixin`（桌面应用 / CLI /
    VS Code / JetBrains），本类补充自动注册（``auto_register``）和
    进程检查（``_check_process``）方法。

    Usage::

        discovery = AgentDiscovery()
        discovered = discovery.discover_all()
        n = discovery.auto_register(catalog, discovered)
    """

    # ── 自动注册 ───────────────────────────────────────────────────
    def auto_register(
        self,
        catalog: Any,
        discovered: list[DiscoveredAgent],
    ) -> int:
        """自动将发现的 Agent 注册到 AgentCatalog.

        对每个发现的 Agent，若 catalog 中不存在同名记录，则创建
        :class:`AgentDescriptor` 并注册。已存在的跳过（幂等）。

        Parameters
        ----------
        catalog : AgentCatalog
            Agent 注册中心实例。
        discovered : list[DiscoveredAgent]
            发现的 Agent 列表。

        Returns
        -------
        int
            本次新注册的 Agent 数量。
        """
        # 延迟导入避免循环依赖
        from maop.core.agent.registry.agent_catalog import AgentDescriptor

        with self._lock:
            count = 0
            for agent in discovered:
                try:
                    # 检查是否已注册
                    existing = catalog.get(agent.name)
                    if existing is not None:
                        agent.already_registered = True
                        logger.debug(
                            "[agent_discovery] 跳过已注册: %s",
                            agent.name,
                        )
                        continue

                    # 构造 adapter_config
                    adapter_config: dict[str, Any] = {}
                    if agent.adapter_type == "cli" and agent.path:
                        adapter_config["command"] = agent.path
                    elif agent.adapter_type == "mcp" and agent.path:
                        adapter_config["extension_path"] = agent.path

                    # 构造 AgentDescriptor 并注册
                    descriptor = AgentDescriptor(
                        name=agent.name,
                        display_name=agent.display_name,
                        vendor=agent.vendor,
                        version=agent.version,
                        adapter_type=agent.adapter_type if agent.adapter_type in ("cli", "http", "mcp", "web") else "",
                        adapter_config=adapter_config,
                    )
                    catalog.register(descriptor)
                    agent.already_registered = True
                    count += 1
                    logger.info(
                        "[agent_discovery] 自动注册: %s (source=%s)",
                        agent.name,
                        agent.source,
                    )
                except Exception as exc:
                    logger.warning(
                        "[agent_discovery] 注册 %s 失败: %s",
                        agent.name,
                        exc,
                    )
            logger.info(
                "[agent_discovery] 自动注册完成: %d/%d",
                count,
                len(discovered),
            )
            return count

    # ── 进程检查 ───────────────────────────────────────────────────
    def _check_process(self, name: str) -> bool:
        """检查指定进程是否在运行.

        平台策略：
          - **Windows**：``tasklist`` 命令过滤进程名
          - **Linux/Mac**：``pgrep`` 命令

        Parameters
        ----------
        name : str
            进程名（如 ``"cursor"`` / ``"Cursor.exe"``）。

        Returns
        -------
        bool
            ``True`` 表示进程在运行，``False`` 表示未运行或检查失败。
        """
        with self._lock:
            try:
                if self._platform.startswith("win"):
                    # Windows: tasklist 过滤
                    result = subprocess.run(
                        ["tasklist", "/FI", f"IMAGENAME eq {name}"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    return name.lower() in result.stdout.lower()
                else:
                    # Linux/Mac: pgrep
                    result = subprocess.run(
                        ["pgrep", "-x", name],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    return result.returncode == 0 and bool(result.stdout.strip())
            except (
                subprocess.TimeoutExpired,
                FileNotFoundError,
                OSError,
            ) as exc:
                logger.debug(
                    "[agent_discovery] 进程检查失败 %s: %s",
                    name,
                    exc,
                )
                return False
    # ── 可执行文件查找 ─────────────────────────────────────────────
    def _find_executable(self, name: str) -> str | None:
        """在 PATH 中查找可执行文件.

        使用 ``shutil.which`` 跨平台查找。对含连字符的名称，
        额外尝试去除连字符的变体（如 ``claude-code`` → ``claude``）。

        Parameters
        ----------
        name : str
            可执行文件名（如 ``"claude-code"``）。

        Returns
        -------
        str | None
            找到的完整路径，未找到返回 ``None``。
        """
        with self._lock:
            # 直接查找
            path = shutil.which(name)
            if path:
                return path

            # 尝试去除连字符的变体
            if "-" in name:
                short_name = name.split("-")[0]
                path = shutil.which(short_name)
                if path:
                    return path

            # 尝试替换连字符为下划线
            if "-" in name:
                alt_name = name.replace("-", "_")
                path = shutil.which(alt_name)
                if path:
                    return path

            return None
