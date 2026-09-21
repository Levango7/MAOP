"""``DesktopAppAdapter`` 测试 — 桌面 IDE 应用适配器。

测试覆盖：
  - connect: CLI / IPC / HTTP 各自可用时连接成功，全部不可用时失败
  - execute: CLI 优先、回退 IPC、回退 HTTP、全部失败抛异常
  - health_check: 进程运行 / 不运行
  - fallback_order: 自定义优先级
  - config: 配置校验
"""
from __future__ import annotations

import socket
import subprocess
import sys
from typing import Any  # noqa: F401
from unittest.mock import MagicMock, patch  # noqa: F401

import pytest

from maop.core.agent.adapters.cli_adapter import CLIAdapter
from maop.core.agent.adapters.desktop_app_adapter import (
    DesktopAppAdapter,
    DesktopAppConfig,
)

# Python 解释器命令（basename "python" 在白名单内）
PYTHON_CMD = sys.executable


# ======================================================================
# 辅助函数
# ======================================================================


def _make_subprocess_result(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess:
    """构造 ``subprocess.CompletedProcess`` 用于 mock。"""
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _patch_cli_execute(
    monkeypatch: pytest.MonkeyPatch, return_value: str | None = None, side_effect=None
) -> MagicMock:
    """mock ``CLIAdapter.execute``，控制 CLI 执行的返回值或异常。"""
    mock_fn = MagicMock()
    if side_effect is not None:
        mock_fn.side_effect = side_effect
    else:
        mock_fn.return_value = return_value
    monkeypatch.setattr(CLIAdapter, "execute", mock_fn)
    return mock_fn


def _patch_ipc_socket(
    monkeypatch: pytest.MonkeyPatch, response_bytes: bytes = b'{"result": "ok"}'
) -> MagicMock:
    """mock ``socket.socket``，模拟 IPC 通信。

    确保平台支持 AF_UNIX（Windows 旧版本默认不支持）。
    """
    # 确保 AF_UNIX 存在（Windows 旧版本没有）
    if not hasattr(socket, "AF_UNIX"):
        monkeypatch.setattr(socket, "AF_UNIX", 1, raising=False)

    mock_sock = MagicMock()
    # recv 先返回响应数据，再返回空字节表示结束
    mock_sock.recv.side_effect = [response_bytes, b""]
    mock_socket_class = MagicMock(return_value=mock_sock)
    monkeypatch.setattr(socket, "socket", mock_socket_class)
    return mock_sock


def _patch_http_client(
    monkeypatch: pytest.MonkeyPatch, response_text: str = "http_result"
) -> MagicMock:
    """mock ``httpx.Client``，模拟 HTTP 通信。"""
    import httpx

    mock_resp = MagicMock()
    mock_resp.text = response_text
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    mock_client_class = MagicMock(return_value=mock_client)
    monkeypatch.setattr(httpx, "Client", mock_client_class)
    return mock_client


def _patch_process_detection(
    monkeypatch: pytest.MonkeyPatch, running: bool = True, process_name: str = "Cursor.exe"
) -> None:
    """mock ``subprocess.run``，模拟进程检测结果。"""
    if running:
        stdout = f"{process_name}     1234 Console  1  100,000 K"
        # 两平台进程命中时都是 0
        returncode = 0
    else:
        stdout = "INFO: No tasks are running which match the specified criteria."
        # 未命中时的**真实返回码**随平台而异：
        #   - Windows tasklist：返回 0，靠 stdout 里没有进程名来判断
        #   - Linux/Mac pgrep -x：返回 **1**
        # 而 desktop_app_adapter 的 Linux 分支只看 `returncode == 0`。
        # 此前这里恒为 0，导致该用例在 Linux/macOS 上误判为 True（Windows 上
        # 因还检查 stdout 而侥幸通过）——CI 的 ubuntu/macos 因此失败。
        returncode = 0 if sys.platform.startswith("win") else 1
    mock_result = _make_subprocess_result(returncode=returncode, stdout=stdout)
    monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_result))


# ======================================================================
# connect 测试
# ======================================================================


