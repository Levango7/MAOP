"""CapabilityProbe — Agent 能力动态探测器.

通过发送标准测试任务并分析返回结果，动态探测 Agent 的实际能力。
当未提供 adapter 时，回退到静态分析（基于 AgentDescriptor 声明的
capabilities）。

探测的能力：
  - ``code_generation``  — 代码生成能力
  - ``code_review``      — 代码审查能力
  - ``chat``             — 对话能力
  - ``long_context``     — 长上下文能力
  - ``tool_use``         — 工具使用能力

线程安全：``threading.RLock`` 保护内部缓存与统计。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from pydantic import BaseModel, Field

from maop.core.agent.registry.agent_catalog import (
    AgentCatalog,
    AgentCapability,
    AgentDescriptor,
)

logger = logging.getLogger(__name__)


# ── Pydantic 模型 ────────────────────────────────────────────────
class ProbeResult(BaseModel):
    """能力探测结果."""

    agent_name: str = Field(..., description="Agent 名称")
    capabilities: list[str] = Field(default_factory=list, description="探测到的能力列表")
    response_time_s: float = Field(default=0.0, ge=0.0, description="探测总耗时(秒)")
    details: dict[str, Any] = Field(default_factory=dict, description="详细信息")


# ── 标准测试任务 ─────────────────────────────────────────────────
# 代码生成测试任务
_CODE_GEN_TASK: str = "Write a Python function to add two numbers"

# 代码审查测试任务
_CODE_REVIEW_TASK: str = "Review this code: def add(a,b): return a+b"

# 对话测试任务
_CHAT_TASK: str = "Hello, what can you do?"

# 工具使用测试任务
_TOOL_USE_TASK: str = "List files in current directory"

# 长上下文测试任务（5000字重复文本 + 问题）
_LONG_CONTEXT_PADDING: str = "This is a long context padding sentence for testing. " * 125  # ~5000 字
_LONG_CONTEXT_TASK: str = (
    _LONG_CONTEXT_PADDING
    + "\n\nBased on the above context, summarize the key points in one sentence."
)

# 能力标签（与 AgentCapability.value 对齐）
_CAP_CODE_GENERATION: str = AgentCapability.CODE_GENERATION.value
_CAP_CODE_REVIEW: str = AgentCapability.CODE_REVIEW.value
_CAP_CHAT: str = AgentCapability.CHAT.value
_CAP_LONG_CONTEXT: str = AgentCapability.LONG_CONTEXT.value
_CAP_TOOL_USE: str = AgentCapability.TOOL_USE.value


class CapabilityProbe:
    """Agent 能力动态探测器.

    Usage::

        probe = CapabilityProbe()
        result = probe.probe("claude-code", adapter=some_adapter)
        print(result.capabilities)  # ['code_generation', 'tool_use', ...]

        # 仅静态分析（无 adapter）
        result = probe.probe("claude-code")
        print(result.capabilities)  # 基于 AgentDescriptor.capabilities

    Parameters
    ----------
    catalog : AgentCatalog | None
        Agent 元数据注册中心。``None`` 时使用 ``AgentCatalog.default()``。
        仅在 ``adapter=None`` 的静态分析路径中使用。
    """

    def __init__(self, catalog: AgentCatalog | None = None) -> None:
        self._catalog: AgentCatalog = catalog if catalog is not None else AgentCatalog.default()
        self._lock = threading.RLock()
        # 探测结果缓存：agent_name -> ProbeResult
        self._cache: dict[str, ProbeResult] = {}
        # 探测统计
        self._total_probes: int = 0
        self._total_static: int = 0
        self._total_dynamic: int = 0

    # ── 综合探测 ────────────────────────────────────────────────
    def probe(
        self,
        agent_name: str,
        adapter: Any = None,
    ) -> ProbeResult:
        """探测 Agent 实际能力.

        当 ``adapter`` 为 None 时，仅返回静态分析结果（基于
        AgentDescriptor 声明的 capabilities）。否则动态发送测试任务
        并分析返回。

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        adapter : AgentAdapter | None
            Agent 适配器实例。``None`` 时仅静态分析。

        Returns
        -------
        ProbeResult
            探测结果。
        """
        start = time.monotonic()
        with self._lock:
            self._total_probes += 1

        if adapter is None:
            # 静态分析路径
            with self._lock:
                self._total_static += 1
            capabilities, details = self._static_probe(agent_name)
            result = ProbeResult(
                agent_name=agent_name,
                capabilities=capabilities,
                response_time_s=time.monotonic() - start,
                details=details,
            )
        else:
            # 动态探测路径
            with self._lock:
                self._total_dynamic += 1
            capabilities, details = self._dynamic_probe(agent_name, adapter)
            result = ProbeResult(
                agent_name=agent_name,
                capabilities=capabilities,
                response_time_s=time.monotonic() - start,
                details=details,
            )

        # 缓存结果
        with self._lock:
            self._cache[agent_name] = result
        return result

    # ── 单项探测 ────────────────────────────────────────────────
    def probe_code_generation(
        self,
        agent_name: str,
        adapter: Any = None,
    ) -> bool:
        """探测代码生成能力.

        动态探测时发送 ``_CODE_GEN_TASK``，检查返回是否包含函数定义
        特征（``def`` 或 ``function`` 或 ``func``）。
        """
        if adapter is None:
            return self._has_capability_static(agent_name, AgentCapability.CODE_GENERATION)
        response = self._safe_execute(adapter, _CODE_GEN_TASK)
        if response is None:
            return False
        return self._analyze_code_generation(response)

    def probe_code_review(
        self,
        agent_name: str,
        adapter: Any = None,
    ) -> bool:
        """探测代码审查能力.

        动态探测时发送 ``_CODE_REVIEW_TASK``，检查返回是否包含审查
        关键词（``review``/``good``/``issue``/``suggest``/``improve``/
        ``correct``/``ok``/``fine``）。
        """
        if adapter is None:
            return self._has_capability_static(agent_name, AgentCapability.CODE_REVIEW)
        response = self._safe_execute(adapter, _CODE_REVIEW_TASK)
        if response is None:
            return False
        return self._analyze_code_review(response)

    def probe_chat(
        self,
        agent_name: str,
        adapter: Any = None,
    ) -> bool:
        """探测对话能力.

        动态探测时发送 ``_CHAT_TASK``，检查返回是否非空且长度 >= 5。
        """
        if adapter is None:
            return self._has_capability_static(agent_name, AgentCapability.CHAT)
        response = self._safe_execute(adapter, _CHAT_TASK)
        if response is None:
            return False
        return self._analyze_chat(response)

    def probe_long_context(
        self,
        agent_name: str,
        adapter: Any = None,
    ) -> bool:
        """探测长上下文能力.

        动态探测时发送 ``_LONG_CONTEXT_TASK``（约5000字），检查返回
        是否非空且未报错。
        """
        if adapter is None:
            return self._has_capability_static(agent_name, AgentCapability.LONG_CONTEXT)
        response = self._safe_execute(adapter, _LONG_CONTEXT_TASK)
        if response is None:
            return False
        return self._analyze_long_context(response)

    def probe_tool_use(
        self,
        agent_name: str,
        adapter: Any = None,
    ) -> bool:
        """探测工具使用能力.

        动态探测时发送 ``_TOOL_USE_TASK``，检查返回是否包含文件列表
        特征（包含 ``.`` 扩展名或路径分隔符）。
        """
        if adapter is None:
            return self._has_capability_static(agent_name, AgentCapability.TOOL_USE)
        response = self._safe_execute(adapter, _TOOL_USE_TASK)
        if response is None:
            return False
        return self._analyze_tool_use(response)

    # ── 缓存与统计 ──────────────────────────────────────────────
    def get_cached(self, agent_name: str) -> ProbeResult | None:
        """获取上次探测的缓存结果.

        Returns
        -------
        ProbeResult | None
            缓存结果，未探测过返回 None。
        """
        with self._lock:
            return self._cache.get(agent_name)

    def get_stats(self) -> dict[str, int]:
        """获取探测统计.

        Returns
        -------
        dict[str, int]
            ``total_probes`` / ``total_static`` / ``total_dynamic``。
        """
        with self._lock:
            return {
                "total_probes": self._total_probes,
                "total_static": self._total_static,
                "total_dynamic": self._total_dynamic,
            }

    # ── 内部：静态分析 ──────────────────────────────────────────
    def _static_probe(self, agent_name: str) -> tuple[list[str], dict[str, Any]]:
        """静态分析路径：基于 AgentDescriptor.capabilities 返回.

        Returns
        -------
        tuple[list[str], dict[str, Any]]
            (能力列表, 详细信息)。
        """
        descriptor = self._catalog.get(agent_name)
        if descriptor is None:
            return ([], {"source": "static", "found": False, "reason": "agent not in catalog"})
        capabilities = [c.value if hasattr(c, "value") else str(c) for c in descriptor.capabilities]
        return (
            capabilities,
            {
                "source": "static",
                "found": True,
                "declared_capabilities": capabilities,
                "max_context_length": descriptor.max_context_length,
                "supports_tool_call": descriptor.supports_tool_call,
            },
        )

    def _has_capability_static(
        self,
        agent_name: str,
        capability: AgentCapability,
    ) -> bool:
        """静态检查 Agent 是否声明了指定能力."""
        descriptor = self._catalog.get(agent_name)
        if descriptor is None:
            return False
        return capability in descriptor.capabilities

    # ── 内部：动态探测 ──────────────────────────────────────────
    def _dynamic_probe(
        self,
        agent_name: str,
        adapter: Any,
    ) -> tuple[list[str], dict[str, Any]]:
        """动态探测路径：发送测试任务并分析返回.

        Returns
        -------
        tuple[list[str], dict[str, Any]]
            (能力列表, 详细信息)。
        """
        details: dict[str, Any] = {"source": "dynamic", "tests": {}}
        capabilities: list[str] = []

        # 代码生成
        try:
            resp = adapter.execute(_CODE_GEN_TASK)
            ok = self._analyze_code_generation(resp)
            details["tests"]["code_generation"] = {"ok": ok, "response_len": len(resp)}
            if ok:
                capabilities.append(_CAP_CODE_GENERATION)
        except Exception as exc:
            details["tests"]["code_generation"] = {"ok": False, "error": str(exc)}

        # 代码审查
        try:
            resp = adapter.execute(_CODE_REVIEW_TASK)
            ok = self._analyze_code_review(resp)
            details["tests"]["code_review"] = {"ok": ok, "response_len": len(resp)}
            if ok:
                capabilities.append(_CAP_CODE_REVIEW)
        except Exception as exc:
            details["tests"]["code_review"] = {"ok": False, "error": str(exc)}

        # 对话
        try:
            resp = adapter.execute(_CHAT_TASK)
            ok = self._analyze_chat(resp)
            details["tests"]["chat"] = {"ok": ok, "response_len": len(resp)}
            if ok:
                capabilities.append(_CAP_CHAT)
        except Exception as exc:
            details["tests"]["chat"] = {"ok": False, "error": str(exc)}

        # 长上下文
        try:
            resp = adapter.execute(_LONG_CONTEXT_TASK)
            ok = self._analyze_long_context(resp)
            details["tests"]["long_context"] = {"ok": ok, "response_len": len(resp)}
            if ok:
                capabilities.append(_CAP_LONG_CONTEXT)
        except Exception as exc:
            details["tests"]["long_context"] = {"ok": False, "error": str(exc)}

        # 工具使用
        try:
            resp = adapter.execute(_TOOL_USE_TASK)
            ok = self._analyze_tool_use(resp)
            details["tests"]["tool_use"] = {"ok": ok, "response_len": len(resp)}
            if ok:
                capabilities.append(_CAP_TOOL_USE)
        except Exception as exc:
            details["tests"]["tool_use"] = {"ok": False, "error": str(exc)}

        return (capabilities, details)

    # ── 内部：响应分析 ──────────────────────────────────────────
    @staticmethod
    def _safe_execute(adapter: Any, task: str) -> str | None:
        """安全调用 adapter.execute，异常时返回 None."""
        try:
            return adapter.execute(task)
        except Exception as exc:
            logger.debug("[capability_probe] adapter.execute failed: %s", exc)
            return None

    @staticmethod
    def _analyze_code_generation(response: str) -> bool:
        """分析代码生成响应.

        判定标准：响应中包含 ``def`` / ``function`` / ``func`` 关键词
        且长度 >= 10。
        """
        if not response or len(response) < 10:
            return False
        lower = response.lower()
        return any(kw in lower for kw in ("def ", "function ", "func ", "def(", "lambda "))

    @staticmethod
    def _analyze_code_review(response: str) -> bool:
        """分析代码审查响应.

        判定标准：响应中包含审查相关关键词。
        """
        if not response or len(response) < 3:
            return False
        lower = response.lower()
        keywords = (
            "review", "good", "issue", "suggest", "improve",
            "correct", "ok", "fine", "nice", "bug", "fix",
            "looks", "proper", "clean", "style",
        )
        return any(kw in lower for kw in keywords)

    @staticmethod
    def _analyze_chat(response: str) -> bool:
        """分析对话响应.

        判定标准：响应非空且长度 >= 5。
        """
        return bool(response) and len(response) >= 5

    @staticmethod
    def _analyze_long_context(response: str) -> bool:
        """分析长上下文响应.

        判定标准：响应非空且长度 >= 10（未报错即认为能处理）。
        """
        return bool(response) and len(response) >= 10

    @staticmethod
    def _analyze_tool_use(response: str) -> bool:
        """分析工具使用响应.

        判定标准：响应中包含文件特征（``.`` 扩展名或路径分隔符或
        ``file``/``directory``/``dir`` 关键词）。
        """
        if not response or len(response) < 3:
            return False
        lower = response.lower()
        # 包含扩展名或路径分隔符
        if "." in lower or "/" in lower or "\\" in lower:
            return True
        # 包含文件/目录关键词
        return any(kw in lower for kw in ("file", "directory", "dir", "folder", "listing"))