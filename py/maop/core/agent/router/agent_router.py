"""MAOP Agent Router — Agent 路由策略 + 降级链.

从 ``AgentCatalog`` 中按策略选择最优 Agent，并构建降级链。
支持能力匹配 / 成本优化 / 负载均衡 / 优先级 / 轮询五种策略；
提供 acquire/release 并发槽位管理（线程安全）。

核心组件：
  - ``RoutingContext``   — 路由请求上下文（Pydantic 模型）
  - ``RoutingResult``    — 路由结果（Pydantic 模型）
  - ``RoutingStrategy``  — 路由策略枚举
  - ``AgentRouter``      — 路由器

Usage::

    from maop.core.agent.router import (
        AgentRouter, RoutingContext, RoutingResult, RoutingStrategy,
    )
    from maop.core.agent.registry.agent_catalog import AgentCapability

    router = AgentRouter()
    ctx = RoutingContext(required_capabilities=[AgentCapability.CODE_GENERATION])
    result = router.route(ctx, strategy=RoutingStrategy.COST_OPTIMIZED)
    if result.primary:
        agent = result.primary
        if router.acquire(agent.name):
            try:
                ...  # 执行 Agent 调用
            finally:
                router.release(agent.name)
        else:
            # 并发已满，尝试降级链
            for fb in result.fallbacks:
                ...
"""

from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from maop.core.agent.registry.agent_catalog import (
    AgentCapability,
    AgentCatalog,
    AgentDescriptor,
    BillingModel,
)
# 依赖 sqlite_connect / get_db_path：AgentCatalog 已封装持久化，
# 此处导入以保持模块依赖图完整（供未来扩展直接访问 DB 时使用）。
from maop.core.backends.db_utils import get_db_path, sqlite_connect  # noqa: F401

logger = logging.getLogger(__name__)


# ── 成本优先级映射 ─────────────────────────────────────────────────
# BillingModel → 优先级数字，越小越优。
# SUBSCRIPTION（包月已付费，边际成本为0）优于 CREDIT / PER_CALL / PER_TOKEN。
_COST_PRIORITY: dict[BillingModel, int] = {
    BillingModel.FREE: 0,
    BillingModel.SUBSCRIPTION: 1,
    BillingModel.CREDIT: 2,
    BillingModel.PER_CALL: 3,
    BillingModel.PER_TOKEN: 4,
    BillingModel.BLACKBOX: 5,
}

# max_cost_tier → 允许的最大成本优先级（含）。
# low: 仅免费/包月；medium: 加积分/按次；high: 加按Token；any: 全部。
_COST_TIER_LIMIT: dict[str, int] = {
    "low": 1,
    "medium": 3,
    "high": 4,
    "any": 5,
}


# ── 枚举 ──────────────────────────────────────────────────────────
class RoutingStrategy(str, Enum):
    """路由策略枚举（str mixin 便于 JSON 序列化与比较）。"""

    CAPABILITY_MATCH = "capability_match"  # 按能力匹配（能力越多越优先）
    COST_OPTIMIZED = "cost_optimized"      # 按成本优化（成本越低越优先）
    LOAD_BALANCED = "load_balanced"        # 负载均衡（活跃数越少越优先）
    PRIORITY = "priority"                  # 按优先级（先注册先服务）
    ROUND_ROBIN = "round_robin"            # 轮询


# ── Pydantic 模型 ─────────────────────────────────────────────────
class RoutingContext(BaseModel):
    """路由请求上下文.

    封装一次路由决策所需的全部输入：能力需求、偏好、排除、成本约束等。
    所有可变默认值使用 ``Field(default_factory=...)`` 避免 Pydantic 共享
    可变默认值的陷阱。
    """

    required_capabilities: list[AgentCapability] = Field(
        default_factory=list, description="需要的能力（候选必须具备全部）",
    )
    preferred_agent: str = Field(default="", description="优先 Agent 名（在候选中则直选）")
    excluded_agents: list[str] = Field(
        default_factory=list, description="排除的 Agent 名",
    )
    max_cost_tier: str = Field(
        default="low", description="成本偏好: low/medium/high/any",
    )
    require_healthy: bool = Field(default=True, description="是否要求健康")
    session_id: str = Field(default="", description="会话 ID（用于负载均衡粘性）")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="附加元数据（扩展用）",
    )


