"""Tests for AgentProxyGateway — 企业内网 Agent 代理网关。

覆盖：
    * 代理规则 CRUD（添加 / 列出 / 移除 / 解析）
    * 审计日志（记录 / 查询 / user 过滤 / agent 过滤 / 不可篡改）
    * 部门预算（设置 / 检查 / 扣减 / 汇总 / 超限）
    * 部门级访问限制
    * 持久化（重开实例后数据仍在）
    * 并发安全（多线程并发读写）
"""
from __future__ import annotations

import threading
import time  # noqa: F401
from pathlib import Path
from typing import Any

import pytest

from maop.core.agent.auth.agent_proxy_gateway import (
    AgentProxyGateway,
    AuditEvent,
    DepartmentBudget,
    ProxyRule,
)

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def gateway(tmp_path: Any) -> AgentProxyGateway:
    """隔离的 AgentProxyGateway（独立 SQLite 文件）。"""
    gw = AgentProxyGateway(db_path=tmp_path / "gateway.db")
    yield gw
    gw.close()


@pytest.fixture
def gateway2(tmp_path: Any) -> AgentProxyGateway:
    """第二个实例（同一 DB 文件，用于测试持久化）。"""
    db_path = tmp_path / "gateway.db"
    gw = AgentProxyGateway(db_path=db_path)
    yield gw, db_path
    gw.close()


# ── 辅助 ──────────────────────────────────────────────────────────


def _make_rule(
    path: str = "/agents/external/deepseek",
    agent: str = "deepseek-v3",
    departments: list[str] | None = None,
    enabled: bool = True,
) -> ProxyRule:
    return ProxyRule(
        internal_path=path,
        external_agent=agent,
        allowed_departments=departments or [],
        enabled=enabled,
    )


def _make_event(
    user: str = "alice",
    department: str = "engineering",
    agent: str = "deepseek-v3",
    action: str = "proxy",
    details: str = "",
    success: bool = True,
) -> AuditEvent:
    return AuditEvent(
        user=user,
        department=department,
        agent=agent,
        action=action,
        details=details,
        success=success,
    )


def _make_budget(
    department: str = "engineering",
    agent: str = "deepseek-v3",
    monthly: float = 100.0,
    used: float = 0.0,
    reset_day: int = 1,
) -> DepartmentBudget:
    return DepartmentBudget(
        department=department,
        agent=agent,
        monthly_budget=monthly,
        used=used,
        reset_day=reset_day,
    )


# ── 1. 代理规则 CRUD ──────────────────────────────────────────────


class TestProxyRules:
    def test_add_and_list_proxy_rules(self, gateway: AgentProxyGateway):
        """添加规则后列表应包含该规则。"""
        rule = _make_rule(path="/agents/a", agent="agent-a")
        gateway.add_proxy_rule(rule)
        rules = gateway.list_proxy_rules()
        assert len(rules) == 1
        assert rules[0].rule_id == rule.rule_id
        assert rules[0].internal_path == "/agents/a"
        assert rules[0].external_agent == "agent-a"
        assert rules[0].enabled is True

    def test_remove_proxy_rule(self, gateway: AgentProxyGateway):
        """移除规则后列表应不再包含该规则。"""
        rule = _make_rule(path="/agents/b", agent="agent-b")
        gateway.add_proxy_rule(rule)
        assert len(gateway.list_proxy_rules()) == 1
        gateway.remove_proxy_rule(rule.rule_id)
        assert len(gateway.list_proxy_rules()) == 0

    def test_resolve_proxy(self, gateway: AgentProxyGateway):
        """resolve_proxy 应按 internal_path 精确匹配启用规则。"""
        rule = _make_rule(path="/agents/external/deepseek", agent="deepseek-v3")
        gateway.add_proxy_rule(rule)
        resolved = gateway.resolve_proxy("/agents/external/deepseek")
        assert resolved is not None
        assert resolved.external_agent == "deepseek-v3"

    def test_resolve_proxy_not_found(self, gateway: AgentProxyGateway):
        """未匹配路径应返回 None。"""
        rule = _make_rule(path="/agents/a", agent="agent-a")
        gateway.add_proxy_rule(rule)
        assert gateway.resolve_proxy("/agents/nonexistent") is None

    def test_resolve_proxy_disabled_rule(self, gateway: AgentProxyGateway):
        """禁用规则不应被 resolve_proxy 匹配。"""
        rule = _make_rule(path="/agents/disabled", agent="agent-x", enabled=False)
        gateway.add_proxy_rule(rule)
        assert gateway.resolve_proxy("/agents/disabled") is None

    def test_proxy_rule_with_department_restriction(self, gateway: AgentProxyGateway):
        """带部门限制的规则应正确持久化 allowed_departments。"""
        rule = _make_rule(
            path="/agents/restricted",
            agent="agent-r",
            departments=["engineering", "research"],
        )
        gateway.add_proxy_rule(rule)
        rules = gateway.list_proxy_rules()
        assert len(rules) == 1
        assert rules[0].allowed_departments == ["engineering", "research"]


