"""厂商生态管理测试.

覆盖三个模块：
    1. VendorEcosystem — 注册/查询/协作建议/预置厂商
    2. VendorSSO — 登录/登出/会话/凭证获取
    3. VendorBilling — 计费/账单/预算

所有测试使用 ``tmp_path`` 隔离 SQLite，不触碰真实 DB。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone  # noqa: F401
from pathlib import Path

import pytest

from maop.core.agent.vendor.vendor_billing import (
    AgentUsage,  # noqa: F401
    BudgetStatus,  # noqa: F401
    VendorBilling,
    VendorBillingSummary,  # noqa: F401
)
from maop.core.agent.vendor.vendor_ecosystem import (
    Vendor,
    VendorEcosystem,
    get_preset_vendors,
)
from maop.core.agent.vendor.vendor_sso import (
    VendorSession,  # noqa: F401
    VendorSSO,
)
from tests.thread_join_guard import join_all

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def ecosystem(tmp_path: Path) -> VendorEcosystem:
    """使用隔离 DB 的 VendorEcosystem。"""
    return VendorEcosystem(db_path=tmp_path / "vendor.db")


@pytest.fixture
def ecosystem_with_vendors(tmp_path: Path) -> VendorEcosystem:
    """预置 10 个厂商的 VendorEcosystem。"""
    eco = VendorEcosystem(db_path=tmp_path / "vendor.db")
    for v in get_preset_vendors():
        eco.register_vendor(v)
    return eco


@pytest.fixture
def sso(tmp_path: Path, ecosystem_with_vendors: VendorEcosystem) -> VendorSSO:
    """使用隔离 DB 的 VendorSSO。"""
    return VendorSSO(
        ecosystem=ecosystem_with_vendors,
        db_path=tmp_path / "vendor_sso.db",
    )


@pytest.fixture
def billing(
    tmp_path: Path,
    ecosystem_with_vendors: VendorEcosystem,
) -> VendorBilling:
    """使用隔离 DB 的 VendorBilling。"""
    return VendorBilling(
        ecosystem=ecosystem_with_vendors,
        db_path=tmp_path / "vendor_billing.db",
    )


def _register_alibaba_agents(eco: VendorEcosystem) -> None:
    """注册 3 个阿里 Agent 到 ecosystem。"""
    for agent in ["agent-a", "agent-b", "agent-c"]:
        eco.register_agent_to_vendor(agent, "alibaba")


# ════════════════════════════════════════════════════════════════
# 模块1: VendorEcosystem 测试
# ════════════════════════════════════════════════════════════════


class TestVendorEcosystem:
    """VendorEcosystem — 厂商注册/查询/协作建议/预置厂商。"""

    def test_register_and_get_vendor(self, ecosystem: VendorEcosystem) -> None:
        """注册厂商后可通过 get_vendor 查询。"""
        vendor = Vendor(name="alibaba", display_name="阿里巴巴")
        ecosystem.register_vendor(vendor)
        got = ecosystem.get_vendor("alibaba")
        assert got is not None
        assert got.name == "alibaba"
        assert got.display_name == "阿里巴巴"

    def test_get_vendor_not_found(self, ecosystem: VendorEcosystem) -> None:
        """未注册厂商返回 None。"""
        assert ecosystem.get_vendor("nonexistent") is None

    def test_register_vendor_upsert(self, ecosystem: VendorEcosystem) -> None:
        """重复注册同名厂商执行 upsert 更新。"""
        ecosystem.register_vendor(Vendor(name="alibaba", display_name="阿里"))
        ecosystem.register_vendor(
            Vendor(name="alibaba", display_name="阿里巴巴", sso_enabled=True)
        )
        got = ecosystem.get_vendor("alibaba")
        assert got is not None
        assert got.display_name == "阿里巴巴"
        assert got.sso_enabled is True

    def test_list_vendors_empty(self, ecosystem: VendorEcosystem) -> None:
        """空注册表返回空列表。"""
        assert ecosystem.list_vendors() == []

    def test_list_vendors_multiple(self, ecosystem: VendorEcosystem) -> None:
        """列出多个已注册厂商。"""
        for name, display in [("alibaba", "阿里"), ("tencent", "腾讯")]:
            ecosystem.register_vendor(Vendor(name=name, display_name=display))
        vendors = ecosystem.list_vendors()
        assert len(vendors) == 2
        names = {v.name for v in vendors}
        assert names == {"alibaba", "tencent"}

    def test_register_agent_to_vendor(
        self, ecosystem_with_vendors: VendorEcosystem
    ) -> None:
        """将 Agent 归属到厂商。"""
        ecosystem_with_vendors.register_agent_to_vendor("agent-x", "alibaba")
        assert ecosystem_with_vendors.get_vendor_of_agent("agent-x") == "alibaba"

    def test_register_agent_to_unknown_vendor_raises(
        self, ecosystem: VendorEcosystem
    ) -> None:
        """归属到未注册厂商抛出 ValueError。"""
        with pytest.raises(ValueError, match="未注册"):
            ecosystem.register_agent_to_vendor("agent-x", "ghost")

    def test_get_agents_by_vendor(
        self, ecosystem_with_vendors: VendorEcosystem
    ) -> None:
        """获取厂商下所有 Agent。"""
        _register_alibaba_agents(ecosystem_with_vendors)
        agents = ecosystem_with_vendors.get_agents_by_vendor("alibaba")
        assert set(agents) == {"agent-a", "agent-b", "agent-c"}

    def test_get_agents_by_vendor_empty(
        self, ecosystem_with_vendors: VendorEcosystem
    ) -> None:
        """无 Agent 的厂商返回空列表。"""
        assert ecosystem_with_vendors.get_agents_by_vendor("tencent") == []

    def test_get_vendor_of_agent_none(
        self, ecosystem_with_vendors: VendorEcosystem
    ) -> None:
        """未归属任何厂商的 Agent 返回 None。"""
        assert ecosystem_with_vendors.get_vendor_of_agent("lonely") is None

    def test_suggest_collaboration(
        self, ecosystem_with_vendors: VendorEcosystem
    ) -> None:
        """建议同厂商可协作的 Agent。"""
        _register_alibaba_agents(ecosystem_with_vendors)
        # agent-a 的协作建议应为 agent-b, agent-c
        suggestions = ecosystem_with_vendors.suggest_collaboration("agent-a")
        assert set(suggestions) == {"agent-b", "agent-c"}
        # 不含自身
        assert "agent-a" not in suggestions

    def test_suggest_collaboration_no_vendor(
        self, ecosystem_with_vendors: VendorEcosystem
    ) -> None:
        """未归属厂商的 Agent 协作建议为空。"""
        assert ecosystem_with_vendors.suggest_collaboration("lonely") == []

    def test_suggest_collaboration_sole_agent(
        self, ecosystem_with_vendors: VendorEcosystem
    ) -> None:
        """厂商下唯一 Agent 的协作建议为空。"""
        ecosystem_with_vendors.register_agent_to_vendor("solo", "tencent")
        assert ecosystem_with_vendors.suggest_collaboration("solo") == []

    def test_reassign_agent_to_different_vendor(
        self, ecosystem_with_vendors: VendorEcosystem
    ) -> None:
        """Agent 重新归属到不同厂商。"""
        ecosystem_with_vendors.register_agent_to_vendor("agent-x", "alibaba")
        ecosystem_with_vendors.register_agent_to_vendor("agent-x", "tencent")
        assert ecosystem_with_vendors.get_vendor_of_agent("agent-x") == "tencent"
        assert ecosystem_with_vendors.get_agents_by_vendor("alibaba") == []
        assert ecosystem_with_vendors.get_agents_by_vendor("tencent") == ["agent-x"]


class TestPresetVendors:
    """预置厂商数据测试。"""

    def test_preset_vendor_count(self) -> None:
        """预置厂商数量为 10。"""
        vendors = get_preset_vendors()
        assert len(vendors) == 10

    def test_preset_vendor_names(self) -> None:
        """预置厂商名称完整。"""
        vendors = get_preset_vendors()
        names = {v.name for v in vendors}
        expected = {
            "alibaba", "tencent", "bytedance", "huawei", "zhipu",
            "meituan", "csdn", "deepseek", "opensource", "international",
        }
        assert names == expected

    def test_preset_vendor_with_sso(self) -> None:
        """阿里/腾讯/字节/华为支持 SSO。"""
        vendors = get_preset_vendors()
        sso_vendors = {v.name for v in vendors if v.sso_enabled}
        assert {"alibaba", "tencent", "bytedance", "huawei"} <= sso_vendors

    def test_preset_international_region(self) -> None:
        """国际厂商 region 为 international。"""
        vendors = get_preset_vendors()
        intl = next(v for v in vendors if v.name == "international")
        assert intl.region == "international"

    def test_preset_vendors_load_into_ecosystem(
        self, ecosystem: VendorEcosystem
    ) -> None:
        """预置厂商可全部加载到 ecosystem。"""
        for v in get_preset_vendors():
            ecosystem.register_vendor(v)
        assert len(ecosystem.list_vendors()) == 10


# ════════════════════════════════════════════════════════════════
# 模块2: VendorSSO 测试
# ════════════════════════════════════════════════════════════════


class TestVendorSSO:
    """VendorSSO — 厂商统一认证。"""

    def test_login_vendor_creates_session(self, sso: VendorSSO) -> None:
        """厂商登录创建活跃会话。"""
        session = sso.login_vendor(
            "alibaba",
            {"user_id": "u1", "token": "tok-123"},
        )
        assert session.vendor_name == "alibaba"
        assert session.status == "active"
        assert session.user_id == "u1"
        assert session.token == "tok-123"
        assert session.session_id  # 非空

    def test_login_unknown_vendor_raises(self, sso: VendorSSO) -> None:
        """登录未注册厂商抛出 ValueError。"""
        with pytest.raises(ValueError, match="未注册"):
            sso.login_vendor("ghost", {"token": "x"})

    def test_get_session(self, sso: VendorSSO) -> None:
        """通过 session_id 获取会话。"""
        session = sso.login_vendor("alibaba", {"user_id": "u1"})
        got = sso.get_session(session.session_id)
        assert got is not None
        assert got.session_id == session.session_id

    def test_get_session_not_found(self, sso: VendorSSO) -> None:
        """不存在的 session_id 返回 None。"""
        assert sso.get_session("nonexistent") is None

    def test_check_session_valid(self, sso: VendorSSO) -> None:
        """活跃会话校验有效。"""
        session = sso.login_vendor("alibaba", {"token": "tok"})
        assert sso.check_session_valid(session.session_id) is True

    def test_check_session_invalid_after_logout(self, sso: VendorSSO) -> None:
        """登出后会话无效。"""
        session = sso.login_vendor("alibaba", {"token": "tok"})
        sso.logout_vendor("alibaba", session.session_id)
        assert sso.check_session_valid(session.session_id) is False

    def test_check_session_expired(self, sso: VendorSSO) -> None:
        """过期会话无效。"""
        # ttl_seconds=0 使会话立即过期
        session = sso.login_vendor(
            "alibaba", {"token": "tok", "ttl_seconds": 0}
        )
        # 等待过期
        time.sleep(0.01)
        assert sso.check_session_valid(session.session_id) is False

    def test_get_agent_credential(
        self, sso: VendorSSO, ecosystem_with_vendors: VendorEcosystem
    ) -> None:
        """从厂商会话获取 Agent 凭证。"""
        ecosystem_with_vendors.register_agent_to_vendor("agent-a", "alibaba")
        session = sso.login_vendor("alibaba", {"user_id": "u1", "token": "tok-xyz"})
        cred = sso.get_agent_credential("agent-a", session.session_id)
        assert cred is not None
        assert cred["token"] == "tok-xyz"
        assert cred["user_id"] == "u1"
        assert cred["agent_name"] == "agent-a"
        assert cred["vendor_name"] == "alibaba"

    def test_get_agent_credential_cross_vendor_denied(
        self, sso: VendorSSO, ecosystem_with_vendors: VendorEcosystem
    ) -> None:
        """跨厂商 Agent 不能获取其他厂商会话凭证。"""
        ecosystem_with_vendors.register_agent_to_vendor("agent-a", "alibaba")
        ecosystem_with_vendors.register_agent_to_vendor("agent-b", "tencent")
        session = sso.login_vendor("alibaba", {"token": "tok-ali"})
        # agent-b 属于 tencent，不能使用 alibaba 会话
        cred = sso.get_agent_credential("agent-b", session.session_id)
        assert cred is None

    def test_get_agent_credential_invalid_session(
        self, sso: VendorSSO, ecosystem_with_vendors: VendorEcosystem
    ) -> None:
        """无效会话返回 None。"""
        ecosystem_with_vendors.register_agent_to_vendor("agent-a", "alibaba")
        cred = sso.get_agent_credential("agent-a", "invalid-session")
        assert cred is None

    def test_list_active_sessions(
        self, sso: VendorSSO
    ) -> None:
        """列出活跃会话。"""
        sso.login_vendor("alibaba", {"token": "t1"})
        sso.login_vendor("tencent", {"token": "t2"})
        active = sso.list_active_sessions()
        assert len(active) == 2

    def test_list_active_sessions_by_vendor(self, sso: VendorSSO) -> None:
        """按厂商筛选活跃会话。"""
        sso.login_vendor("alibaba", {"token": "t1"})
        sso.login_vendor("tencent", {"token": "t2"})
        ali_active = sso.list_active_sessions("alibaba")
        assert len(ali_active) == 1
        assert ali_active[0].vendor_name == "alibaba"

    def test_list_active_sessions_excludes_revoked(self, sso: VendorSSO) -> None:
        """登出后不出现在活跃会话列表。"""
        s1 = sso.login_vendor("alibaba", {"token": "t1"})
        sso.login_vendor("alibaba", {"token": "t2"})
        sso.logout_vendor("alibaba", s1.session_id)
        active = sso.list_active_sessions("alibaba")
        assert len(active) == 1
        assert active[0].session_id != s1.session_id


# ════════════════════════════════════════════════════════════════
# 模块3: VendorBilling 测试
# ════════════════════════════════════════════════════════════════


class TestVendorBilling:
    """VendorBilling — 厂商统一计费。"""

    def test_record_usage(
        self,
        billing: VendorBilling,
        ecosystem_with_vendors: VendorEcosystem,
    ) -> None:
        """记录 Agent 用量。"""
        ecosystem_with_vendors.register_agent_to_vendor("agent-a", "alibaba")
        billing.record_usage("agent-a", cost=1.5, tokens=100, calls=2)
        summary = billing.get_vendor_summary("alibaba", period="all")
        assert summary.total_cost == 1.5
        assert summary.total_tokens == 100
        assert summary.total_calls == 2

    def test_record_usage_unknown_agent(
        self,
        billing: VendorBilling,
    ) -> None:
        """未归属厂商的 Agent 记录到 unknown。"""
        billing.record_usage("lonely", cost=0.5)
        summary = billing.get_vendor_summary("unknown", period="all")
        assert summary.total_cost == 0.5

    def test_vendor_summary_aggregation(
        self,
        billing: VendorBilling,
        ecosystem_with_vendors: VendorEcosystem,
    ) -> None:
        """厂商账单聚合多个 Agent 用量。"""
        for agent in ["agent-a", "agent-b", "agent-c"]:
            ecosystem_with_vendors.register_agent_to_vendor(agent, "alibaba")
        billing.record_usage("agent-a", cost=1.0, tokens=50, calls=1)
        billing.record_usage("agent-b", cost=2.0, tokens=100, calls=2)
        billing.record_usage("agent-c", cost=3.0, tokens=150, calls=3)
        summary = billing.get_vendor_summary("alibaba", period="all")
        assert summary.total_cost == 6.0
        assert summary.total_tokens == 300
        assert summary.total_calls == 6
        assert summary.agent_count == 3

    def test_vendor_summary_period_month(
        self,
        billing: VendorBilling,
        ecosystem_with_vendors: VendorEcosystem,
    ) -> None:
        """月度周期筛选。"""
        ecosystem_with_vendors.register_agent_to_vendor("agent-a", "alibaba")
        billing.record_usage("agent-a", cost=5.0)
        summary = billing.get_vendor_summary("alibaba", period="month")
        assert summary.total_cost == 5.0
        assert summary.period == "month"

    def test_vendor_summary_empty(
        self,
        billing: VendorBilling,
    ) -> None:
        """无用量记录的厂商账单为零。"""
        summary = billing.get_vendor_summary("alibaba", period="all")
        assert summary.total_cost == 0.0
        assert summary.total_tokens == 0
        assert summary.total_calls == 0
        assert summary.agent_count == 0

    def test_agent_breakdown(
        self,
        billing: VendorBilling,
        ecosystem_with_vendors: VendorEcosystem,
    ) -> None:
        """厂商下各 Agent 用量明细与百分比。"""
        for agent in ["agent-a", "agent-b"]:
            ecosystem_with_vendors.register_agent_to_vendor(agent, "alibaba")
        billing.record_usage("agent-a", cost=3.0)
        billing.record_usage("agent-b", cost=1.0)
        breakdown = billing.get_agent_breakdown("alibaba", period="all")
        assert len(breakdown) == 2
        # 按成本降序
        assert breakdown[0].agent_name == "agent-a"
        assert breakdown[0].cost == 3.0
        assert breakdown[0].percentage == 75.0
        assert breakdown[1].agent_name == "agent-b"
        assert breakdown[1].cost == 1.0
        assert breakdown[1].percentage == 25.0

    def test_agent_breakdown_empty(self, billing: VendorBilling) -> None:
        """无用量记录的厂商明细为空。"""
        assert billing.get_agent_breakdown("alibaba") == []

    def test_set_and_check_budget(
        self,
        billing: VendorBilling,
        ecosystem_with_vendors: VendorEcosystem,
    ) -> None:
        """设置预算并检查未超支。"""
        ecosystem_with_vendors.register_agent_to_vendor("agent-a", "alibaba")
        billing.record_usage("agent-a", cost=30.0)
        billing.set_vendor_budget("alibaba", 100.0)
        status = billing.check_vendor_budget("alibaba")
        assert status.budget == 100.0
        assert status.used == 30.0
        assert status.remaining == 70.0
        assert status.exceeded is False

    def test_budget_exceeded(
        self,
        billing: VendorBilling,
        ecosystem_with_vendors: VendorEcosystem,
    ) -> None:
        """超支检测。"""
        ecosystem_with_vendors.register_agent_to_vendor("agent-a", "alibaba")
        billing.record_usage("agent-a", cost=150.0)
        billing.set_vendor_budget("alibaba", 100.0)
        status = billing.check_vendor_budget("alibaba")
        assert status.exceeded is True
        assert status.remaining < 0

    def test_budget_not_set(self, billing: VendorBilling) -> None:
        """未设置预算时 exceeded 为 False。"""
        status = billing.check_vendor_budget("alibaba")
        assert status.budget == 0.0
        assert status.exceeded is False

    def test_set_negative_budget_raises(self, billing: VendorBilling) -> None:
        """负数预算抛出 ValueError。"""
        with pytest.raises(ValueError, match="不能为负数"):
            billing.set_vendor_budget("alibaba", -10.0)


# ════════════════════════════════════════════════════════════════
# 线程安全测试
# ════════════════════════════════════════════════════════════════


class TestThreadSafety:
    """多线程并发安全测试。"""

    @pytest.mark.timeout(240)
    def test_concurrent_register_vendors(self, tmp_path: Path) -> None:
        """并发注册厂商不丢数据。"""
        eco = VendorEcosystem(db_path=tmp_path / "vendor.db")
        errors: list[Exception] = []

        def worker(idx: int) -> None:
            try:
                eco.register_vendor(
                    Vendor(name=f"vendor-{idx}", display_name=f"厂商{idx}")
                )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        join_all(threads, 120.0)

        assert errors == []
        assert len(eco.list_vendors()) == 20

    @pytest.mark.timeout(240)
    def test_concurrent_record_usage(
        self,
        billing: VendorBilling,
        ecosystem_with_vendors: VendorEcosystem,
    ) -> None:
        """并发记录用量聚合正确。"""
        ecosystem_with_vendors.register_agent_to_vendor("agent-a", "alibaba")
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(10):
                    billing.record_usage("agent-a", cost=1.0, tokens=10, calls=1)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        join_all(threads, 120.0)

        assert errors == []
        summary = billing.get_vendor_summary("alibaba", period="all")
        # 5 线程 × 10 次 × cost=1.0 = 50.0
        assert summary.total_cost == 50.0
        assert summary.total_calls == 50