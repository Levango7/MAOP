"""AgentDiscovery 扫描方法 Mixin.

本模块从 ``agent_discovery.py`` 拆分而来，将 :class:`AgentDiscovery` 的扫描
相关方法（桌面应用 / CLI / VS Code / JetBrains）抽离为 Mixin，以控制单文件
行数。实际的 :class:`AgentDiscovery` 类继承本 Mixin 并补充
``auto_register`` / ``_check_process`` 等方法。

线程安全：所有公共方法用 ``threading.RLock`` 保护，可并发调用。
跨平台：根据 ``sys.platform`` 分派不同的扫描策略。
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading

from maop.core.agent.discovery.discovery_models import (
    SCAN_TARGETS,
    DiscoveredAgent,
    _get_display_info,
)

logger = logging.getLogger(__name__)


class AgentDiscoveryMixin:
    """AgentDiscovery 扫描方法 Mixin.

    提供 Agent 自动发现的扫描逻辑（桌面应用 / CLI / VS Code / JetBrains）。
    实际的 :class:`AgentDiscovery` 类继承本 Mixin 并补充
    ``auto_register`` / ``_check_process`` 等方法。

    线程安全：所有公共方法用 ``threading.RLock`` 保护，可并发调用。
    跨平台：根据 ``sys.platform`` 分派不同的扫描策略。

    Usage::

        discovery = AgentDiscovery()  # AgentDiscovery 继承本 Mixin
        discovered = discovery.discover_all()
    """

    def __init__(self) -> None:
        # RLock 允许同线程内嵌套加锁（discover_all 调用子扫描方法）
        self._lock = threading.RLock()
        self._platform: str = sys.platform

    # ── 主入口 ─────────────────────────────────────────────────────
    def discover_all(self) -> list[DiscoveredAgent]:
        """扫描所有来源，返回发现的 Agent 列表.

        合并四类来源（桌面应用 / CLI / VS Code / JetBrains），
        对同名 Agent 去重——保留首个发现（按 desktop → cli → vscode → jetbrains
        优先级），确保结果稳定。

        Returns
        -------
        list[DiscoveredAgent]
            去重后的发现 Agent 列表，按发现顺序排列。
        """
        with self._lock:
            results: list[DiscoveredAgent] = []
            # 按优先级依次扫描
            results.extend(self.discover_desktop_apps())
            results.extend(self.discover_cli_tools())
            results.extend(self.discover_vscode_extensions())
            results.extend(self.discover_jetbrains_plugins())

            # 去重：同名 Agent 保留第一个发现的
            seen: set[str] = set()
            unique: list[DiscoveredAgent] = []
            for agent in results:
                if agent.name not in seen:
                    seen.add(agent.name)
                    unique.append(agent)
                else:
                    logger.debug(
                        "[agent_discovery] 跳过重复发现: %s (source=%s)",
                        agent.name,
                        agent.source,
                    )
            logger.info(
                "[agent_discovery] 共发现 %d 个 Agent（去重前 %d 个）",
                len(unique),
                len(results),
            )
            return unique

    # ── 桌面应用扫描 ───────────────────────────────────────────────
    def discover_desktop_apps(self) -> list[DiscoveredAgent]:
        """扫描桌面应用（AI 代码编辑器 / IDE）.

        平台策略：
          - **Windows**：扫描注册表 ``App Paths`` + 开始菜单快捷方式
          - **Linux**：扫描 ``/usr/share/applications/*.desktop``
          - **Mac**：扫描 ``/Applications/*.app``

        Returns
        -------
        list[DiscoveredAgent]
            发现的桌面应用列表，``source="desktop"``。
        """
        with self._lock:
            results: list[DiscoveredAgent] = []
            targets = SCAN_TARGETS["desktop"]

            for name in targets:
                try:
                    path = self._find_desktop_app(name)
                    if path:
                        display_name, vendor = _get_display_info(name)
                        results.append(
                            DiscoveredAgent(
                                name=name,
                                display_name=display_name,
                                vendor=vendor,
                                source="desktop",
                                path=path,
                                adapter_type="cli",
                            )
                        )
                        logger.debug(
                            "[agent_discovery] 发现桌面应用: %s @ %s",
                            name,
                            path,
                        )
                except Exception as exc:
                    logger.debug(
                        "[agent_discovery] 扫描桌面应用 %s 失败: %s",
                        name,
                        exc,
                    )
            return results

    def _find_desktop_app(self, name: str) -> str:
        """在当前平台查找桌面应用安装路径.

        Parameters
        ----------
        name : str
            应用标识名（如 ``"cursor"``）。

        Returns
        -------
        str
            找到的路径，未找到返回空字符串。
        """
        if self._platform.startswith("win"):
            return self._find_desktop_app_windows(name)
        elif self._platform.startswith("linux"):
            return self._find_desktop_app_linux(name)
        elif self._platform.startswith("darwin"):
            return self._find_desktop_app_mac(name)
        return ""

    def _find_desktop_app_windows(self, name: str) -> str:
        """Windows 平台查找桌面应用.

        依次检查：
          1. 注册表 ``HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths``
          2. 开始菜单快捷方式目录
          3. ``shutil.which`` 兜底
        """
        # 注册表 App Paths
        try:
            import winreg  # type: ignore[import-not-found]

            # 尝试多种可执行文件名变体
            exe_candidates = [
                f"{name}.exe",
                f"{name.replace('-', '')}.exe",
                f"{name.split('-')[0]}.exe",
            ]
            for exe_name in exe_candidates:
                for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                    try:
                        key_path = (
                            rf"SOFTWARE\Microsoft\Windows\CurrentVersion"
                            rf"\App Paths\{exe_name}"
                        )
                        with winreg.OpenKey(hive, key_path) as key:
                            value, _ = winreg.QueryValueEx(key, None)
                            if value and os.path.isfile(value):
                                return value
                    except OSError:
                        continue
        except ImportError:
            pass

        # 开始菜单快捷方式目录
        start_menu_dirs = [
            os.path.join(
                os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                "Start Menu", "Programs",
            ),
            os.path.join(
                os.environ.get("PROGRAMDATA", ""), "Microsoft", "Windows",
                "Start Menu", "Programs",
            ),
        ]
        for menu_dir in start_menu_dirs:
            if os.path.isdir(menu_dir):
                # 递归搜索 .lnk 文件
                for root, _dirs, files in os.walk(menu_dir):
                    for fname in files:
                        lower = fname.lower()
                        if (
                            lower.endswith(".lnk")
                            and name.lower() in lower
                        ):
                            return os.path.join(root, fname)

        # shutil.which 兜底
        exe = shutil.which(name)
        return exe or ""

    def _find_desktop_app_linux(self, name: str) -> str:
        """Linux 平台查找桌面应用.

        扫描 ``/usr/share/applications/*.desktop`` 和
        ``~/.local/share/applications/*.desktop``。
        """
        desktop_dirs = [
            "/usr/share/applications",
            os.path.expanduser("~/.local/share/applications"),
        ]
        for desktop_dir in desktop_dirs:
            if not os.path.isdir(desktop_dir):
                continue
            for fname in os.listdir(desktop_dir):
                if not fname.endswith(".desktop"):
                    continue
                fpath = os.path.join(desktop_dir, fname)
                # .desktop 文件名或内容包含 name 即视为匹配
                if name.lower() in fname.lower():
                    return fpath
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read().lower()
                        if name.lower() in content:
                            return fpath
                except OSError:
                    continue
        return ""

    def _find_desktop_app_mac(self, name: str) -> str:
        """Mac 平台查找桌面应用.

        扫描 ``/Applications`` 和 ``~/Applications`` 下的 ``.app`` 目录。
        """
        app_dirs = ["/Applications", os.path.expanduser("~/Applications")]
        for apps_root in app_dirs:
            if not os.path.isdir(apps_root):
                continue
            for entry in os.listdir(apps_root):
                if not entry.endswith(".app"):
                    continue
                # 应用名匹配（忽略大小写和空格/连字符差异）
                entry_clean = entry.replace(".app", "").replace(" ", "").lower()
                name_clean = name.replace("-", "").replace(" ", "").lower()
                if name_clean in entry_clean or entry_clean in name_clean:
                    return os.path.join(apps_root, entry)
        return ""

    # ── CLI 工具扫描 ───────────────────────────────────────────────
    def discover_cli_tools(self) -> list[DiscoveredAgent]:
        """扫描 PATH 中的 CLI 工具.

        使用 ``shutil.which`` 检查每个目标命令是否在 PATH 中可用。

        Returns
        -------
        list[DiscoveredAgent]
            发现的 CLI 工具列表，``source="cli"``。
        """
        with self._lock:
            results: list[DiscoveredAgent] = []
            targets = SCAN_TARGETS["cli"]

            for name in targets:
                try:
                    exe_path = self._find_executable(name)
                    if exe_path:
                        display_name, vendor = _get_display_info(name)
                        results.append(
                            DiscoveredAgent(
                                name=name,
                                display_name=display_name,
                                vendor=vendor,
                                source="cli",
                                path=exe_path,
                                adapter_type="cli",
                            )
                        )
                        logger.debug(
                            "[agent_discovery] 发现 CLI 工具: %s @ %s",
                            name,
                            exe_path,
                        )
                except Exception as exc:
                    logger.debug(
                        "[agent_discovery] 扫描 CLI 工具 %s 失败: %s",
                        name,
                        exc,
                    )
            return results

    # ── VS Code 扩展扫描 ───────────────────────────────────────────
    def discover_vscode_extensions(self) -> list[DiscoveredAgent]:
        """扫描 VS Code 扩展目录.

        扫描 ``~/.vscode/extensions`` 和 ``~/.vscode-insiders/extensions``，
        通过目录名匹配扩展 ID。

        Returns
        -------
        list[DiscoveredAgent]
            发现的 VS Code 扩展列表，``source="vscode"``，
            ``adapter_type="mcp"``。
        """
        with self._lock:
            results: list[DiscoveredAgent] = []
            targets = SCAN_TARGETS["vscode"]

            # VS Code 扩展目录候选
            ext_dirs = [
                os.path.expanduser("~/.vscode/extensions"),
                os.path.expanduser("~/.vscode-insiders/extensions"),
                os.path.expanduser("~/.cursor/extensions"),
            ]

            for ext_dir in ext_dirs:
                if not os.path.isdir(ext_dir):
                    continue
                try:
                    entries = os.listdir(ext_dir)
                except OSError:
                    continue
                # 对每个目标扩展检查是否已安装
                for target in targets:
                    for entry in entries:
                        # 扩展目录名格式：publisher.name-version
                        if target.lower() in entry.lower():
                            # 避免重复添加同一扩展
                            if any(r.name == target for r in results):
                                continue
                            display_name, vendor = _get_display_info(target)
                            ext_path = os.path.join(ext_dir, entry)
                            # 尝试提取版本号
                            version = self._extract_vscode_version(entry)
                            results.append(
                                DiscoveredAgent(
                                    name=target,
                                    display_name=display_name,
                                    vendor=vendor,
                                    source="vscode",
                                    path=ext_path,
                                    version=version,
                                    adapter_type="mcp",
                                )
                            )
                            logger.debug(
                                "[agent_discovery] 发现 VS Code 扩展: %s @ %s",
                                target,
                                ext_path,
                            )
                            break
            return results

    def _extract_vscode_version(self, entry: str) -> str:
        """从 VS Code 扩展目录名提取版本号.

        扩展目录名格式为 ``publisher.name-1.2.3``，取最后一个 ``-`` 后的部分。

        Parameters
        ----------
        entry : str
            扩展目录名。

        Returns
        -------
        str
            版本号字符串，无法提取时返回空字符串。
        """
        parts = entry.rsplit("-", 1)
        if len(parts) == 2:
            return parts[1]
        return ""

    # ── JetBrains 插件扫描 ─────────────────────────────────────────
    def discover_jetbrains_plugins(self) -> list[DiscoveredAgent]:
        """扫描 JetBrains 插件目录.

        扫描各 JetBrains IDE 的 plugins 目录（IntelliJ / PyCharm /
        WebStorm / GoLand 等），通过插件目录名匹配。

        Returns
        -------
        list[DiscoveredAgent]
            发现的 JetBrains 插件列表，``source="jetbrains"``，
            ``adapter_type="mcp"``。
        """
        with self._lock:
            results: list[DiscoveredAgent] = []
            targets = SCAN_TARGETS["jetbrains"]

            # JetBrains 插件目录候选（各 IDE）
            home = os.path.expanduser("~")
            config_dir = ".config" if self._platform.startswith("linux") else ""
            plugin_base_dirs: list[str] = []
            if config_dir:
                plugin_base_dirs.append(
                    os.path.join(home, config_dir, "JetBrains")
                )
            else:
                plugin_base_dirs.append(os.path.join(home, "Library", "ApplicationSupport", "JetBrains"))
                # Windows: %APPDATA%\JetBrains
                appdata = os.environ.get("APPDATA", "")
                if appdata:
                    plugin_base_dirs.append(os.path.join(appdata, "JetBrains"))

            for base_dir in plugin_base_dirs:
                if not os.path.isdir(base_dir):
                    continue
                try:
                    ide_dirs = os.listdir(base_dir)
                except OSError:
                    continue
                for ide_dir in ide_dirs:
                    plugins_dir = os.path.join(base_dir, ide_dir, "plugins")
                    if not os.path.isdir(plugins_dir):
                        continue
                    try:
                        plugin_entries = os.listdir(plugins_dir)
                    except OSError:
                        continue
                    for target in targets:
                        if any(r.name == target for r in results):
                            continue
                        for entry in plugin_entries:
                            entry_lower = entry.lower()
                            target_lower = target.lower().replace(".", "")
                            # 插件目录名可能是 com.github.copilot 或 copilot
                            if (
                                target.lower() in entry_lower
                                or target_lower in entry_lower.replace(".", "")
                            ):
                                display_name, vendor = _get_display_info(target)
                                plugin_path = os.path.join(plugins_dir, entry)
                                results.append(
                                    DiscoveredAgent(
                                        name=target,
                                        display_name=display_name,
                                        vendor=vendor,
                                        source="jetbrains",
                                        path=plugin_path,
                                        adapter_type="mcp",
                                    )
                                )
                                logger.debug(
                                    "[agent_discovery] 发现 JetBrains 插件: %s @ %s",
                                    target,
                                    plugin_path,
                                )
                                break
            return results
