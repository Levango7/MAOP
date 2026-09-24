"""Tests for the F2-03 enhanced parallel health_check_all.

2026-09-25：本文件原先还覆盖 ``maop.core.mcp.tool_signing`` 与
``maop.core.mcp.tool_discovery``，那两个模块已被删除 —— 它们与
``maop.core.marketplace.*`` 功能重叠且从未接线（详见
``scripts/check_wiring.py`` 的登记）。

对应的签名/发现测试**不是被丢弃，而是已有等价覆盖**：
  - Ed25519 签名往返 / 篡改拒绝 / 错误密钥 / 畸形签名
    → ``tests/test_marketplace_f301.py``
  - 密钥注册 / 轮换 / 吊销 / 黑名单
    → ``tests/test_marketplace_key_mgmt.py``
唯一功能补充（PEM 一站式密钥生成）已移植为
``maop.core.marketplace.signing.generate_keypair``，其往返验证归入
``test_marketplace_f301.py``。

本文件现只保留 health_check_all 的超时与异常隔离测试。
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

# ── TestEnhancedHealthCheck ───────────────────────────────────


class TestEnhancedHealthCheckAll:
    """Test the F2-03 enhanced parallel health_check_all with timeout
    and exception isolation."""

    async def test_timeout_marks_unhealthy(self, tmp_path):
        from maop.core.mcp.mcp_hub import MCPHub

        hub = MCPHub(root_dir=tmp_path)
        # Inject a fake transport + config so health_check has something to ping.
        # We make health_check slow by patching it to sleep.

        # Register a fake server by injecting into internal dicts.
        from maop.core.mcp.mcp_hub_types import MCPServerConfig, TransportType

        config = MCPServerConfig(name="slow-server", transport=TransportType.STDIO)
        hub._transports["slow"] = None  # type: ignore[assignment]
        hub._configs["slow"] = config


        async def slow_check(sid: str) -> bool:
            await asyncio.sleep(10)
            return True

        with patch.object(hub, "health_check", slow_check):
            results = await hub.health_check_all(timeout_s=0.1)

        assert "slow" in results
        assert results["slow"] is False

    async def test_exception_isolated(self, tmp_path):
        """One server raising should not cancel the others."""
        from maop.core.mcp.mcp_hub import MCPHub
        from maop.core.mcp.mcp_hub_types import MCPServerConfig, TransportType

        hub = MCPHub(root_dir=tmp_path)
        config = MCPServerConfig(name="srv", transport=TransportType.STDIO)
        hub._transports["bad"] = None  # type: ignore[assignment]
        hub._transports["good"] = None  # type: ignore[assignment]
        hub._configs["bad"] = config
        hub._configs["good"] = config

        call_count = {"n": 0}

        async def flaky_check(sid: str) -> bool:
            call_count["n"] += 1
            if sid == "bad":
                raise RuntimeError("boom")
            return True

        with patch.object(hub, "health_check", flaky_check):
            results = await hub.health_check_all()

        assert results["bad"] is False  # exception → unhealthy
        assert results["good"] is True
        assert call_count["n"] == 2  # both were called

    async def test_empty_returns_empty_dict(self, tmp_path):
        from maop.core.mcp.mcp_hub import MCPHub

        hub = MCPHub(root_dir=tmp_path)
        results = await hub.health_check_all()
        assert results == {}

    async def test_backward_compatible_no_timeout(self, tmp_path):
        """Without timeout_s, the method still works (backward compat)."""
        from maop.core.mcp.mcp_hub import MCPHub
        from maop.core.mcp.mcp_hub_types import MCPServerConfig, TransportType

        hub = MCPHub(root_dir=tmp_path)
        config = MCPServerConfig(name="srv", transport=TransportType.STDIO)
        hub._transports["s1"] = None  # type: ignore[assignment]
        hub._configs["s1"] = config

        async def ok_check(sid: str) -> bool:
            return True

        with patch.object(hub, "health_check", ok_check):
            results = await hub.health_check_all()

        assert results == {"s1": True}