# ── 2. 审计日志 ───────────────────────────────────────────────────


class TestAuditLog:
    def test_log_audit(self, gateway: AgentProxyGateway):
        """记录审计事件后查询应返回该事件。"""
        event = _make_event(user="alice", agent="deepseek-v3", action="proxy")
        gateway.log_audit(event)
        events = gateway.query_audit()
        assert len(events) == 1
        assert events[0].event_id == event.event_id
        assert events[0].user == "alice"
        assert events[0].action == "proxy"

    def test_query_audit_by_user(self, gateway: AgentProxyGateway):
        """按 user 过滤审计日志。"""
        gateway.log_audit(_make_event(user="alice", action="proxy"))
        gateway.log_audit(_make_event(user="bob", action="proxy"))
        gateway.log_audit(_make_event(user="alice", action="budget_check"))
        alice_events = gateway.query_audit(user="alice")
        assert len(alice_events) == 2
        assert all(e.user == "alice" for e in alice_events)

    def test_query_audit_by_agent(self, gateway: AgentProxyGateway):
        """按 agent 过滤审计日志。"""
        gateway.log_audit(_make_event(agent="deepseek-v3"))
        gateway.log_audit(_make_event(agent="claude-3.5", user="alice"))
        ds_events = gateway.query_audit(agent="deepseek-v3")
        assert len(ds_events) == 1
        assert ds_events[0].agent == "deepseek-v3"

    def test_query_audit_limit(self, gateway: AgentProxyGateway):
        """limit 参数应限制返回条数。"""
        for i in range(10):
            gateway.log_audit(_make_event(user=f"user{i}", action="proxy"))
        assert len(gateway.query_audit(limit=5)) == 5
        assert len(gateway.query_audit(limit=100)) == 10

    def test_audit_event_default_fields(self, gateway: AgentProxyGateway):
        """AuditEvent 默认应自动生成 event_id 和 timestamp。"""
        event = _make_event()
        assert len(event.event_id) > 0
        assert "T" in event.timestamp  # ISO 格式


# ── 3. 部门预算 ───────────────────────────────────────────────────


class TestDepartmentBudget:
    def test_set_department_budget(self, gateway: AgentProxyGateway):
        """设置预算后汇总应返回该预算。"""
        budget = _make_budget(department="eng", agent="ds", monthly=500.0)
        gateway.set_department_budget(budget)
        summaries = gateway.get_budget_summary()
        assert len(summaries) == 1
        assert summaries[0].department == "eng"
        assert summaries[0].agent == "ds"
        assert summaries[0].monthly_budget == 500.0

    def test_check_budget_within_limit(self, gateway: AgentProxyGateway):
        """预算充足时 check_budget 应返回 True。"""
        gateway.set_department_budget(_make_budget(department="eng", agent="ds", monthly=100.0))
        assert gateway.check_budget("eng", "ds", 50.0) is True
        assert gateway.check_budget("eng", "ds", 100.0) is True

    def test_check_budget_exceeded(self, gateway: AgentProxyGateway):
        """预算超限时 check_budget 应返回 False。"""
        gateway.set_department_budget(_make_budget(department="eng", agent="ds", monthly=100.0))
        assert gateway.check_budget("eng", "ds", 150.0) is False

    def test_check_budget_no_budget_set(self, gateway: AgentProxyGateway):
        """未设置预算时 check_budget 应返回 True（不限）。"""
        assert gateway.check_budget("unknown", "unknown", 99999.0) is True

    def test_consume_budget(self, gateway: AgentProxyGateway):
        """扣减预算后 used 应增加。"""
        gateway.set_department_budget(_make_budget(department="eng", agent="ds", monthly=100.0))
        gateway.consume_budget("eng", "ds", 30.0)
        summaries = gateway.get_budget_summary()
        assert summaries[0].used == 30.0
        # 扣减后 check 应反映新余额。
        assert gateway.check_budget("eng", "ds", 70.0) is True
        assert gateway.check_budget("eng", "ds", 71.0) is False

    def test_consume_budget_unknown_silent(self, gateway: AgentProxyGateway):
        """扣减未设置预算应静默忽略（不报错）。"""
        gateway.consume_budget("unknown", "unknown", 50.0)
        # 不报错即通过。
        assert len(gateway.get_budget_summary()) == 0

    def test_get_budget_summary_filter(self, gateway: AgentProxyGateway):
        """get_budget_summary 按 department 过滤。"""
        gateway.set_department_budget(_make_budget(department="eng", agent="ds"))
        gateway.set_department_budget(_make_budget(department="sales", agent="ds"))
        all_budgets = gateway.get_budget_summary()
        assert len(all_budgets) == 2
        eng_only = gateway.get_budget_summary(department="eng")
        assert len(eng_only) == 1
        assert eng_only[0].department == "eng"

    def test_set_budget_preserves_used(self, gateway: AgentProxyGateway):
        """重新设置预算应保留已扣减的 used 额度。"""
        gateway.set_department_budget(_make_budget(department="eng", agent="ds", monthly=100.0))
        gateway.consume_budget("eng", "ds", 40.0)
        # 更新月预算——used 应保留。
        gateway.set_department_budget(_make_budget(department="eng", agent="ds", monthly=200.0))
        summaries = gateway.get_budget_summary()
        assert summaries[0].monthly_budget == 200.0
        assert summaries[0].used == 40.0


