"""CapabilityProbe 白盒测试.

覆盖：ProbeResult 构造、静态分析（基于 AgentDescriptor）、动态探测
（mock adapter）、单项探测、响应分析、缓存、统计、线程安全。
每个测试使用 tmp_path 隔离 SQLite。
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from maop.core.agent.ops.capability_probe import (
    CapabilityProbe,
    ProbeResult,
)
from maop.core.agent.registry.agent_catalog import (
    AgentCapability,
    AgentCatalog,
    AgentDescriptor,
)

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def catalog(tmp_path: Path) -> AgentCatalog:
    """使用隔离 DB 的 AgentCatalog。"""
    return AgentCatalog(db_path=tmp_path / "catalog.db")


@pytest.fixture
def probe(catalog: AgentCatalog) -> CapabilityProbe:
    """使用隔离 catalog 的 CapabilityProbe。"""
    return CapabilityProbe(catalog=catalog)


def _make_descriptor(
    name: str,
    capabilities: list[AgentCapability],
) -> AgentDescriptor:
    """构造指定能力的 AgentDescriptor。"""
    return AgentDescriptor(
        name=name,
        display_name=name,
        capabilities=capabilities,
    )


# ── 1. TestProbeResult ─────────────────────────────────────────


class TestProbeResult:
    """ProbeResult 构造。"""

    def test_default_values(self) -> None:
        """默认构造。"""
        result = ProbeResult(agent_name="a")
        assert result.agent_name == "a"
        assert result.capabilities == []
        assert result.response_time_s == 0.0
        assert result.details == {}

    def test_full_construction(self) -> None:
        """完整构造。"""
        result = ProbeResult(
            agent_name="claude",
            capabilities=["code_generation", "chat"],
            response_time_s=1.5,
            details={"source": "dynamic"},
        )
        assert result.agent_name == "claude"
        assert result.capabilities == ["code_generation", "chat"]
        assert result.response_time_s == 1.5
        assert result.details["source"] == "dynamic"


# ── 2. TestStaticProbe ─────────────────────────────────────────


class TestStaticProbe:
    """静态分析路径（adapter=None）。"""

    def test_static_probe_with_declared_capabilities(
        self, probe: CapabilityProbe, catalog: AgentCatalog,
    ) -> None:
        """有声明能力时返回声明列表。"""
        catalog.register(_make_descriptor(
            "agent-a", [AgentCapability.CODE_GENERATION, AgentCapability.CHAT],
        ))
        result = probe.probe("agent-a")
        assert result.agent_name == "agent-a"
        assert "code_generation" in result.capabilities
        assert "chat" in result.capabilities
        assert result.details["source"] == "static"
        assert result.details["found"] is True

    def test_static_probe_unknown_agent(
        self, probe: CapabilityProbe,
    ) -> None:
        """未知 Agent 返回空能力列表。"""
        result = probe.probe("nonexistent-agent")
        assert result.capabilities == []
        assert result.details["found"] is False

    def test_static_probe_caches_result(
        self, probe: CapabilityProbe, catalog: AgentCatalog,
    ) -> None:
        """静态探测结果应缓存。"""
        catalog.register(_make_descriptor("agent-b", [AgentCapability.CHAT]))
        probe.probe("agent-b")
        cached = probe.get_cached("agent-b")
        assert cached is not None
        assert cached.agent_name == "agent-b"

    def test_static_single_capability_check(
        self, probe: CapabilityProbe, catalog: AgentCatalog,
    ) -> None:
        """单项静态能力检查。"""
        catalog.register(_make_descriptor(
            "agent-c", [AgentCapability.CODE_GENERATION],
        ))
        assert probe.probe_code_generation("agent-c") is True
        assert probe.probe_code_review("agent-c") is False
        assert probe.probe_chat("agent-c") is False

    def test_static_stats(
        self, probe: CapabilityProbe, catalog: AgentCatalog,
    ) -> None:
        """静态探测统计。"""
        catalog.register(_make_descriptor("agent-d", [AgentCapability.CHAT]))
        probe.probe("agent-d")
        probe.probe("agent-d")
        stats = probe.get_stats()
        assert stats["total_probes"] == 2
        assert stats["total_static"] == 2
        assert stats["total_dynamic"] == 0


# ── 3. TestDynamicProbe ────────────────────────────────────────


class TestDynamicProbe:
    """动态探测路径（mock adapter）。"""

    def test_dynamic_probe_all_capabilities(
        self, probe: CapabilityProbe,
    ) -> None:
        """动态探测识别全部能力。"""
        adapter = MagicMock()
        # 为不同任务返回不同响应
        def _execute(task: str, **kwargs) -> str:
            if "Write a Python function" in task:
                return "def add(a, b):\n    return a + b"
            if "Review this code" in task:
                return "The code looks good and correct."
            if "Hello, what can you do" in task:
                return "I can help you with coding and review."
            if "List files" in task:
                return "main.py\nutils.py\nREADME.md"
            if "summarize the key points" in task:
                return "The context describes padding for testing purposes."
            return "ok"
        adapter.execute.side_effect = _execute

        result = probe.probe("agent-x", adapter=adapter)
        assert "code_generation" in result.capabilities
        assert "code_review" in result.capabilities
        assert "chat" in result.capabilities
        assert "tool_use" in result.capabilities
        assert "long_context" in result.capabilities
        assert result.details["source"] == "dynamic"
        assert result.response_time_s >= 0.0

    def test_dynamic_probe_no_capabilities(
        self, probe: CapabilityProbe,
    ) -> None:
        """adapter 返回空响应时无能力。"""
        adapter = MagicMock()
        adapter.execute.return_value = ""
        result = probe.probe("agent-y", adapter=adapter)
        assert result.capabilities == []

    def test_dynamic_probe_adapter_exception(
        self, probe: CapabilityProbe,
    ) -> None:
        """adapter 抛异常时该能力标记为 False。"""
        adapter = MagicMock()
        adapter.execute.side_effect = RuntimeError("connection failed")
        result = probe.probe("agent-z", adapter=adapter)
        assert result.capabilities == []
        # details 应记录错误
        for cap in ("code_generation", "code_review", "chat", "long_context", "tool_use"):
            assert cap in result.details["tests"]
            assert result.details["tests"][cap]["ok"] is False

    def test_dynamic_probe_stats(
        self, probe: CapabilityProbe,
    ) -> None:
        """动态探测统计。"""
        adapter = MagicMock()
        adapter.execute.return_value = "def f(): pass"
        probe.probe("agent-q", adapter=adapter)
        stats = probe.get_stats()
        assert stats["total_probes"] == 1
        assert stats["total_dynamic"] == 1
        assert stats["total_static"] == 0


# ── 4. TestSingleCapabilityProbe ───────────────────────────────


class TestSingleCapabilityProbe:
    """单项动态能力探测。"""

    def test_probe_code_generation_dynamic(self, probe: CapabilityProbe) -> None:
        """动态探测代码生成能力（正向）。"""
        adapter = MagicMock()
        adapter.execute.return_value = "def add(a, b):\n    return a + b"
        assert probe.probe_code_generation("a", adapter=adapter) is True

    def test_probe_code_generation_dynamic_negative(self, probe: CapabilityProbe) -> None:
        """动态探测代码生成能力（负向）。"""
        adapter = MagicMock()
        adapter.execute.return_value = "I cannot generate code."
        assert probe.probe_code_generation("a", adapter=adapter) is False

    def test_probe_tool_use_dynamic(self, probe: CapabilityProbe) -> None:
        """动态探测工具使用能力。"""
        adapter = MagicMock()
        adapter.execute.return_value = "main.py\nsrc/utils.py"
        assert probe.probe_tool_use("a", adapter=adapter) is True


# ── 5. TestThreadSafety ─────────────────────────────────────────


class TestThreadSafety:
    """线程安全测试。"""

    def test_concurrent_probe(self, probe: CapabilityProbe, catalog: AgentCatalog) -> None:
        """并发探测不抛异常。"""
        catalog.register(_make_descriptor("agent-t", [AgentCapability.CHAT]))
        errors: list[Exception] = []

        def _run() -> None:
            try:
                probe.probe("agent-t")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_run) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        stats = probe.get_stats()
        assert stats["total_probes"] == 10