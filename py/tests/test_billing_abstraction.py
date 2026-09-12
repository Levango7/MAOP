"""BillingEngine 白盒测试.

覆盖：BillingRecord 构造 / 各计费模式 charge / 估算成本 / 额度集成 /
记录查询 / Agent 摘要 / CostTracker 集成。
每个测试使用 tmp_path 隔离 SQLite，不触碰真实 DB。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from maop.core.agent.billing.billing_abstraction import (
    BillingEngine,
    BillingRecord,
    BillingResult,
)
from maop.core.agent.billing.quota_bucket import QuotaBucket, QuotaEntry
from maop.core.agent.registry.agent_catalog import (
    AgentCatalog,
    AgentDescriptor,
    BillingModel,
)
from maop.core.cost_tracker import CostTracker


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
def cost_tracker(tmp_path: Path) -> CostTracker:
    """使用隔离 DB 的 CostTracker。"""
    return CostTracker(root_dir=tmp_path / "cost")


@pytest.fixture
def engine(
    catalog: AgentCatalog,
    quota_bucket: QuotaBucket,
    tmp_path: Path,
) -> BillingEngine:
    """使用隔离 DB 的 BillingEngine（无 CostTracker）。"""
    return BillingEngine(
        catalog=catalog,
        quota_bucket=quota_bucket,
        db_path=tmp_path / "billing.db",
    )


@pytest.fixture
def engine_with_tracker(
    catalog: AgentCatalog,
    quota_bucket: QuotaBucket,
    cost_tracker: CostTracker,
    tmp_path: Path,
) -> BillingEngine:
    """使用隔离 DB 的 BillingEngine（带 CostTracker）。"""
    return BillingEngine(
        catalog=catalog,
        quota_bucket=quota_bucket,
        cost_tracker=cost_tracker,
        db_path=tmp_path / "billing.db",
    )


def _make_descriptor(
    name: str,
    billing_model: BillingModel,
    **billing_config: Any,
) -> AgentDescriptor:
    """构造指定计费模式的 AgentDescriptor。"""
    return AgentDescriptor(
        name=name,
        display_name=name,
        billing_model=billing_model,
        billing_config=billing_config,
    )


# ── 1. TestBillingRecord ────────────────────────────────────────


class TestBillingRecord:
    """BillingRecord 构造与默认值。"""

    def test_default_optional_fields(self) -> None:
        """必填字段提供后，可选字段使用默认值。"""
        record = BillingRecord(agent_name="a", billing_model="free")
        assert record.tokens_consumed == 0
        assert record.calls_made == 0
        assert record.cost_usd == 0.0
        assert record.quota_consumed == 0
        assert record.quota_source == ""
        assert record.timestamp == ""

    def test_full_construction(self) -> None:
        """完整构造。"""
        record = BillingRecord(
            agent_name="a",
            billing_model="per_token",
            tokens_consumed=100,
            calls_made=1,
            cost_usd=0.05,
            quota_consumed=100,
            quota_source="free:100",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        assert record.tokens_consumed == 100
        assert record.cost_usd == 0.05


class TestBillingResult:
    """BillingResult 构造。"""

    def test_success_result(self) -> None:
        """成功结果。"""
        record = BillingRecord(agent_name="a", billing_model="free")
        result = BillingResult(success=True, record=record)
        assert result.success
        assert result.record is not None
        assert result.error == ""

    def test_failure_result(self) -> None:
        """失败结果。"""
        result = BillingResult(success=False, error="not found")
        assert not result.success
        assert result.record is None
        assert result.error == "not found"


# ── 2. TestBillingEngineCharge ──────────────────────────────────


class TestBillingEngineCharge:
    """各计费模式 charge。"""

    def test_charge_free_model(self, engine: BillingEngine, catalog: AgentCatalog) -> None:
        """FREE 模式不计费。"""
        catalog.register(_make_descriptor("free_agent", BillingModel.FREE))
        result = engine.charge("free_agent", tokens=100, calls=1)
        assert result.success
        assert result.record is not None
        assert result.record.billing_model == "free"
        assert result.record.quota_consumed == 0
        assert result.record.cost_usd == 0.0

    def test_charge_per_token_model(self, engine: BillingEngine, catalog: AgentCatalog) -> None:
        """PER_TOKEN 模式按 token 计费。"""
        catalog.register(_make_descriptor(
            "pt_agent", BillingModel.PER_TOKEN, price_per_token=0.001,
        ))
        engine._quota.set_quota("pt_agent", QuotaEntry(free_quota=10000))
        result = engine.charge("pt_agent", tokens=1000, calls=1)
        assert result.success
        assert result.record is not None
        assert result.record.quota_consumed == 1000
        assert result.record.cost_usd == 1.0

    def test_charge_per_call_model(self, engine: BillingEngine, catalog: AgentCatalog) -> None:
        """PER_CALL 模式按调用次数计费。"""
        catalog.register(_make_descriptor(
            "pc_agent", BillingModel.PER_CALL, price_per_call=0.05,
        ))
        engine._quota.set_quota("pc_agent", QuotaEntry(free_quota=10000))
        result = engine.charge("pc_agent", tokens=0, calls=3)
        assert result.success
        assert result.record is not None
        assert result.record.quota_consumed == 3
        assert result.record.cost_usd == 0.15

    def test_charge_subscription_model(self, engine: BillingEngine, catalog: AgentCatalog) -> None:
        """SUBSCRIPTION 模式不消耗额度。"""
        catalog.register(_make_descriptor("sub_agent", BillingModel.SUBSCRIPTION))
        result = engine.charge("sub_agent", tokens=500, calls=2)
        assert result.success
        assert result.record is not None
        assert result.record.quota_consumed == 0
        assert result.record.cost_usd == 0.0

    def test_charge_credit_model(self, engine: BillingEngine, catalog: AgentCatalog) -> None:
        """CREDIT 模式按 calls 消耗额度，无美元成本。"""
        catalog.register(_make_descriptor("credit_agent", BillingModel.CREDIT))
        # 设置额度
        engine._quota.set_quota("credit_agent", QuotaEntry(free_quota=100))
        result = engine.charge("credit_agent", tokens=0, calls=5)
        assert result.success
        assert result.record is not None
        assert result.record.quota_consumed == 5
        assert result.record.cost_usd == 0.0

    def test_charge_blackbox_model(self, engine: BillingEngine, catalog: AgentCatalog) -> None:
        """BLACKBOX 模式按 calls * blackbox_estimate_per_call 消耗。"""
        catalog.register(_make_descriptor(
            "bb_agent", BillingModel.BLACKBOX, blackbox_estimate_per_call=10,
        ))
        engine._quota.set_quota("bb_agent", QuotaEntry(free_quota=1000))
        result = engine.charge("bb_agent", tokens=0, calls=3)
        assert result.success
        assert result.record is not None
        assert result.record.quota_consumed == 30  # 3 * 10
        assert result.record.cost_usd == 0.0

    def test_charge_blackbox_default_per_call(self, engine: BillingEngine, catalog: AgentCatalog) -> None:
        """BLACKBOX 未配置 blackbox_estimate_per_call 时默认 1。"""
        catalog.register(_make_descriptor("bb_agent", BillingModel.BLACKBOX))
        engine._quota.set_quota("bb_agent", QuotaEntry(free_quota=100))
        result = engine.charge("bb_agent", calls=5)
        assert result.success
        assert result.record is not None
        assert result.record.quota_consumed == 5

    def test_charge_agent_not_found(self, engine: BillingEngine) -> None:
        """未注册 Agent 返回失败。"""
        result = engine.charge("ghost")
        assert not result.success
        assert "not found" in result.error


# ── 3. TestBillingEngineEstimate ────────────────────────────────


class TestBillingEngineEstimate:
    """估算各模式成本。"""

    def test_estimate_free(self, engine: BillingEngine, catalog: AgentCatalog) -> None:
        """FREE 估算 0。"""
        catalog.register(_make_descriptor("a", BillingModel.FREE))
        assert engine.estimate_cost("a", tokens=100) == 0.0

    def test_estimate_per_token(self, engine: BillingEngine, catalog: AgentCatalog) -> None:
        """PER_TOKEN 估算。"""
        catalog.register(_make_descriptor("a", BillingModel.PER_TOKEN, price_per_token=0.002))
        assert engine.estimate_cost("a", tokens=500) == 1.0

    def test_estimate_per_call(self, engine: BillingEngine, catalog: AgentCatalog) -> None:
        """PER_CALL 估算。"""
        catalog.register(_make_descriptor("a", BillingModel.PER_CALL, price_per_call=0.1))
        assert engine.estimate_cost("a", calls=4) == 0.4

    def test_estimate_subscription(self, engine: BillingEngine, catalog: AgentCatalog) -> None:
        """SUBSCRIPTION 估算 0。"""
        catalog.register(_make_descriptor("a", BillingModel.SUBSCRIPTION))
        assert engine.estimate_cost("a", tokens=1000) == 0.0

    def test_estimate_credit(self, engine: BillingEngine, catalog: AgentCatalog) -> None:
        """CREDIT 估算 0（无美元成本）。"""
        catalog.register(_make_descriptor("a", BillingModel.CREDIT))
        assert engine.estimate_cost("a", calls=10) == 0.0

    def test_estimate_blackbox(self, engine: BillingEngine, catalog: AgentCatalog) -> None:
        """BLACKBOX 估算 0（无美元成本）。"""
        catalog.register(_make_descriptor("a", BillingModel.BLACKBOX))
        assert engine.estimate_cost("a", calls=10) == 0.0

    def test_estimate_nonexistent_agent(self, engine: BillingEngine) -> None:
        """不存在 Agent 估算 0。"""
        assert engine.estimate_cost("ghost", tokens=100) == 0.0


# ── 4. TestBillingEngineQuotaIntegration ────────────────────────


class TestBillingEngineQuotaIntegration:
    """额度扣减 / 不足失败 / 混合扣减。"""

    def test_charge_consumes_quota(
        self,
        engine: BillingEngine,
        catalog: AgentCatalog,
    ) -> None:
        """charge 实际扣减额度。"""
        catalog.register(_make_descriptor("a", BillingModel.PER_TOKEN))
        engine._quota.set_quota("a", QuotaEntry(free_quota=1000))
        result = engine.charge("a", tokens=300)
        assert result.success
        remaining = engine._quota.get_remaining("a")
        assert remaining["free"] == 700

    def test_charge_quota_insufficient_fails(
        self,
        engine: BillingEngine,
        catalog: AgentCatalog,
    ) -> None:
        """额度不足返回失败。"""
        catalog.register(_make_descriptor("a", BillingModel.PER_TOKEN))
        engine._quota.set_quota("a", QuotaEntry(free_quota=100))
        result = engine.charge("a", tokens=200)
        assert not result.success
        assert "quota" in result.error.lower()

    def test_charge_mixed_bucket_consumption(
        self,
        engine: BillingEngine,
        catalog: AgentCatalog,
    ) -> None:
        """混合桶扣减：免费 + 预付。"""
        catalog.register(_make_descriptor("a", BillingModel.PER_TOKEN))
        engine._quota.set_quota("a", QuotaEntry(free_quota=100, prepaid_quota=200))
        result = engine.charge("a", tokens=150)
        assert result.success
        assert result.record is not None
        # 验证 quota_source 包含两个桶
        assert "free" in result.record.quota_source
        assert "prepaid" in result.record.quota_source

    def test_charge_no_quota_needed_for_free(
        self,
        engine: BillingEngine,
        catalog: AgentCatalog,
    ) -> None:
        """FREE 模式无需额度即可成功。"""
        catalog.register(_make_descriptor("a", BillingModel.FREE))
        # 不设置任何额度
        result = engine.charge("a", tokens=1000)
        assert result.success

    def test_charge_no_quota_needed_for_subscription(
        self,
        engine: BillingEngine,
        catalog: AgentCatalog,
    ) -> None:
        """SUBSCRIPTION 模式无需额度即可成功。"""
        catalog.register(_make_descriptor("a", BillingModel.SUBSCRIPTION))
        result = engine.charge("a", tokens=1000)
        assert result.success


# ── 5. TestBillingEngineRecords ─────────────────────────────────


class TestBillingEngineRecords:
    """计费记录 / 查询 / 限制。"""

    def test_record_persisted(self, engine: BillingEngine, catalog: AgentCatalog) -> None:
        """charge 后记录可查询。"""
        catalog.register(_make_descriptor("a", BillingModel.PER_TOKEN, price_per_token=0.001))
        engine._quota.set_quota("a", QuotaEntry(free_quota=10000))
        engine.charge("a", tokens=100)
        records = engine.get_billing_records("a")
        assert len(records) == 1
        assert records[0].agent_name == "a"
        assert records[0].tokens_consumed == 100

    def test_multiple_records_ordered_desc(
        self,
        engine: BillingEngine,
        catalog: AgentCatalog,
    ) -> None:
        """多条记录按时间倒序。"""
        catalog.register(_make_descriptor("a", BillingModel.PER_CALL, price_per_call=0.1))
        engine._quota.set_quota("a", QuotaEntry(free_quota=10000))
        engine.charge("a", calls=1)
        engine.charge("a", calls=2)
        engine.charge("a", calls=3)
        records = engine.get_billing_records("a")
        assert len(records) == 3
        # 倒序：最后一条 calls=3 在前
        assert records[0].calls_made == 3
        assert records[2].calls_made == 1

    def test_records_limit(self, engine: BillingEngine, catalog: AgentCatalog) -> None:
        """limit 限制返回数。"""
        catalog.register(_make_descriptor("a", BillingModel.PER_CALL))
        engine._quota.set_quota("a", QuotaEntry(free_quota=10000))
        for _ in range(5):
            engine.charge("a", calls=1)
        records = engine.get_billing_records("a", limit=3)
        assert len(records) == 3

    def test_records_filter_by_agent(
        self,
        engine: BillingEngine,
        catalog: AgentCatalog,
    ) -> None:
        """按 Agent 筛选。"""
        catalog.register(_make_descriptor("a", BillingModel.FREE))
        catalog.register(_make_descriptor("b", BillingModel.FREE))
        engine.charge("a")
        engine.charge("b")
        records_a = engine.get_billing_records("a")
        records_b = engine.get_billing_records("b")
        assert len(records_a) == 1
        assert len(records_b) == 1
        assert records_a[0].agent_name == "a"

    def test_records_empty(self, engine: BillingEngine) -> None:
        """无记录返回空列表。"""
        records = engine.get_billing_records("ghost")
        assert records == []

    def test_records_all_agents(self, engine: BillingEngine, catalog: AgentCatalog) -> None:
        """不传 agent_name 返回所有记录。"""
        catalog.register(_make_descriptor("a", BillingModel.FREE))
        catalog.register(_make_descriptor("b", BillingModel.FREE))
        engine.charge("a")
        engine.charge("b")
        records = engine.get_billing_records()
        assert len(records) == 2


# ── 6. TestBillingEngineSummary ─────────────────────────────────


class TestBillingEngineSummary:
    """Agent 计费摘要。"""

    def test_summary_basic(
        self,
        engine: BillingEngine,
        catalog: AgentCatalog,
    ) -> None:
        """基本摘要字段。"""
        catalog.register(_make_descriptor("a", BillingModel.PER_TOKEN, price_per_token=0.001))
        engine._quota.set_quota("a", QuotaEntry(free_quota=10000))
        engine.charge("a", tokens=100)
        engine.charge("a", tokens=200)
        summary = engine.get_agent_billing_summary("a")
        assert summary["agent_name"] == "a"
        assert summary["billing_model"] == "per_token"
        assert summary["total_calls"] == 2
        assert summary["total_tokens"] == 300
        assert summary["total_cost_usd"] == 0.3
        assert summary["total_quota_consumed"] == 300

    def test_summary_includes_quota_remaining(
        self,
        engine: BillingEngine,
        catalog: AgentCatalog,
    ) -> None:
        """摘要包含 quota_remaining。"""
        catalog.register(_make_descriptor("a", BillingModel.PER_TOKEN))
        engine._quota.set_quota("a", QuotaEntry(free_quota=1000))
        engine.charge("a", tokens=300)
        summary = engine.get_agent_billing_summary("a")
        assert summary["quota_remaining"]["free"] == 700

    def test_summary_nonexistent_agent(self, engine: BillingEngine) -> None:
        """不存在 Agent 的摘要。"""
        summary = engine.get_agent_billing_summary("ghost")
        assert summary["agent_name"] == "ghost"
        assert summary["billing_model"] == "unknown"
        assert summary["total_calls"] == 0

    def test_summary_no_records(
        self,
        engine: BillingEngine,
        catalog: AgentCatalog,
    ) -> None:
        """有 Agent 但无记录。"""
        catalog.register(_make_descriptor("a", BillingModel.FREE))
        summary = engine.get_agent_billing_summary("a")
        assert summary["total_calls"] == 0
        assert summary["total_cost_usd"] == 0.0


# ── 7. TestBillingEngineCostTracker ─────────────────────────────


class TestBillingEngineCostTracker:
    """CostTracker 集成 / 无 CostTracker。"""

    def test_charge_with_cost_tracker(
        self,
        engine_with_tracker: BillingEngine,
        catalog: AgentCatalog,
    ) -> None:
        """带 CostTracker 时记录到 cost_entries。"""
        catalog.register(_make_descriptor("a", BillingModel.PER_TOKEN, price_per_token=0.001))
        engine_with_tracker._quota.set_quota("a", QuotaEntry(free_quota=10000))
        result = engine_with_tracker.charge(
            "a", tokens=100, session_id="sess1", model="gpt-4o",
        )
        assert result.success
        # 验证 CostTracker 记录
        entries = engine_with_tracker._cost_tracker.get_entries(agent="a")
        assert len(entries) == 1
        assert entries[0].session_id == "sess1"
        assert entries[0].model == "gpt-4o"

    def test_charge_without_cost_tracker(
        self,
        engine: BillingEngine,
        catalog: AgentCatalog,
    ) -> None:
        """无 CostTracker 时 charge 仍成功。"""
        catalog.register(_make_descriptor("a", BillingModel.PER_TOKEN))
        engine._quota.set_quota("a", QuotaEntry(free_quota=10000))
        result = engine.charge("a", tokens=100)
        assert result.success
        assert engine._cost_tracker is None

    def test_cost_tracker_failure_does_not_break_charge(
        self,
        engine_with_tracker: BillingEngine,
        catalog: AgentCatalog,
    ) -> None:
        """CostTracker 失败不影响 charge 主流程。"""
        catalog.register(_make_descriptor("a", BillingModel.PER_TOKEN))
        engine_with_tracker._quota.set_quota("a", QuotaEntry(free_quota=10000))
        # 让 CostTracker.record 抛异常
        original_record = engine_with_tracker._cost_tracker.record

        def boom(**kwargs: Any) -> None:
            raise RuntimeError("boom")

        engine_with_tracker._cost_tracker.record = boom
        try:
            result = engine_with_tracker.charge("a", tokens=100)
        finally:
            engine_with_tracker._cost_tracker.record = original_record
        assert result.success
        assert result.record is not None

    def test_charge_persists_record_with_cost_tracker(
        self,
        engine_with_tracker: BillingEngine,
        catalog: AgentCatalog,
    ) -> None:
        """带 CostTracker 时 billing_records 也持久化。"""
        catalog.register(_make_descriptor("a", BillingModel.PER_TOKEN, price_per_token=0.001))
        engine_with_tracker._quota.set_quota("a", QuotaEntry(free_quota=10000))
        engine_with_tracker.charge("a", tokens=100)
        records = engine_with_tracker.get_billing_records("a")
        assert len(records) == 1
        assert records[0].cost_usd == 0.1
