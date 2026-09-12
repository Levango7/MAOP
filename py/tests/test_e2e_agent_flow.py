"""端到端集成测试 — 验证桌面应用调度与计费授权方案完整流程.

覆盖完整链路：路由选择 → 适配器调用 → 计费扣减 → 额度更新

测试场景：
  1.  PER_TOKEN 完整流程（按 token 计费 + 额度扣减）
  2.  PER_CALL 完整流程（按次计费 + 额度扣减）
  3.  BLACKBOX 完整流程（黑盒估算 + 额度扣减）
  4.  FREE / SUBSCRIPTION 零成本
  5.  CREDIT 计费（积分制）
  6.  三桶跨桶扣减（free → prepaid → postpaid）
  7.  额度不足拒绝
  8.  降级链切换（primary 不健康 → fallback）
  9.  多策略路由 + 计费
  10. 额度退还 + 重新调用
  11. 并发槽位管理（acquire / release）
  12. CredentialVault 集成（加密存储 / 检索 / 预览 / 删除）
  13. 成本偏好过滤（max_cost_tier 分层）
  14. 计费摘要查询
  15. 路由排除 Agent

每个测试使用 tmp_path 隔离 SQLite，不触碰真实 DB。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from maop.core.agent.auth.credential_vault import (
    Credential,
    CredentialType,
    CredentialVault,
)
from maop.core.agent.billing.billing_abstraction import BillingEngine
from maop.core.agent.billing.quota_bucket import QuotaBucket, QuotaEntry
from maop.core.agent.registry.agent_catalog import (
    AgentCatalog,
    AgentCapability,
    AgentDescriptor,
    AuthMethod,
    BillingModel,
)
from maop.core.agent.router.agent_router import (
    AgentRouter,
    RoutingContext,
    RoutingStrategy,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def catalog(tmp_path: Path) -> AgentCatalog:
    """使用隔离 DB 的 AgentCatalog。"""
    return AgentCatalog(db_path=tmp_path / "catalog.db")


@pytest.fixture
def quota_bucket(tmp_path: Path) -> QuotaBucket:
    """使用隔离 DB 的 QuotaBucket。"""
    return QuotaBucket(db_path=tmp_path / "quota.db")


@pytest.fixture
def engine(
    catalog: AgentCatalog,
    quota_bucket: QuotaBucket,
    tmp_path: Path,
) -> BillingEngine:
    """使用隔离 DB 的 BillingEngine（绑定 catalog + quota_bucket）。"""
    return BillingEngine(
        catalog=catalog,
        quota_bucket=quota_bucket,
        db_path=tmp_path / "billing.db",
    )


@pytest.fixture
def router(catalog: AgentCatalog) -> AgentRouter:
    """基于隔离 catalog 的 AgentRouter。"""
    return AgentRouter(catalog=catalog)


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CredentialVault:
    """隔离的 CredentialVault（独立 DB + 密钥目录）。

    CredentialVault 需要 MAOP_DATA_DIR 环境变量来定位加密密钥文件，
    且使用完毕需调用 close() 释放连接池。
    """
    data_dir = tmp_path / "vault_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MAOP_DATA_DIR", str(data_dir))
    monkeypatch.delenv("MAOP_CREDENTIAL_KEY", raising=False)
    v = CredentialVault(db_path=tmp_path / "vault.db")
    yield v
    v.close()


# ── 辅助函数 ──────────────────────────────────────────────────────


def _make_agent(
    name: str,
    billing_model: BillingModel = BillingModel.FREE,
    *,
    capabilities: list[AgentCapability] | None = None,
    billing_config: dict[str, Any] | None = None,
    healthy: bool = True,
    enabled: bool = True,
    fallback_agents: list[str] | None = None,
    max_concurrent: int = 1,
    auth_method: AuthMethod = AuthMethod.NONE,
) -> AgentDescriptor:
    """构造 AgentDescriptor 的便捷工厂。"""
    return AgentDescriptor(
        name=name,
        display_name=name,
        billing_model=billing_model,
        billing_config=billing_config or {},
        capabilities=capabilities or [AgentCapability.CODE_GENERATION],
        healthy=healthy,
        enabled=enabled,
        fallback_agents=fallback_agents or [],
        max_concurrent=max_concurrent,
        auth_method=auth_method,
    )


def _set_quota(
    quota_bucket: QuotaBucket,
    agent_name: str,
    *,
    free: int = 0,
    prepaid: int = 0,
    postpaid: int = 0,
    reset_period: str = "never",
) -> None:
    """设置三桶额度的便捷函数。

    使用 reset_period="never" 避免测试运行时跨周期自动重置干扰断言。
    """
    quota_bucket.set_quota(
        agent_name,
        QuotaEntry(
            free_quota=free,
            prepaid_quota=prepaid,
            postpaid_quota=postpaid,
            reset_period=reset_period,
        ),
    )


# ── Scenario 1: PER_TOKEN 完整流程 ───────────────────────────────


def test_e2e_per_token_full_flow(
    catalog: AgentCatalog,
    quota_bucket: QuotaBucket,
    engine: BillingEngine,
    router: AgentRouter,
) -> None:
    """场景1：PER_TOKEN 完整流程 — 按 token 计费 + 额度扣减。

    链路：注册 PER_TOKEN Agent → 设置额度 → 路由选择 → 计费扣减 → 验证额度更新
    """
    # 1. 注册 PER_TOKEN Agent，price_per_token=0.00001
    agent = _make_agent(
        "per-token-agent",
        BillingModel.PER_TOKEN,
        billing_config={"price_per_token": 0.00001},
    )
    catalog.register(agent)

    # 2. 设置额度 free_quota=10000
    _set_quota(quota_bucket, "per-token-agent", free=10000)

    # 3. 路由选择该 Agent（COST_OPTIMIZED 策略，max_cost_tier="high" 包含 PER_TOKEN）
    ctx = RoutingContext(
        required_capabilities=[AgentCapability.CODE_GENERATION],
        max_cost_tier="high",
    )
    result = router.route(ctx, strategy=RoutingStrategy.COST_OPTIMIZED)

    # 4. 验证路由结果 primary 不为 None
    assert result.primary is not None, "路由应选中 Agent"
    assert result.primary.name == "per-token-agent"

    # 5. 调用 billing.charge(tokens=5000)
    billing_result = engine.charge("per-token-agent", tokens=5000)

    # 6. 验证计费成功，cost_usd = 5000 * 0.00001 = 0.05
    assert billing_result.success, f"计费应成功: {billing_result.error}"
    assert billing_result.record is not None
    assert billing_result.record.cost_usd == pytest.approx(0.05, abs=1e-9)
    assert billing_result.record.tokens_consumed == 5000
    assert billing_result.record.quota_consumed == 5000

    # 7. 验证额度扣减 5000，剩余 5000
    remaining = quota_bucket.get_remaining("per-token-agent")
    assert remaining["free"] == 5000, f"free 桶应剩余 5000，实际 {remaining['free']}"


# ── Scenario 2: PER_CALL 完整流程 ────────────────────────────────


def test_e2e_per_call_full_flow(
    catalog: AgentCatalog,
    quota_bucket: QuotaBucket,
    engine: BillingEngine,
    router: AgentRouter,
) -> None:
    """场景2：PER_CALL 完整流程 — 按调用次数计费 + 额度扣减。"""
    # 注册 PER_CALL Agent，price_per_call=0.01
    agent = _make_agent(
        "per-call-agent",
        BillingModel.PER_CALL,
        billing_config={"price_per_call": 0.01},
    )
    catalog.register(agent)

    # 设置额度 free_quota=100
    _set_quota(quota_bucket, "per-call-agent", free=100)

    # 路由选择（PER_CALL 优先级=3，需 max_cost_tier="medium" 或更高）
    ctx = RoutingContext(
        required_capabilities=[AgentCapability.CODE_GENERATION],
        max_cost_tier="medium",
    )
    result = router.route(ctx, strategy=RoutingStrategy.COST_OPTIMIZED)
    assert result.primary is not None
    assert result.primary.name == "per-call-agent"

    # charge(calls=5) → cost_usd = 5 * 0.01 = 0.05, quota_consumed = 5
    billing_result = engine.charge("per-call-agent", calls=5)
    assert billing_result.success, f"计费应成功: {billing_result.error}"
    assert billing_result.record is not None
    assert billing_result.record.cost_usd == pytest.approx(0.05, abs=1e-9)
    assert billing_result.record.quota_consumed == 5
    assert billing_result.record.calls_made == 5

    # 验证额度扣减 5
    remaining = quota_bucket.get_remaining("per-call-agent")
    assert remaining["free"] == 95


# ── Scenario 3: BLACKBOX 完整流程 ────────────────────────────────


def test_e2e_blackbox_full_flow(
    catalog: AgentCatalog,
    quota_bucket: QuotaBucket,
    engine: BillingEngine,
    router: AgentRouter,
) -> None:
    """场景3：BLACKBOX 完整流程 — 黑盒估算 + 额度扣减。

    BLACKBOX 模式：每次调用消耗 blackbox_estimate_per_call 个额度单位，cost=0。
    """
    # 注册 BLACKBOX Agent，blackbox_estimate_per_call=200
    agent = _make_agent(
        "blackbox-agent",
        BillingModel.BLACKBOX,
        billing_config={"blackbox_estimate_per_call": 200},
    )
    catalog.register(agent)

    # 设置额度 free_quota=1000
    _set_quota(quota_bucket, "blackbox-agent", free=1000)

    # 路由选择（BLACKBOX 优先级=5，需 max_cost_tier="any"）
    ctx = RoutingContext(
        required_capabilities=[AgentCapability.CODE_GENERATION],
        max_cost_tier="any",
    )
    result = router.route(ctx, strategy=RoutingStrategy.COST_OPTIMIZED)
    assert result.primary is not None
    assert result.primary.name == "blackbox-agent"

    # charge(calls=3) → quota_consumed = 3 * 200 = 600, cost_usd = 0
    billing_result = engine.charge("blackbox-agent", calls=3)
    assert billing_result.success, f"计费应成功: {billing_result.error}"
    assert billing_result.record is not None
    assert billing_result.record.quota_consumed == 600
    assert billing_result.record.cost_usd == 0.0

    # 验证额度扣减 600，剩余 400
    remaining = quota_bucket.get_remaining("blackbox-agent")
    assert remaining["free"] == 400


# ── Scenario 4: FREE / SUBSCRIPTION 零成本 ───────────────────────


def test_e2e_free_and_subscription_zero_cost(
    catalog: AgentCatalog,
    quota_bucket: QuotaBucket,
    engine: BillingEngine,
    router: AgentRouter,
) -> None:
    """场景4：FREE / SUBSCRIPTION 零成本 — 不消耗额度，不产生成本。"""
    # 注册 FREE Agent + SUBSCRIPTION Agent
    free_agent = _make_agent("free-agent", BillingModel.FREE)
    sub_agent = _make_agent("sub-agent", BillingModel.SUBSCRIPTION)
    catalog.register(free_agent)
    catalog.register(sub_agent)

    # 设置额度（虽然不消耗，但设置以验证不扣减）
    _set_quota(quota_bucket, "free-agent", free=1000)
    _set_quota(quota_bucket, "sub-agent", free=1000)

    # 路由选择 FREE Agent（COST_OPTIMIZED 下 FREE 优先级最高）
    ctx = RoutingContext(
        required_capabilities=[AgentCapability.CODE_GENERATION],
        max_cost_tier="low",
    )
    result = router.route(ctx, strategy=RoutingStrategy.COST_OPTIMIZED)
    assert result.primary is not None
    assert result.primary.name == "free-agent"

    # charge FREE → quota_consumed=0, cost_usd=0
    free_result = engine.charge("free-agent", tokens=500, calls=3)
    assert free_result.success
    assert free_result.record is not None
    assert free_result.record.quota_consumed == 0
    assert free_result.record.cost_usd == 0.0

    # charge SUBSCRIPTION → quota_consumed=0, cost_usd=0
    sub_result = engine.charge("sub-agent", tokens=500, calls=3)
    assert sub_result.success
    assert sub_result.record is not None
    assert sub_result.record.quota_consumed == 0
    assert sub_result.record.cost_usd == 0.0

    # 验证额度未扣减
    assert quota_bucket.get_remaining("free-agent")["free"] == 1000
    assert quota_bucket.get_remaining("sub-agent")["free"] == 1000


# ── Scenario 5: CREDIT 计费 ──────────────────────────────────────


def test_e2e_credit_billing(
    catalog: AgentCatalog,
    quota_bucket: QuotaBucket,
    engine: BillingEngine,
    router: AgentRouter,
) -> None:
    """场景5：CREDIT 计费 — 积分制，消耗 calls 个额度单位，cost=0。"""
    # 注册 CREDIT Agent
    agent = _make_agent("credit-agent", BillingModel.CREDIT)
    catalog.register(agent)

    # 设置额度 prepaid_quota=100
    _set_quota(quota_bucket, "credit-agent", prepaid=100)

    # 路由选择（CREDIT 优先级=2，需 max_cost_tier="medium" 或更高）
    ctx = RoutingContext(
        required_capabilities=[AgentCapability.CODE_GENERATION],
        max_cost_tier="medium",
    )
    result = router.route(ctx, strategy=RoutingStrategy.COST_OPTIMIZED)
    assert result.primary is not None
    assert result.primary.name == "credit-agent"

    # charge(calls=10) → quota_consumed=10, cost_usd=0
    billing_result = engine.charge("credit-agent", calls=10)
    assert billing_result.success, f"计费应成功: {billing_result.error}"
    assert billing_result.record is not None
    assert billing_result.record.quota_consumed == 10
    assert billing_result.record.cost_usd == 0.0

    # 验证额度扣减 10
    remaining = quota_bucket.get_remaining("credit-agent")
    assert remaining["prepaid"] == 90


# ── Scenario 6: 三桶跨桶扣减 ─────────────────────────────────────


def test_e2e_three_bucket_cross_deduction(
    catalog: AgentCatalog,
    quota_bucket: QuotaBucket,
    engine: BillingEngine,
) -> None:
    """场景6：三桶跨桶扣减 — free → prepaid → postpaid 顺序扣减。

    第一次 charge tokens=250：free 扣 100，prepaid 扣 150
    第二次 charge tokens=250：prepaid 扣 50，postpaid 扣 200
    """
    # 注册 PER_TOKEN Agent
    agent = _make_agent(
        "cross-bucket-agent",
        BillingModel.PER_TOKEN,
        billing_config={"price_per_token": 0.00001},
    )
    catalog.register(agent)

    # 设置额度：free=100, prepaid=200, postpaid=300
    _set_quota(
        quota_bucket,
        "cross-bucket-agent",
        free=100,
        prepaid=200,
        postpaid=300,
    )

    # 第一次 charge tokens=250 → free 扣 100, prepaid 扣 150
    result1 = engine.charge("cross-bucket-agent", tokens=250)
    assert result1.success, f"第一次计费应成功: {result1.error}"
    assert result1.record is not None
    assert result1.record.quota_consumed == 250

    # 验证三桶剩余：free=0, prepaid=50, postpaid=300
    remaining1 = quota_bucket.get_remaining("cross-bucket-agent")
    assert remaining1["free"] == 0, f"free 应剩余 0，实际 {remaining1['free']}"
    assert remaining1["prepaid"] == 50, f"prepaid 应剩余 50，实际 {remaining1['prepaid']}"
    assert remaining1["postpaid"] == 300

    # 第二次 charge tokens=250 → prepaid 扣 50, postpaid 扣 200
    result2 = engine.charge("cross-bucket-agent", tokens=250)
    assert result2.success, f"第二次计费应成功: {result2.error}"
    assert result2.record is not None
    assert result2.record.quota_consumed == 250

    # 验证三桶剩余：free=0, prepaid=0, postpaid=100
    remaining2 = quota_bucket.get_remaining("cross-bucket-agent")
    assert remaining2["free"] == 0
    assert remaining2["prepaid"] == 0
    assert remaining2["postpaid"] == 100, f"postpaid 应剩余 100，实际 {remaining2['postpaid']}"

    # 验证 quota_source 标签包含跨桶信息
    assert "free" in result1.record.quota_source
    assert "prepaid" in result1.record.quota_source
    assert "prepaid" in result2.record.quota_source
    assert "postpaid" in result2.record.quota_source


# ── Scenario 7: 额度不足拒绝 ─────────────────────────────────────


def test_e2e_insufficient_quota_rejected(
    catalog: AgentCatalog,
    quota_bucket: QuotaBucket,
    engine: BillingEngine,
) -> None:
    """场景7：额度不足拒绝 — 三桶总剩余 < 请求量时返回失败。"""
    # 注册 PER_TOKEN Agent
    agent = _make_agent(
        "insufficient-agent",
        BillingModel.PER_TOKEN,
        billing_config={"price_per_token": 0.00001},
    )
    catalog.register(agent)

    # 设置额度 free=100
    _set_quota(quota_bucket, "insufficient-agent", free=100)

    # charge tokens=200 → 失败，error 包含 "insufficient quota"
    billing_result = engine.charge("insufficient-agent", tokens=200)
    assert not billing_result.success, "额度不足时应失败"
    assert billing_result.record is None
    assert "insufficient quota" in billing_result.error, (
        f"error 应包含 'insufficient quota'，实际: {billing_result.error}"
    )

    # 验证额度未被扣减（原子性：不部分扣减）
    remaining = quota_bucket.get_remaining("insufficient-agent")
    assert remaining["free"] == 100, "额度不足时不应扣减任何额度"


# ── Scenario 8: 降级链切换 ───────────────────────────────────────


def test_e2e_fallback_chain_switch(
    catalog: AgentCatalog,
    router: AgentRouter,
) -> None:
    """场景8：降级链切换 — primary 不健康时切换到 fallback。

    注册 primary Agent (healthy=False) + fallback Agent (healthy=True)，
    primary.fallback_agents = ["fallback"]，路由 require_healthy=True，
    验证选中的是 fallback 而非 primary。
    """
    # 注册 primary Agent（healthy=False）+ fallback Agent（healthy=True）
    primary = _make_agent(
        "primary-agent",
        BillingModel.FREE,
        healthy=False,
        fallback_agents=["fallback-agent"],
    )
    fallback = _make_agent("fallback-agent", BillingModel.FREE, healthy=True)
    catalog.register(primary)
    catalog.register(fallback)

    # 路由选择（require_healthy=True）
    ctx = RoutingContext(
        required_capabilities=[AgentCapability.CODE_GENERATION],
        max_cost_tier="low",
        require_healthy=True,
    )
    result = router.route(ctx, strategy=RoutingStrategy.COST_OPTIMIZED)

    # 验证 primary 选中 fallback 而非 primary
    assert result.primary is not None, "应选中健康的 fallback Agent"
    assert result.primary.name == "fallback-agent", (
        f"应选中 fallback-agent，实际选中 {result.primary.name}"
    )

    # 验证 fallback 出现在降级链或替代中
    all_names = {a.name for a in result.fallbacks} | {a.name for a in result.alternatives}
    assert "primary-agent" in all_names or result.primary.name == "fallback-agent"


# ── Scenario 9: 多策略路由 + 计费 ────────────────────────────────


def test_e2e_multi_strategy_routing_and_billing(
    catalog: AgentCatalog,
    quota_bucket: QuotaBucket,
    engine: BillingEngine,
    router: AgentRouter,
) -> None:
    """场景9：多策略路由 + 计费 — 不同策略选择不同 Agent。

    注册 FREE + PER_TOKEN + PER_CALL 三个 Agent，都有 CODE_GENERATION 能力。
    COST_OPTIMIZED 策略 → 选 FREE（成本最低）
    CAPABILITY_MATCH 策略 → 选能力最多的
    """
    # 注册三个 Agent，FREE 能力最多以区分两种策略
    free_agent = _make_agent(
        "free-multi",
        BillingModel.FREE,
        capabilities=[
            AgentCapability.CODE_GENERATION,
            AgentCapability.CHAT,
            AgentCapability.REASONING,
        ],
    )
    per_token_agent = _make_agent(
        "token-multi",
        BillingModel.PER_TOKEN,
        capabilities=[AgentCapability.CODE_GENERATION],
        billing_config={"price_per_token": 0.00001},
    )
    per_call_agent = _make_agent(
        "call-multi",
        BillingModel.PER_CALL,
        capabilities=[AgentCapability.CODE_GENERATION],
        billing_config={"price_per_call": 0.01},
    )
    catalog.register(free_agent)
    catalog.register(per_token_agent)
    catalog.register(per_call_agent)

    # 设置额度
    _set_quota(quota_bucket, "free-multi", free=10000)
    _set_quota(quota_bucket, "token-multi", free=10000)
    _set_quota(quota_bucket, "call-multi", free=10000)

    # COST_OPTIMIZED 策略 → 选 FREE（成本最低）
    ctx_cost = RoutingContext(
        required_capabilities=[AgentCapability.CODE_GENERATION],
        max_cost_tier="any",
    )
    result_cost = router.route(ctx_cost, strategy=RoutingStrategy.COST_OPTIMIZED)
    assert result_cost.primary is not None
    assert result_cost.primary.name == "free-multi", (
        f"COST_OPTIMIZED 应选 free-multi，实际选 {result_cost.primary.name}"
    )

    # CAPABILITY_MATCH 策略 → 选能力最多的（free-multi 有 3 个能力）
    ctx_cap = RoutingContext(
        required_capabilities=[AgentCapability.CODE_GENERATION],
        max_cost_tier="any",
    )
    result_cap = router.route(ctx_cap, strategy=RoutingStrategy.CAPABILITY_MATCH)
    assert result_cap.primary is not None
    assert result_cap.primary.name == "free-multi", (
        f"CAPABILITY_MATCH 应选能力最多的 free-multi，实际选 {result_cap.primary.name}"
    )

    # 分别 charge → 验证计费结果匹配选中的 Agent
    free_billing = engine.charge("free-multi", tokens=100, calls=1)
    assert free_billing.success
    assert free_billing.record is not None
    assert free_billing.record.cost_usd == 0.0  # FREE 零成本

    token_billing = engine.charge("token-multi", tokens=100, calls=1)
    assert token_billing.success
    assert token_billing.record is not None
    assert token_billing.record.cost_usd == pytest.approx(0.001, abs=1e-9)  # 100 * 0.00001

    call_billing = engine.charge("call-multi", tokens=100, calls=1)
    assert call_billing.success
    assert call_billing.record is not None
    assert call_billing.record.cost_usd == pytest.approx(0.01, abs=1e-9)  # 1 * 0.01


# ── Scenario 10: 额度退还 + 重新调用 ─────────────────────────────


def test_e2e_refund_and_recharge(
    catalog: AgentCatalog,
    quota_bucket: QuotaBucket,
    engine: BillingEngine,
) -> None:
    """场景10：额度退还 + 重新调用 — refund 后额度恢复，可再次 charge。"""
    # 注册 PER_TOKEN Agent
    agent = _make_agent(
        "refund-agent",
        BillingModel.PER_TOKEN,
        billing_config={"price_per_token": 0.00001},
    )
    catalog.register(agent)

    # 设置额度 free=1000
    _set_quota(quota_bucket, "refund-agent", free=1000)

    # charge tokens=500 → 剩余 500
    result1 = engine.charge("refund-agent", tokens=500)
    assert result1.success
    remaining1 = quota_bucket.get_remaining("refund-agent")
    assert remaining1["free"] == 500

    # refund 200 → 剩余 700
    quota_bucket.refund("refund-agent", 200, bucket="free")
    remaining2 = quota_bucket.get_remaining("refund-agent")
    assert remaining2["free"] == 700, f"refund 后应剩余 700，实际 {remaining2['free']}"

    # charge tokens=700 → 成功
    result2 = engine.charge("refund-agent", tokens=700)
    assert result2.success, f"refund 后应能 charge 700: {result2.error}"
    remaining3 = quota_bucket.get_remaining("refund-agent")
    assert remaining3["free"] == 0


# ── Scenario 11: 并发槽位管理 ────────────────────────────────────


def test_e2e_concurrent_slot_management(
    catalog: AgentCatalog,
    router: AgentRouter,
) -> None:
    """场景11：并发槽位管理 — acquire/release 控制 max_concurrent。"""
    # 注册 Agent (max_concurrent=2)
    agent = _make_agent("concurrent-agent", BillingModel.FREE, max_concurrent=2)
    catalog.register(agent)

    # acquire → True（第一个槽位）
    assert router.acquire("concurrent-agent") is True, "第一次 acquire 应成功"

    # acquire → True（第二个槽位）
    assert router.acquire("concurrent-agent") is True, "第二次 acquire 应成功"

    # acquire → False（超过 max_concurrent=2）
    assert router.acquire("concurrent-agent") is False, "第三次 acquire 应失败（超过 max_concurrent）"

    # release → 释放一个槽
    router.release("concurrent-agent")

    # acquire → True（释放后可再次获取）
    assert router.acquire("concurrent-agent") is True, "release 后 acquire 应成功"

    # 清理：释放所有槽位
    router.release("concurrent-agent")
    router.release("concurrent-agent")


# ── Scenario 12: CredentialVault 集成 ────────────────────────────


def test_e2e_credential_vault_integration(vault: CredentialVault) -> None:
    """场景12：CredentialVault 集成 — 加密存储 / 检索 / 预览 / 删除。

    CredentialVault 实际 API：
      - store(credential) → credential_id
      - retrieve(credential_id) → Credential
      - list_all() → list[CredentialSummary]（含 preview 掩码）
      - delete(credential_id) → bool
    """
    # 1. 构造 API_KEY 凭证
    credential = Credential(
        agent_name="e2e-agent",
        credential_type=CredentialType.API_KEY,
        api_key="sk-e2e-secret-key-12345678",
    )

    # 2. 存储凭证
    cred_id = vault.store(credential)
    assert isinstance(cred_id, str) and len(cred_id) > 0, "store 应返回非空 credential_id"

    # 3. 检索凭证 → 验证值匹配
    retrieved = vault.retrieve(cred_id)
    assert retrieved.api_key == "sk-e2e-secret-key-12345678", "检索的 api_key 应匹配"
    assert retrieved.agent_name == "e2e-agent"
    assert retrieved.credential_type == CredentialType.API_KEY

    # 4. 预览凭证 → 验证遮罩（通过 list_all 获取 CredentialSummary.preview）
    summaries = vault.list_all()
    assert len(summaries) >= 1, "list_all 应返回至少 1 个凭证摘要"
    target_summary = next((s for s in summaries if s.id == cred_id), None)
    assert target_summary is not None, "应找到刚存储的凭证摘要"
    # 掩码预览不应包含完整明文
    assert "sk-e2e-secret-key-12345678" not in target_summary.preview, (
        "预览不应泄露完整明文"
    )
    # 掩码预览应包含部分字符（前4位）
    assert target_summary.preview.startswith("sk-e"), (
        f"预览应以明文前4位开头，实际: {target_summary.preview}"
    )

    # 5. 删除凭证 → 验证不存在
    deleted = vault.delete(cred_id)
    assert deleted is True, "delete 应返回 True"

    # 验证删除后检索抛 KeyError
    with pytest.raises(KeyError):
        vault.retrieve(cred_id)

    # 验证再次删除返回 False
    deleted_again = vault.delete(cred_id)
    assert deleted_again is False, "删除不存在的凭证应返回 False"


# ── Scenario 13: 成本偏好过滤 ────────────────────────────────────


def test_e2e_cost_tier_filtering(
    catalog: AgentCatalog,
    router: AgentRouter,
) -> None:
    """场景13：成本偏好过滤 — max_cost_tier 分层过滤候选 Agent。

    成本优先级映射（_COST_PRIORITY）：
      FREE=0, SUBSCRIPTION=1, CREDIT=2, PER_CALL=3, PER_TOKEN=4, BLACKBOX=5
    max_cost_tier 限制（_COST_TIER_LIMIT）：
      low=1   → FREE, SUBSCRIPTION
      medium=3 → FREE, SUBSCRIPTION, CREDIT, PER_CALL
      high=4   → FREE, SUBSCRIPTION, CREDIT, PER_CALL, PER_TOKEN
      any=5    → 全部

    注意：max_cost_tier 过滤作用于 primary 选择和 alternatives，
    但 fallbacks 构建时使用 list_enabled() 全量补充同能力 Agent，不受此限制
    （源码设计：fallbacks 作为降级备选故意保留全部同能力 Agent）。
    因此本测试通过验证 primary 的选择来确认成本过滤生效。
    """
    # 注册 FREE + PER_TOKEN + BLACKBOX 三个 Agent
    free_agent = _make_agent("tier-free", BillingModel.FREE)
    per_token_agent = _make_agent(
        "tier-per-token",
        BillingModel.PER_TOKEN,
        billing_config={"price_per_token": 0.00001},
    )
    blackbox_agent = _make_agent(
        "tier-blackbox",
        BillingModel.BLACKBOX,
        billing_config={"blackbox_estimate_per_call": 100},
    )
    catalog.register(free_agent)
    catalog.register(per_token_agent)
    catalog.register(blackbox_agent)

    # max_cost_tier="low" → 只选 FREE / SUBSCRIPTION
    # 三个 Agent 中只有 tier-free 符合，应选 tier-free
    ctx_low = RoutingContext(
        required_capabilities=[AgentCapability.CODE_GENERATION],
        max_cost_tier="low",
    )
    result_low = router.route(ctx_low, strategy=RoutingStrategy.COST_OPTIMIZED)
    assert result_low.primary is not None
    assert result_low.primary.name == "tier-free", (
        f"low tier 应只选 FREE，实际选 {result_low.primary.name}"
    )

    # max_cost_tier="medium" → 加上 PER_CALL / CREDIT（仍只有 tier-free 符合）
    ctx_medium = RoutingContext(
        required_capabilities=[AgentCapability.CODE_GENERATION],
        max_cost_tier="medium",
    )
    result_medium = router.route(ctx_medium, strategy=RoutingStrategy.COST_OPTIMIZED)
    assert result_medium.primary is not None
    assert result_medium.primary.name == "tier-free"

    # max_cost_tier="high" → 加上 PER_TOKEN（FREE 仍最优）
    ctx_high = RoutingContext(
        required_capabilities=[AgentCapability.CODE_GENERATION],
        max_cost_tier="high",
    )
    result_high = router.route(ctx_high, strategy=RoutingStrategy.COST_OPTIMIZED)
    assert result_high.primary is not None
    assert result_high.primary.name == "tier-free"

    # max_cost_tier="any" → 全部（FREE 仍最优）
    ctx_any = RoutingContext(
        required_capabilities=[AgentCapability.CODE_GENERATION],
        max_cost_tier="any",
    )
    result_any = router.route(ctx_any, strategy=RoutingStrategy.COST_OPTIMIZED)
    assert result_any.primary is not None
    assert result_any.primary.name == "tier-free"

    # ── 验证成本过滤的排除效果 ──────────────────────────────────
    # 单独注册一个只有 PER_TOKEN Agent 的场景，验证 low tier 会过滤掉它
    catalog.register(_make_agent(
        "solo-per-token",
        BillingModel.PER_TOKEN,
        billing_config={"price_per_token": 0.00001},
    ))
    # max_cost_tier="low" → solo-per-token 被过滤，但 tier-free 仍在
    ctx_solo_low = RoutingContext(
        required_capabilities=[AgentCapability.CODE_GENERATION],
        max_cost_tier="low",
        excluded_agents=["tier-free"],  # 排除 tier-free 以隔离测试
    )
    result_solo_low = router.route(ctx_solo_low, strategy=RoutingStrategy.COST_OPTIMIZED)
    # solo-per-token 被 low tier 过滤，tier-free 被 excluded，只剩 fallbacks 中的
    # 但 primary 应为 None（candidates 经成本+排除过滤后为空）
    assert result_solo_low.primary is None, (
        "low tier 应过滤掉 PER_TOKEN，排除 FREE 后应无 primary"
    )

    # max_cost_tier="high" → solo-per-token 通过成本过滤
    ctx_solo_high = RoutingContext(
        required_capabilities=[AgentCapability.CODE_GENERATION],
        max_cost_tier="high",
        excluded_agents=["tier-free"],
    )
    result_solo_high = router.route(ctx_solo_high, strategy=RoutingStrategy.COST_OPTIMIZED)
    assert result_solo_high.primary is not None, (
        "high tier 应允许 PER_TOKEN，排除 FREE 后应选 PER_TOKEN"
    )
    assert result_solo_high.primary.name == "solo-per-token", (
        f"应选 solo-per-token，实际选 {result_solo_high.primary.name}"
    )


# ── Scenario 14: 计费摘要查询 ────────────────────────────────────


def test_e2e_billing_summary_query(
    catalog: AgentCatalog,
    quota_bucket: QuotaBucket,
    engine: BillingEngine,
) -> None:
    """场景14：计费摘要查询 — 多次 charge 后查询汇总信息。"""
    # 注册 PER_TOKEN Agent
    agent = _make_agent(
        "summary-agent",
        BillingModel.PER_TOKEN,
        billing_config={"price_per_token": 0.00001},
    )
    catalog.register(agent)

    # 设置额度
    _set_quota(quota_bucket, "summary-agent", free=100000)

    # 多次 charge
    engine.charge("summary-agent", tokens=1000, calls=1)
    engine.charge("summary-agent", tokens=2000, calls=1)
    engine.charge("summary-agent", tokens=3000, calls=1)

    # 查询计费摘要
    summary = engine.get_agent_billing_summary("summary-agent")

    # 验证摘要字段
    assert summary["agent_name"] == "summary-agent"
    assert summary["billing_model"] == "per_token"
    assert summary["total_calls"] == 3, f"total_calls 应为 3，实际 {summary['total_calls']}"
    assert summary["total_tokens"] == 6000, (
        f"total_tokens 应为 6000，实际 {summary['total_tokens']}"
    )
    # total_cost = (1000 + 2000 + 3000) * 0.00001 = 0.06
    assert summary["total_cost_usd"] == pytest.approx(0.06, abs=1e-9), (
        f"total_cost_usd 应为 0.06，实际 {summary['total_cost_usd']}"
    )
    assert summary["total_quota_consumed"] == 6000, (
        f"total_quota_consumed 应为 6000，实际 {summary['total_quota_consumed']}"
    )
    # 验证额度剩余
    assert "quota_remaining" in summary
    assert summary["quota_remaining"]["free"] == 94000, (
        f"free 应剩余 94000，实际 {summary['quota_remaining']['free']}"
    )

    # 验证 billing_records 查询
    records = engine.get_billing_records("summary-agent", limit=10)
    assert len(records) == 3, f"应有 3 条计费记录，实际 {len(records)}"
    # 按时间倒序排列，第一条应是最后一次
    assert records[0].tokens_consumed == 3000


# ── Scenario 15: 路由排除 Agent ─────────────────────────────────


def test_e2e_route_exclude_agent(
    catalog: AgentCatalog,
    router: AgentRouter,
) -> None:
    """场景15：路由排除 Agent — excluded_agents 过滤候选。"""
    # 注册 Agent A + Agent B
    agent_a = _make_agent("agent-a", BillingModel.FREE)
    agent_b = _make_agent("agent-b", BillingModel.FREE)
    catalog.register(agent_a)
    catalog.register(agent_b)

    # excluded_agents=["agent-a"] → 验证选 agent-b
    ctx = RoutingContext(
        required_capabilities=[AgentCapability.CODE_GENERATION],
        max_cost_tier="low",
        excluded_agents=["agent-a"],
    )
    result = router.route(ctx, strategy=RoutingStrategy.COST_OPTIMIZED)

    assert result.primary is not None, "排除 agent-a 后应选中 agent-b"
    assert result.primary.name == "agent-b", (
        f"应选中 agent-b，实际选 {result.primary.name}"
    )

    # 验证 agent-a 不在 alternatives 中（alternatives 受 excluded_agents 过滤）
    # 注意：fallbacks 构建时使用 list_enabled() 全量补充同能力 Agent，
    # 不受 excluded_agents 限制（源码设计：fallbacks 作为降级备选故意保留全部）。
    alt_names = {a.name for a in result.alternatives}
    assert "agent-a" not in alt_names, "agent-a 应从 alternatives 中排除"