"""桌面应用调度集成测试 — P2 收尾：单元测试 + 健康检查集成。

测试覆盖：
  1.1 adapter_factory 测试 — build_adapter 对各 driver 的分支构建
  1.2 driver 函数测试 — DRIVERS 注册表 + _run_desktop_app 生命周期
  1.3 配置传递测试 — AgentDef / AgentConfig 新字段 + _make_config 传递
  1.4 适配器导出测试 — adapters 包导出 DesktopAppAdapter 等

所有测试用 mock（mock subprocess、socket、httpx），不依赖真实桌面应用。
"""
from __future__ import annotations

import asyncio
import inspect
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from maop.core.agent.adapters import (
    CLIAdapter,
    DesktopAppAdapter,
    IDEExtensionAdapter,
    VibeCodingAdapter,
)
from maop.core.agent.adapters.adapter_factory import build_adapter
from maop.core.agent.adapters.desktop_app_adapter import DesktopAppConfig
from maop.core.agent.adapters.ide_extension_adapter import IDEExtensionConfig
from maop.core.agent.adapters.vibe_coding_adapter import VibeCodingConfig
from maop.delegate.drivers import DRIVERS, _run_desktop_app
from maop.delegate.models import AgentConfig


# ======================================================================
# 辅助函数
# ======================================================================


