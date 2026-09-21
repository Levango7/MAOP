"""Tests for ``maop.core.agent.adapters.vibe_coding_adapter`` — Vibe Coding 平台适配器。

测试覆盖：
  - 连接：平台可达 / 不可达
  - 项目生成：成功 / 超时 / 失败
  - 状态轮询：queued / completed
  - 项目导出：zip / git
  - 健康检查
  - 配置验证
  - 平台特定 URL

所有 HTTP 请求通过 ``httpx.MockTransport`` mock，不发起真实请求。
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from maop.core.agent.adapters.vibe_coding_adapter import (
    VibeCodingAdapter,
    VibeCodingConfig,
)


@pytest.fixture(autouse=True)
def _no_system_proxy(monkeypatch):
    """Strip proxy env vars so invalid.localhost tests see a direct
    connection error instead of a proxy 502 (same rationale as
    test_agent_adapters.py)."""
    for var in (
        "http_proxy", "https_proxy", "all_proxy",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    ):
        monkeypatch.delenv(var, raising=False)


# ======================================================================
# 辅助函数
# ======================================================================


def _make_http_client(
    handler: Any, base_url: str = "http://test.local"
) -> httpx.Client:
    """用 ``httpx.MockTransport`` 创建一个 mock HTTP 客户端。"""
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport, base_url=base_url)


def _make_adapter(
    platform: str = "bolt-new",
    base_url: str = "http://test.local",
    **kwargs: Any,
) -> VibeCodingAdapter:
    """创建一个带默认配置的 VibeCodingAdapter。"""
    config = VibeCodingConfig(
        platform=platform, base_url=base_url, **kwargs
    )
    return VibeCodingAdapter(config)


# ======================================================================
# 连接测试
# ======================================================================


class TestVibeCodingConnect:
    def test_connect_platform_reachable(self, monkeypatch):
        """平台可达时连接成功。"""
        adapter = _make_adapter(base_url="http://test.local")

        def handler(request):
            return httpx.Response(200, json={"status": "ok"})

        # 保存原始 httpx.Client 引用，避免 mock 后递归调用
        original_client = httpx.Client

        def mock_client(*args, **kwargs):
            transport = httpx.MockTransport(handler)
            return original_client(transport=transport)

        monkeypatch.setattr(
            "maop.core.agent.adapters.vibe_coding_adapter.httpx.Client",
            mock_client,
        )
        assert adapter.connect() is True
        assert adapter._connected is True

    def test_connect_platform_unreachable(self, monkeypatch):
        """平台不可达时连接失败。"""
        adapter = _make_adapter(base_url="http://invalid.localhost")

        def handler(request):
            raise httpx.ConnectError("connection refused")

        # 保存原始 httpx.Client 引用，避免 mock 后递归调用
        original_client = httpx.Client

        def mock_client(*args, **kwargs):
            transport = httpx.MockTransport(handler)
            return original_client(transport=transport)

        monkeypatch.setattr(
            "maop.core.agent.adapters.vibe_coding_adapter.httpx.Client",
            mock_client,
        )
        assert adapter.connect() is False
        assert adapter._connected is False


# ======================================================================
# 项目生成测试
# ======================================================================


class TestVibeCodingGenerateProject:
    def test_generate_project_success(self, monkeypatch):
        """项目生成成功返回项目 URL。"""
        adapter = _make_adapter(
            max_generation_time_s=10.0,
            poll_interval_s=0.1,
        )

        def handler(request):
            # 提交生成请求
            if request.method == "POST" and request.url.path == "/api/projects":
                body = json.loads(request.content)
                return httpx.Response(
                    200, json={"project_id": "proj-abc", "description": body["description"]}
                )
            # 轮询状态 — 直接返回完成
            if "status" in request.url.path:
                return httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "project_url": "http://test.local/projects/proj-abc",
                    },
                )
            return httpx.Response(404)

        adapter._client = _make_http_client(handler)
        adapter._connected = True
        # mock sleep 避免真实等待
        monkeypatch.setattr(
            "maop.core.agent.adapters.vibe_coding_adapter.sleep",
            lambda _: None,
        )

        result = adapter.execute("创建一个 Todo 应用")
        assert "proj-abc" in result
        assert "http://test.local/projects/proj-abc" == result

    def test_generate_project_timeout(self, monkeypatch):
        """项目生成超时抛出 TimeoutError。"""
        adapter = _make_adapter(
            max_generation_time_s=0.05,
            poll_interval_s=0.01,
        )

        def handler(request):
            if request.method == "POST":
                return httpx.Response(200, json={"project_id": "proj-timeout"})
            # 状态始终为 generating，永不完成
            return httpx.Response(200, json={"status": "generating"})

        adapter._client = _make_http_client(handler)
        adapter._connected = True
        monkeypatch.setattr(
            "maop.core.agent.adapters.vibe_coding_adapter.sleep",
            lambda _: None,
        )

        with pytest.raises(TimeoutError, match="超时"):
            adapter.execute("test task")

    def test_generate_project_failure(self, monkeypatch):
        """项目生成失败抛出 RuntimeError。"""
        adapter = _make_adapter(
            max_generation_time_s=10.0,
            poll_interval_s=0.1,
        )

        def handler(request):
            if request.method == "POST":
                return httpx.Response(200, json={"project_id": "proj-fail"})
            # 状态为 failed
            return httpx.Response(
                200,
                json={"status": "failed", "error": "模板解析错误"},
            )

        adapter._client = _make_http_client(handler)
        adapter._connected = True
        monkeypatch.setattr(
            "maop.core.agent.adapters.vibe_coding_adapter.sleep",
            lambda _: None,
        )

        with pytest.raises(RuntimeError, match="生成失败"):
            adapter.execute("test task")


# ======================================================================
# 状态轮询测试
# ======================================================================


class TestVibeCodingPollStatus:
    def test_poll_status_queued(self):
        """轮询返回 queued 状态。"""
        adapter = _make_adapter()

        def handler(request):
            return httpx.Response(
                200,
                json={"status": "queued", "position": 3},
            )

        adapter._client = _make_http_client(handler)
        result = adapter._poll_status("proj-123")
        assert result["status"] == "queued"
        assert result["position"] == 3

    def test_poll_status_completed(self):
        """轮询返回 completed 状态。"""
        adapter = _make_adapter()

        def handler(request):
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "project_url": "http://test.local/projects/proj-done",
                },
            )

        adapter._client = _make_http_client(handler)
        result = adapter._poll_status("proj-done")
        assert result["status"] == "completed"
        assert "proj-done" in result["project_url"]


# ======================================================================
# 项目导出测试
# ======================================================================


class TestVibeCodingExportProject:
    def test_export_project_zip(self):
        """导出 zip 格式返回下载 URL。"""
        adapter = _make_adapter()

        def handler(request):
            body = json.loads(request.content)
            assert body["format"] == "zip"
            return httpx.Response(
                200,
                json={"download_url": "http://test.local/download/proj-123.zip"},
            )

        adapter._client = _make_http_client(handler)
        url = adapter._export_project("proj-123", "zip")
        assert "proj-123" in url
        assert url.endswith(".zip")

    def test_export_project_git(self):
        """导出 git 格式返回 git 仓库 URL。"""
        adapter = _make_adapter()

        def handler(request):
            body = json.loads(request.content)
            assert body["format"] == "git"
            return httpx.Response(
                200,
                json={"download_url": "https://git.test.local/proj-456.git"},
            )

        adapter._client = _make_http_client(handler)
        url = adapter._export_project("proj-456", "git")
        assert "proj-456" in url
        assert url.endswith(".git")


# ======================================================================
# 健康检查测试
# ======================================================================


class TestVibeCodingHealthCheck:
    def test_health_check(self):
        """平台可达时健康检查返回 True，不可达返回 False。"""
        adapter = _make_adapter()

        def handler_ok(request):
            return httpx.Response(200, json={"status": "ok"})

        adapter._client = _make_http_client(handler_ok)
        assert adapter.health_check() is True

        # 无客户端时返回 False
        adapter_no_client = _make_adapter()
        assert adapter_no_client.health_check() is False

        # 5xx 状态码返回 False
        def handler_500(request):
            return httpx.Response(500, text="server error")

        adapter_500 = _make_adapter()
        adapter_500._client = _make_http_client(handler_500)
        assert adapter_500.health_check() is False


# ======================================================================
# 配置验证测试
# ======================================================================


class TestVibeCodingConfigValidation:
    def test_config_validation(self):
        """配置验证：非法导出格式被拒绝，非法时间参数被拒绝。"""
        # 非法导出格式
        with pytest.raises(Exception, match="导出格式"):
            VibeCodingConfig(platform="bolt-new", export_format="tarball")

        # 非法时间参数（零）
        with pytest.raises(Exception, match="正数"):
            VibeCodingConfig(platform="bolt-new", max_generation_time_s=0)

        # 非法时间参数（负数）
        with pytest.raises(Exception, match="正数"):
            VibeCodingConfig(platform="bolt-new", poll_interval_s=-1)

        # 未知字段被拒绝
        with pytest.raises(ValidationError):
            VibeCodingConfig(platform="bolt-new", unknown_field=True)

        # 合法配置
        cfg = VibeCodingConfig(
            platform="v0",
            base_url="https://v0.dev",
            export_format="git",
            max_generation_time_s=600.0,
            poll_interval_s=2.0,
        )
        assert cfg.platform == "v0"
        assert cfg.export_format == "git"


# ======================================================================
# 平台特定 URL 测试
# ======================================================================


class TestVibeCodingPlatformUrls:
    def test_platform_specific_urls(self):
        """各平台默认 URL 和端点构建正确。"""
        cases = [
            ("bolt-new", "https://bolt.new"),
            ("v0", "https://v0.dev"),
            ("lovable", "https://lovable.dev"),
            ("replit-agent", "https://replit.com"),
        ]
        for platform, expected_base in cases:
            config = VibeCodingConfig(platform=platform)
            adapter = VibeCodingAdapter(config)
            # 默认 URL 自动填充
            assert adapter.vibe_config.base_url == expected_base
            # 端点 URL 构建正确
            assert adapter._generate_url() == f"{expected_base}/api/projects"
            assert (
                adapter._status_url("pid")
                == f"{expected_base}/api/projects/pid/status"
            )
            assert (
                adapter._export_url("pid")
                == f"{expected_base}/api/projects/pid/export"
            )

    def test_custom_base_url_overrides_default(self):
        """自定义 base_url 覆盖平台默认 URL。"""
        adapter = _make_adapter(
            platform="bolt-new",
            base_url="https://custom.bolt.local",
        )
        assert adapter.vibe_config.base_url == "https://custom.bolt.local"
        assert (
            adapter._generate_url()
            == "https://custom.bolt.local/api/projects"
        )


# ======================================================================
# 生命周期测试
# ======================================================================


class TestVibeCodingLifecycle:
    def test_disconnect(self):
        """disconnect 关闭客户端并重置状态。"""
        adapter = _make_adapter()

        def handler(request):
            return httpx.Response(200)

        adapter._client = _make_http_client(handler)
        adapter._connected = True
        adapter._auth_value = "some_token"
        adapter.disconnect()
        assert adapter._client is None
        assert adapter._connected is False
        assert adapter._auth_value == ""

    def test_sync_config(self):
        """sync_config 替换配置并断开重连。"""
        adapter = _make_adapter(
            platform="bolt-new",
            base_url="http://test.local",
        )
        adapter._connected = True
        adapter.sync_config({
            "platform": "v0",
            "base_url": "http://v0.local",
            "export_format": "git",
            "max_generation_time_s": 600.0,
        })
        assert adapter.vibe_config.platform == "v0"
        assert adapter.vibe_config.base_url == "http://v0.local"
        assert adapter.vibe_config.export_format == "git"
        assert adapter.vibe_config.max_generation_time_s == 600.0

    def test_execute_auto_connect_fails(self):
        """未连接且 connect 失败时 execute 抛出 RuntimeError。"""
        adapter = _make_adapter(base_url="http://invalid.localhost")
        with pytest.raises(RuntimeError, match="未连接"):
            adapter.execute("test task")

    def test_download_project(self):
        """下载项目文件返回字节内容。"""
        adapter = _make_adapter()
        file_content = b"PK\x03\x04fake-zip-content"

        def handler(request):
            return httpx.Response(200, content=file_content)

        adapter._client = _make_http_client(handler)
        data = adapter._download_project("http://test.local/download/proj.zip")
        assert data == file_content

    def test_export_invalid_format_raises(self):
        """非法导出格式抛出 ValueError。"""
        adapter = _make_adapter()
        adapter._client = _make_http_client(lambda r: httpx.Response(200))
        with pytest.raises(ValueError, match="导出格式"):
            adapter._export_project("proj-123", "tarball")