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

Usage::

    from maop.core.agent.discovery.agent_discovery import AgentDiscovery

    discovery = AgentDiscovery()
    discovered = discovery.discover_all()
    for agent in discovered:
        print(f"{agent.name} ({agent.source}): {agent.path}")
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── 预定义扫描目标 ──────────────────────────────────────────────────
#: 桌面应用扫描清单（AI 代码编辑器 / IDE）
SCAN_TARGETS: dict[str, list[str]] = {
    "desktop": [
        "cursor",
        "trae",
        "qoder",
        "zcode",
        "catpaw",
        "codebuddy",
        "marscode",
        "deepseek-harness",
        "goose",
        "zed",
        "void",
        "melty",
        "pearai",
    ],
    #: CLI 工具扫描清单（PATH 中的命令行 Agent）
    "cli": [
        "claude-code",
        "codex",
        "aider",
        "opencode",
        "gemini-cli",
        "amazon-q-cli",
        "cline-cli",
        "goose",
        "inscode",
        "codearts",
    ],
    #: VS Code 扩展扫描清单（扩展 ID 或名称关键字）
    "vscode": [
        "github.copilot",
        "continue",
        "tabnine",
        "cline",
        "refact-ai",
        "tongyi-lingma",
        "codegeex",
        "baidu-comate",
    ],
    #: JetBrains 插件扫描清单（插件 ID 或名称关键字）
    "jetbrains": [
        "github.copilot",
        "tabnine",
        "codeium",
        "continue",
        "tongyi-lingma",
        "codegeex",
        "refact-ai",
    ],
}


# ── 厂商映射（用于补充 display_name 和 vendor）──────────────────────
_VENDOR_MAP: dict[str, tuple[str, str]] = {
    # name -> (display_name, vendor)
    "cursor": ("Cursor", "Anysphere"),
    "trae": ("Trae", "ByteDance"),
    "qoder": ("Qoder", "Alibaba"),
    "zcode": ("ZCode", "ZCode"),
    "catpaw": ("CatPaw", "Independent"),
    "codebuddy": ("CodeBuddy", "Tencent"),
    "marscode": ("MarsCode", "ByteDance"),
    "deepseek-harness": ("DeepSeek Harness", "DeepSeek"),
    "goose": ("Goose", "Block"),
    "zed": ("Zed", "Zed Industries"),
    "void": ("Void", "Independent"),
    "melty": ("Melty", "Independent"),
    "pearai": ("PearAI", "Independent"),
    "claude-code": ("Claude Code", "Anthropic"),
    "codex": ("Codex", "OpenAI"),
    "aider": ("Aider", "OpenSource"),
    "opencode": ("OpenCode", "OpenSource"),
    "gemini-cli": ("Gemini CLI", "Google"),
    "amazon-q-cli": ("Amazon Q CLI", "Amazon"),
    "cline-cli": ("Cline CLI", "OpenSource"),
    "inscode": ("InsCode", "CSDN"),
    "codearts": ("CodeArts", "Huawei"),
    "github.copilot": ("GitHub Copilot", "GitHub"),
    "continue": ("Continue", "OpenSource"),
    "tabnine": ("Tabnine", "Tabnine"),
    "cline": ("Cline", "OpenSource"),
    "refact-ai": ("Refact AI", "Refact"),
    "tongyi-lingma": ("Tongyi Lingma", "Alibaba"),
    "codegeex": ("CodeGeeX", "Zhipu"),
    "baidu-comate": ("Baidu Comate", "Baidu"),
    "codeium": ("Codeium", "Codeium"),
}


def _get_display_info(name: str) -> tuple[str, str]:
    """获取 Agent 的显示名和厂商.

    Parameters
    ----------
    name : str
        Agent 标识名。

    Returns
    -------
    tuple[str, str]
        (display_name, vendor)，未找到时返回 (name, "")。
    """
    info = _VENDOR_MAP.get(name)
    if info is not None:
        return info
    # 未知 Agent：首字母大写作为 display_name，vendor 留空
    return (name.replace("-", " ").replace(".", " ").title(), "")


# ── DiscoveredAgent 数据模型 ────────────────────────────────────────
class DiscoveredAgent(BaseModel):
    """发现的 Agent 信息描述符.

    Attributes
    ----------
    name : str
        Agent 标识名（如 ``"cursor"`` / ``"claude-code"``）。
    display_name : str
        展示名称（如 ``"Cursor"`` / ``"Claude Code"``）。
    vendor : str
        厂商/供应商（如 ``"Anysphere"`` / ``"Anthropic"``）。
    source : str
        发现来源：``"desktop"`` / ``"cli"`` / ``"vscode"`` / ``"jetbrains"``。
    path : str
        安装路径或可执行文件路径。
    version : str
        版本号（若可探测）。
    adapter_type : str
        建议的适配器类型：``"cli"`` / ``"http"`` / ``"mcp"`` / ``"web"``。
    already_registered : bool
        是否已在 AgentCatalog 中注册（由 ``auto_register`` 填充）。
    """

    name: str = Field(..., min_length=1, description="Agent 标识名")
    display_name: str = Field(default="", description="展示名称")
    vendor: str = Field(default="", description="厂商")
    source: str = Field(..., min_length=1, description="发现来源")
    path: str = Field(default="", description="安装路径")
    version: str = Field(default="", description="版本号")
    adapter_type: str = Field(default="cli", description="建议的适配器类型")
    already_registered: bool = Field(default=False, description="是否已注册")


# ── AgentDiscovery 发现器 ───────────────────────────────────────────
class AgentDiscovery:
    """Agent 自动发现器 — 扫描系统中已安装的 AI Agent 工具.

    线程安全：所有公共方法用 ``threading.RLock`` 保护，可并发调用。
    跨平台：根据 ``sys.platform`` 分派不同的扫描策略。

    Usage::

        discovery = AgentDiscovery()
        discovered = discovery.discover_all()
        n = discovery.auto_register(catalog, discovered)
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