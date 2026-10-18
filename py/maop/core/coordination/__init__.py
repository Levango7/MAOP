"""MAOP Coordination — Multi-Agent协同核心协议栈。

Exports
-------
A2A ↔ Blackboard 桥接:
    - A2ABlackboardBridge: A2A JSON-RPC 2.0 消息与 Blackboard 双向桥接
    - A2AMessage, AgentCard: A2A 协议信封与能力描述

Agent 注册发现:
    - InMemoryAgentStore / RedisAgentStore: AgentCard 持久化存储
    - RedisAgentRegistry: 动态注册表 + 能力匹配 + 健康探测

分布式黑板同步 (M2-A):
    - BlackboardTaskContext: Worker 端读写分布式黑板的便捷接口

分布式 DAG 断点续跑 (M2-C):
    - 通过 BlackboardTaskContext 桥接 Worker ↔ Blackboard

协调可观测性 (M2-D):
    - CoordinationMetrics: 计数器 / 直方图 / 计时器
    - CoordinationTracer: 分布式追踪上下文传播
    - CoordinationProbe: 实时只读状态快照

MCP Hub:
    - MCPHub: 多传输协议 (Stdio/SSE/WS/StreamableHTTP) 的 MCP 工具中心
    MCPServer / *Transport: MCP 服务端与传输抽象
    - InMemoryMCPRegistry: 本地工具注册表

MCP Hub Bridge (M2-E):
    - CoordMCPHubBridge: 协调层 ↔ 生产层 MCPHub 桥接适配器
"""

from maop.core.coordination.a2a_blackboard_bridge import (
    A2ABlackboardBridge,
    A2AMessage,
    AgentCard,
)
from maop.core.coordination.a2a_transport_server import (
    A2ATransportServer,
    InMemoryTaskStore,
)
from maop.core.coordination.agent_registry import (
    DefaultHealthChecker,
    InMemoryAgentStore,
    RedisAgentRegistry,
    RedisAgentStore,
)
from maop.core.coordination.bb_task_context import BlackboardTaskContext
from maop.core.coordination.mcp_coord_bridge import CoordMCPHubBridge
from maop.core.coordination.mcp_hub import (
    InMemoryMCPRegistry,
    MCPHub,
    MCPServer,
    SSETransport,
    StdioTransport,
    StreamableHTTPTransport,
    WebSocketTransport,
)
from maop.core.coordination.observability import (
    CoordinationMetrics,
    CoordinationProbe,
    CoordinationTracer,
)

__all__ = [
    "A2ABlackboardBridge",
    "A2AMessage",
    "A2ATransportServer",
    "AgentCard",
    "BlackboardTaskContext",
    "CoordMCPHubBridge",
    "CoordinationMetrics",
    "CoordinationProbe",
    "CoordinationTracer",
    "DefaultHealthChecker",
    "InMemoryAgentStore",
    "InMemoryMCPRegistry",
    "InMemoryTaskStore",
    "MCPHub",
    "MCPServer",
    "RedisAgentRegistry",
    "RedisAgentStore",
    "SSETransport",
    "StdioTransport",
    "StreamableHTTPTransport",
    "WebSocketTransport",
]
