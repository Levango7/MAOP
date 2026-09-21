"""AgentDiscovery 模块单元测试 — 覆盖自动发现、跨平台扫描、自动注册.

测试策略：
  - **mock 扫描函数**：不依赖真实安装环境，mock ``shutil.which`` /
    ``os.listdir`` / ``os.path.isdir`` 等。
  - **mock 进程检查**：``_check_process`` mock subprocess.run。
  - **使用内存 catalog**：AgentCatalog 用临时 DB 路径，测试后清理。

共 20 个测试用例。
"""

from __future__ import annotations

import os  # noqa: F401
import threading
from typing import Any  # noqa: F401
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from maop.core.agent.discovery.agent_discovery import (
    SCAN_TARGETS,
    AgentDiscovery,
    DiscoveredAgent,
)
from maop.core.agent.registry.agent_catalog import (
    AgentCatalog,
    AgentDescriptor,
)

# ── DiscoveredAgent 模型测试 ───────────────────────────────────────

class TestDiscoveredAgent:
    """DiscoveredAgent Pydantic 模型测试。"""

    def test_discovered_agent_creation(self) -> None:
        """测试 DiscoveredAgent 基本创建。"""
        agent = DiscoveredAgent(
            name="cursor",
            display_name="Cursor",
            vendor="Anysphere",
            source="desktop",
            path="/Applications/Cursor.app",
            version="1.0.0",
            adapter_type="cli",
        )
        assert agent.name == "cursor"
        assert agent.display_name == "Cursor"
        assert agent.vendor == "Anysphere"
        assert agent.source == "desktop"
        assert agent.path == "/Applications/Cursor.app"
        assert agent.version == "1.0.0"
        assert agent.adapter_type == "cli"
        assert agent.already_registered is False

    def test_discovered_agent_defaults(self) -> None:
        """测试 DiscoveredAgent 默认值。"""
        agent = DiscoveredAgent(name="test-agent", source="cli")
        assert agent.display_name == ""
        assert agent.vendor == ""
        assert agent.path == ""
        assert agent.version == ""
        assert agent.adapter_type == "cli"
        assert agent.already_registered is False

    def test_discovered_agent_requires_name(self) -> None:
        """测试 DiscoveredAgent name 为空时抛出校验错误。"""
        with pytest.raises(ValidationError):
            DiscoveredAgent(name="", source="cli")

    def test_discovered_agent_requires_source(self) -> None:
        """测试 DiscoveredAgent source 为空时抛出校验错误。"""
        with pytest.raises(ValidationError):
            DiscoveredAgent(name="test", source="")


# ── SCAN_TARGETS 测试 ──────────────────────────────────────────────

class TestScanTargets:
    """SCAN_TARGETS 预定义扫描目标测试。"""

    def test_scan_targets_structure(self) -> None:
        """测试 SCAN_TARGETS 包含四类来源。"""
        assert "desktop" in SCAN_TARGETS
        assert "cli" in SCAN_TARGETS
        assert "vscode" in SCAN_TARGETS
        assert "jetbrains" in SCAN_TARGETS
        for key in ("desktop", "cli", "vscode", "jetbrains"):
            assert isinstance(SCAN_TARGETS[key], list)
            assert len(SCAN_TARGETS[key]) > 0

    def test_scan_targets_desktop_contents(self) -> None:
        """测试 SCAN_TARGETS desktop 包含预期项。"""
        desktop = SCAN_TARGETS["desktop"]
        expected = ["cursor", "trae", "qoder", "zcode", "catpaw"]
        for name in expected:
            assert name in desktop, f"缺少桌面应用: {name}"

    def test_scan_targets_cli_contents(self) -> None:
        """测试 SCAN_TARGETS cli 包含预期项。"""
        cli = SCAN_TARGETS["cli"]
        expected = ["claude-code", "codex", "aider", "opencode", "codearts"]
        for name in expected:
            assert name in cli, f"缺少 CLI 工具: {name}"

    def test_scan_targets_vscode_contents(self) -> None:
        """测试 SCAN_TARGETS vscode 包含预期项。"""
        vscode = SCAN_TARGETS["vscode"]
        expected = ["github.copilot", "continue", "tabnine", "cline"]
        for name in expected:
            assert name in vscode, f"缺少 VS Code 扩展: {name}"


# ── AgentDiscovery 初始化与线程安全测试 ─────────────────────────────

class TestAgentDiscoveryInit:
    """AgentDiscovery 初始化测试。"""

    def test_init_creates_lock(self) -> None:
        """测试初始化创建 RLock。"""
        discovery = AgentDiscovery()
        assert hasattr(discovery, "_lock")
        assert isinstance(discovery._lock, type(threading.RLock()))

    def test_init_detects_platform(self) -> None:
        """测试初始化检测平台。"""
        discovery = AgentDiscovery()
        assert hasattr(discovery, "_platform")
        assert isinstance(discovery._platform, str)
        assert len(discovery._platform) > 0


