"""Tests for ``maop.core.agent.adapters`` — CLI / HTTP / MCPBridge / Web adapters.

测试覆盖：
  - CLIAdapter: 命令执行 / 超时 / 输出解析 / 白名单 / 沙箱 / 环境屏蔽
  - HTTPAdapter: 请求构建 / 响应提取 / 重试 / 超时 / 健康检查
  - MCPBridgeAdapter: 桥接 / 错误处理 / 无 hub
  - WebAdapter: 认证方式 / 请求执行 / 响应提取 / 健康检查
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from maop.core.agent.adapters import (
    CLIAdapter,
    CLIAdapterConfig,
    HTTPAdapter,
    HTTPAdapterConfig,
    MCPBridgeAdapter,
    MCPBridgeAdapterConfig,
    WebAdapter,
    WebAdapterConfig,
)
from maop.core.mcp.mcp_hub_types import ToolResult


@pytest.fixture(autouse=True)
def _no_system_proxy(monkeypatch):
    """Make network-touching adapter tests hermetic.

    Tests that point adapters at ``invalid.localhost`` assume a direct
    connection error (DNS/ refused). When the dev machine has an HTTP proxy
    configured (http_proxy/https_proxy env vars), httpx routes the request
    through the proxy, which answers ``502 Bad Gateway`` instead — changing
    both the control flow and the exception message. Strip proxy vars so the
    client connects directly. Production proxy support is unaffected (this is
    test-only).
    """
    for var in (
        "http_proxy", "https_proxy", "all_proxy",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    ):
        monkeypatch.delenv(var, raising=False)

# sys.executable 的 basename 去掉 .exe 后为 "python"，在白名单中
PYTHON_CMD = sys.executable


# ======================================================================
# 辅助函数
# ======================================================================


def _make_completed_process(
    cmd: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess:
    """构造一个 ``subprocess.CompletedProcess`` 用于 mock。"""
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


def _mock_subprocess_run(stdout: str = "", stderr: str = "", returncode: int = 0):
    """创建一个替换 ``subprocess.run`` 的 mock 函数。"""

    def _run(cmd, **kwargs):
        return _make_completed_process(cmd, returncode, stdout, stderr)

    return _run


def _make_http_client(
    handler: Any, base_url: str = "http://test.local"
) -> httpx.Client:
    """用 ``httpx.MockTransport`` 创建一个 mock HTTP 客户端。"""
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport, base_url=base_url)


# ======================================================================
# CLIAdapter — 配置
# ======================================================================


class TestCLIAdapterConfig:
    def test_config_defaults(self):
        """默认配置值正确。"""
        cfg = CLIAdapterConfig()
        assert cfg.command == ""
        assert cfg.args == []
        assert cfg.timeout_s == 30.0
        assert cfg.output_format == "text"
        assert cfg.task_via_stdin is False
        assert cfg.network_enabled is False

    def test_config_extra_forbid(self):
        """未知字段被拒绝（extra='forbid'）。"""
        with pytest.raises(Exception):
            CLIAdapterConfig(command="python", unknown_field=True)


# ======================================================================
# CLIAdapter — 连接与白名单
# ======================================================================


class TestCLIAdapterConnect:
    def test_connect_success(self):
        """白名单内命令连接成功。"""
        adapter = CLIAdapter(CLIAdapterConfig(command=PYTHON_CMD))
        assert adapter.connect() is True
        assert adapter._connected is True

    def test_connect_empty_command(self):
        """空命令连接失败。"""
        adapter = CLIAdapter(CLIAdapterConfig(command=""))
        assert adapter.connect() is False
        assert adapter._connected is False

    def test_connect_non_whitelisted_command(self):
        """非白名单命令连接失败。"""
        adapter = CLIAdapter(CLIAdapterConfig(command="rm -rf /"))
        assert adapter.connect() is False

    def test_connect_invalid_syntax(self):
        """无效命令语法连接失败。"""
        adapter = CLIAdapter(CLIAdapterConfig(command='python "unclosed'))
        assert adapter.connect() is False

    def test_health_check_true_when_whitelisted(self):
        """白名单内命令健康检查返回 True。"""
        adapter = CLIAdapter(CLIAdapterConfig(command=PYTHON_CMD))
        assert adapter.health_check() is True

    def test_health_check_false_when_not_whitelisted(self):
        """非白名单命令健康检查返回 False。"""
        adapter = CLIAdapter(CLIAdapterConfig(command="rm"))
        assert adapter.health_check() is False


# ======================================================================
# CLIAdapter — 执行与输出解析
# ======================================================================


class TestCLIAdapterExecute:
    def test_execute_text_output(self, monkeypatch):
        """text 格式直接返回 stdout。"""
        adapter = CLIAdapter(CLIAdapterConfig(command=PYTHON_CMD))
        adapter.connect()
        monkeypatch.setattr(
            "maop.core.agent.adapters.cli_adapter.subprocess.run",
            _mock_subprocess_run(stdout="hello world"),
        )
        result = adapter.execute("test task")
        assert result == "hello world"

    def test_execute_json_output(self, monkeypatch):
        """json 格式解析 JSON 并返回序列化字符串。"""
        adapter = CLIAdapter(
            CLIAdapterConfig(command=PYTHON_CMD, output_format="json")
        )
        adapter.connect()
        monkeypatch.setattr(
            "maop.core.agent.adapters.cli_adapter.subprocess.run",
            _mock_subprocess_run(stdout='{"key": "value", "num": 42}'),
        )
        result = adapter.execute("task")
        data = json.loads(result)
        assert data["key"] == "value"
        assert data["num"] == 42

    def test_execute_json_with_key_extraction(self, monkeypatch):
        """json 格式 + output_json_key 提取指定字段。"""
        adapter = CLIAdapter(
            CLIAdapterConfig(
                command=PYTHON_CMD,
                output_format="json",
                output_json_key="result",
            )
        )
        adapter.connect()
        monkeypatch.setattr(
            "maop.core.agent.adapters.cli_adapter.subprocess.run",
            _mock_subprocess_run(stdout='{"result": "extracted", "other": "x"}'),
        )
        assert adapter.execute("task") == "extracted"

    def test_execute_jsonl_output(self, monkeypatch):
        """jsonl 格式逐行解析。"""
        adapter = CLIAdapter(
            CLIAdapterConfig(command=PYTHON_CMD, output_format="jsonl")
        )
        adapter.connect()
        jsonl = '{"a": 1}\n{"a": 2}\n{"a": 3}'
        monkeypatch.setattr(
            "maop.core.agent.adapters.cli_adapter.subprocess.run",
            _mock_subprocess_run(stdout=jsonl),
        )
        result = adapter.execute("task")
        lines = result.strip().splitlines()
        assert json.loads(lines[0])["a"] == 1
        assert json.loads(lines[1])["a"] == 2
        assert json.loads(lines[2])["a"] == 3

    def test_execute_jsonl_with_key(self, monkeypatch):
        """jsonl 格式 + output_json_key 逐行提取。"""
        adapter = CLIAdapter(
            CLIAdapterConfig(
                command=PYTHON_CMD,
                output_format="jsonl",
                output_json_key="text",
            )
        )
        adapter.connect()
        jsonl = '{"text": "line1"}\n{"text": "line2"}'
        monkeypatch.setattr(
            "maop.core.agent.adapters.cli_adapter.subprocess.run",
            _mock_subprocess_run(stdout=jsonl),
        )
        result = adapter.execute("task")
        assert result == "line1\nline2"

    def test_execute_unknown_format_raises(self, monkeypatch):
        """未知 output_format 抛出 ValueError。"""
        adapter = CLIAdapter(
            CLIAdapterConfig(command=PYTHON_CMD, output_format="xml")
        )
        adapter.connect()
        monkeypatch.setattr(
            "maop.core.agent.adapters.cli_adapter.subprocess.run",
            _mock_subprocess_run(stdout="<xml/>"),
        )
        with pytest.raises(ValueError, match="Unknown output_format"):
            adapter.execute("task")

    def test_execute_timeout(self, monkeypatch):
        """超时抛出 TimeoutError。"""
        adapter = CLIAdapter(
            CLIAdapterConfig(command=PYTHON_CMD, timeout_s=0.01)
        )
        adapter.connect()

        def _slow_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 0.01)

        monkeypatch.setattr(
            "maop.core.agent.adapters.cli_adapter.subprocess.run", _slow_run
        )
        with pytest.raises(TimeoutError, match="timed out"):
            adapter.execute("task")

    def test_execute_nonzero_exit_raises(self, monkeypatch):
        """非零退出码抛出 RuntimeError。"""
        adapter = CLIAdapter(CLIAdapterConfig(command=PYTHON_CMD))
        adapter.connect()
        monkeypatch.setattr(
            "maop.core.agent.adapters.cli_adapter.subprocess.run",
            _mock_subprocess_run(stdout="", stderr="error msg", returncode=1),
        )
        with pytest.raises(RuntimeError, match="exited with code 1"):
            adapter.execute("task")

    def test_execute_via_stdin(self, monkeypatch):
        """task_via_stdin=True 时任务通过 stdin 传递。"""
        adapter = CLIAdapter(
            CLIAdapterConfig(command=PYTHON_CMD, task_via_stdin=True)
        )
        adapter.connect()

        captured: dict[str, Any] = {}

        def _capture_run(cmd, **kwargs):
            captured["input"] = kwargs.get("input")
            return _make_completed_process(cmd, 0, stdout="ok", stderr="")

        monkeypatch.setattr(
            "maop.core.agent.adapters.cli_adapter.subprocess.run", _capture_run
        )
        adapter.execute("my stdin task")
        assert captured["input"] == "my stdin task"

    def test_execute_with_arg_flag(self, monkeypatch):
        """task_arg_flag 设置时任务通过命令行参数传递。"""
        adapter = CLIAdapter(
            CLIAdapterConfig(command=PYTHON_CMD, task_arg_flag="--prompt")
        )
        adapter.connect()

        captured: dict[str, Any] = {}

        def _capture_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _make_completed_process(cmd, 0, stdout="ok", stderr="")

        monkeypatch.setattr(
            "maop.core.agent.adapters.cli_adapter.subprocess.run", _capture_run
        )
        adapter.execute("my arg task")
        assert "--prompt" in captured["cmd"]
        assert "my arg task" in captured["cmd"]

    def test_execute_auto_connect(self, monkeypatch):
        """未连接时 execute 自动调用 connect。"""
        adapter = CLIAdapter(CLIAdapterConfig(command=PYTHON_CMD))
        # 不调用 connect，直接 execute
        monkeypatch.setattr(
            "maop.core.agent.adapters.cli_adapter.subprocess.run",
            _mock_subprocess_run(stdout="auto"),
        )
        assert adapter.execute("task") == "auto"
        assert adapter._connected is True


# ======================================================================
# CLIAdapter — 沙箱与环境
# ======================================================================


class TestCLIAdapterSandbox:
    def test_cwd_sandbox_rejected(self):
        """cwd 不在 allowed_dirs 内时连接失败。"""
        adapter = CLIAdapter(
            CLIAdapterConfig(
                command=PYTHON_CMD,
                cwd="/tmp/forbidden",
                allowed_dirs=["/tmp/allowed"],
            )
        )
        assert adapter.connect() is False

    def test_cwd_sandbox_allowed(self):
        """cwd 在 allowed_dirs 子树内时连接成功。"""
        allowed_dir = str(Path(__file__).resolve().parent)
        adapter = CLIAdapter(
            CLIAdapterConfig(
                command=PYTHON_CMD,
                cwd=allowed_dir,
                allowed_dirs=[allowed_dir],
            )
        )
        assert adapter.connect() is True

    def test_blocked_env_removed(self, monkeypatch):
        """blocked_env 中的环境变量被屏蔽。"""
        adapter = CLIAdapter(
            CLIAdapterConfig(
                command=PYTHON_CMD,
                env={"MY_VAR": "visible", "SECRET_VAR": "should_be_removed"},
                blocked_env=["SECRET_VAR"],
            )
        )
        adapter.connect()

        captured: dict[str, Any] = {}

        def _capture_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return _make_completed_process(cmd, 0, stdout="ok", stderr="")

        monkeypatch.setattr(
            "maop.core.agent.adapters.cli_adapter.subprocess.run", _capture_run
        )
        adapter.execute("task")
        env = captured["env"]
        assert env is not None
        assert "SECRET_VAR" not in env
        assert env.get("MY_VAR") == "visible"

    def test_no_env_override_when_empty(self, monkeypatch):
        """env 和 blocked_env 都为空时 env=None（继承当前环境）。"""
        adapter = CLIAdapter(CLIAdapterConfig(command=PYTHON_CMD))
        adapter.connect()

        captured: dict[str, Any] = {}

        def _capture_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return _make_completed_process(cmd, 0, stdout="ok", stderr="")

        monkeypatch.setattr(
            "maop.core.agent.adapters.cli_adapter.subprocess.run", _capture_run
        )
        adapter.execute("task")
        assert captured["env"] is None


# ======================================================================
# CLIAdapter — 生命周期
# ======================================================================


class TestCLIAdapterLifecycle:
    def test_sync_config(self):
        """sync_config 替换配置并断开连接。"""
        adapter = CLIAdapter(CLIAdapterConfig(command=PYTHON_CMD))
        adapter.connect()
        assert adapter._connected is True
        adapter.sync_config({"command": PYTHON_CMD, "timeout_s": 60})
        assert adapter._connected is False
        assert adapter.config.timeout_s == 60

    def test_disconnect(self):
        """disconnect 重置连接状态。"""
        adapter = CLIAdapter(CLIAdapterConfig(command=PYTHON_CMD))
        adapter.connect()
        adapter.disconnect()
        assert adapter._connected is False


# ======================================================================
# HTTPAdapter — 配置与辅助
# ======================================================================


class TestHTTPAdapterConfig:
    def test_config_defaults(self):
        cfg = HTTPAdapterConfig()
        assert cfg.method == "POST"
        assert cfg.task_field == "prompt"
        assert cfg.max_retries == 3
        assert cfg.response_path == ""

    def test_build_headers_with_api_key(self, monkeypatch):
        """api_key_env 设置时注入 Authorization 头。"""
        monkeypatch.setenv("TEST_API_KEY", "secret123")
        adapter = HTTPAdapter(
            HTTPAdapterConfig(base_url="http://test", api_key_env="TEST_API_KEY")
        )
        headers = adapter._build_headers()
        assert headers["Authorization"] == "Bearer secret123"

    def test_build_headers_without_api_key(self, monkeypatch):
        """api_key_env 未设置时无 Authorization 头。"""
        monkeypatch.delenv("TEST_API_KEY", raising=False)
        adapter = HTTPAdapter(
            HTTPAdapterConfig(base_url="http://test", api_key_env="TEST_API_KEY")
        )
        headers = adapter._build_headers()
        assert "Authorization" not in headers

    def test_build_url(self):
        adapter = HTTPAdapter(
            HTTPAdapterConfig(base_url="http://test.local", path="/v1/chat")
        )
        assert adapter._build_url() == "http://test.local/v1/chat"

    def test_build_url_no_path(self):
        adapter = HTTPAdapter(
            HTTPAdapterConfig(base_url="http://test.local")
        )
        assert adapter._build_url() == "http://test.local"


# ======================================================================
# HTTPAdapter — 执行与响应提取
# ======================================================================


class TestHTTPAdapterExecute:
    def test_execute_success(self):
        """成功请求返回响应内容。"""
        adapter = HTTPAdapter(
            HTTPAdapterConfig(
                base_url="http://test.local",
                path="/api",
                task_field="prompt",
                response_path="result",
            )
        )

        def handler(request):
            body = json.loads(request.content)
            return httpx.Response(200, json={"result": f"echo:{body['prompt']}"})

        adapter._client = _make_http_client(handler)
        adapter._connected = True
        assert adapter.execute("hello") == "echo:hello"

    def test_response_path_nested(self):
        """点分路径从嵌套 JSON 中提取值。"""
        adapter = HTTPAdapter(
            HTTPAdapterConfig(
                base_url="http://test.local",
                path="/api",
                response_path="choices.0.message.content",
            )
        )

        def handler(request):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "nested value", "role": "assistant"}}
                    ]
                },
            )

        adapter._client = _make_http_client(handler)
        adapter._connected = True
        assert adapter.execute("task") == "nested value"

    def test_response_no_path_returns_full_json(self):
        """response_path 为空时返回整个 JSON。"""
        adapter = HTTPAdapter(
            HTTPAdapterConfig(base_url="http://test.local", path="/api")
        )

        def handler(request):
            return httpx.Response(200, json={"a": 1, "b": 2})

        adapter._client = _make_http_client(handler)
        adapter._connected = True
        result = adapter.execute("task")
        data = json.loads(result)
        assert data == {"a": 1, "b": 2}

    def test_retry_on_failure(self):
        """请求失败时重试 max_retries 次。"""
        adapter = HTTPAdapter(
            HTTPAdapterConfig(
                base_url="http://test.local",
                path="/api",
                response_path="result",
                max_retries=3,
            )
        )
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(500, text="server error")
            return httpx.Response(200, json={"result": "ok"})

        adapter._client = _make_http_client(handler)
        adapter._connected = True
        result = adapter.execute("task")
        assert result == "ok"
        assert call_count == 3

    def test_retry_exhausted_raises(self):
        """重试耗尽后抛出 RuntimeError。"""
        adapter = HTTPAdapter(
            HTTPAdapterConfig(
                base_url="http://test.local",
                path="/api",
                max_retries=2,
            )
        )

        def handler(request):
            return httpx.Response(500, text="always fail")

        adapter._client = _make_http_client(handler)
        adapter._connected = True
        with pytest.raises(RuntimeError, match="failed after 2 retries"):
            adapter.execute("task")

    def test_execute_auto_connect(self):
        """未连接时 execute 自动调用 connect（会失败因无真实服务）。"""
        adapter = HTTPAdapter(
            HTTPAdapterConfig(base_url="http://invalid.localhost", path="/api")
        )
        with pytest.raises(RuntimeError, match="not connected"):
            adapter.execute("task")


# ======================================================================
# HTTPAdapter — 生命周期
# ======================================================================


class TestHTTPAdapterLifecycle:
    def test_health_check_no_client(self):
        """无客户端时健康检查返回 False。"""
        adapter = HTTPAdapter()
        assert adapter.health_check() is False

    def test_health_check_ok(self):
        """服务可达时健康检查返回 True。"""
        adapter = HTTPAdapter(
            HTTPAdapterConfig(base_url="http://test.local")
        )

        def handler(request):
            return httpx.Response(200, json={"status": "ok"})

        adapter._client = _make_http_client(handler)
        assert adapter.health_check() is True

    def test_disconnect(self):
        """disconnect 关闭客户端。"""
        adapter = HTTPAdapter(
            HTTPAdapterConfig(base_url="http://test.local")
        )

        def handler(request):
            return httpx.Response(200)

        adapter._client = _make_http_client(handler)
        adapter._connected = True
        adapter.disconnect()
        assert adapter._client is None
        assert adapter._connected is False


# ======================================================================
# MCPBridgeAdapter — 配置与连接
# ======================================================================


def _make_mock_hub(
    healthy: bool = True,
    tool_result: ToolResult | None = None,
) -> MagicMock:
    """创建一个 mock MCPHub。"""
    hub = MagicMock()
    hub.health_check = AsyncMock(return_value=healthy)
    if tool_result is None:
        tool_result = ToolResult(content=[{"text": "mock result"}], is_error=False)
    hub.call_tool_by_name = AsyncMock(return_value=tool_result)
    hub.disconnect = AsyncMock(return_value=True)
    return hub


class TestMCPBridgeAdapter:
    def test_config_defaults(self):
        cfg = MCPBridgeAdapterConfig()
        assert cfg.server_id == ""
        assert cfg.tool_name == ""
        assert cfg.timeout_s == 30.0

    def test_connect_success(self):
        """hub 健康时连接成功。"""
        hub = _make_mock_hub(healthy=True)
        adapter = MCPBridgeAdapter(
            MCPBridgeAdapterConfig(server_id="srv1", tool_name="tool1"),
            mcp_hub=hub,
        )
        assert adapter.connect() is True
        assert adapter._connected is True
        hub.health_check.assert_awaited_once_with("srv1")

    def test_connect_no_hub(self):
        """无 hub 实例时连接失败。"""
        adapter = MCPBridgeAdapter(
            MCPBridgeAdapterConfig(server_id="srv1", tool_name="tool1"),
        )
        assert adapter.connect() is False

    def test_connect_empty_server_id(self):
        """空 server_id 连接失败。"""
        hub = _make_mock_hub()
        adapter = MCPBridgeAdapter(
            MCPBridgeAdapterConfig(server_id="", tool_name="tool1"),
            mcp_hub=hub,
        )
        assert adapter.connect() is False

    def test_connect_unhealthy(self):
        """hub 不健康时连接失败。"""
        hub = _make_mock_hub(healthy=False)
        adapter = MCPBridgeAdapter(
            MCPBridgeAdapterConfig(server_id="srv1", tool_name="tool1"),
            mcp_hub=hub,
        )
        assert adapter.connect() is False

    def test_execute_success(self):
        """成功调用工具并返回文本。"""
        result = ToolResult(
            content=[{"text": "analysis result"}, {"text": "part2"}],
            is_error=False,
        )
        hub = _make_mock_hub(tool_result=result)
        adapter = MCPBridgeAdapter(
            MCPBridgeAdapterConfig(server_id="srv1", tool_name="srv.tool"),
            mcp_hub=hub,
        )
        adapter.connect()
        output = adapter.execute("analyze this")
        assert output == "analysis result\npart2"
        hub.call_tool_by_name.assert_awaited_once_with(
            "srv.tool", {"prompt": "analyze this"}
        )

    def test_execute_error_raises(self):
        """工具返回错误时抛出 RuntimeError。"""
        result = ToolResult(
            content=[], is_error=True, error_message="tool failed"
        )
        hub = _make_mock_hub(tool_result=result)
        adapter = MCPBridgeAdapter(
            MCPBridgeAdapterConfig(server_id="srv1", tool_name="tool1"),
            mcp_hub=hub,
        )
        adapter.connect()
        with pytest.raises(RuntimeError, match="tool failed"):
            adapter.execute("task")

    def test_execute_no_hub_raises(self):
        """无 hub 时 execute 抛出 RuntimeError。"""
        adapter = MCPBridgeAdapter(
            MCPBridgeAdapterConfig(server_id="srv1", tool_name="tool1"),
        )
        with pytest.raises(RuntimeError, match="not connected"):
            adapter.execute("task")

    def test_health_check_true(self):
        """hub 健康时 health_check 返回 True。"""
        hub = _make_mock_hub(healthy=True)
        adapter = MCPBridgeAdapter(
            MCPBridgeAdapterConfig(server_id="srv1", tool_name="tool1"),
            mcp_hub=hub,
        )
        assert adapter.health_check() is True

    def test_health_check_false_no_hub(self):
        """无 hub 时 health_check 返回 False。"""
        adapter = MCPBridgeAdapter(
            MCPBridgeAdapterConfig(server_id="srv1", tool_name="tool1"),
        )
        assert adapter.health_check() is False

    def test_disconnect_calls_hub_disconnect(self):
        """disconnect 调用 hub.disconnect。"""
        hub = _make_mock_hub()
        adapter = MCPBridgeAdapter(
            MCPBridgeAdapterConfig(server_id="srv1", tool_name="tool1"),
            mcp_hub=hub,
        )
        adapter.connect()
        adapter.disconnect()
        hub.disconnect.assert_awaited_once_with("srv1")
        assert adapter._connected is False

    def test_sync_config(self):
        """sync_config 替换配置。"""
        hub = _make_mock_hub()
        adapter = MCPBridgeAdapter(
            MCPBridgeAdapterConfig(server_id="srv1", tool_name="tool1"),
            mcp_hub=hub,
        )
        adapter.connect()
        adapter.sync_config({"server_id": "srv2", "tool_name": "tool2"})
        assert adapter.config.server_id == "srv2"
        assert adapter._connected is False


# ======================================================================
# WebAdapter — 配置与认证
# ======================================================================


class TestWebAdapterConfig:
    def test_config_defaults(self):
        cfg = WebAdapterConfig()
        assert cfg.auth_method == "token"
        assert cfg.auth_credentials_ref == ""
        assert cfg.timeout_s == 30.0

    def test_build_headers_token(self, monkeypatch):
        """token 认证注入 Authorization 头。"""
        monkeypatch.setenv("TEST_TOKEN", "tok123")
        adapter = WebAdapter(
            WebAdapterConfig(
                api_url="http://test",
                auth_method="token",
                auth_credentials_ref="TEST_TOKEN",
            )
        )
        adapter._auth_value = "tok123"
        headers = adapter._build_headers()
        assert headers["Authorization"] == "Bearer tok123"

    def test_build_headers_cookie(self, monkeypatch):
        """cookie 认证注入 Cookie 头。"""
        adapter = WebAdapter(
            WebAdapterConfig(
                api_url="http://test",
                auth_method="cookie",
                auth_credentials_ref="TEST_COOKIE",
            )
        )
        adapter._auth_value = "session=abc"
        headers = adapter._build_headers()
        assert headers["Cookie"] == "session=abc"

    def test_build_headers_session(self, monkeypatch):
        """session 认证注入 X-Session-Id 头。"""
        adapter = WebAdapter(
            WebAdapterConfig(
                api_url="http://test",
                auth_method="session",
                auth_credentials_ref="TEST_SESSION",
            )
        )
        adapter._auth_value = "sess456"
        headers = adapter._build_headers()
        assert headers["X-Session-Id"] == "sess456"

    def test_build_body_task_placeholder(self):
        """请求模板中 {task} 占位符被替换。"""
        adapter = WebAdapter(
            WebAdapterConfig(
                api_url="http://test",
                request_template={
                    "query": "analyze: {task}",
                    "mode": "strict",
                },
            )
        )
        body = adapter._build_body("my code")
        assert body["query"] == "analyze: my code"
        assert body["mode"] == "strict"
        assert body["task"] == "my code"


# ======================================================================
# WebAdapter — 执行与生命周期
# ======================================================================


class TestWebAdapterExecute:
    def test_execute_success(self):
        """成功请求返回响应内容。"""
        adapter = WebAdapter(
            WebAdapterConfig(
                api_url="http://test.local/api",
                response_path="output",
            )
        )

        def handler(request):
            body = json.loads(request.content)
            return httpx.Response(200, json={"output": f"done:{body['task']}"})

        adapter._client = _make_http_client(handler)
        adapter._connected = True
        assert adapter.execute("web task") == "done:web task"

    def test_response_path_nested(self):
        """点分路径从嵌套 JSON 中提取值。"""
        adapter = WebAdapter(
            WebAdapterConfig(
                api_url="http://test.local/api",
                response_path="data.0.value",
            )
        )

        def handler(request):
            return httpx.Response(
                200, json={"data": [{"value": "deep", "other": "x"}]}
            )

        adapter._client = _make_http_client(handler)
        adapter._connected = True
        assert adapter.execute("task") == "deep"

    def test_execute_http_error_raises(self):
        """HTTP 错误状态码抛出 RuntimeError。"""
        adapter = WebAdapter(
            WebAdapterConfig(api_url="http://test.local/api")
        )

        def handler(request):
            return httpx.Response(500, text="server error")

        adapter._client = _make_http_client(handler)
        adapter._connected = True
        with pytest.raises(RuntimeError, match="request failed"):
            adapter.execute("task")

    def test_execute_auto_connect_fails(self):
        """未连接且 connect 失败时抛出 RuntimeError。"""
        adapter = WebAdapter(
            WebAdapterConfig(api_url="http://invalid.localhost/api")
        )
        with pytest.raises(RuntimeError, match="not connected"):
            adapter.execute("task")

    def test_health_check_no_client(self):
        """无客户端时健康检查返回 False。"""
        adapter = WebAdapter()
        assert adapter.health_check() is False

    def test_health_check_ok(self):
        """服务可达时健康检查返回 True。"""
        adapter = WebAdapter(
            WebAdapterConfig(api_url="http://test.local/api")
        )

        def handler(request):
            return httpx.Response(200, json={"status": "ok"})

        adapter._client = _make_http_client(handler)
        assert adapter.health_check() is True

    def test_disconnect(self):
        """disconnect 关闭客户端。"""
        adapter = WebAdapter(
            WebAdapterConfig(api_url="http://test.local/api")
        )

        def handler(request):
            return httpx.Response(200)

        adapter._client = _make_http_client(handler)
        adapter._connected = True
        adapter._auth_value = "some_auth"
        adapter.disconnect()
        assert adapter._client is None
        assert adapter._connected is False
        assert adapter._auth_value == ""

    def test_sync_config(self):
        """sync_config 替换配置。"""
        adapter = WebAdapter(
            WebAdapterConfig(api_url="http://test.local/api")
        )
        adapter.sync_config({"api_url": "http://new.local/api", "timeout_s": 60})
        assert adapter.config.api_url == "http://new.local/api"
        assert adapter.config.timeout_s == 60