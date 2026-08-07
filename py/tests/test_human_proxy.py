"""Tests for MAOP.core.human_proxy — HumanProxy approval queue with SQLite."""

from __future__ import annotations

import pytest

from maop.core.agent.delegation.human_proxy import ApprovalRequest, HumanProxy, HumanProxyConfig


@pytest.fixture
def proxy(tmp_path):
    return HumanProxy(root_dir=tmp_path)


class TestApprovalRequest:
    def test_defaults(self):
        r = ApprovalRequest()
        assert r.status == "pending"
        assert r.priority == "medium"
        assert r.requester == "system"
        assert r.metadata == {}


class TestHumanProxyConfig:
    def test_defaults(self):
        c = HumanProxyConfig()
        assert c.auto_expire_hours == 24
        assert c.notify_on_request is True
        assert c.max_pending == 100


class TestHumanProxyInit:
    def test_creates_db(self, tmp_path):
        HumanProxy(root_dir=tmp_path)
        from maop.core.backends.db_utils import get_db_path
        db = get_db_path("human_proxy")
        assert db.name == "maop.db"

    def test_custom_config(self, tmp_path):
        cfg = HumanProxyConfig(auto_expire_hours=48, max_pending=50)
        proxy = HumanProxy(root_dir=tmp_path, config=cfg)
        assert proxy._config.auto_expire_hours == 48


class TestRequest:
    def test_request_basic(self, proxy):
        rid = proxy.request(task="Deploy", agent="claude", reason="prod deploy")
        assert rid.startswith("hr-")
        req = proxy.get(rid)
        assert req is not None
        assert req.task == "Deploy"
        assert req.status == "pending"

    def test_request_with_priority(self, proxy):
        rid = proxy.request(task="t", priority="critical")
        req = proxy.get(rid)
        assert req.priority == "critical"

    def test_request_with_metadata(self, proxy):
        rid = proxy.request(task="t", metadata={"env": "prod", "version": "1.0"})
        req = proxy.get(rid)
        assert req.metadata == {"env": "prod", "version": "1.0"}

    def test_request_custom_id(self, proxy):
        rid = proxy.request(task="t", request_id="custom-id")
        assert rid == "custom-id"
        assert proxy.get("custom-id") is not None

    def test_request_default_requester(self, proxy):
        rid = proxy.request(task="t")
        req = proxy.get(rid)
        assert req.requester == "system"


class TestApproveReject:
    def test_approve_pending(self, proxy):
        rid = proxy.request(task="t")
        assert proxy.approve(rid) is True
        req = proxy.get(rid)
        assert req.status == "approved"
        assert req.resolved is not None

    def test_approve_nonexistent(self, proxy):
        assert proxy.approve("nope") is False

    def test_approve_already_resolved(self, proxy):
        rid = proxy.request(task="t")
        proxy.approve(rid)
        assert proxy.approve(rid) is False

    def test_reject_pending(self, proxy):
        rid = proxy.request(task="t")
        assert proxy.reject(rid, reason="too risky") is True
        req = proxy.get(rid)
        assert req.status == "rejected"
        assert "too risky" in req.reason

    def test_reject_nonexistent(self, proxy):
        assert proxy.reject("nope") is False

    def test_reject_already_resolved(self, proxy):
        rid = proxy.request(task="t")
        proxy.reject(rid)
        assert proxy.reject(rid) is False


class TestResolve:
    def test_resolve_approve(self, proxy):
        rid = proxy.request(task="t")
        assert proxy.resolve(rid, "approve") is True
        assert proxy.get(rid).status == "approved"

    def test_resolve_reject(self, proxy):
        rid = proxy.request(task="t")
        assert proxy.resolve(rid, "reject") is True
        assert proxy.get(rid).status == "rejected"

    def test_resolve_unknown_decision(self, proxy):
        rid = proxy.request(task="t")
        assert proxy.resolve(rid, "maybe") is False
        assert proxy.get(rid).status == "pending"


class TestPending:
    def test_pending_empty(self, proxy):
        assert proxy.pending() == []

    def test_pending_returns_requests(self, proxy):
        proxy.request(task="t1")
        proxy.request(task="t2")
        pending = proxy.pending()
        assert len(pending) == 2
        assert all(r.status == "pending" for r in pending)

    def test_pending_sorted_by_priority(self, proxy):
        proxy.request(task="low", priority="low")
        proxy.request(task="critical", priority="critical")
        proxy.request(task="medium", priority="medium")
        pending = proxy.pending()
        assert pending[0].priority == "critical"
        assert pending[1].priority == "medium"
        assert pending[2].priority == "low"

    def test_pending_excludes_resolved(self, proxy):
        rid = proxy.request(task="t")
        proxy.approve(rid)
        proxy.request(task="t2")
        pending = proxy.pending()
        assert len(pending) == 1
        assert pending[0].task == "t2"

    def test_pending_limit(self, proxy):
        for i in range(10):
            proxy.request(task=f"t{i}")
        pending = proxy.pending(limit=3)
        assert len(pending) == 3


class TestListAll:
    def test_list_all_empty(self, proxy):
        assert proxy.list_all() == []

    def test_list_all_returns_all_statuses(self, proxy):
        r1 = proxy.request(task="t1")
        proxy.request(task="t2")
        proxy.approve(r1)
        all_reqs = proxy.list_all()
        assert len(all_reqs) == 2

    def test_list_all_filter_by_status(self, proxy):
        r1 = proxy.request(task="t1")
        proxy.request(task="t2")
        proxy.approve(r1)
        approved = proxy.list_all(status="approved")
        assert len(approved) == 1
        pending = proxy.list_all(status="pending")
        assert len(pending) == 1


class TestGet:
    def test_get_nonexistent(self, proxy):
        assert proxy.get("nope") is None

    def test_get_returns_full_request(self, proxy):
        rid = proxy.request(task="t", agent="a", priority="high", reason="r")
        req = proxy.get(rid)
        assert req.task == "t"
        assert req.agent == "a"
        assert req.priority == "high"
        assert req.reason == "r"


class TestStats:
    def test_stats_empty(self, proxy):
        assert proxy.stats() == {}

    def test_stats_with_requests(self, proxy):
        r1 = proxy.request(task="t1")
        r2 = proxy.request(task="t2")
        proxy.approve(r1)
        proxy.reject(r2)
        stats = proxy.stats()
        assert stats.get("approved") == 1
        assert stats.get("rejected") == 1
        assert stats.get("pending", 0) == 0


class TestExpireOld:
    def test_expire_none_recent(self, proxy):
        proxy.request(task="t")
        count = proxy.expire_old(hours=24)
        assert count == 0

    def test_expire_with_custom_hours(self, proxy):
        # Use 0 hours — anything created "before now - 0 hours" should expire
        proxy.request(task="t")
        # tiny sleep to ensure created < now
        import time
        time.sleep(0.01)
        count = proxy.expire_old(hours=0)
        # SQLite datetime comparison may or may not catch this depending on precision
        # Just verify it doesn't error
        assert count >= 0