class RoutingResult(BaseModel):
    """路由结果.

    包含选中的主 Agent、降级链、选择原因及其他可用候选。
    ``primary`` 为 None 表示无可用 Agent。
    """

    primary: AgentDescriptor | None = Field(default=None, description="选中的主 Agent")
    fallbacks: list[AgentDescriptor] = Field(
        default_factory=list, description="降级链（按优先级排序）",
    )
    reason: str = Field(default="", description="选择原因（供日志/调试）")
    alternatives: list[AgentDescriptor] = Field(
        default_factory=list, description="其他可用候选（排除 primary 和 fallbacks）",
    )


# ── AgentRouter ───────────────────────────────────────────────────
class AgentRouter:
    """Agent 路由器：从 AgentCatalog 中按策略选择最优 Agent.

    线程安全：使用 RLock 保护 ``_round_robin_idx`` 和 ``_active_count``
    等共享状态。``AgentCatalog`` 自身线程安全（内部 RLock）。

    Parameters
    ----------
    catalog : AgentCatalog | None
        Agent 注册中心。``None`` 时使用 ``AgentCatalog.default()``。
    """

    def __init__(self, catalog: AgentCatalog | None = None) -> None:
        self._catalog: AgentCatalog = catalog if catalog is not None else AgentCatalog.default()
        self._lock = threading.RLock()
        # capability key -> 当前轮询索引
        self._round_robin_idx: dict[str, int] = {}
        # agent name -> 当前活跃并发数
        self._active_count: dict[str, int] = {}

    # ── 公共属性 ──────────────────────────────────────────────────
    @property
    def catalog(self) -> AgentCatalog:
        """底层 AgentCatalog（只读访问）。"""
        return self._catalog

    # ── 路由入口 ──────────────────────────────────────────────────
    def route(
        self,
        ctx: RoutingContext,
        strategy: RoutingStrategy = RoutingStrategy.CAPABILITY_MATCH,
    ) -> RoutingResult:
        """根据策略路由选择 Agent.

        步骤：
          1. 从 catalog.list_enabled() 获取所有启用 Agent
          2. 按 ctx.excluded_agents 排除
          3. 按 ctx.required_capabilities 筛选（必须具备全部）
          4. 按 ctx.max_cost_tier 过滤成本偏好
          5. 若 ctx.preferred_agent 在候选中，直接选为 primary
          6. 否则按 strategy 排序/选择
          7. 若 ctx.require_healthy，过滤不健康的
          8. 取第一个作为 primary
          9. 构建 fallbacks + alternatives
         10. 返回 RoutingResult

        Parameters
        ----------
        ctx : RoutingContext
            路由请求上下文。
        strategy : RoutingStrategy
            路由策略，默认 CAPABILITY_MATCH。

        Returns
        -------
        RoutingResult
            路由结果。无候选时 primary=None。
        """
        # 1. 获取所有启用 Agent
        candidates = self._catalog.list_enabled()
        if not candidates:
            return RoutingResult(reason="no enabled agents in catalog")

        # 2. 排除
        if ctx.excluded_agents:
            candidates = self._filter_by_exclusion(candidates, ctx.excluded_agents)

        # 3. 能力筛选
        if ctx.required_capabilities:
            candidates = self._filter_by_capabilities(candidates, ctx.required_capabilities)

        # 4. 成本偏好过滤
        candidates = self._filter_by_cost_tier(candidates, ctx.max_cost_tier)

        if not candidates:
            return RoutingResult(reason="no candidates after filtering")

        # 5. preferred_agent 优先
        primary: AgentDescriptor | None = None
        reason = ""
        if ctx.preferred_agent:
            for agent in candidates:
                if agent.name == ctx.preferred_agent:
                    primary = agent
                    reason = f"preferred_agent={ctx.preferred_agent!r} matched"
                    break

        # 6. 按策略选择
        if primary is None:
            if strategy == RoutingStrategy.ROUND_ROBIN:
                key = self._rr_key(ctx)
                primary = self._round_robin_select(candidates, key)
                reason = f"round_robin selected {primary.name if primary else 'None'}"
            else:
                sorted_agents = self._sort_by_strategy(candidates, strategy)
                if sorted_agents:
                    primary = sorted_agents[0]
                    reason = f"{strategy.value} selected {primary.name}"

        if primary is None:
            return RoutingResult(reason="no primary after strategy selection")

        # 7. 健康过滤（对 primary）
        if ctx.require_healthy and not primary.healthy:
            healthy_candidates = self._filter_by_health(candidates)
            if healthy_candidates:
                # 在健康候选中重新选择
                if strategy == RoutingStrategy.ROUND_ROBIN:
                    key = self._rr_key(ctx)
                    primary = self._round_robin_select(healthy_candidates, key) or healthy_candidates[0]
                else:
                    sorted_healthy = self._sort_by_strategy(healthy_candidates, strategy)
                    primary = sorted_healthy[0] if sorted_healthy else healthy_candidates[0]
                reason = f"{strategy.value} re-selected healthy {primary.name}"
            else:
                return RoutingResult(reason="no healthy candidates")

        # 8. 构建 fallbacks + alternatives
        fallbacks = self._build_fallbacks(primary, ctx)
        # alternatives: 候选中除 primary 和 fallbacks 外的其他
        fb_names = {a.name for a in fallbacks}
        alternatives = [a for a in candidates if a.name != primary.name and a.name not in fb_names]

        return RoutingResult(
            primary=primary,
            fallbacks=fallbacks,
            reason=reason,
            alternatives=alternatives,
        )

    # ── 筛选方法 ──────────────────────────────────────────────────
    def _filter_by_capabilities(
        self,
        agents: list[AgentDescriptor],
        caps: list[AgentCapability],
    ) -> list[AgentDescriptor]:
        """筛选具备所有指定能力的 Agent。"""
        required = set(caps)
        return [a for a in agents if required.issubset(set(a.capabilities))]

    def _filter_by_health(self, agents: list[AgentDescriptor]) -> list[AgentDescriptor]:
        """筛选健康 Agent。"""
        return [a for a in agents if a.healthy]

    def _filter_by_exclusion(
        self,
        agents: list[AgentDescriptor],
        excluded: list[str],
    ) -> list[AgentDescriptor]:
        """排除指定 Agent。"""
        excluded_set = set(excluded)
        return [a for a in agents if a.name not in excluded_set]

    def _filter_by_cost_tier(
        self,
        agents: list[AgentDescriptor],
        tier: str,
    ) -> list[AgentDescriptor]:
        """按成本偏好过滤（tier 越严格，允许的计费模式越少）."""
        limit = _COST_TIER_LIMIT.get(tier, _COST_TIER_LIMIT["any"])
        return [a for a in agents if _COST_PRIORITY.get(a.billing_model, 5) <= limit]

    # ── 排序方法 ──────────────────────────────────────────────────
    def _sort_by_cost(self, agents: list[AgentDescriptor]) -> list[AgentDescriptor]:
        """按成本排序：FREE < SUBSCRIPTION < CREDIT < PER_CALL < PER_TOKEN < BLACKBOX."""
        return sorted(
            agents,
            key=lambda a: (_COST_PRIORITY.get(a.billing_model, 5), a.name),
        )

    def _sort_by_load(self, agents: list[AgentDescriptor]) -> list[AgentDescriptor]:
        """按当前活跃数排序，少的优先（同负载按 name 稳定排序）."""
        with self._lock:
            return sorted(
                agents,
                key=lambda a: (self._active_count.get(a.name, 0), a.name),
            )

    def _sort_by_capability_count(self, agents: list[AgentDescriptor]) -> list[AgentDescriptor]:
        """按能力数量降序排序（能力越多越优先，同数量按 name 稳定排序）."""
        return sorted(
            agents,
            key=lambda a: (-len(a.capabilities), a.name),
        )

    def _sort_by_strategy(
        self,
        agents: list[AgentDescriptor],
        strategy: RoutingStrategy,
    ) -> list[AgentDescriptor]:
        """按指定策略排序（ROUND_ROBIN 由 _round_robin_select 处理，不在此列）."""
        if strategy == RoutingStrategy.CAPABILITY_MATCH:
            return self._sort_by_capability_count(agents)
        if strategy == RoutingStrategy.COST_OPTIMIZED:
            return self._sort_by_cost(agents)
        if strategy == RoutingStrategy.LOAD_BALANCED:
            return self._sort_by_load(agents)
        if strategy == RoutingStrategy.PRIORITY:
            # 按注册顺序（list_enabled 返回 dict 插入顺序）
            return list(agents)
        # 默认：保持原序
        return list(agents)

    # ── 轮询 ──────────────────────────────────────────────────────
    def _rr_key(self, ctx: RoutingContext) -> str:
        """生成轮询 key：基于能力列表 + session_id（粘性）."""
        caps = ",".join(sorted(c.value for c in ctx.required_capabilities))
        return f"{caps}|{ctx.session_id}"

    def _round_robin_select(
        self,
        agents: list[AgentDescriptor],
        key: str,
    ) -> AgentDescriptor | None:
        """轮询选择：基于 key 维护索引，每次调用前进一位.

        空候选返回 None；单候选直接返回。
        """
        if not agents:
            return None
        if len(agents) == 1:
            return agents[0]
        with self._lock:
            idx = self._round_robin_idx.get(key, 0) % len(agents)
            selected = agents[idx]
            self._round_robin_idx[key] = (idx + 1) % len(agents)
            return selected

    # ── 降级链构建 ────────────────────────────────────────────────
    def _build_fallbacks(
        self,
        primary: AgentDescriptor,
        ctx: RoutingContext,
    ) -> list[AgentDescriptor]:
        """构建降级链：先取 Agent 自身 fallback_agents，再补充同能力其他 Agent.

        降级链顺序：
          1. primary.fallback_agents 中存在且启用的 Agent（按声明顺序）
          2. 同能力（具备 primary 全部能力）的其他启用 Agent（按 name 排序）
        去重，排除 primary 自身。
        """
        chain: list[AgentDescriptor] = []
        seen: set[str] = {primary.name}

        # 1. primary 自身声明的降级链
        for fb in self._catalog.get_fallback_chain(primary.name):
            if fb.name not in seen:
                chain.append(fb)
                seen.add(fb.name)

        # 2. 补充同能力其他 Agent
        if primary.capabilities:
            same_cap = self._filter_by_capabilities(
                self._catalog.list_enabled(), primary.capabilities,
            )
            same_cap.sort(key=lambda a: a.name)
            for agent in same_cap:
                if agent.name not in seen:
                    chain.append(agent)
                    seen.add(agent.name)
        else:
            # 无能力声明时，补充所有其他启用 Agent
            for agent in self._catalog.list_enabled():
                if agent.name not in seen:
                    chain.append(agent)
                    seen.add(agent.name)

        return chain

    # ── 并发槽位管理 ──────────────────────────────────────────────
    def acquire(self, agent_name: str) -> bool:
        """占用一个 Agent 并发槽位.

        检查 active_count < max_concurrent，是则 +1 返回 True；
        否则返回 False（并发已满）。Agent 不存在时返回 False。

        Parameters
        ----------
        agent_name : str
            Agent 名称。

        Returns
        -------
        bool
            True 表示成功占用槽位；False 表示并发已满或 Agent 不存在。
        """
        desc = self._catalog.get(agent_name)
        if desc is None:
            logger.warning("[agent_router] acquire: agent %r not found", agent_name)
            return False
        max_concurrent = desc.max_concurrent
        with self._lock:
            current = self._active_count.get(agent_name, 0)
            if current >= max_concurrent:
                logger.debug(
                    "[agent_router] acquire: %s at capacity %d/%d",
                    agent_name, current, max_concurrent,
                )
                return False
            self._active_count[agent_name] = current + 1
            return True

    def release(self, agent_name: str) -> None:
        """释放 Agent 并发槽位.

        active_count > 0 则 -1；为 0 时静默忽略（防止下溢）。
        """
        with self._lock:
            current = self._active_count.get(agent_name, 0)
            if current > 0:
                self._active_count[agent_name] = current - 1
            else:
                logger.debug("[agent_router] release: %s already at 0", agent_name)

    def get_active_count(self, agent_name: str) -> int:
        """获取 Agent 当前活跃并发数."""
        with self._lock:
            return self._active_count.get(agent_name, 0)