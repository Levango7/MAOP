"""TenantIsolation 白盒测试（maop.core.agent.ops.tenant_isolation）.

覆盖：Tenant/TenantQuota/TenantUsage 构造、租户 CRUD、Agent 分配、
额度管理、访问检查、使用统计、max_agents 上限、禁用租户、线程安全。
每个测试使用 tmp_path 隔离 SQLite。

注意：本文件测试的是 ``maop.core.agent.ops.tenant_isolation`` 模块，
与现有 ``tests/test_tenant_isolation.py``（测试 ``maop.core.tenant``）
正交，不修改现有文件。
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

import pytest

from maop.core.agent.ops.tenant_isolation import (
    Tenant,
    TenantIsolation,
    TenantQuota,
    TenantUsage,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def iso(tmp_path: Path) -> TenantIsolation:
    """使用隔离 DB 的 TenantIsolation。"""
    return TenantIsolation(db_path=tmp_path / "tenant_ops.db")


@pytest.fixture
def tenant_id() -> str:
    """生成一个 UUID 租户 ID。"""
    return str(uuid.uuid4())


@pytest.fixture
def tenant(tenant_id: str) -> Tenant:
    """构造一个基础租户。"""
    return Tenant(
        tenant_id=tenant_id,
        name="acme",
        display_name="ACME Corp",
        max_agents=5,
        monthly_budget=100.0,
    )


# ── 1. TestTenantModel ─────────────────────────────────────────


class TestTenantModel:
    """Tenant / TenantQuota / TenantUsage 构造。"""

    def test_tenant_default_values(self, tenant_id: str) -> None:
        """Tenant 默认值。"""
        t = Tenant(tenant_id=tenant_id, name="x")
        assert t.display_name == ""
        assert t.max_agents == 0
        assert t.monthly_budget == 0.0
        assert t.enabled is True
        assert t.metadata == {}

    def test_tenant_full_construction(self, tenant_id: str) -> None:
        """Tenant 完整构造。"""
        t = Tenant(
            tenant_id=tenant_id,
            name="acme",
            display_name="ACME",
            max_agents=10,
            monthly_budget=500.0,
            enabled=False,
            metadata={"region": "cn"},
        )
        assert t.name == "acme"
        assert t.max_agents == 10
        assert t.enabled is False
        assert t.metadata == {"region": "cn"}

    def test_tenant_quota_construction(self, tenant_id: str) -> None:
        """TenantQuota 构造。"""
        q = TenantQuota(
            tenant_id=tenant_id,
            agent_name="claude",
            quota_limit=1000,
        )
        assert q.quota_used == 0
        assert q.reset_period == "monthly"

    def test_tenant_usage_construction(self) -> None:
        """TenantUsage 构造。"""
        u = TenantUsage(agent_name="claude")
        assert u.calls == 0
        assert u.cost == 0.0
        assert u.quota_used == 0
        assert u.quota_limit == 0

    def test_mutable_defaults_not_shared(self, tenant_id: str) -> None:
        """可变默认值不应共享。"""
        t1 = Tenant(tenant_id=tenant_id, name="a")
        t2 = Tenant(tenant_id=str(uuid.uuid4()), name="b")
        t1.metadata["k"] = "v"
        assert t2.metadata == {}


# ── 2. TestTenantCRUD ──────────────────────────────────────────


class TestTenantCRUD:
    """租户 CRUD。"""

    def test_create_and_get_tenant(
        self, iso: TenantIsolation, tenant: Tenant,
    ) -> None:
        """创建并获取租户。"""
        iso.create_tenant(tenant)
        got = iso.get_tenant(tenant.tenant_id)
        assert got is not None
        assert got.name == tenant.name
        assert got.display_name == tenant.display_name
        assert got.max_agents == tenant.max_agents

    def test_get_nonexistent_tenant(self, iso: TenantIsolation) -> None:
        """获取不存在的租户返回 None。"""
        assert iso.get_tenant("nonexistent") is None

    def test_list_tenants(self, iso: TenantIsolation, tenant_id: str) -> None:
        """列出所有租户。"""
        t1 = Tenant(tenant_id=tenant_id, name="alpha")
        t2 = Tenant(tenant_id=str(uuid.uuid4()), name="beta")
        iso.create_tenant(t1)
        iso.create_tenant(t2)
        tenants = iso.list_tenants()
        assert len(tenants) == 2
        names = {t.name for t in tenants}
        assert names == {"alpha", "beta"}

    def test_create_tenant_upsert(
        self, iso: TenantIsolation, tenant_id: str,
    ) -> None:
        """重复创建同 ID 租户应 upsert。"""
        t1 = Tenant(tenant_id=tenant_id, name="v1")
        iso.create_tenant(t1)
        t2 = Tenant(tenant_id=tenant_id, name="v2", display_name="Version 2")
        iso.create_tenant(t2)
        got = iso.get_tenant(tenant_id)
        assert got is not None
        assert got.name == "v2"
        assert got.display_name == "Version 2"


# ── 3. TestAgentAssignment ─────────────────────────────────────


class TestAgentAssignment:
    """Agent 分配。"""

    def test_assign_and_get_agents(
        self, iso: TenantIsolation, tenant: Tenant,
    ) -> None:
        """分配 Agent 并获取列表。"""
        iso.create_tenant(tenant)
        iso.assign_agent_to_tenant(tenant.tenant_id, "claude-code")
        iso.assign_agent_to_tenant(tenant.tenant_id, "aider")
        agents = iso.get_tenant_agents(tenant.tenant_id)
        assert agents == ["aider", "claude-code"]  # 排序

    def test_assign_to_nonexistent_tenant_raises(
        self, iso: TenantIsolation,
    ) -> None:
        """分配到不存在的租户应抛异常。"""
        with pytest.raises(ValueError, match="tenant not found"):
            iso.assign_agent_to_tenant("nonexistent", "agent")

    def test_assign_idempotent(
        self, iso: TenantIsolation, tenant: Tenant,
    ) -> None:
        """重复分配同一 Agent 应幂等。"""
        iso.create_tenant(tenant)
        iso.assign_agent_to_tenant(tenant.tenant_id, "claude")
        iso.assign_agent_to_tenant(tenant.tenant_id, "claude")
        agents = iso.get_tenant_agents(tenant.tenant_id)
        assert agents == ["claude"]

    def test_max_agents_limit(
        self, iso: TenantIsolation, tenant_id: str,
    ) -> None:
        """max_agents 上限应生效。"""
        tenant = Tenant(tenant_id=tenant_id, name="limited", max_agents=2)
        iso.create_tenant(tenant)
        iso.assign_agent_to_tenant(tenant_id, "a1")
        iso.assign_agent_to_tenant(tenant_id, "a2")
        with pytest.raises(ValueError, match="max_agents"):
            iso.assign_agent_to_tenant(tenant_id, "a3")


# ── 4. TestQuota ───────────────────────────────────────────────


class TestQuota:
    """额度管理。"""

    def test_set_and_get_quota(
        self, iso: TenantIsolation, tenant: Tenant,
    ) -> None:
        """设置并获取额度。"""
        iso.create_tenant(tenant)
        quota = TenantQuota(
            tenant_id=tenant.tenant_id,
            agent_name="claude",
            quota_limit=1000,
        )
        iso.set_tenant_quota(tenant.tenant_id, "claude", quota)
        got = iso.get_tenant_quota(tenant.tenant_id, "claude")
        assert got is not None
        assert got.quota_limit == 1000
        assert got.quota_used == 0

    def test_get_nonexistent_quota(
        self, iso: TenantIsolation, tenant_id: str,
    ) -> None:
        """获取不存在的额度返回 None。"""
        assert iso.get_tenant_quota(tenant_id, "agent") is None


# ── 5. TestAccessCheck ─────────────────────────────────────────


class TestAccessCheck:
    """访问检查。"""

    def test_check_access_allowed(
        self, iso: TenantIsolation, tenant: Tenant,
    ) -> None:
        """已分配且启用 → 允许访问。"""
        iso.create_tenant(tenant)
        iso.assign_agent_to_tenant(tenant.tenant_id, "claude")
        assert iso.check_tenant_access(tenant.tenant_id, "claude") is True

    def test_check_access_unassigned_agent(
        self, iso: TenantIsolation, tenant: Tenant,
    ) -> None:
        """未分配的 Agent → 拒绝访问。"""
        iso.create_tenant(tenant)
        assert iso.check_tenant_access(tenant.tenant_id, "unknown") is False

    def test_check_access_disabled_tenant(
        self, iso: TenantIsolation, tenant_id: str,
    ) -> None:
        """禁用租户 → 拒绝访问。"""
        tenant = Tenant(tenant_id=tenant_id, name="disabled", enabled=False)
        iso.create_tenant(tenant)
        # 禁用租户不能分配 agent（会抛异常），所以先启用分配再禁用
        tenant_enabled = Tenant(tenant_id=tenant_id, name="disabled", enabled=True)
        iso.create_tenant(tenant_enabled)
        iso.assign_agent_to_tenant(tenant_id, "claude")
        # 再禁用
        tenant_disabled = Tenant(tenant_id=tenant_id, name="disabled", enabled=False)
        iso.create_tenant(tenant_disabled)
        assert iso.check_tenant_access(tenant_id, "claude") is False

    def test_check_access_quota_exhausted(
        self, iso: TenantIsolation, tenant: Tenant,
    ) -> None:
        """额度耗尽 → 拒绝访问。"""
        iso.create_tenant(tenant)
        iso.assign_agent_to_tenant(tenant.tenant_id, "claude")
        iso.set_tenant_quota(
            tenant.tenant_id, "claude",
            TenantQuota(
                tenant_id=tenant.tenant_id,
                agent_name="claude",
                quota_limit=100,
                quota_used=100,  # 已用完
            ),
        )
        assert iso.check_tenant_access(tenant.tenant_id, "claude") is False

    def test_check_access_nonexistent_tenant(self, iso: TenantIsolation) -> None:
        """不存在的租户 → 拒绝访问。"""
        assert iso.check_tenant_access("nonexistent", "agent") is False


# ── 6. TestUsage ───────────────────────────────────────────────


class TestUsage:
    """使用情况。"""

    def test_record_and_get_usage(
        self, iso: TenantIsolation, tenant: Tenant,
    ) -> None:
        """记录并获取使用情况。"""
        iso.create_tenant(tenant)
        iso.assign_agent_to_tenant(tenant.tenant_id, "claude")
        iso.set_tenant_quota(
            tenant.tenant_id, "claude",
            TenantQuota(
                tenant_id=tenant.tenant_id,
                agent_name="claude",
                quota_limit=1000,
            ),
        )
        iso.record_usage(tenant.tenant_id, "claude", calls=5, cost=0.5, quota_used=50)
        iso.record_usage(tenant.tenant_id, "claude", calls=3, cost=0.3, quota_used=30)
        usages = iso.get_tenant_usage(tenant.tenant_id)
        assert len(usages) == 1
        u = usages[0]
        assert u.agent_name == "claude"
        assert u.calls == 8
        assert u.cost == pytest.approx(0.8)
        assert u.quota_used == 80

    def test_get_usage_empty(
        self, iso: TenantIsolation, tenant: Tenant,
    ) -> None:
        """无使用记录返回空列表。"""
        iso.create_tenant(tenant)
        assert iso.get_tenant_usage(tenant.tenant_id) == []


# ── 7. TestThreadSafety ─────────────────────────────────────────


class TestThreadSafety:
    """线程安全测试。"""

    def test_concurrent_create_tenant(self, iso: TenantIsolation) -> None:
        """并发创建租户不抛异常。"""
        errors: list[Exception] = []

        def _run(i: int) -> None:
            try:
                t = Tenant(
                    tenant_id=str(uuid.uuid4()),
                    name=f"tenant-{i}",
                )
                iso.create_tenant(t)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_run, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(iso.list_tenants()) == 10

    def test_concurrent_assign_agent(
        self, iso: TenantIsolation, tenant_id: str,
    ) -> None:
        """并发分配 Agent 不抛异常。"""
        tenant = Tenant(tenant_id=tenant_id, name="conc", max_agents=0)
        iso.create_tenant(tenant)
        errors: list[Exception] = []

        def _run(i: int) -> None:
            try:
                iso.assign_agent_to_tenant(tenant_id, f"agent-{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_run, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        agents = iso.get_tenant_agents(tenant_id)
        assert len(agents) == 10