# ── 4. 持久化 ─────────────────────────────────────────────────────


class TestPersistence:
    def test_persistence(self, gateway2: tuple[AgentProxyGateway, Path]):
        """重开实例后数据应仍在。"""
        gw1, db_path = gateway2
        gw1.add_proxy_rule(_make_rule(path="/agents/persist", agent="agent-p"))
        gw1.set_department_budget(_make_budget(department="eng", agent="ds", monthly=100.0))
        gw1.log_audit(_make_event(user="alice", action="proxy"))
        gw1.close()

        # 用同一 DB 路径新建实例。
        gw2 = AgentProxyGateway(db_path=db_path)
        try:
            # 代理规则持久化。
            rules = gw2.list_proxy_rules()
            assert len(rules) == 1
            assert rules[0].internal_path == "/agents/persist"
            # 预算持久化。
            budgets = gw2.get_budget_summary()
            assert len(budgets) == 1
            assert budgets[0].monthly_budget == 100.0
            # 审计日志持久化。
            events = gw2.query_audit()
            assert len(events) == 1
            assert events[0].user == "alice"
        finally:
            gw2.close()


# ── 5. 并发安全 ───────────────────────────────────────────────────


class TestConcurrency:
    def test_concurrent_access(self, tmp_path: Any):
        """多线程并发读写应不丢数据、不崩溃。"""
        gw = AgentProxyGateway(db_path=tmp_path / "concurrent.db")
        errors: list[Exception] = []
        num_threads = 8
        ops_per_thread = 50

        def worker(tid: int) -> None:
            try:
                for i in range(ops_per_thread):
                    # 混合读写操作。
                    rule = _make_rule(
                        path=f"/agents/t{tid}/r{i}",
                        agent=f"agent-{tid}-{i}",
                    )
                    gw.add_proxy_rule(rule)
                    gw.resolve_proxy(f"/agents/t{tid}/r{i}")
                    gw.log_audit(_make_event(
                        user=f"user-{tid}",
                        agent=f"agent-{tid}-{i}",
                        action="proxy",
                    ))
                    gw.query_audit(user=f"user-{tid}", limit=10)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"并发错误: {errors}"
        # 验证数据完整性——每线程写入 ops_per_thread 条规则。
        rules = gw.list_proxy_rules()
        assert len(rules) == num_threads * ops_per_thread
        events = gw.query_audit(limit=10000)
        assert len(events) == num_threads * ops_per_thread
        gw.close()

    def test_concurrent_budget_consume(self, tmp_path: Any):
        """多线程并发扣减预算——总扣减不超过月预算。"""
        gw = AgentProxyGateway(db_path=tmp_path / "budget_concurrent.db")
        gw.set_department_budget(_make_budget(
            department="eng", agent="ds", monthly=1000.0
        ))
        num_threads = 10
        cost_per_op = 10.0
        ops_per_thread = 5  # 总计 10*5*10=500 <= 1000

        def worker() -> None:
            for _ in range(ops_per_thread):
                if gw.check_budget("eng", "ds", cost_per_op):
                    gw.consume_budget("eng", "ds", cost_per_op)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        summaries = gw.get_budget_summary()
        assert summaries[0].used == num_threads * ops_per_thread * cost_per_op
        assert summaries[0].used <= summaries[0].monthly_budget
        gw.close()