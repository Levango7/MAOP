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
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from maop.core.agent.registry.agent_catalog import (
    AgentCapability,
    AgentCatalog,
    AgentDescriptor,
    BillingModel,
)
from maop.core.agent.router.rate_limiter import RateLimiter
from maop.core.agent.router.routing_audit import RoutingAuditEvent, RoutingAuditLogger
# 依赖 sqlite_connect / get_db_path：AgentCatalog 已封装持久化，
# 此处导入以保持模块依赖图完整（供未来扩展直接访问 DB 时使用）。
from maop.core.backends.db_utils import get_db_path, sqlite_connect  # noqa: F401

if TYPE_CHECKING:
    # 仅用于类型注解，避免运行时循环导入
    from maop.core.agent.llm_chat.model_gateway import ModelGateway

logger = logging.getLogger(__name__)

# model_gateway 权限校验最大重试次数（防止无限循环）
_MAX_PERMISSION_RETRIES: int = 3
# 限流重选最大重试次数（防止候选全部被限流时无限循环）
_MAX_RATE_LIMIT_RETRIES: int = 32


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
    audit_logger : RoutingAuditLogger | None
        路由决策审计日志记录器。``None`` 时审计功能关闭（默认）。
    model_gateway : ModelGateway | None
        模型授权网关。``None`` 时跳过模型权限校验（默认）。
        传入后，路由选中的 Agent 若声明了 ``adapter_config["model"]``，
        将调用 ``check_access(model, agent=name)`` 校验权限，不通过则
        排除该 Agent 并重选（最多重试 3 次）。
    """

    def __init__(
        self,
        catalog: AgentCatalog | None = None,
        audit_logger: RoutingAuditLogger | None = None,
        model_gateway: "ModelGateway | None" = None,
    ) -> None:
        self._catalog: AgentCatalog = catalog if catalog is not None else AgentCatalog.default()
        self._lock = threading.RLock()
        # capability key -> 当前轮询索引
        self._round_robin_idx: dict[str, int] = {}
        # agent name -> 当前活跃并发数
        self._active_count: dict[str, int] = {}
        # 限流器：按 Agent 粒度的滑动窗口限流（rate_limit_per_min）
        self._rate_limiter: RateLimiter = RateLimiter()
        # 路由决策审计日志（可选；None 表示不记录审计）
        self._audit_logger: RoutingAuditLogger | None = audit_logger
        # 模型授权网关（可选；None 表示跳过模型权限校验）
        self._model_gateway: "ModelGateway | None" = model_gateway

    # ── 公共属性 ──────────────────────────────────────────────────
    @property
    def catalog(self) -> AgentCatalog:
        """底层 AgentCatalog（只读访问）。"""
        return self._catalog

    @property
    def rate_limiter(self) -> RateLimiter:
        """限流器实例（只读访问，供外部查询当前速率/手动 cleanup）。"""
        return self._rate_limiter

    @property
    def audit_logger(self) -> RoutingAuditLogger | None:
        """审计日志记录器（None 表示未启用审计）。"""
        return self._audit_logger

    @property
    def model_gateway(self) -> "ModelGateway | None":
        """模型授权网关（None 表示未启用模型权限校验）。"""
        return self._model_gateway

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
         6.5 限流过滤 + model_gateway 权限校验（失败则排除重选）
          7. 若 ctx.require_healthy，过滤不健康的
          8. 取第一个作为 primary，构建 fallbacks + alternatives
         8.5 记录路由成功审计
          9. 返回 RoutingResult

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
            self._audit("route_failed", "", strategy, "no enabled agents in catalog", ctx)
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
            self._audit("route_failed", "", strategy, "no candidates after filtering", ctx)
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
            primary, reason = self._select_primary(candidates, strategy, ctx)

        if primary is None:
            self._audit("route_failed", "", strategy, "no primary after strategy selection", ctx)
            return RoutingResult(reason="no primary after strategy selection")

        # 6.5 限流过滤 + model_gateway 权限校验（步骤6之后、步骤7之前）
        #     校验失败则从候选移除当前 primary 并重选，防止无限循环：
        #     限流重选上限 = 候选总数（每个最多被排除一次）；
        #     权限重选上限 = _MAX_PERMISSION_RETRIES（3 次）。
        rate_retries = 0
        perm_retries = 0
        rate_retry_limit = len(candidates)  # 最多排除全部候选
        while True:
            # 6.5a 限流检查（rate_limit_per_min=0 表示不限流，直接放行）
            if not self._rate_limiter.check_and_record(
                primary.name, primary.rate_limit_per_min,
            ):
                rate_retries += 1
                self._audit(
                    "rate_limited", primary.name, strategy,
                    f"rate_limit_per_min={primary.rate_limit_per_min} exceeded",
                    ctx, extra_context={"rate_retries": rate_retries},
                )
                if rate_retries > rate_retry_limit:
                    self._audit(
                        "route_failed", "", strategy,
                        "all candidates rate limited", ctx,
                    )
                    return RoutingResult(reason="all candidates rate limited")
                # 从候选移除并重选
                candidates = [a for a in candidates if a.name != primary.name]
                if not candidates:
                    self._audit(
                        "route_failed", "", strategy,
                        "all candidates rate limited", ctx,
                    )
                    return RoutingResult(reason="all candidates rate limited")
                primary, reason = self._select_primary(candidates, strategy, ctx)
                if primary is None:
                    self._audit(
                        "route_failed", "", strategy,
                        "no primary after rate-limit reselect", ctx,
                    )
                    return RoutingResult(reason="no primary after rate-limit reselect")
                continue

            # 6.5b model_gateway 权限校验（仅当配置了网关且 Agent 声明了 model）
            if self._model_gateway is not None:
                model = primary.adapter_config.get("model", "") if primary.adapter_config else ""
                if model:
                    decision = self._model_gateway.check_access(
                        model, agent=primary.name,
                    )
                    if not decision.allowed:
                        perm_retries += 1
                        self._audit(
                            "route_failed", primary.name, strategy,
                            f"model permission denied for {model!r}: {decision.reason}",
                            ctx, extra_context={
                                "model": model, "perm_retries": perm_retries,
                            },
                        )
                        if perm_retries > _MAX_PERMISSION_RETRIES:
                            return RoutingResult(
                                reason=f"model permission retries exceeded {_MAX_PERMISSION_RETRIES}",
                            )
                        # 从候选移除并重选
                        candidates = [a for a in candidates if a.name != primary.name]
                        if not candidates:
                            self._audit(
                                "route_failed", "", strategy,
                                "all candidates denied by model gateway", ctx,
                            )
                            return RoutingResult(
                                reason="all candidates denied by model gateway",
                            )
                        primary, reason = self._select_primary(candidates, strategy, ctx)
                        if primary is None:
                            self._audit(
                                "route_failed", "", strategy,
                                "no primary after model-gateway reselect", ctx,
                            )
                            return RoutingResult(
                                reason="no primary after model-gateway reselect",
                            )
                        continue
            # 限流与权限均通过，退出校验循环
            break

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
                self._audit("route_failed", "", strategy, "no healthy candidates", ctx)
                return RoutingResult(reason="no healthy candidates")

        # 8. 构建 fallbacks + alternatives
        fallbacks = self._build_fallbacks(primary, ctx)
        # alternatives: 候选中除 primary 和 fallbacks 外的其他
        fb_names = {a.name for a in fallbacks}
        alternatives = [a for a in candidates if a.name != primary.name and a.name not in fb_names]

        # 8.5 记录路由成功审计
        self._audit(
            "route_selected", primary.name, strategy, reason, ctx,
            extra_context={
                "fallback_count": len(fallbacks),
                "alternative_count": len(alternatives),
            },
        )

        return RoutingResult(
            primary=primary,
            fallbacks=fallbacks,
            reason=reason,
            alternatives=alternatives,
        )

    # ── 内部辅助：策略选择 + 审计 ────────────────────────────────
    def _select_primary(
        self,
        candidates: list[AgentDescriptor],
        strategy: RoutingStrategy,
        ctx: RoutingContext,
    ) -> tuple[AgentDescriptor | None, str]:
        """按策略从候选中选择 primary，返回 (primary, reason).

        ROUND_ROBIN 使用 ``_round_robin_select``；其余策略使用
        ``_sort_by_strategy`` 取首位。空候选返回 (None, "")。
        """
        if not candidates:
            return None, ""
        if strategy == RoutingStrategy.ROUND_ROBIN:
            key = self._rr_key(ctx)
            primary = self._round_robin_select(candidates, key)
            reason = f"round_robin selected {primary.name if primary else 'None'}"
            return primary, reason
        sorted_agents = self._sort_by_strategy(candidates, strategy)
        if not sorted_agents:
            return None, ""
        return sorted_agents[0], f"{strategy.value} selected {sorted_agents[0].name}"

    def _audit(
        self,
        event_type: str,
        agent_name: str,
        strategy: RoutingStrategy | str,
        reason: str,
        ctx: RoutingContext | None = None,
        extra_context: dict[str, Any] | None = None,
    ) -> None:
        """记录一条路由审计事件（未配置 audit_logger 时静默跳过）.

        Parameters
        ----------
        event_type : str
            事件类型：route_selected / route_failed / fallback_switched
            / concurrent_rejected / rate_limited。
        agent_name : str
            相关 Agent 名称。
        strategy : RoutingStrategy | str
            路由策略。
        reason : str
            决策原因。
        ctx : RoutingContext | None
            路由上下文（提取 required_capabilities / excluded 等到 context）。
        extra_context : dict | None
            额外上下文键值对，合并到 context。
        """
        if self._audit_logger is None:
            return
        context: dict[str, Any] = {}
        if ctx is not None:
            context = {
                "required_capabilities": [c.value for c in ctx.required_capabilities],
                "excluded_agents": list(ctx.excluded_agents),
                "preferred_agent": ctx.preferred_agent,
                "max_cost_tier": ctx.max_cost_tier,
                "require_healthy": ctx.require_healthy,
            }
        if extra_context:
            context.update(extra_context)
        strategy_str = strategy.value if isinstance(strategy, RoutingStrategy) else str(strategy)
        self._audit_logger.log(RoutingAuditEvent(
            event_type=event_type,
            agent_name=agent_name,
            strategy=strategy_str,
            reason=reason,
            context=context,
        ))

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

        当 ``_round_robin_idx`` 超过 1000 个条目时，清理最旧的 500 个条目，
        防止字典无限增长（每个唯一的 ``(capabilities, session_id)`` 组合
        创建一个条目，永不清理）。
        """
        if not agents:
            return None
        if len(agents) == 1:
            return agents[0]
        with self._lock:
            # 清理过期的轮询索引，防止 _round_robin_idx 无限增长
            if len(self._round_robin_idx) > 1000:
                # 保留最近500个条目（删除最早的500个）
                keys = list(self._round_robin_idx.keys())
                for k in keys[:500]:
                    del self._round_robin_idx[k]
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
            # 无能力声明时，只使用显式 fallback_agents，不补充其他 Agent
            pass  # chain already has fallback_agents from above

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
                # 记录并发拒绝审计
                self._audit(
                    "concurrent_rejected", agent_name, "",
                    f"concurrent capacity {current}/{max_concurrent} reached",
                    extra_context={"active": current, "max_concurrent": max_concurrent},
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
    # ── 降级切换审计 ──────────────────────────────────────────────
    def log_fallback_switch(
        self,
        from_agent: str,
        to_agent: str,
        strategy: RoutingStrategy | str = "",
        reason: str = "",
    ) -> None:
        """记录从 primary 切换到 fallback 的降级事件.

        路由本身只返回降级链，实际切换发生在调用方。调用方在执行降级
        切换时调用本方法记录审计。

        Parameters
        ----------
        from_agent : str
            原 primary Agent 名称。
        to_agent : str
            切换到的 fallback Agent 名称。
        strategy : RoutingStrategy | str
            路由策略。
        reason : str
            切换原因（如 "concurrent full" / "timeout"）。
        """
        self._audit(
            "fallback_switched", to_agent, strategy,
            reason or f"switched from {from_agent!r} to {to_agent!r}",
            extra_context={"from_agent": from_agent, "to_agent": to_agent},
        )