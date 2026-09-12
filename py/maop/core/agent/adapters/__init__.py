"""MAOP Agent Adapters — 通用适配器实现。

提供四种开箱即用的 ``AgentAdapter`` 实现，覆盖 MAOP 对接外部 Agent
的主要集成场景：

  - :class:`CLIAdapter` — 通过 ``subprocess`` 调用外部 CLI 工具
    （如 Claude Code、Aider、codex 等），内置命令白名单与工作目录沙箱。
  - :class:`HTTPAdapter` — 通过 ``httpx`` 调用外部 AI 服务 HTTP API
    （OpenAI 兼容、DeepSeek、Ollama 等），支持点分响应路径提取与重试。
  - :class:`MCPBridgeAdapter` — 将已注册的 MCPHub Server 包装为
    ``AgentAdapter``，复用 MCPHub 单例的连接/调用逻辑。
  - :class:`WebAdapter` — 对接仅有 Web 界面、无公开 API 的 Agent，
    通过模拟 HTTP 请求（Cookie / Token / Session）调用。

所有适配器均继承 :class:`maop.core.agent.delegation.agent_proxy.AgentAdapter`
并实现其 5 个抽象方法：``connect``、``execute``、``health_check``、
``sync_config``、``disconnect``。

Usage::

    from maop.core.agent.adapters import CLIAdapter, CLIAdapterConfig

    adapter = CLIAdapter(CLIAdapterConfig(command="python -m my_agent"))
    if adapter.connect():
        result = adapter.execute("Analyze this code")
"""
from __future__ import annotations

from maop.core.agent.adapters.cli_adapter import CLIAdapter, CLIAdapterConfig
from maop.core.agent.adapters.http_adapter import HTTPAdapter, HTTPAdapterConfig
from maop.core.agent.adapters.mcp_bridge_adapter import (
    MCPBridgeAdapter,
    MCPBridgeAdapterConfig,
)
from maop.core.agent.adapters.web_adapter import WebAdapter, WebAdapterConfig

__all__ = [
    "CLIAdapter",
    "CLIAdapterConfig",
    "HTTPAdapter",
    "HTTPAdapterConfig",
    "MCPBridgeAdapter",
    "MCPBridgeAdapterConfig",
    "WebAdapter",
    "WebAdapterConfig",
]