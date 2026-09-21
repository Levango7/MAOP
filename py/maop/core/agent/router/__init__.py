"""MAOP Agent Router — Agent 路由策略 + 降级链模块.

根据 ``RoutingStrategy`` 从 ``AgentCatalog`` 中选择最优 Agent，并构建降级链。
支持能力匹配、成本优化、负载均衡、优先级、轮询等策略；提供 acquire/release
并发槽位管理。

Usage::

    from maop.core.agent.router import (
        AgentRouter, RoutingContext, RoutingResult, RoutingStrategy,
    )

    router = AgentRouter.default()
    ctx = RoutingContext(required_capabilities=[AgentCapability.CODE_GENERATION])
    result = router.route(ctx, strategy=RoutingStrategy.COST_OPTIMIZED)
    if result.primary:
        ...  # 使用 result.primary，失败时按 result.fallbacks 降级

See:
    - agent_router.py — AgentRouter 实现
"""

from __future__ import annotations

from maop.core.agent.router.agent_router import (
    AgentRouter,
    RoutingContext,
    RoutingResult,
    RoutingStrategy,
)
from maop.core.agent.router.rate_limiter import RateLimiter
from maop.core.agent.router.routing_audit import (
    RoutingAuditEvent,
    RoutingAuditLogger,
)

__all__ = [
    "AgentRouter",
    "RateLimiter",
    "RoutingAuditEvent",
    "RoutingAuditLogger",
    "RoutingContext",
    "RoutingResult",
    "RoutingStrategy",
]