# ── 桌面应用扫描测试 ───────────────────────────────────────────────

class TestDiscoverDesktopApps:
    """discover_desktop_apps 桌面应用扫描测试。"""

    def test_discover_desktop_apps_empty_when_none_found(self) -> None:
        """测试无桌面应用时返回空列表。"""
        discovery = AgentDiscovery()
        # mock _find_desktop_app 返回空（未安装）
        with patch.object(discovery, "_find_desktop_app", return_value=""):
            result = discovery.discover_desktop_apps()
            assert result == []

    def test_discover_desktop_apps_finds_installed(self) -> None:
        """测试发现已安装的桌面应用。"""
        discovery = AgentDiscovery()

        # mock _find_desktop_app：cursor 已安装，其余未安装
        def mock_find(name: str) -> str:
            if name == "cursor":
                return "/Applications/Cursor.app"
            return ""

        with patch.object(discovery, "_find_desktop_app", side_effect=mock_find):
            result = discovery.discover_desktop_apps()
            assert len(result) == 1
            assert result[0].name == "cursor"
            assert result[0].source == "desktop"
            assert result[0].path == "/Applications/Cursor.app"
            assert result[0].display_name == "Cursor"
            assert result[0].vendor == "Anysphere"

    def test_discover_desktop_apps_multiple_found(self) -> None:
        """测试发现多个桌面应用。"""
        discovery = AgentDiscovery()

        def mock_find(name: str) -> str:
            installed = {"cursor", "trae", "qoder"}
            if name in installed:
                return f"/Applications/{name}.app"
            return ""

        with patch.object(discovery, "_find_desktop_app", side_effect=mock_find):
            result = discovery.discover_desktop_apps()
            names = [r.name for r in result]
            assert "cursor" in names
            assert "trae" in names
            assert "qoder" in names
            assert len(result) == 3

    def test_discover_desktop_apps_exception_tolerant(self) -> None:
        """测试扫描异常不影响其他目标。"""
        discovery = AgentDiscovery()

        call_count = {"n": 0}

        def mock_find(name: str) -> str:
            call_count["n"] += 1
            if name == "cursor":
                raise RuntimeError("扫描失败")
            if name == "trae":
                return "/Applications/Trae.app"
            return ""

        with patch.object(discovery, "_find_desktop_app", side_effect=mock_find):
            result = discovery.discover_desktop_apps()
            # cursor 扫描失败被跳过，trae 正常发现
            assert any(r.name == "trae" for r in result)
            assert not any(r.name == "cursor" for r in result)


# ── CLI 工具扫描测试 ───────────────────────────────────────────────

class TestDiscoverCliTools:
    """discover_cli_tools CLI 工具扫描测试。"""

    def test_discover_cli_tools_empty_when_none_found(self) -> None:
        """测试无 CLI 工具时返回空列表。"""
        discovery = AgentDiscovery()
        with patch.object(discovery, "_find_executable", return_value=None):
            result = discovery.discover_cli_tools()
            assert result == []

    def test_discover_cli_tools_finds_installed(self) -> None:
        """测试发现已安装的 CLI 工具。"""
        discovery = AgentDiscovery()

        def mock_find(name: str) -> str | None:
            if name == "claude-code":
                return "/usr/local/bin/claude-code"
            return None

        with patch.object(discovery, "_find_executable", side_effect=mock_find):
            result = discovery.discover_cli_tools()
            assert len(result) == 1
            assert result[0].name == "claude-code"
            assert result[0].source == "cli"
            assert result[0].path == "/usr/local/bin/claude-code"
            assert result[0].adapter_type == "cli"


# ── VS Code 扩展扫描测试 ───────────────────────────────────────────

class TestDiscoverVscodeExtensions:
    """discover_vscode_extensions VS Code 扩展扫描测试。"""

    def test_discover_vscode_empty_when_no_dir(self) -> None:
        """测试无扩展目录时返回空列表。"""
        discovery = AgentDiscovery()
        with patch("os.path.isdir", return_value=False):
            result = discovery.discover_vscode_extensions()
            assert result == []

    def test_discover_vscode_finds_extension(self) -> None:
        """测试发现已安装的 VS Code 扩展。"""
        discovery = AgentDiscovery()
        # mock 扩展目录存在且包含 copilot 扩展
        ext_dir = "/fake/.vscode/extensions"
        fake_entries = ["github.copilot-1.200.0"]

        def mock_isdir(path: str) -> bool:
            return path == ext_dir

        def mock_listdir(path: str) -> list[str]:
            if path == ext_dir:
                return fake_entries
            return []

        def mock_expanduser(path: str) -> str:
            # 将 ~ 替换为 /fake，保留后续路径
            if path.startswith("~"):
                return "/fake" + path[1:]
            return path

        with (
            patch("os.path.isdir", side_effect=mock_isdir),
            patch("os.listdir", side_effect=mock_listdir),
            patch("os.path.expanduser", side_effect=mock_expanduser),
        ):
            result = discovery.discover_vscode_extensions()
            assert len(result) >= 1
            copilot = [r for r in result if r.name == "github.copilot"]
            assert len(copilot) == 1
            assert copilot[0].source == "vscode"
            assert copilot[0].adapter_type == "mcp"
            assert copilot[0].version == "1.200.0"