class TestConnect:
    """connect() — 检测桌面应用是否可用。"""

    def test_connect_cli_available(self):
        """CLI 可用时 connect 成功。"""
        config = DesktopAppConfig(
            app_name="cursor",
            cli_command=PYTHON_CMD,
        )
        adapter = DesktopAppAdapter(config)
        assert adapter.connect() is True
        assert adapter._connected is True

    def test_connect_ipc_available(self, monkeypatch):
        """IPC 可用时 connect 成功。"""
        _patch_ipc_socket(monkeypatch)
        config = DesktopAppConfig(
            app_name="cursor",
            ipc_path="/tmp/cursor.sock",
        )
        adapter = DesktopAppAdapter(config)
        assert adapter.connect() is True
        assert adapter._connected is True

    def test_connect_http_available(self):
        """HTTP 可用时 connect 成功。"""
        config = DesktopAppConfig(
            app_name="cursor",
            http_url="http://localhost:3456",
        )
        adapter = DesktopAppAdapter(config)
        assert adapter.connect() is True
        assert adapter._connected is True

    def test_connect_none_available(self):
        """全部不可用时 connect 失败。"""
        config = DesktopAppConfig(
            app_name="cursor",
            # 全部通信方式配置为空
        )
        adapter = DesktopAppAdapter(config)
        assert adapter.connect() is False
        assert adapter._connected is False


# ======================================================================
# execute 测试
# ======================================================================


class TestExecute:
    """execute() — 按优先级执行任务。"""

    def test_execute_via_cli(self, monkeypatch):
        """CLI 优先执行。"""
        _patch_cli_execute(monkeypatch, return_value="cli_result")
        config = DesktopAppConfig(
            app_name="cursor",
            cli_command=PYTHON_CMD,
            ipc_path="/tmp/cursor.sock",
            http_url="http://localhost:3456",
        )
        adapter = DesktopAppAdapter(config)
        result = adapter.execute("test task")
        assert result == "cli_result"

    def test_execute_fallback_to_ipc(self, monkeypatch):
        """CLI 失败时回退到 IPC。"""
        # CLI 抛异常 → _try_cli 返回 None
        _patch_cli_execute(monkeypatch, side_effect=RuntimeError("cli fail"))
        # IPC 返回成功响应
        _patch_ipc_socket(monkeypatch, response_bytes=b'{"result": "ipc_ok"}')

        config = DesktopAppConfig(
            app_name="cursor",
            cli_command=PYTHON_CMD,
            ipc_path="/tmp/cursor.sock",
        )
        adapter = DesktopAppAdapter(config)
        result = adapter.execute("test task")
        assert result == '{"result": "ipc_ok"}'

    def test_execute_fallback_to_http(self, monkeypatch):
        """CLI 和 IPC 失败时回退到 HTTP。"""
        # CLI 失败
        _patch_cli_execute(monkeypatch, side_effect=RuntimeError("cli fail"))
        # IPC 失败 — socket.connect 抛异常
        if not hasattr(socket, "AF_UNIX"):
            monkeypatch.setattr(socket, "AF_UNIX", 1, raising=False)
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = ConnectionRefusedError("ipc fail")
        monkeypatch.setattr(socket, "socket", MagicMock(return_value=mock_sock))
        # HTTP 成功
        _patch_http_client(monkeypatch, response_text="http_ok")

        config = DesktopAppConfig(
            app_name="cursor",
            cli_command=PYTHON_CMD,
            ipc_path="/tmp/cursor.sock",
            http_url="http://localhost:3456",
        )
        adapter = DesktopAppAdapter(config)
        result = adapter.execute("test task")
        assert result == "http_ok"

    def test_execute_all_fail(self, monkeypatch):
        """全部通信方式失败时抛 RuntimeError。"""
        # CLI 失败
        _patch_cli_execute(monkeypatch, side_effect=RuntimeError("cli fail"))
        # IPC 失败
        if not hasattr(socket, "AF_UNIX"):
            monkeypatch.setattr(socket, "AF_UNIX", 1, raising=False)
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = ConnectionRefusedError("ipc fail")
        monkeypatch.setattr(socket, "socket", MagicMock(return_value=mock_sock))
        # HTTP 失败
        import httpx

        mock_http_client = MagicMock()
        mock_http_client.post.side_effect = httpx.ConnectError("http fail")
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(httpx, "Client", MagicMock(return_value=mock_http_client))

        config = DesktopAppConfig(
            app_name="cursor",
            cli_command=PYTHON_CMD,
            ipc_path="/tmp/cursor.sock",
            http_url="http://localhost:3456",
        )
        adapter = DesktopAppAdapter(config)
        with pytest.raises(RuntimeError, match="全部通信方式"):
            adapter.execute("test task")


# ======================================================================
# health_check 测试
# ======================================================================


