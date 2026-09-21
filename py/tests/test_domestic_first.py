"""国产优先路由策略 (DOMESTIC_FIRST) 测试.

覆盖：
    - region 字段默认值与显式赋值
    - region 字段 SQLite 持久化
    - DOMESTIC_FIRST 策略优先选国产
    - 国产不可用时回退到国际
    - 结合能力过滤/健康过滤/排除/禁用
    - 预置 Agent 的 region 正确性

每个测试使用 tmp_path 隔离 DB，不污染主库。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.agent.registry.agent_catalog import (
    AgentCapability,
    AgentCatalog,
    AgentDescriptor,
    AuthMethod,  # noqa: F401
    BillingModel,  # noqa: F401
)
from maop.core.agent.registry.preset_agents import get_preset_agents
from maop.core.agent.router.agent_router import (
    AgentRouter,
    RoutingContext,
    RoutingStrategy,
)

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def catalog(tmp_path: Path) -> AgentCatalog:
    """使用隔离 tmp_path DB 的全新 AgentCatalog。"""
    return AgentCatalog(db_path=tmp_path / "domestic_first.db")


@pytest.fixture
def router(catalog: AgentCatalog) -> AgentRouter:
    """基于隔离 catalog 的全新 AgentRouter。"""
    return AgentRouter(catalog=catalog)


def _desc(name: str, **kwargs: object) -> AgentDescriptor:
    """构造 AgentDescriptor，name 必填，其余可选覆盖。"""
    return AgentDescriptor(name=name, **kwargs)  # type: ignore[arg-type]


def _ctx(**kwargs: object) -> RoutingContext:
    """构造 RoutingContext，默认 max_cost_tier='any' 避免计费过滤干扰。"""
    defaults: dict[str, object] = {"max_cost_tier": "any"}
    defaults.update(kwargs)
    return RoutingContext(**defaults)  # type: ignore[arg-type]


# 预期国产工具名称（10个国产AI编程工具 + codearts 华为云）
DOMESTIC_PRESET_NAMES = {
    "trae", "qoder", "zcode", "catpaw", "codebuddy",
    "marscode", "tongyi-lingma", "codegeex", "baidu-comate",
    "deepseek-harness", "codearts",
}


# ── 1. region 字段基础 ────────────────────────────────────────────


def test_domestic_agent_has_domestic_region() -> None:
    """显式设置 region='domestic' 的 Agent，其 region 字段为 'domestic'。"""
    agent = _desc("trae", region="domestic")
    assert agent.region == "domestic"


def test_international_agent_has_international_region() -> None:
    """显式设置 region='international' 的 Agent，其 region 字段为 'international'。"""
    agent = _desc("cursor", region="international")
    assert agent.region == "international"


def test_region_field_default() -> None:
    """未指定 region 时，默认值为 'international'（向后兼容）。"""
    agent = _desc("some-agent")
    assert agent.region == "international"


# ── 2. region 持久化 ──────────────────────────────────────────────


def test_region_field_persistence(tmp_path: Path) -> None:
    """region 字段正确持久化到 SQLite 并能重新加载。"""
    db_path = tmp_path / "persistence.db"
    # 第一次实例：注册带 region 的 Agent
    cat1 = AgentCatalog(db_path=db_path)
    cat1.register(_desc("domestic_agent", region="domestic"))
    cat1.register(_desc("intl_agent", region="international"))
    # 第二次实例：从同一 DB 加载，验证 region 持久化
    cat2 = AgentCatalog(db_path=db_path)
    d = cat2.get("domestic_agent")
    i = cat2.get("intl_agent")
    assert d is not None
    assert d.region == "domestic"
    assert i is not None
    assert i.region == "international"


# ── 3. DOMESTIC_FIRST 策略 ────────────────────────────────────────


def test_domestic_first_strategy_prefers_domestic(
    catalog: AgentCatalog, router: AgentRouter,
) -> None:
    """DOMESTIC_FIRST 策略优先选择国产 Agent。"""
    catalog.register(_desc("intl_agent", region="international"))
    catalog.register(_desc("domestic_agent", region="domestic"))
    result = router.route(_ctx(), strategy=RoutingStrategy.DOMESTIC_FIRST)
    assert result.primary is not None
    assert result.primary.name == "domestic_agent"
    assert result.primary.region == "domestic"


def test_domestic_first_fallback_to_international(
    catalog: AgentCatalog, router: AgentRouter,
) -> None:
    """国产 Agent 都不可用时，回退到国际 Agent。"""
    # 国产 Agent 不健康，国际 Agent 健康
    catalog.register(_desc("domestic_agent", region="domestic", healthy=False))
    catalog.register(_desc("intl_agent", region="international", healthy=True))
    result = router.route(
        _ctx(require_healthy=True), strategy=RoutingStrategy.DOMESTIC_FIRST,
    )
    assert result.primary is not None
    assert result.primary.name == "intl_agent"
    assert result.primary.region == "international"


def test_domestic_first_with_capability_filter(
    catalog: AgentCatalog, router: AgentRouter,
) -> None:
    """DOMESTIC_FIRST 结合能力过滤：先按能力筛选，再按国产优先排序。"""
    # 国产 Agent 不具备所需能力，国际 Agent 具备
    catalog.register(_desc(
        "domestic_no_cap", region="domestic",
        capabilities=[AgentCapability.CHAT],
    ))
    catalog.register(_desc(
        "intl_with_cap", region="international",
        capabilities=[AgentCapability.CODE_GENERATION],
    ))
    result = router.route(
        _ctx(required_capabilities=[AgentCapability.CODE_GENERATION]),
        strategy=RoutingStrategy.DOMESTIC_FIRST,
    )
    assert result.primary is not None
    assert result.primary.name == "intl_with_cap"


def test_domestic_first_with_unhealthy_domestic(
    catalog: AgentCatalog, router: AgentRouter,
) -> None:
    """国产 Agent 不健康时，选择健康的国际 Agent。"""
    catalog.register(_desc(
        "domestic_sick", region="domestic", healthy=False,
        capabilities=[AgentCapability.CODE_GENERATION],
    ))
    catalog.register(_desc(
        "intl_healthy", region="international", healthy=True,
        capabilities=[AgentCapability.CODE_GENERATION],
    ))
    result = router.route(
        _ctx(
            required_capabilities=[AgentCapability.CODE_GENERATION],
            require_healthy=True,
        ),
        strategy=RoutingStrategy.DOMESTIC_FIRST,
    )
    assert result.primary is not None
    assert result.primary.name == "intl_healthy"


def test_domestic_first_all_domestic_disabled(
    catalog: AgentCatalog, router: AgentRouter,
) -> None:
    """国产 Agent 全部禁用时，选择启用的国际 Agent。"""
    catalog.register(_desc("domestic_off", region="domestic", enabled=False))
    catalog.register(_desc("intl_on", region="international", enabled=True))
    result = router.route(_ctx(), strategy=RoutingStrategy.DOMESTIC_FIRST)
    assert result.primary is not None
    assert result.primary.name == "intl_on"


def test_domestic_first_strategy_with_excluded(
    catalog: AgentCatalog, router: AgentRouter,
) -> None:
    """排除国产 Agent 后，仍优先选择剩余的国产 Agent。"""
    catalog.register(_desc("domestic_a", region="domestic"))
    catalog.register(_desc("domestic_b", region="domestic"))
    catalog.register(_desc("intl_c", region="international"))
    result = router.route(
        _ctx(excluded_agents=["domestic_a"]),
        strategy=RoutingStrategy.DOMESTIC_FIRST,
    )
    assert result.primary is not None
    assert result.primary.name == "domestic_b"
    assert result.primary.region == "domestic"


def test_domestic_first_mixed_capabilities(
    catalog: AgentCatalog, router: AgentRouter,
) -> None:
    """混合能力场景：国产和国际都具备所需能力时，优先国产（不按能力数量排序）。"""
    # 国产 Agent 能力少但 region=domestic
    catalog.register(_desc(
        "domestic_basic", region="domestic",
        capabilities=[AgentCapability.CODE_GENERATION],
    ))
    # 国际 Agent 能力多但 region=international
    catalog.register(_desc(
        "intl_full", region="international",
        capabilities=[
            AgentCapability.CODE_GENERATION, AgentCapability.REASONING,
            AgentCapability.LONG_CONTEXT,
        ],
    ))
    result = router.route(
        _ctx(required_capabilities=[AgentCapability.CODE_GENERATION]),
        strategy=RoutingStrategy.DOMESTIC_FIRST,
    )
    assert result.primary is not None
    # DOMESTIC_FIRST 不按能力数量排序，只按 region 优先
    assert result.primary.name == "domestic_basic"
    assert result.primary.region == "domestic"


# ── 4. 预置 Agent region 正确性 ───────────────────────────────────


def test_preset_agents_region_correct() -> None:
    """预置 Agent 的 region 字段正确：国产工具为 domestic，其余为 international。"""
    presets = get_preset_agents()
    for desc in presets:
        if desc.name in DOMESTIC_PRESET_NAMES:
            assert desc.region == "domestic", (
                f"{desc.name} 应为国产（region='domestic'），实际为 {desc.region!r}"
            )
        else:
            assert desc.region == "international", (
                f"{desc.name} 应为国际（region='international'），实际为 {desc.region!r}"
            )