"""AgentRouter 白盒测试.

覆盖：上下文/结果模型、筛选（能力/健康/排除/成本）、排序（成本/负载/能力数）、
各策略路由、preferred_agent、降级链构建、并发槽位、轮询公平性。
每个测试使用隔离 tmp_path DB，不污染主库。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.agent.registry.agent_catalog import (
    AgentCapability,
    AgentCatalog,
    AgentDescriptor,
    AuthMethod,
    BillingModel,
)
from maop.core.agent.router.agent_router import (
    AgentRouter,
    RoutingContext,
    RoutingResult,
    RoutingStrategy,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def catalog(tmp_path: Path) -> AgentCatalog:
    """使用隔离 tmp_path DB 的全新 AgentCatalog。"""
    return AgentCatalog(db_path=tmp_path / "agent_router.db")


@pytest.fixture
def router(catalog: AgentCatalog) -> AgentRouter:
    """基于隔离 catalog 的全新 AgentRouter。"""
    return AgentRouter(catalog=catalog)


def _desc(name: str, **kwargs: object) -> AgentDescriptor:
    """构造 AgentDescriptor，name 必填，其余可选覆盖。"""
    return AgentDescriptor(name=name, **kwargs)  # type: ignore[arg-type]


def _register_diverse_agents(catalog: AgentCatalog) -> None:
    """注册一组多样化的 Agent 供路由测试使用.

    - free_coder: FREE + CODE_GENERATION + CHAT
    - sub_coder: SUBSCRIPTION + CODE_GENERATION
    - token_coder: PER_TOKEN + CODE_GENERATION + REASONING
    - call_reviewer: PER_CALL + CODE_REVIEW
    - blackbox_agent: BLACKBOX + CHAT
    - unhealthy_coder: PER_CALL + CODE_GENERATION, healthy=False
    """
    catalog.register(_desc(
        "free_coder",
        capabilities=[AgentCapability.CODE_GENERATION, AgentCapability.CHAT],
        billing_model=BillingModel.FREE,
    ))
    catalog.register(_desc(
        "sub_coder",
        capabilities=[AgentCapability.CODE_GENERATION],
        billing_model=BillingModel.SUBSCRIPTION,
    ))
    catalog.register(_desc(
        "token_coder",
        capabilities=[AgentCapability.CODE_GENERATION, AgentCapability.REASONING],
        billing_model=BillingModel.PER_TOKEN,
    ))
    catalog.register(_desc(
        "call_reviewer",
        capabilities=[AgentCapability.CODE_REVIEW],
        billing_model=BillingModel.PER_CALL,
    ))
    catalog.register(_desc(
        "blackbox_agent",
        capabilities=[AgentCapability.CHAT],
        billing_model=BillingModel.BLACKBOX,
    ))
    catalog.register(_desc(
        "unhealthy_coder",
        capabilities=[AgentCapability.CODE_GENERATION],
        billing_model=BillingModel.PER_CALL,
        healthy=False,
    ))


# ── 1. TestRoutingContext ────────────────────────────────────────


class TestRoutingContext:
    """RoutingContext 模型测试。"""

    def test_default_values(self) -> None:
        """默认构造：空能力列表、空偏好、low 成本、要求健康。"""
        ctx = RoutingContext()
        assert ctx.required_capabilities == []
        assert ctx.preferred_agent == ""
        assert ctx.excluded_agents == []
        assert ctx.max_cost_tier == "low"
        assert ctx.require_healthy is True
        assert ctx.session_id == ""
        assert ctx.metadata == {}

    def test_with_capabilities(self) -> None:
        """带能力列表构造。"""
        ctx = RoutingContext(
            required_capabilities=[AgentCapability.CODE_GENERATION, AgentCapability.CHAT],
        )
        assert len(ctx.required_capabilities) == 2
        assert AgentCapability.CODE_GENERATION in ctx.required_capabilities

    def test_with_preferred_and_excluded(self) -> None:
        """带偏好和排除列表构造。"""
        ctx = RoutingContext(
            preferred_agent="claude",
            excluded_agents=["gpt", "gemini"],
        )
        assert ctx.preferred_agent == "claude"
        assert ctx.excluded_agents == ["gpt", "gemini"]

    def test_metadata_custom(self) -> None:
        """自定义 metadata。"""
        ctx = RoutingContext(metadata={"priority": "high", "tenant": "acme"})
        assert ctx.metadata["priority"] == "high"
        assert ctx.metadata["tenant"] == "acme"

    def test_mutable_defaults_are_independent(self) -> None:
        """可变默认值在不同实例间独立（不共享）。"""
        c1 = RoutingContext()
        c2 = RoutingContext()
        c1.excluded_agents.append("a")
        c1.metadata["k"] = "v"
        assert c2.excluded_agents == []
        assert c2.metadata == {}


# ── 2. TestRoutingResult ─────────────────────────────────────────


class TestRoutingResult:
    """RoutingResult 模型测试。"""

    def test_default_values(self) -> None:
        """默认构造：primary=None，空降级链。"""
        result = RoutingResult()
        assert result.primary is None
        assert result.fallbacks == []
        assert result.reason == ""
        assert result.alternatives == []

    def test_with_primary(self) -> None:
        """带 primary 构造。"""
        agent = _desc("test_agent")
        result = RoutingResult(primary=agent, reason="selected")
        assert result.primary is not None
        assert result.primary.name == "test_agent"
        assert result.reason == "selected"

    def test_with_fallbacks(self) -> None:
        """带降级链构造。"""
        a1 = _desc("a1")
        a2 = _desc("a2")
        result = RoutingResult(primary=a1, fallbacks=[a2])
        assert len(result.fallbacks) == 1
        assert result.fallbacks[0].name == "a2"

    def test_mutable_defaults_are_independent(self) -> None:
        """可变默认值在不同实例间独立。"""
        r1 = RoutingResult()
        r2 = RoutingResult()
        r1.fallbacks.append(_desc("x"))
        assert r2.fallbacks == []


# ── 3. TestAgentRouterFiltering ──────────────────────────────────


class TestAgentRouterFiltering:
    """AgentRouter 筛选方法测试。"""

    def test_filter_by_capabilities_subset(self, router: AgentRouter) -> None:
        """具备所有指定能力的 Agent 通过筛选。"""
        agents = [
            _desc("a", capabilities=[AgentCapability.CODE_GENERATION, AgentCapability.CHAT]),
            _desc("b", capabilities=[AgentCapability.CODE_GENERATION]),
            _desc("c", capabilities=[AgentCapability.CHAT]),
        ]
        result = router._filter_by_capabilities(agents, [AgentCapability.CODE_GENERATION])
        names = [a.name for a in result]
        assert "a" in names
        assert "b" in names
        assert "c" not in names

    def test_filter_by_capabilities_all_required(self, router: AgentRouter) -> None:
        """必须具备全部指定能力。"""
        agents = [
            _desc("a", capabilities=[AgentCapability.CODE_GENERATION, AgentCapability.CHAT]),
            _desc("b", capabilities=[AgentCapability.CODE_GENERATION]),
        ]
        result = router._filter_by_capabilities(
            agents, [AgentCapability.CODE_GENERATION, AgentCapability.CHAT],
        )
        assert [a.name for a in result] == ["a"]

    def test_filter_by_capabilities_empty(self, router: AgentRouter) -> None:
        """空能力列表筛选：全部通过。"""
        agents = [_desc("a"), _desc("b")]
        result = router._filter_by_capabilities(agents, [])
        assert len(result) == 2

    def test_filter_by_health(self, router: AgentRouter) -> None:
        """仅保留健康 Agent。"""
        agents = [
            _desc("healthy", healthy=True),
            _desc("sick", healthy=False),
        ]
        result = router._filter_by_health(agents)
        assert [a.name for a in result] == ["healthy"]

    def test_filter_by_exclusion(self, router: AgentRouter) -> None:
        """排除指定 Agent。"""
        agents = [_desc("a"), _desc("b"), _desc("c")]
        result = router._filter_by_exclusion(agents, ["b"])
        assert [a.name for a in result] == ["a", "c"]

    def test_filter_by_cost_tier_low(self, router: AgentRouter) -> None:
        """low tier 仅保留 FREE 和 SUBSCRIPTION。"""
        agents = [
            _desc("free", billing_model=BillingModel.FREE),
            _desc("sub", billing_model=BillingModel.SUBSCRIPTION),
            _desc("per_call", billing_model=BillingModel.PER_CALL),
            _desc("blackbox", billing_model=BillingModel.BLACKBOX),
        ]
        result = router._filter_by_cost_tier(agents, "low")
        names = [a.name for a in result]
        assert "free" in names
        assert "sub" in names
        assert "per_call" not in names

    def test_filter_by_cost_tier_any(self, router: AgentRouter) -> None:
        """any tier 保留全部。"""
        agents = [
            _desc("free", billing_model=BillingModel.FREE),
            _desc("blackbox", billing_model=BillingModel.BLACKBOX),
        ]
        result = router._filter_by_cost_tier(agents, "any")
        assert len(result) == 2


# ── 4. TestAgentRouterSorting ────────────────────────────────────


class TestAgentRouterSorting:
    """AgentRouter 排序方法测试。"""

    def test_sort_by_cost_order(self, router: AgentRouter) -> None:
        """成本排序：FREE < SUBSCRIPTION < CREDIT < PER_CALL < PER_TOKEN < BLACKBOX。"""
        agents = [
            _desc("blackbox", billing_model=BillingModel.BLACKBOX),
            _desc("per_token", billing_model=BillingModel.PER_TOKEN),
            _desc("free", billing_model=BillingModel.FREE),
            _desc("subscription", billing_model=BillingModel.SUBSCRIPTION),
            _desc("per_call", billing_model=BillingModel.PER_CALL),
            _desc("credit", billing_model=BillingModel.CREDIT),
        ]
        result = router._sort_by_cost(agents)
        names = [a.name for a in result]
        assert names == ["free", "subscription", "credit", "per_call", "per_token", "blackbox"]

    def test_sort_by_cost_stable_same_tier(self, router: AgentRouter) -> None:
        """同成本 tier 内按 name 稳定排序。"""
        agents = [
            _desc("z", billing_model=BillingModel.FREE),
            _desc("a", billing_model=BillingModel.FREE),
        ]
        result = router._sort_by_cost(agents)
        assert [x.name for x in result] == ["a", "z"]

    def test_sort_by_load_least_active_first(self, router: AgentRouter) -> None:
        """负载排序：活跃数少的优先。"""
        agents = [_desc("a"), _desc("b"), _desc("c")]
        # 模拟 a 有 2 个活跃，b 有 0 个，c 有 1 个
        router._active_count["a"] = 2
        router._active_count["c"] = 1
        result = router._sort_by_load(agents)
        names = [a.name for a in result]
        # b(0) < c(1) < a(2)
        assert names == ["b", "c", "a"]

    def test_sort_by_capability_count_desc(self, router: AgentRouter) -> None:
        """能力数量降序排序。"""
        agents = [
            _desc("one", capabilities=[AgentCapability.CHAT]),
            _desc("three", capabilities=[AgentCapability.CHAT, AgentCapability.CODE_GENERATION, AgentCapability.REASONING]),
            _desc("two", capabilities=[AgentCapability.CHAT, AgentCapability.CODE_GENERATION]),
        ]
        result = router._sort_by_capability_count(agents)
        names = [a.name for a in result]
        assert names == ["three", "two", "one"]


# ── 5. TestAgentRouterRoute ──────────────────────────────────────


class TestAgentRouterRoute:
    """AgentRouter.route() 各策略测试。"""

    def test_route_no_enabled_agents(self, router: AgentRouter) -> None:
        """空 catalog 路由返回 primary=None。"""
        ctx = RoutingContext()
        result = router.route(ctx)
        assert result.primary is None
        assert "no enabled" in result.reason

    def test_route_capability_match_prefers_more_capabilities(self, router: AgentRouter) -> None:
        """CAPABILITY_MATCH 策略优先选择能力更多的 Agent。"""
        _register_diverse_agents(router.catalog)
        ctx = RoutingContext(
            required_capabilities=[AgentCapability.CODE_GENERATION],
            max_cost_tier="any",
        )
        result = router.route(ctx, strategy=RoutingStrategy.CAPABILITY_MATCH)
        assert result.primary is not None
        # free_coder 有 2 能力，token_coder 有 2 能力，sub_coder 有 1，unhealthy_coder 有 1
        # free_coder 和 token_coder 都有 2 能力，按 name 排序 free_coder 优先
        assert result.primary.name == "free_coder"

    def test_route_cost_optimized_selects_free(self, router: AgentRouter) -> None:
        """COST_OPTIMIZED 策略选择成本最低的 Agent。"""
        _register_diverse_agents(router.catalog)
        ctx = RoutingContext(
            required_capabilities=[AgentCapability.CODE_GENERATION],
            max_cost_tier="any",
        )
        result = router.route(ctx, strategy=RoutingStrategy.COST_OPTIMIZED)
        assert result.primary is not None
        assert result.primary.billing_model == BillingModel.FREE

    def test_route_load_balanced_selects_least_active(self, router: AgentRouter) -> None:
        """LOAD_BALANCED 策略选择活跃数最少的 Agent。"""
        _register_diverse_agents(router.catalog)
        # 给 free_coder 加负载
        router._active_count["free_coder"] = 5
        ctx = RoutingContext(
            required_capabilities=[AgentCapability.CODE_GENERATION],
            max_cost_tier="any",
        )
        result = router.route(ctx, strategy=RoutingStrategy.LOAD_BALANCED)
        assert result.primary is not None
        # free_coder 有 5 活跃，应避开
        assert result.primary.name != "free_coder"

    def test_route_priority_preserves_registration_order(self, router: AgentRouter) -> None:
        """PRIORITY 策略保持注册顺序（先注册先服务）."""
        # 按特定顺序注册
        router.catalog.register(_desc("first", capabilities=[AgentCapability.CHAT]))
        router.catalog.register(_desc("second", capabilities=[AgentCapability.CHAT]))
        ctx = RoutingContext(max_cost_tier="any")
        result = router.route(ctx, strategy=RoutingStrategy.PRIORITY)
        assert result.primary is not None
        # list_enabled 返回 dict 插入顺序，first 先注册
        assert result.primary.name == "first"

    def test_route_preferred_agent_selected(self, router: AgentRouter) -> None:
        """preferred_agent 在候选中时直接选中。"""
        _register_diverse_agents(router.catalog)
        ctx = RoutingContext(
            required_capabilities=[AgentCapability.CODE_GENERATION],
            max_cost_tier="any",
            preferred_agent="sub_coder",
        )
        result = router.route(ctx)
        assert result.primary is not None
        assert result.primary.name == "sub_coder"

    def test_route_preferred_agent_not_in_candidates(self, router: AgentRouter) -> None:
        """preferred_agent 不在候选中时按策略选择。"""
        _register_diverse_agents(router.catalog)
        ctx = RoutingContext(
            required_capabilities=[AgentCapability.CODE_GENERATION],
            max_cost_tier="any",
            preferred_agent="nonexistent",
        )
        result = router.route(ctx, strategy=RoutingStrategy.COST_OPTIMIZED)
        assert result.primary is not None
        # 回退到成本最优
        assert result.primary.billing_model == BillingModel.FREE

    def test_route_excluded_agents_filtered(self, router: AgentRouter) -> None:
        """excluded_agents 中的 Agent 不参与路由。"""
        _register_diverse_agents(router.catalog)
        ctx = RoutingContext(
            required_capabilities=[AgentCapability.CODE_GENERATION],
            max_cost_tier="any",
            excluded_agents=["free_coder"],
        )
        result = router.route(ctx, strategy=RoutingStrategy.COST_OPTIMIZED)
        assert result.primary is not None
        assert result.primary.name != "free_coder"

    def test_route_require_healthy_filters_unhealthy(self, router: AgentRouter) -> None:
        """require_healthy=True 时过滤不健康 Agent。"""
        _register_diverse_agents(router.catalog)
        # unhealthy_coder 是 PER_CALL，排除 free_coder/sub_coder/token_coder 后
        # 仅剩 unhealthy_coder，但它不健康
        ctx = RoutingContext(
            required_capabilities=[AgentCapability.CODE_GENERATION],
            max_cost_tier="any",
            excluded_agents=["free_coder", "sub_coder", "token_coder"],
            require_healthy=True,
        )
        result = router.route(ctx)
        assert result.primary is None
        assert "no healthy" in result.reason

    def test_route_require_healthy_false_selects_unhealthy(self, router: AgentRouter) -> None:
        """require_healthy=False 时不健康 Agent 也可选。"""
        _register_diverse_agents(router.catalog)
        ctx = RoutingContext(
            required_capabilities=[AgentCapability.CODE_GENERATION],
            max_cost_tier="any",
            excluded_agents=["free_coder", "sub_coder", "token_coder"],
            require_healthy=False,
        )
        result = router.route(ctx, strategy=RoutingStrategy.COST_OPTIMIZED)
        assert result.primary is not None
        assert result.primary.name == "unhealthy_coder"

    def test_route_no_capability_match(self, router: AgentRouter) -> None:
        """无 Agent 具备所需能力时返回 primary=None。"""
        _register_diverse_agents(router.catalog)
        ctx = RoutingContext(
            required_capabilities=[AgentCapability.MULTIMODAL],
            max_cost_tier="any",
        )
        result = router.route(ctx)
        assert result.primary is None

    def test_route_builds_fallbacks(self, router: AgentRouter) -> None:
        """路由结果包含降级链。"""
        router.catalog.register(_desc(
            "primary",
            capabilities=[AgentCapability.CHAT],
            billing_model=BillingModel.FREE,
            fallback_agents=["backup"],
        ))
        router.catalog.register(_desc(
            "backup",
            capabilities=[AgentCapability.CHAT],
            billing_model=BillingModel.SUBSCRIPTION,
        ))
        ctx = RoutingContext(max_cost_tier="any")
        result = router.route(ctx, strategy=RoutingStrategy.PRIORITY)
        assert result.primary is not None
        assert result.primary.name == "primary"
        assert len(result.fallbacks) >= 1
        assert any(f.name == "backup" for f in result.fallbacks)

    def test_route_fallbacks_exclude_primary(self, router: AgentRouter) -> None:
        """降级链不包含 primary 自身。"""
        router.catalog.register(_desc(
            "main",
            capabilities=[AgentCapability.CHAT],
            fallback_agents=["secondary"],
        ))
        router.catalog.register(_desc("secondary", capabilities=[AgentCapability.CHAT]))
        ctx = RoutingContext(max_cost_tier="any")
        result = router.route(ctx, strategy=RoutingStrategy.PRIORITY)
        assert result.primary is not None
        assert all(f.name != result.primary.name for f in result.fallbacks)

    def test_route_alternatives_populated(self, router: AgentRouter) -> None:
        """alternatives 包含除 primary 和 fallbacks 外的候选。"""
        _register_diverse_agents(router.catalog)
        ctx = RoutingContext(
            required_capabilities=[AgentCapability.CODE_GENERATION],
            max_cost_tier="any",
        )
        result = router.route(ctx, strategy=RoutingStrategy.COST_OPTIMIZED)
        assert result.primary is not None
        # alternatives 不含 primary
        assert all(a.name != result.primary.name for a in result.alternatives)

    def test_route_cost_tier_low_excludes_expensive(self, router: AgentRouter) -> None:
        """max_cost_tier=low 排除 PER_TOKEN/PER_CALL/BLACKBOX。"""
        _register_diverse_agents(router.catalog)
        ctx = RoutingContext(
            required_capabilities=[AgentCapability.CODE_GENERATION],
            max_cost_tier="low",
        )
        result = router.route(ctx, strategy=RoutingStrategy.COST_OPTIMIZED)
        assert result.primary is not None
        assert result.primary.billing_model in (BillingModel.FREE, BillingModel.SUBSCRIPTION)


# ── 6. TestAgentRouterConcurrency ────────────────────────────────


class TestAgentRouterConcurrency:
    """AgentRouter 并发槽位管理测试。"""

    def test_acquire_success(self, router: AgentRouter) -> None:
        """acquire 成功占用槽位。"""
        router.catalog.register(_desc("agent", max_concurrent=2))
        assert router.acquire("agent") is True
        assert router.get_active_count("agent") == 1

    def test_acquire_until_max(self, router: AgentRouter) -> None:
        """达到 max_concurrent 后 acquire 返回 False。"""
        router.catalog.register(_desc("agent", max_concurrent=2))
        assert router.acquire("agent") is True
        assert router.acquire("agent") is True
        assert router.acquire("agent") is False
        assert router.get_active_count("agent") == 2

    def test_release_decrements_count(self, router: AgentRouter) -> None:
        """release 递减活跃数。"""
        router.catalog.register(_desc("agent", max_concurrent=2))
        router.acquire("agent")
        router.acquire("agent")
        assert router.get_active_count("agent") == 2
        router.release("agent")
        assert router.get_active_count("agent") == 1

    def test_release_at_zero_noop(self, router: AgentRouter) -> None:
        """活跃数为 0 时 release 静默忽略。"""
        router.catalog.register(_desc("agent"))
        router.release("agent")
        assert router.get_active_count("agent") == 0

    def test_acquire_nonexistent_agent(self, router: AgentRouter) -> None:
        """acquire 不存在的 Agent 返回 False。"""
        assert router.acquire("ghost") is False

    def test_acquire_release_cycle(self, router: AgentRouter) -> None:
        """acquire/release 循环后可重新 acquire。"""
        router.catalog.register(_desc("agent", max_concurrent=1))
        assert router.acquire("agent") is True
        assert router.acquire("agent") is False  # 已满
        router.release("agent")
        assert router.acquire("agent") is True  # 释放后可再次 acquire

    def test_get_active_count_default_zero(self, router: AgentRouter) -> None:
        """未 acquire 的 Agent 活跃数为 0。"""
        assert router.get_active_count("any") == 0

    def test_concurrent_agents_independent(self, router: AgentRouter) -> None:
        """不同 Agent 的并发槽位独立。"""
        router.catalog.register(_desc("a", max_concurrent=1))
        router.catalog.register(_desc("b", max_concurrent=1))
        assert router.acquire("a") is True
        assert router.acquire("b") is True
        assert router.acquire("a") is False
        assert router.acquire("b") is False
        assert router.get_active_count("a") == 1
        assert router.get_active_count("b") == 1


# ── 7. TestAgentRouterRoundRobin ─────────────────────────────────


class TestAgentRouterRoundRobin:
    """AgentRouter 轮询策略测试。"""

    def test_round_robin_select_returns_none_for_empty(self, router: AgentRouter) -> None:
        """空候选轮询返回 None。"""
        assert router._round_robin_select([], "key") is None

    def test_round_robin_select_single_candidate(self, router: AgentRouter) -> None:
        """单候选轮询直接返回该候选。"""
        agent = _desc("only")
        result = router._round_robin_select([agent], "key")
        assert result is not None
        assert result.name == "only"

    def test_round_robin_fairness(self, router: AgentRouter) -> None:
        """轮询公平性：连续调用遍历所有候选。"""
        agents = [_desc("a"), _desc("b"), _desc("c")]
        selections: list[str] = []
        for _ in range(6):
            selected = router._round_robin_select(agents, "test_key")
            assert selected is not None
            selections.append(selected.name)
        # 6 次调用应遍历 a,b,c 各 2 次
        assert selections.count("a") == 2
        assert selections.count("b") == 2
        assert selections.count("c") == 2
        # 前三次应各不相同
        assert len(set(selections[:3])) == 3

    def test_round_robin_independent_keys(self, router: AgentRouter) -> None:
        """不同 key 的轮询索引独立。"""
        agents = [_desc("a"), _desc("b")]
        r1 = router._round_robin_select(agents, "key1")
        r2 = router._round_robin_select(agents, "key2")
        # 不同 key 都从索引 0 开始
        assert r1 is not None
        assert r2 is not None
        assert r1.name == "a"
        assert r2.name == "a"

    def test_round_robin_strategy_route(self, router: AgentRouter) -> None:
        """ROUND_ROBIN 策略路由：连续路由遍历候选。"""
        router.catalog.register(_desc("a", capabilities=[AgentCapability.CHAT], billing_model=BillingModel.FREE))
        router.catalog.register(_desc("b", capabilities=[AgentCapability.CHAT], billing_model=BillingModel.FREE))
        ctx = RoutingContext(
            required_capabilities=[AgentCapability.CHAT],
            max_cost_tier="low",
        )
        r1 = router.route(ctx, strategy=RoutingStrategy.ROUND_ROBIN)
        r2 = router.route(ctx, strategy=RoutingStrategy.ROUND_ROBIN)
        assert r1.primary is not None
        assert r2.primary is not None
        assert r1.primary.name != r2.primary.name

    def test_round_robin_session_stickiness(self, router: AgentRouter) -> None:
        """相同 session_id 的轮询 key 一致（粘性）."""
        ctx1 = RoutingContext(session_id="sess1")
        ctx2 = RoutingContext(session_id="sess1")
        ctx3 = RoutingContext(session_id="sess2")
        # 相同 session 应产生相同 key
        assert router._rr_key(ctx1) == router._rr_key(ctx2)
        # 不同 session 产生不同 key
        assert router._rr_key(ctx1) != router._rr_key(ctx3)


# ── 8. TestRoutingStrategy ───────────────────────────────────────


class TestRoutingStrategy:
    """RoutingStrategy 枚举测试。"""

    def test_strategy_values(self) -> None:
        """策略枚举值正确。"""
        assert RoutingStrategy.CAPABILITY_MATCH.value == "capability_match"
        assert RoutingStrategy.COST_OPTIMIZED.value == "cost_optimized"
        assert RoutingStrategy.LOAD_BALANCED.value == "load_balanced"
        assert RoutingStrategy.PRIORITY.value == "priority"
        assert RoutingStrategy.ROUND_ROBIN.value == "round_robin"

    def test_strategy_is_str_enum(self) -> None:
        """策略是 str Enum（可比较字符串）."""
        assert RoutingStrategy.COST_OPTIMIZED == "cost_optimized"
        assert isinstance(RoutingStrategy.PRIORITY, str)