# ── JetBrains 插件扫描测试 ─────────────────────────────────────────

class TestDiscoverJetbrainsPlugins:
    """discover_jetbrains_plugins JetBrains 插件扫描测试。"""

    def test_discover_jetbrains_empty_when_no_dir(self) -> None:
        """测试无插件目录时返回空列表。"""
        discovery = AgentDiscovery()
        with patch("os.path.isdir", return_value=False):
            result = discovery.discover_jetbrains_plugins()
            assert result == []


# ── discover_all 综合测试 ──────────────────────────────────────────

class TestDiscoverAll:
    """discover_all 综合扫描测试。"""

    def test_discover_all_returns_list(self) -> None:
        """测试 discover_all 返回列表。"""
        discovery = AgentDiscovery()
        with (
            patch.object(discovery, "discover_desktop_apps", return_value=[]),
            patch.object(discovery, "discover_cli_tools", return_value=[]),
            patch.object(discovery, "discover_vscode_extensions", return_value=[]),
            patch.object(discovery, "discover_jetbrains_plugins", return_value=[]),
        ):
            result = discovery.discover_all()
            assert isinstance(result, list)

    def test_discover_all_dedup(self) -> None:
        """测试 discover_all 对同名 Agent 去重。"""
        discovery = AgentDiscovery()
        # goose 同时出现在 desktop 和 cli 中
        desktop_agents = [
            DiscoveredAgent(name="goose", display_name="Goose", source="desktop", path="/app/goose"),
        ]
        cli_agents = [
            DiscoveredAgent(name="goose", display_name="Goose", source="cli", path="/bin/goose"),
            DiscoveredAgent(name="aider", display_name="Aider", source="cli", path="/bin/aider"),
        ]
        with (
            patch.object(discovery, "discover_desktop_apps", return_value=desktop_agents),
            patch.object(discovery, "discover_cli_tools", return_value=cli_agents),
            patch.object(discovery, "discover_vscode_extensions", return_value=[]),
            patch.object(discovery, "discover_jetbrains_plugins", return_value=[]),
        ):
            result = discovery.discover_all()
            names = [r.name for r in result]
            # goose 只出现一次（保留 desktop 来源的）
            assert names.count("goose") == 1
            assert "aider" in names
            goose = next(r for r in result if r.name == "goose")
            assert goose.source == "desktop"

    def test_discover_all_merges_all_sources(self) -> None:
        """测试 discover_all 合并所有来源。"""
        discovery = AgentDiscovery()
        desktop = [DiscoveredAgent(name="cursor", display_name="Cursor", source="desktop")]
        cli = [DiscoveredAgent(name="aider", display_name="Aider", source="cli")]
        vscode = [DiscoveredAgent(name="continue", display_name="Continue", source="vscode")]
        jetbrains = [DiscoveredAgent(name="codeium", display_name="Codeium", source="jetbrains")]
        with (
            patch.object(discovery, "discover_desktop_apps", return_value=desktop),
            patch.object(discovery, "discover_cli_tools", return_value=cli),
            patch.object(discovery, "discover_vscode_extensions", return_value=vscode),
            patch.object(discovery, "discover_jetbrains_plugins", return_value=jetbrains),
        ):
            result = discovery.discover_all()
            names = {r.name for r in result}
            assert names == {"cursor", "aider", "continue", "codeium"}


# ── _find_executable 测试 ──────────────────────────────────────────

class TestFindExecutable:
    """_find_executable 可执行文件查找测试。"""

    def test_find_executable_none_when_not_found(self) -> None:
        """测试未找到可执行文件返回 None。"""
        discovery = AgentDiscovery()
        with patch("shutil.which", return_value=None):
            result = discovery._find_executable("nonexistent-tool-xyz")
            assert result is None

    def test_find_executable_returns_path(self) -> None:
        """测试找到可执行文件返回路径。"""
        discovery = AgentDiscovery()
        with patch("shutil.which", return_value="/usr/local/bin/aider"):
            result = discovery._find_executable("aider")
            assert result == "/usr/local/bin/aider"

    def test_find_executable_tries_short_name(self) -> None:
        """测试含连字符的名称尝试短名变体。"""
        discovery = AgentDiscovery()
        call_args: list[str] = []

        def mock_which(name: str) -> str | None:
            call_args.append(name)
            if name == "claude":
                return "/usr/bin/claude"
            return None

        with patch("shutil.which", side_effect=mock_which):
            result = discovery._find_executable("claude-code")
            assert result == "/usr/bin/claude"
            assert "claude-code" in call_args
            assert "claude" in call_args


