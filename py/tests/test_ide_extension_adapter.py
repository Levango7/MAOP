"""``IDEExtensionAdapter`` 测试 — IDE 插件调度适配器。

测试覆盖：
  - connect: WebSocket 连接成功 / 失败
  - execute: 通过 Companion 执行 / 插件未找到 / 超时
  - health_check: 已连接 / 未连接
  - disconnect: 断开连接
  - reconnect: 重连成功 / 达到最大次数
  - config: 配置校验
  - command_protocol: 命令协议格式

所有测试 mock ``websockets`` 库，不进行真实 WebSocket 连接。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maop.core.agent.adapters.ide_extension_adapter import (
    IDEExtensionAdapter,
    IDEExtensionConfig,
)


# ======================================================================
# 辅助函数
# ======================================================================


def _make_config(
    extension_id: str = "github.copilot",
    ide_type: str = "vscode",
    companion_url: str = "ws://localhost:7890",
    **kwargs: Any,
) -> IDEExtensionConfig:
    """构造测试用配置。"""
    params: dict[str, Any] = {
        "extension_id": extension_id,
        "ide_type": ide_type,
        "companion_url": companion_url,
    }
    params.update(kwargs)
    return IDEExtensionConfig(**params)


def _make_ws_mock(
    recv_response: str = '{"success": true, "result": "ok"}',
    recv_side_effect: Any = None,
) -> MagicMock:
    """构造 mock WebSocket 连接对象。

    Parameters
    ----------
    recv_response : str
        ``recv`` 默认返回的响应字符串。
    recv_side_effect : Any
        若提供，作为 ``recv`` 的 ``side_effect``（优先于 ``recv_response``）。
    """
    ws = MagicMock()
    ws.send = AsyncMock()
    if recv_side_effect is not None:
        ws.recv = AsyncMock(side_effect=recv_side_effect)
    else:
        ws.recv = AsyncMock(return_value=recv_response)
    ws.close = AsyncMock()
    return ws


def _patch_websockets_connect(
    monkeypatch: pytest.MonkeyPatch,
    ws_mock: MagicMock | None = None,
    connect_side_effect: Any = None,
) -> MagicMock:
    """mock ``websockets.connect``，模拟 WebSocket 连接。

    Parameters
    ----------
    ws_mock : MagicMock | None
        连接成功时返回的 WebSocket 对象。为 ``None`` 时自动创建。
    connect_side_effect : Any
        若提供，作为 ``connect`` 的 ``side_effect``（模拟连接异常）。
    """
    import websockets

    if ws_mock is None:
        ws_mock = _make_ws_mock()

    mock_connect = AsyncMock(return_value=ws_mock)
    if connect_side_effect is not None:
        mock_connect.side_effect = connect_side_effect

    monkeypatch.setattr(websockets, "connect", mock_connect)
    return ws_mock


# ======================================================================
# connect 测试
# ======================================================================


class TestConnect:
    """connect() — 通过 WebSocket 连接 Companion。"""

    def test_connect_success(self, monkeypatch):
        """WebSocket 连接成功时 connect 返回 True。"""
        ws_mock = _patch_websockets_connect(monkeypatch)
        config = _make_config()
        adapter = IDEExtensionAdapter(config)

        assert adapter.connect() is True
        assert adapter._connected is True
        assert adapter._ws is ws_mock

    def test_connect_failure(self, monkeypatch):
        """WebSocket 连接失败时 connect 返回 False。"""
        _patch_websockets_connect(
            monkeypatch,
            connect_side_effect=ConnectionRefusedError("连接被拒绝"),
        )
        config = _make_config()
        adapter = IDEExtensionAdapter(config)

        assert adapter.connect() is False
        assert adapter._connected is False
        assert adapter._ws is None

    def test_connect_already_connected(self, monkeypatch):
        """已连接时再次 connect 直接返回 True，不重复连接。"""
        ws_mock = _patch_websockets_connect(monkeypatch)
        config = _make_config()
        adapter = IDEExtensionAdapter(config)

        # 第一次连接
        assert adapter.connect() is True
        # 第二次连接应直接返回，不再次调用 websockets.connect
        import websockets

        call_count_before = websockets.connect.call_count
        assert adapter.connect() is True
        assert websockets.connect.call_count == call_count_before


# ======================================================================
# execute 测试
# ======================================================================


class TestExecute:
    """execute() — 通过 Companion 调用 IDE 插件。"""

    def test_execute_via_companion(self, monkeypatch):
        """通过 Companion 成功执行任务。"""
        response = json.dumps({
            "success": True,
            "result": "代码分析完成：无问题",
            "error": "",
        })
        ws_mock = _patch_websockets_connect(monkeypatch, ws_mock=_make_ws_mock(response))
        config = _make_config()
        adapter = IDEExtensionAdapter(config)
        adapter.connect()

        result = adapter.execute("分析这段代码")
        assert result == "代码分析完成：无问题"
        # 验证发送的消息格式
        sent_msg = ws_mock.send.call_args[0][0]
        sent = json.loads(sent_msg)
        assert sent["action"] == "execute"
        assert sent["extension_id"] == "github.copilot"
        assert sent["task"] == "分析这段代码"
        assert "timeout_s" in sent

    def test_execute_extension_not_found(self, monkeypatch):
        """插件未找到时 execute 抛 RuntimeError。"""
        response = json.dumps({
            "success": False,
            "result": "",
            "error": "Extension not found: github.copilot",
        })
        _patch_websockets_connect(monkeypatch, ws_mock=_make_ws_mock(response))
        config = _make_config()
        adapter = IDEExtensionAdapter(config)
        adapter.connect()

        with pytest.raises(RuntimeError, match="Extension not found"):
            adapter.execute("test task")

    def test_execute_timeout(self, monkeypatch):
        """命令超时时 execute 抛 TimeoutError。"""
        # recv 抛 asyncio.TimeoutError 模拟超时
        ws_mock = _patch_websockets_connect(monkeypatch)
        ws_mock.recv = AsyncMock(side_effect=asyncio.TimeoutError("超时"))
        config = _make_config(command_timeout_s=0.1)
        adapter = IDEExtensionAdapter(config)
        adapter.connect()

        # 超时后会尝试重连，重连也用 mock 的 connect（成功），但 recv 仍超时
        # 最终重连成功后再次超时 → 抛 TimeoutError
        with pytest.raises((TimeoutError, RuntimeError)):
            adapter.execute("test task")

    def test_execute_auto_connect(self, monkeypatch):
        """未连接时 execute 自动连接。"""
        response = json.dumps({
            "success": True,
            "result": "ok",
            "error": "",
        })
        _patch_websockets_connect(monkeypatch, ws_mock=_make_ws_mock(response))
        config = _make_config()
        adapter = IDEExtensionAdapter(config)
        # 不手动 connect，直接 execute
        result = adapter.execute("test task")
        assert result == "ok"
        assert adapter._connected is True


# ======================================================================
# health_check 测试
# ======================================================================


class TestHealthCheck:
    """health_check() — 检查 Companion 连接和插件可用性。"""

    def test_health_check_connected(self, monkeypatch):
        """已连接且插件可用时 health_check 返回 True。"""
        # ping 和 health 两次调用，都返回成功
        responses = [
            json.dumps({"success": True, "result": "pong"}),
            json.dumps({"success": True, "result": "healthy"}),
        ]
        ws_mock = _patch_websockets_connect(
            monkeypatch,
            ws_mock=_make_ws_mock(recv_side_effect=responses),
        )
        config = _make_config()
        adapter = IDEExtensionAdapter(config)
        adapter.connect()

        assert adapter.health_check() is True

    def test_health_check_disconnected(self, monkeypatch):
        """未连接且无法连接时 health_check 返回 False。"""
        _patch_websockets_connect(
            monkeypatch,
            connect_side_effect=ConnectionRefusedError("无法连接"),
        )
        config = _make_config()
        adapter = IDEExtensionAdapter(config)
        # 不连接，直接 health_check
        assert adapter.health_check() is False

    def test_health_check_plugin_unhealthy(self, monkeypatch):
        """Companion 连通但插件不健康时返回 False。"""
        responses = [
            json.dumps({"success": True, "result": "pong"}),  # ping 成功
            json.dumps({"success": False, "error": "插件未激活"}),  # health 失败
        ]
        _patch_websockets_connect(
            monkeypatch,
            ws_mock=_make_ws_mock(recv_side_effect=responses),
        )
        config = _make_config()
        adapter = IDEExtensionAdapter(config)
        adapter.connect()

        assert adapter.health_check() is False


# ======================================================================
# disconnect 测试
# =====================================================================


class TestDisconnect:
    """disconnect() — 断开 Companion 连接。"""

    def test_disconnect(self, monkeypatch):
        """disconnect 断开连接并重置状态。"""
        ws_mock = _patch_websockets_connect(monkeypatch)
        config = _make_config()
        adapter = IDEExtensionAdapter(config)
        adapter.connect()
        assert adapter._connected is True

        adapter.disconnect()
        assert adapter._connected is False
        assert adapter._ws is None
        # 验证 WebSocket close 被调用
        ws_mock.close.assert_awaited()

    def test_disconnect_when_not_connected(self):
        """未连接时 disconnect 不抛异常。"""
        config = _make_config()
        adapter = IDEExtensionAdapter(config)
        # 不应抛异常
        adapter.disconnect()
        assert adapter._connected is False


# ======================================================================
# reconnect 测试
# ======================================================================


class TestReconnect:
    """_reconnect() — 重连逻辑。"""

    def test_reconnect(self, monkeypatch):
        """重连成功时返回 True。"""
        ws_mock = _patch_websockets_connect(monkeypatch)
        config = _make_config(reconnect_interval_s=0.01)
        adapter = IDEExtensionAdapter(config)
        adapter.connect()

        # 手动破坏连接后重连
        adapter._ws = None
        adapter._connected = False
        assert adapter._reconnect() is True
        assert adapter._connected is True
        assert adapter._ws is ws_mock

    def test_reconnect_max_attempts(self, monkeypatch):
        """达到最大重连次数仍失败时返回 False。"""
        _patch_websockets_connect(
            monkeypatch,
            connect_side_effect=ConnectionRefusedError("始终失败"),
        )
        config = _make_config(
            max_reconnect=3,
            reconnect_interval_s=0.01,
        )
        adapter = IDEExtensionAdapter(config)

        assert adapter._reconnect() is False
        assert adapter._connected is False
        assert adapter._ws is None

    def test_reconnect_zero_max(self, monkeypatch):
        """max_reconnect=0 时不重连，直接返回 False。"""
        _patch_websockets_connect(
            monkeypatch,
            connect_side_effect=ConnectionRefusedError("失败"),
        )
        config = _make_config(max_reconnect=0)
        adapter = IDEExtensionAdapter(config)

        assert adapter._reconnect() is False


# ======================================================================
# 配置校验测试
# =====================================================================


class TestConfigValidation:
    """IDEExtensionConfig — 配置校验。"""

    def test_config_validation_defaults(self):
        """配置默认值正确。"""
        config = _make_config()
        assert config.extension_id == "github.copilot"
        assert config.ide_type == "vscode"
        assert config.companion_url == "ws://localhost:7890"
        assert config.companion_token == ""
        assert config.command_timeout_s == 30.0
        assert config.reconnect_interval_s == 5.0
        assert config.max_reconnect == 3

    def test_config_validation_invalid_ide_type(self):
        """不支持的 IDE 类型时抛 ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="不支持的 IDE 类型"):
            IDEExtensionConfig(
                extension_id="github.copilot",
                ide_type="unknown_ide",
                companion_url="ws://localhost:7890",
            )

    def test_config_validation_invalid_url(self):
        """companion_url 不以 ws:// 开头时抛 ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="必须以"):
            IDEExtensionConfig(
                extension_id="github.copilot",
                ide_type="vscode",
                companion_url="http://localhost:7890",
            )

    def test_config_validation_empty_url(self):
        """companion_url 为空时抛 ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="不能为空"):
            IDEExtensionConfig(
                extension_id="github.copilot",
                ide_type="vscode",
                companion_url="",
            )

    def test_config_validation_negative_timeout(self):
        """command_timeout_s 为非正数时抛 ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="必须为正数"):
            _make_config(command_timeout_s=0)

    def test_config_validation_negative_max_reconnect(self):
        """max_reconnect 为负数时抛 ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="不能为负数"):
            _make_config(max_reconnect=-1)

    def test_config_validation_extra_field_forbidden(self):
        """多余字段被禁止。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            IDEExtensionConfig(
                extension_id="github.copilot",
                ide_type="vscode",
                companion_url="ws://localhost:7890",
                unknown_field="value",  # type: ignore[call-arg]
            )


# ======================================================================
# 命令协议格式测试
# =====================================================================


class TestCommandProtocol:
    """命令协议格式 — 确保发送的 JSON 符合协议规范。"""

    def test_command_protocol_format(self):
        """_build_command 生成的命令包含所有必需字段。"""
        config = _make_config(extension_id="alibaba.tongyi-lingma")
        adapter = IDEExtensionAdapter(config)

        # execute 命令
        cmd = adapter._build_command("execute", task="分析代码", timeout_s=60.0)
        assert cmd["action"] == "execute"
        assert cmd["extension_id"] == "alibaba.tongyi-lingma"
        assert cmd["task"] == "分析代码"
        assert cmd["timeout_s"] == 60.0

        # health 命令
        cmd = adapter._build_command("health")
        assert cmd["action"] == "health"
        assert cmd["extension_id"] == "alibaba.tongyi-lingma"
        assert cmd["timeout_s"] == 30.0  # 默认值
        # health 命令不应包含 task 字段
        assert "task" not in cmd

        # ping 命令
        cmd = adapter._build_command("ping")
        assert cmd["action"] == "ping"
        assert cmd["extension_id"] == "alibaba.tongyi-lingma"
        assert "task" not in cmd

    def test_command_protocol_invalid_action(self):
        """不支持的 action 抛 ValueError。"""
        config = _make_config()
        adapter = IDEExtensionAdapter(config)
        with pytest.raises(ValueError, match="不支持的 action"):
            adapter._build_command("invalid_action")

    def test_command_protocol_send_format(self, monkeypatch):
        """_send_command 发送的 JSON 格式正确。"""
        response = json.dumps({"success": True, "result": "ok"})
        ws_mock = _patch_websockets_connect(
            monkeypatch,
            ws_mock=_make_ws_mock(response),
        )
        config = _make_config()
        adapter = IDEExtensionAdapter(config)
        adapter.connect()

        command = adapter._build_command("execute", task="test")
        result = adapter._send_command(command)
        assert result == {"success": True, "result": "ok"}

        # 验证发送的消息是合法 JSON 且包含必需字段
        sent_raw = ws_mock.send.call_args[0][0]
        sent = json.loads(sent_raw)
        assert sent["action"] == "execute"
        assert sent["extension_id"] == "github.copilot"
        assert sent["task"] == "test"
        assert "timeout_s" in sent


# ======================================================================
# sync_config 测试
# =====================================================================


class TestSyncConfig:
    """sync_config() — 更新配置。"""

    def test_sync_config_updates_config(self, monkeypatch):
        """sync_config 更新配置并重连。"""
        _patch_websockets_connect(monkeypatch)
        config = _make_config()
        adapter = IDEExtensionAdapter(config)
        adapter.connect()

        # 更新配置
        adapter.sync_config({
            "extension_id": "alibaba.tongyi-lingma",
            "ide_type": "jetbrains",
            "companion_url": "ws://localhost:9999",
        })
        assert adapter._config.extension_id == "alibaba.tongyi-lingma"
        assert adapter._config.ide_type == "jetbrains"
        assert adapter._config.companion_url == "ws://localhost:9999"

    def test_sync_config_not_reconnect_when_disconnected(self, monkeypatch):
        """未连接时 sync_config 不自动重连。"""
        connect_mock = _patch_websockets_connect(monkeypatch)
        config = _make_config()
        adapter = IDEExtensionAdapter(config)
        # 不连接，直接 sync_config
        adapter.sync_config({
            "extension_id": "alibaba.tongyi-lingma",
            "ide_type": "vscode",
            "companion_url": "ws://localhost:9999",
        })
        assert adapter._connected is False
        assert adapter._config.extension_id == "alibaba.tongyi-lingma"