def _config(**overrides: Any) -> AgentConfig:
    """构造 AgentConfig，默认 driver=desktop_app，可覆盖任意字段。"""
    defaults: dict[str, Any] = {
        "name": "test-desktop-agent",
        "cli": "cursor",
        "driver": "desktop_app",
        "model": "test-model",
        "timeout_s": 30,
        "process_name": "Cursor.exe",
        "ipc_path": r"\\.\pipe\cursor",
        "http_url": "http://localhost:3456",
        "fallback_order": ["cli", "ipc", "http"],
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


# ======================================================================
# 1.1 adapter_factory 测试
# ======================================================================


class TestBuildAdapter:
    """测试 build_adapter 工厂函数对各 driver 类型的分支构建。"""

    def test_build_adapter_cli(self):
        """driver=cli 时返回 CLIAdapter 实例。"""
        config = _config(driver="cli", cli="echo", cli_args="hello")
        adapter = build_adapter(config)
        assert isinstance(adapter, CLIAdapter), "driver=cli 应返回 CLIAdapter"

    def test_build_adapter_desktop_app(self):
        """driver=desktop_app 时返回 DesktopAppAdapter，字段正确传递。"""
        config = _config(
            driver="desktop_app",
            name="cursor",
            cli="cursor",
            cli_args="--arg1 value1",
            process_name="Cursor.exe",
            ipc_path=r"\\.\pipe\cursor",
            http_url="http://localhost:3456",
            fallback_order=["cli", "ipc", "http"],
            timeout_s=45,
        )
        adapter = build_adapter(config)
        assert isinstance(adapter, DesktopAppAdapter), "driver=desktop_app 应返回 DesktopAppAdapter"
        # 验证字段正确传递到内部 _app_config
        app_cfg: DesktopAppConfig = adapter._app_config
        assert app_cfg.app_name == "cursor"
        assert app_cfg.cli_command == "cursor"
        assert app_cfg.process_name == "Cursor.exe"
        assert app_cfg.ipc_path == r"\\.\pipe\cursor"
        assert app_cfg.http_url == "http://localhost:3456"
        assert app_cfg.fallback_order == ["cli", "ipc", "http"]
        assert app_cfg.cli_timeout_s == 45.0

    def test_build_adapter_ide_extension(self):
        """driver=ide_extension 时返回 IDEExtensionAdapter。"""
        config = _config(
            driver="ide_extension",
            name="github.copilot",
            http_url="ws://localhost:7890",
            timeout_s=60,
        )
        adapter = build_adapter(config)
        assert isinstance(adapter, IDEExtensionAdapter), "driver=ide_extension 应返回 IDEExtensionAdapter"

    def test_build_adapter_vibe_coding(self):
        """driver=vibe_coding 时返回 VibeCodingAdapter。"""
        config = _config(
            driver="vibe_coding",
            name="bolt-new",
            http_url="https://bolt.new",
            timeout_s=300,
        )
        adapter = build_adapter(config)
        assert isinstance(adapter, VibeCodingAdapter), "driver=vibe_coding 应返回 VibeCodingAdapter"

    def test_build_adapter_unknown_driver(self):
        """未知 driver 回退 CLIAdapter 并记录 warning。"""
        config = _config(driver="unknown_driver_xyz", cli="echo")
        with patch("maop.core.agent.adapters.adapter_factory.logger") as mock_logger:
            adapter = build_adapter(config)
            # 应回退为 CLIAdapter
            assert isinstance(adapter, CLIAdapter), "未知 driver 应回退 CLIAdapter"
            # 应记录 warning 日志
            mock_logger.warning.assert_called_once()

    def test_build_adapter_none_fields(self):
        """可选字段为 None 时不崩溃（process_name/ipc_path/http_url/fallback_order）。"""
        config = _config(
            driver="desktop_app",
            name="test-app",
            cli="",
            process_name=None,
            ipc_path=None,
            http_url=None,
            fallback_order=None,
        )
        adapter = build_adapter(config)
        assert isinstance(adapter, DesktopAppAdapter), "None 字段不应导致构建失败"
        app_cfg: DesktopAppConfig = adapter._app_config
        # None 字段应被替换为默认空值
        assert app_cfg.process_name == ""
        assert app_cfg.ipc_path == ""
        assert app_cfg.http_url == ""
        assert app_cfg.fallback_order == ["cli", "ipc", "http"]


# ======================================================================
# 1.2 driver 函数测试
# ======================================================================


class TestDriverFunctions:
    """测试 DRIVERS 注册表和 _run_desktop_app 生命周期。"""

    def test_run_desktop_app_registered(self):
        """DRIVERS["desktop_app"] 存在且是 coroutine function。"""
        assert "desktop_app" in DRIVERS, "desktop_app 应在 DRIVERS 注册表中"
        fn = DRIVERS["desktop_app"]
        assert inspect.iscoroutinefunction(fn), "desktop_app driver 应是 coroutine function"

    def test_run_ide_extension_registered(self):
        """DRIVERS["ide_extension"] 存在且是 coroutine function。"""
        assert "ide_extension" in DRIVERS, "ide_extension 应在 DRIVERS 注册表中"
        fn = DRIVERS["ide_extension"]
        assert inspect.iscoroutinefunction(fn), "ide_extension driver 应是 coroutine function"

    def test_run_vibe_coding_registered(self):
        """DRIVERS["vibe_coding"] 存在且是 coroutine function。"""
        assert "vibe_coding" in DRIVERS, "vibe_coding 应在 DRIVERS 注册表中"
        fn = DRIVERS["vibe_coding"]
        assert inspect.iscoroutinefunction(fn), "vibe_coding driver 应是 coroutine function"

    @pytest.mark.asyncio
    async def test_run_desktop_app_mock(self):
        """mock build_adapter，验证 connect→execute→disconnect 调用顺序。"""
        config = _config(driver="desktop_app", cli="cursor")

        # 创建 mock 适配器，记录调用顺序
        mock_adapter = MagicMock()
        mock_adapter.connect.return_value = True
        mock_adapter.execute.return_value = "task completed"
        mock_adapter.disconnect.return_value = None

        with patch(
            "maop.core.agent.adapters.adapter_factory.build_adapter",
            return_value=mock_adapter,
        ):
            result = await _run_desktop_app(config, "test task", 10, ".", "trace-1")

        # 验证返回成功的 MaopResult
        assert result.exit_code == 0
        assert result.stdout == "task completed"
        assert result.driver == "desktop_app"
        # 验证调用顺序：connect → execute → disconnect
        mock_adapter.connect.assert_called_once()
        mock_adapter.execute.assert_called_once_with("test task")
        mock_adapter.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_desktop_app_connect_failure(self):
        """connect 返回 False 时返回错误 MaopResult。"""
        config = _config(driver="desktop_app", cli="cursor")

        mock_adapter = MagicMock()
        mock_adapter.connect.return_value = False  # 连接失败
        mock_adapter.disconnect.return_value = None

        with patch(
            "maop.core.agent.adapters.adapter_factory.build_adapter",
            return_value=mock_adapter,
        ):
            result = await _run_desktop_app(config, "test task", 10, ".", "trace-2")

        # 应返回错误结果
        assert result.exit_code == -2
        assert "连接失败" in result.error or "failed" in result.error.lower()
        # connect 失败时未进入 try/finally，disconnect 不应被调用
        # （连接未建立，无需断开 — 这是正确行为）
        mock_adapter.disconnect.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_desktop_app_execute_timeout(self):
        """execute 超时返回错误 MaopResult。"""
        config = _config(driver="desktop_app", cli="cursor")

        mock_adapter = MagicMock()
        mock_adapter.connect.return_value = True

        # execute 模拟超时：sleep 超过 timeout
        def _slow_execute(task: str) -> str:
            import time
            time.sleep(5)
            return "should not reach"

        mock_adapter.execute.side_effect = _slow_execute
        mock_adapter.disconnect.return_value = None

        with patch(
            "maop.core.agent.adapters.adapter_factory.build_adapter",
            return_value=mock_adapter,
        ):
            # timeout=1 秒，execute sleep 5 秒 → 超时
            result = await _run_desktop_app(config, "test task", 1, ".", "trace-3")

        # 应返回超时错误
        assert result.exit_code == -1
        assert "TIMEOUT" in result.error or "timeout" in result.error.lower()


# ======================================================================
# 1.3 配置传递测试
# ======================================================================


class TestConfigPassing:
    """测试 AgentDef / AgentConfig 新字段及 _make_config 传递。"""

    def test_agent_def_new_fields(self):
        """AgentDef 有 process_name/ipc_path/http_url/fallback_order 字段，默认 None。"""
        from maop.config.loader import AgentDef

        defn = AgentDef()
        assert defn.process_name is None, "AgentDef.process_name 默认应为 None"
        assert defn.ipc_path is None, "AgentDef.ipc_path 默认应为 None"
        assert defn.http_url is None, "AgentDef.http_url 默认应为 None"
        assert defn.fallback_order is None, "AgentDef.fallback_order 默认应为 None"
        # 验证字段可设置
        defn2 = AgentDef(
            process_name="Cursor.exe",
            ipc_path=r"\\.\pipe\cursor",
            http_url="http://localhost:3456",
            fallback_order=["cli", "ipc", "http"],
        )
        assert defn2.process_name == "Cursor.exe"
        assert defn2.ipc_path == r"\\.\pipe\cursor"
        assert defn2.http_url == "http://localhost:3456"
        assert defn2.fallback_order == ["cli", "ipc", "http"]

    def test_agent_config_new_fields(self):
        """AgentConfig 有对应的新字段。"""
        cfg = AgentConfig(name="test")
        assert cfg.process_name is None, "AgentConfig.process_name 默认应为 None"
        assert cfg.ipc_path is None, "AgentConfig.ipc_path 默认应为 None"
        assert cfg.http_url is None, "AgentConfig.http_url 默认应为 None"
        assert cfg.fallback_order is None, "AgentConfig.fallback_order 默认应为 None"
        # 验证字段可设置
        cfg2 = AgentConfig(
            name="test",
            process_name="Cursor.exe",
            ipc_path=r"\\.\pipe\cursor",
            http_url="http://localhost:3456",
            fallback_order=["cli", "ipc", "http"],
        )
        assert cfg2.process_name == "Cursor.exe"
        assert cfg2.ipc_path == r"\\.\pipe\cursor"
        assert cfg2.http_url == "http://localhost:3456"
        assert cfg2.fallback_order == ["cli", "ipc", "http"]

    def test_make_config_passes_fields(self):
        """_make_config 正确传递新字段到 AgentConfig。"""
        from maop.config.loader import AgentDef
        from maop.delegate.agent_resolver import AgentResolver

        # 构造 AgentDef 带新字段
        defn = AgentDef(
            cli="cursor",
            driver="desktop_app",
            process_name="Cursor.exe",
            ipc_path=r"\\.\pipe\cursor",
            http_url="http://localhost:3456",
            fallback_order=["cli", "ipc", "http"],
            timeout_s=45,
        )

        # AgentResolver 需要一个 config 对象，用 mock
        mock_config = MagicMock()
        mock_config.agents = {"test-agent": defn}
        mock_config._version = 0

        resolver = AgentResolver(config=mock_config)
        cfg = resolver._make_config("test-agent", defn)

        # 验证新字段正确传递
        assert cfg.process_name == "Cursor.exe", "process_name 应传递到 AgentConfig"
        assert cfg.ipc_path == r"\\.\pipe\cursor", "ipc_path 应传递到 AgentConfig"
        assert cfg.http_url == "http://localhost:3456", "http_url 应传递到 AgentConfig"
        assert cfg.fallback_order == ["cli", "ipc", "http"], "fallback_order 应传递到 AgentConfig"
        assert cfg.driver == "desktop_app"
        assert cfg.timeout_s == 45

    def test_make_config_safe_opt_magicmock(self):
        """MagicMock 不会导致验证失败（_safe_opt 将非 str 值转为 None）。"""
        from maop.config.loader import AgentDef
        from maop.delegate.agent_resolver import AgentResolver

        # 用 MagicMock 作为 defn，模拟测试中常见的 mock 场景
        mock_defn = MagicMock()
        mock_defn.cli = "cursor"
        mock_defn.driver = "desktop_app"
        mock_defn.cli_args = ""
        mock_defn.capabilities = []
        mock_defn.timeout_s = 30
        mock_defn.model = ""
        mock_defn.provider = ""
        mock_defn.wrapper = ""
        mock_defn.command = ""
        # process_name 等字段是 MagicMock 自动生成的属性（非 str）
        # _safe_opt 应将它们转为 None

        mock_config = MagicMock()
        mock_config.agents = {"mock-agent": mock_defn}
        mock_config._version = 0

        resolver = AgentResolver(config=mock_config)
        # 不应抛异常
        cfg = resolver._make_config("mock-agent", mock_defn)
        # MagicMock 的非 str 属性应被 _safe_opt 转为 None
        assert cfg.process_name is None, "MagicMock 属性应被 _safe_opt 转为 None"
        assert cfg.ipc_path is None
        assert cfg.http_url is None
        assert cfg.fallback_order is None


# ======================================================================
# 1.4 适配器导出测试
# ======================================================================


class TestAdaptersExport:
    """测试 adapters 包正确导出新适配器。"""

    def test_adapters_export(self):
        """from maop.core.agent.adapters import DesktopAppAdapter, IDEExtensionAdapter, VibeCodingAdapter 成功。"""
        # 已在文件顶部 import，这里验证它们是可调用的类
        assert isinstance(DesktopAppAdapter, type), "DesktopAppAdapter 应是类"
        assert isinstance(IDEExtensionAdapter, type), "IDEExtensionAdapter 应是类"
        assert isinstance(VibeCodingAdapter, type), "VibeCodingAdapter 应是类"

        # 验证可以通过包路径直接导入
        from maop.core.agent.adapters import (
            DesktopAppAdapter as _DAA,
            IDEExtensionAdapter as _IEA,
            VibeCodingAdapter as _VCA,
        )
        assert _DAA is DesktopAppAdapter
        assert _IEA is IDEExtensionAdapter
        assert _VCA is VibeCodingAdapter

        # 验证 Config 类也可导入
        from maop.core.agent.adapters import (
            DesktopAppConfig as _DAC,
            IDEExtensionConfig as _IEC,
            VibeCodingConfig as _VCC,
        )
        assert isinstance(_DAC, type)
        assert isinstance(_IEC, type)
        assert isinstance(_VCC, type)

# ======================================================================
# 1.5 健康检查集成测试
# ======================================================================


class TestHealthCheckDiscoveryIntegration:
    """测试 HealthCheckScheduler 与 AgentDiscovery 的集成。

    验证 discover_desktop_apps() 结果正确接入健康检查周期，
    且不改变现有 CLI agent 的健康检查行为。
    """

    def test_check_once_without_discovery_backward_compatible(self):
        """不传 discovery 参数时行为不变（向后兼容）。"""
        from maop.core.agent.router.health_check_scheduler import (
            HealthCheckScheduler,
        )

        mock_catalog = MagicMock()
        mock_catalog.list_all.return_value = []
        scheduler = HealthCheckScheduler(
            catalog=mock_catalog,
            adapters={},
        )
        # 不应抛异常
        scheduler.check_once()
        # _discovery 应为 None
        assert scheduler._discovery is None

    def test_check_once_with_discovery_checks_desktop_apps(self):
        """传 discovery 参数时，check_once 调用 discover_desktop_apps()。"""
        from maop.core.agent.discovery.agent_discovery import (
            AgentDiscovery,
            DiscoveredAgent,
        )
        from maop.core.agent.router.health_check_scheduler import (
            HealthCheckScheduler,
        )

        # mock discovery
        mock_discovery = MagicMock(spec=AgentDiscovery)
        mock_discovery.discover_desktop_apps.return_value = [
            DiscoveredAgent(name="cursor", source="desktop", path="/usr/bin/cursor"),
            DiscoveredAgent(name="trae", source="desktop", path="/usr/bin/trae"),
        ]
        # _check_process 返回 True（运行中）/ False（未运行）
        mock_discovery._check_process.side_effect = [True, False]

        mock_catalog = MagicMock()
        mock_catalog.list_all.return_value = []
        scheduler = HealthCheckScheduler(
            catalog=mock_catalog,
            adapters={},
            discovery=mock_discovery,
        )
        scheduler.check_once()

        # 验证 discover_desktop_apps 被调用
        mock_discovery.discover_desktop_apps.assert_called_once()
        # 验证 _check_process 被调用两次（cursor + trae）
        assert mock_discovery._check_process.call_count == 2
        # 验证结果写入 _statuses
        statuses = scheduler.get_status()
        assert "desktop:cursor" in statuses
        assert statuses["desktop:cursor"].healthy is True
        assert "desktop:trae" in statuses
        assert statuses["desktop:trae"].healthy is False

    def test_check_once_discovery_skips_registered_agents(self):
        """已在 _adapters 中注册的 Agent 跳过 discovery 检查（避免重复）。"""
        from maop.core.agent.discovery.agent_discovery import (
            AgentDiscovery,
            DiscoveredAgent,
        )
        from maop.core.agent.router.health_check_scheduler import (
            HealthCheckScheduler,
        )

        mock_discovery = MagicMock(spec=AgentDiscovery)
        mock_discovery.discover_desktop_apps.return_value = [
            DiscoveredAgent(name="cursor", source="desktop", path="/usr/bin/cursor"),
        ]

        mock_catalog = MagicMock()
        mock_catalog.list_all.return_value = []
        # cursor 已在 adapters 中注册 → 应跳过 discovery 检查
        mock_adapter = MagicMock()
        scheduler = HealthCheckScheduler(
            catalog=mock_catalog,
            adapters={"cursor": mock_adapter},
            discovery=mock_discovery,
        )
        scheduler.check_once()

        # _check_process 不应被调用（cursor 已跳过）
        mock_discovery._check_process.assert_not_called()
        statuses = scheduler.get_status()
        assert "desktop:cursor" not in statuses

    def test_check_once_discovery_failure_does_not_crash(self):
        """discovery 抛异常时不影响现有 Agent 检查。"""
        from maop.core.agent.router.health_check_scheduler import (
            HealthCheckScheduler,
        )

        mock_discovery = MagicMock()
        mock_discovery.discover_desktop_apps.side_effect = RuntimeError("discovery failed")

        mock_catalog = MagicMock()
        mock_catalog.list_all.return_value = []
        scheduler = HealthCheckScheduler(
            catalog=mock_catalog,
            adapters={},
            discovery=mock_discovery,
        )
        # 不应抛异常
        scheduler.check_once()

    def test_check_once_cli_agent_unaffected_by_discovery(self):
        """discovery 不改变现有 CLI agent 的健康检查行为。"""
        from maop.core.agent.discovery.agent_discovery import (
            AgentDiscovery,
            DiscoveredAgent,
        )
        from maop.core.agent.router.health_check_scheduler import (
            HealthCheckScheduler,
        )

        # CLI agent 适配器
        mock_cli_adapter = MagicMock()
        mock_cli_adapter.health_check.return_value = True

        mock_discovery = MagicMock(spec=AgentDiscovery)
        mock_discovery.discover_desktop_apps.return_value = [
            DiscoveredAgent(name="cursor", source="desktop", path="/usr/bin/cursor"),
        ]
        mock_discovery._check_process.return_value = True

        # catalog 中有一个 CLI agent
        mock_cli_agent = MagicMock()
        mock_cli_agent.name = "cli-agent"
        mock_catalog = MagicMock()
        mock_catalog.list_all.return_value = [mock_cli_agent]

        scheduler = HealthCheckScheduler(
            catalog=mock_catalog,
            adapters={"cli-agent": mock_cli_adapter},
            discovery=mock_discovery,
        )
        scheduler.check_once()

        # CLI agent 的 health_check 应被正常调用
        mock_cli_adapter.health_check.assert_called_once()
        # catalog.update_health 应被调用（CLI agent 的健康状态回写）
        mock_catalog.update_health.assert_any_call("cli-agent", True)
        # discovery 也被调用
        mock_discovery.discover_desktop_apps.assert_called_once()
        # 两者结果都在 _statuses 中
        statuses = scheduler.get_status()
        assert "cli-agent" in statuses
        assert "desktop:cursor" in statuses