# ── _check_process 测试 ────────────────────────────────────────────

class TestCheckProcess:
    """_check_process 进程检查测试。"""

    def test_check_process_false_on_exception(self) -> None:
        """测试进程检查异常时返回 False。"""
        discovery = AgentDiscovery()
        with patch("subprocess.run", side_effect=OSError("失败")):
            result = discovery._check_process("nonexistent")
            assert result is False

    def test_check_process_windows_found(self) -> None:
        """测试 Windows 平台进程检查找到进程。"""
        discovery = AgentDiscovery()
        discovery._platform = "win32"
        mock_result = MagicMock()
        mock_result.stdout = "Cursor.exe  1234  Console  1  100,000 K"
        with patch("subprocess.run", return_value=mock_result):
            result = discovery._check_process("Cursor.exe")
            assert result is True

    def test_check_process_windows_not_found(self) -> None:
        """测试 Windows 平台进程检查未找到进程。"""
        discovery = AgentDiscovery()
        discovery._platform = "win32"
        mock_result = MagicMock()
        mock_result.stdout = "信息: 没有运行的任务匹配指定标准。"
        with patch("subprocess.run", return_value=mock_result):
            result = discovery._check_process("NonExistent.exe")
            assert result is False


# ── auto_register 测试 ─────────────────────────────────────────────

class TestAutoRegister:
    """auto_register 自动注册测试。"""

    def test_auto_register_new_agents(self, tmp_path) -> None:
        """测试注册新发现的 Agent。"""
        catalog = AgentCatalog(db_path=str(tmp_path / "test_catalog.db"))
        discovery = AgentDiscovery()
        discovered = [
            DiscoveredAgent(
                name="test-cli-agent",
                display_name="Test CLI Agent",
                vendor="TestVendor",
                source="cli",
                path="/usr/bin/test-cli-agent",
                adapter_type="cli",
            ),
        ]
        count = discovery.auto_register(catalog, discovered)
        assert count == 1
        assert discovered[0].already_registered is True
        # 验证已注册到 catalog
        registered = catalog.get("test-cli-agent")
        assert registered is not None
        assert registered.name == "test-cli-agent"
        assert registered.display_name == "Test CLI Agent"

    def test_auto_register_skip_existing(self, tmp_path) -> None:
        """测试跳过已注册的 Agent。"""
        catalog = AgentCatalog(db_path=str(tmp_path / "test_catalog.db"))
        # 预先注册
        catalog.register(AgentDescriptor(name="existing-agent", display_name="Existing"))
        discovery = AgentDiscovery()
        discovered = [
            DiscoveredAgent(name="existing-agent", display_name="Existing", source="cli"),
            DiscoveredAgent(name="new-agent", display_name="New", source="cli"),
        ]
        count = discovery.auto_register(catalog, discovered)
        assert count == 1  # 只注册了 new-agent
        assert discovered[0].already_registered is True
        assert discovered[1].already_registered is True

    def test_auto_register_returns_zero_for_empty(self, tmp_path) -> None:
        """测试空列表返回 0。"""
        catalog = AgentCatalog(db_path=str(tmp_path / "test_catalog.db"))
        discovery = AgentDiscovery()
        count = discovery.auto_register(catalog, [])
        assert count == 0

    def test_auto_register_exception_tolerant(self, tmp_path) -> None:
        """测试注册异常不影响其他 Agent。"""
        catalog = AgentCatalog(db_path=str(tmp_path / "test_catalog.db"))
        discovery = AgentDiscovery()
        discovered = [
            DiscoveredAgent(name="good-agent", display_name="Good", source="cli"),
            DiscoveredAgent(name="bad-agent", display_name="Bad", source="cli"),
        ]
        # mock catalog.register 对 bad-agent 抛错
        original_register = catalog.register

        def mock_register(desc: AgentDescriptor) -> None:
            if desc.name == "bad-agent":
                raise RuntimeError("注册失败")
            original_register(desc)

        with (
            patch.object(catalog, "register", side_effect=mock_register),
            patch.object(catalog, "get", return_value=None),
        ):
            count = discovery.auto_register(catalog, discovered)
        # good-agent 注册成功，bad-agent 失败被跳过
        assert count == 1