class TestHealthCheck:
    """health_check() — 检查桌面应用进程是否运行。"""

    def test_health_check_process_running(self, monkeypatch):
        """进程运行时健康检查返回 True。"""
        _patch_process_detection(monkeypatch, running=True, process_name="Cursor.exe")
        config = DesktopAppConfig(
            app_name="cursor",
            process_name="Cursor.exe",
        )
        adapter = DesktopAppAdapter(config)
        assert adapter.health_check() is True

    def test_health_check_process_not_running(self, monkeypatch):
        """进程不运行时健康检查返回 False。"""
        _patch_process_detection(monkeypatch, running=False, process_name="Cursor.exe")
        config = DesktopAppConfig(
            app_name="cursor",
            process_name="Cursor.exe",
        )
        adapter = DesktopAppAdapter(config)
        assert adapter.health_check() is False


# ======================================================================
# fallback_order 测试
# ======================================================================


class TestFallbackOrder:
    """fallback_order — 自定义通信方式优先级。"""

    def test_fallback_order_custom(self, monkeypatch):
        """自定义优先级：http 优先于 cli。"""
        # CLI 返回结果（但不应该被调用）
        cli_mock = _patch_cli_execute(monkeypatch, return_value="cli_result")
        # HTTP 返回结果
        _patch_http_client(monkeypatch, response_text="http_first")

        config = DesktopAppConfig(
            app_name="cursor",
            cli_command=PYTHON_CMD,
            http_url="http://localhost:3456",
            fallback_order=["http", "cli"],
        )
        adapter = DesktopAppAdapter(config)
        result = adapter.execute("test task")
        # HTTP 应被优先调用
        assert result == "http_first"
        # CLI 不应被调用
        cli_mock.assert_not_called()

    def test_fallback_order_ipc_first(self, monkeypatch):
        """自定义优先级：ipc 优先。"""
        _patch_cli_execute(monkeypatch, return_value="cli_result")
        _patch_ipc_socket(monkeypatch, response_bytes=b'{"result": "ipc_first"}')
        _patch_http_client(monkeypatch, response_text="http_result")

        config = DesktopAppConfig(
            app_name="cursor",
            cli_command=PYTHON_CMD,
            ipc_path="/tmp/cursor.sock",
            http_url="http://localhost:3456",
            fallback_order=["ipc", "cli", "http"],
        )
        adapter = DesktopAppAdapter(config)
        result = adapter.execute("test task")
        assert result == '{"result": "ipc_first"}'


# ======================================================================
# 配置校验测试
# ======================================================================


class TestConfigValidation:
    """DesktopAppConfig — 配置校验。"""

    def test_config_validation_default_fallback_order(self):
        """默认 fallback_order 为 ["cli", "ipc", "http"]。"""
        config = DesktopAppConfig(app_name="cursor")
        assert config.fallback_order == ["cli", "ipc", "http"]

    def test_config_validation_invalid_method(self):
        """fallback_order 包含无效方式时抛 ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="不支持的通信方式"):
            DesktopAppConfig(
                app_name="cursor",
                fallback_order=["cli", "invalid_method"],
            )

    def test_config_validation_empty_fallback_order(self):
        """fallback_order 为空时抛 ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="不能为空"):
            DesktopAppConfig(
                app_name="cursor",
                fallback_order=[],
            )

    def test_config_validation_extra_field_forbidden(self):
        """多余字段被禁止。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DesktopAppConfig(
                app_name="cursor",
                unknown_field="value",  # type: ignore[call-arg]
            )

    def test_config_validation_defaults(self):
        """配置默认值正确。"""
        config = DesktopAppConfig(app_name="cursor")
        assert config.cli_command == ""
        assert config.cli_args == []
        assert config.ipc_path == ""
        assert config.http_url == ""
        assert config.http_token == ""
        assert config.process_name == ""
        assert config.cli_timeout_s == 30.0
        assert config.ipc_timeout_s == 10.0
        assert config.http_timeout_s == 15.0


# ======================================================================
# disconnect / sync_config 测试
# ======================================================================


class TestLifecycle:
    """disconnect() 和 sync_config() 生命周期方法。"""

    def test_disconnect_resets_state(self):
        """disconnect 重置连接状态。"""
        config = DesktopAppConfig(
            app_name="cursor",
            cli_command=PYTHON_CMD,
        )
        adapter = DesktopAppAdapter(config)
        adapter.connect()
        assert adapter._connected is True
        adapter.disconnect()
        assert adapter._connected is False
        assert adapter._active_method == ""

    def test_sync_config_updates_config(self):
        """sync_config 更新配置。"""
        config = DesktopAppConfig(
            app_name="cursor",
            cli_command=PYTHON_CMD,
        )
        adapter = DesktopAppAdapter(config)
        adapter.connect()
        # 更新配置
        adapter.sync_config({
            "app_name": "trae",
            "cli_command": PYTHON_CMD,
            "http_url": "http://localhost:9999",
        })
        assert adapter._app_config.app_name == "trae"
        assert adapter._app_config.http_url == "http://localhost:9999"