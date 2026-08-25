"""Tests for maop.enterprise.quota — QuotaManager + middleware + router.

覆盖 PRD ``docs/prd-tenant-quota.md`` 的核心场景:
  - 配额 CRUD (set/get/list/update/delete)
  - 使用量更新 (increment/set/reset) + 缓存失效
  - 配额检查: 软限制告警 / 硬限制拒绝 / fail-open / consume
  - 告警管理 (list/resolve/dedup)
  - 中间件: 路径映射 / 429 拒绝 / fail-open / 软限制警告头
  - API 路由: 全端点 smoke test
"""

from __future__ import annotations

from pathlib import Path

import pytest

# P2 修复：无条件 skip 改为 import 条件化 —— maop.enterprise 可导入
# （企业版）时才真正运行测试；个人版（未安装）时才跳过。
try:
    import maop.enterprise  # noqa: F401
except ImportError:
    pytest.skip(reason="maop.enterprise 未发布", allow_module_level=True)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from maop.enterprise.quota import (
    QuotaCheckResult,
    QuotaCreate,
    QuotaManager,
    QuotaUpdate,
    UsageResponse,
)

# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def enterprise_mode():
    """Enable enterprise edition so require_feature(FeatureFlag.TENANT_ISOLATION) passes."""
    from maop.config.edition import Edition, reset_edition, set_edition
    set_edition(Edition.ENTERPRISE)
    yield
    reset_edition()


@pytest.fixture(autouse=True)
def _no_pg_backend(monkeypatch):
    """Force SQLite (no PostgreSQL backend) for consistent test behavior."""
    monkeypatch.delenv("MAOP_STORAGE_BACKEND", raising=False)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return an isolated SQLite db path under tmp_path/data."""
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "maop.db"


@pytest.fixture
def qm(db_path: Path) -> QuotaManager:
    """QuotaManager instance backed by tmp_path SQLite."""
    return QuotaManager(db_path, cache_ttl_s=0.0)  # cache_ttl=0 → always miss → deterministic


# ── Pydantic 模型 ─────────────────────────────────────────────────────


class TestPydanticModels:
    def test_quota_create_defaults(self):
        q = QuotaCreate(tenant_id="t1", resource="api_calls", hard_limit=100)
        assert q.soft_limit == 0
        assert q.period == "total"

    def test_quota_create_validation(self):
        with pytest.raises(ValueError):
            QuotaCreate(tenant_id="", resource="api_calls", hard_limit=100)
        with pytest.raises(ValueError):
            QuotaCreate(tenant_id="t1", resource="api_calls", hard_limit=-1)
        with pytest.raises(ValueError):
            QuotaCreate(
                tenant_id="t1", resource="api_calls",
                hard_limit=100, period="weekly",
            )

    def test_quota_update_all_none(self):
        u = QuotaUpdate()
        assert u.hard_limit is None
        assert u.soft_limit is None
        assert u.period is None

    def test_usage_response_unlimited(self):
        u = UsageResponse(
            tenant_id="t1", resource="api_calls", period="total",
            used=999, hard_limit=0, soft_limit=0,
            remaining=-1, exceeded_soft=False, exceeded_hard=False,
        )
        assert u.remaining == -1

    def test_quota_check_result_defaults(self):
        r = QuotaCheckResult(allowed=True)
        assert r.reason == ""
        assert r.warning == ""
        assert r.alert_id == ""


# ── QuotaManager: 配额 CRUD ───────────────────────────────────────────


class TestQuotaCRUD:
    def test_set_and_get_quota(self, qm: QuotaManager):
        q = qm.set_quota("t1", "api_calls", 1000, soft_limit=800, period="daily")
        assert q.hard_limit == 1000
        assert q.soft_limit == 800
        assert q.period == "daily"
        got = qm.get_quota("t1", "api_calls")
        assert got is not None
        assert got.hard_limit == 1000

    def test_get_quota_missing(self, qm: QuotaManager):
        assert qm.get_quota("t1", "api_calls") is None

    def test_list_quotas_empty(self, qm: QuotaManager):
        assert qm.list_quotas("t1") == []

    def test_list_quotas_multiple(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 1000)
        qm.set_quota("t1", "storage_mb", 5000)
        qm.set_quota("t1", "agents", 50)
        names = {q.resource for q in qm.list_quotas("t1")}
        assert names == {"api_calls", "storage_mb", "agents"}

    def test_set_quota_overwrite(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 1000)
        qm.set_quota("t1", "api_calls", 2000, soft_limit=1500)
        got = qm.get_quota("t1", "api_calls")
        assert got is not None
        assert got.hard_limit == 2000
        assert got.soft_limit == 1500

    def test_set_quota_soft_exceeds_hard_gets_capped(self, qm: QuotaManager):
        """soft_limit > hard_limit 时自动校正为 hard_limit."""
        q = qm.set_quota("t1", "api_calls", 100, soft_limit=200)
        assert q.soft_limit == 100  # capped to hard_limit

    def test_set_quota_unlimited(self, qm: QuotaManager):
        """hard_limit=0 表示不限, soft_limit 不被校正."""
        q = qm.set_quota("t1", "api_calls", 0, soft_limit=100)
        assert q.hard_limit == 0
        assert q.soft_limit == 100

    def test_update_quota_partial(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 1000, soft_limit=800, period="daily")
        q = qm.update_quota("t1", "api_calls", soft_limit=900)
        assert q.hard_limit == 1000  # unchanged
        assert q.soft_limit == 900
        assert q.period == "daily"  # unchanged

    def test_update_quota_not_found(self, qm: QuotaManager):
        with pytest.raises(KeyError):
            qm.update_quota("t1", "api_calls", hard_limit=100)

    def test_delete_quota(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 1000)
        assert qm.delete_quota("t1", "api_calls") is True
        assert qm.get_quota("t1", "api_calls") is None
        assert qm.delete_quota("t1", "api_calls") is False  # already deleted

    def test_set_quota_invalid_args(self, qm: QuotaManager):
        with pytest.raises(ValueError):
            qm.set_quota("t1", "api_calls", -1)
        with pytest.raises(ValueError):
            qm.set_quota("t1", "api_calls", 100, soft_limit=-1)
        with pytest.raises(ValueError):
            qm.set_quota("t1", "api_calls", 100, period="weekly")


# ── QuotaManager: 使用量 ──────────────────────────────────────────────


class TestUsage:
    def test_get_usage_no_quota(self, qm: QuotaManager):
        """无配额设置时返回 hard_limit=0(不限)."""
        u = qm.get_usage("t1", "api_calls")
        assert u.used == 0
        assert u.hard_limit == 0
        assert u.exceeded_hard is False
        assert u.remaining == -1

    def test_update_usage_increment(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 1000)
        used = qm.update_usage("t1", "api_calls", 50)
        assert used == 50
        used = qm.update_usage("t1", "api_calls", 30)
        assert used == 80

    def test_update_usage_negative(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 1000)
        qm.update_usage("t1", "api_calls", 100)
        used = qm.update_usage("t1", "api_calls", -30)
        assert used == 70

    def test_update_usage_zero_noop(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 1000)
        used = qm.update_usage("t1", "api_calls", 0)
        assert used == 0

    def test_set_usage_absolute(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 1000)
        qm.update_usage("t1", "api_calls", 100)
        used = qm.set_usage("t1", "api_calls", 50)
        assert used == 50

    def test_reset_usage_single_resource(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 1000)
        qm.set_quota("t1", "storage_mb", 5000)
        qm.update_usage("t1", "api_calls", 100)
        qm.update_usage("t1", "storage_mb", 200)
        deleted = qm.reset_usage("t1", "api_calls")
        assert deleted == 1
        assert qm.get_usage("t1", "api_calls").used == 0
        assert qm.get_usage("t1", "storage_mb").used == 200

    def test_reset_usage_all_resources(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 1000)
        qm.set_quota("t1", "storage_mb", 5000)
        qm.update_usage("t1", "api_calls", 100)
        qm.update_usage("t1", "storage_mb", 200)
        deleted = qm.reset_usage("t1")
        assert deleted == 2

    def test_list_usage(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 1000)
        qm.set_quota("t1", "storage_mb", 5000)
        usages = qm.list_usage("t1")
        assert len(usages) == 2
        resources = {u.resource for u in usages}
        assert resources == {"api_calls", "storage_mb"}

    def test_usage_exceeded_flags(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 100, soft_limit=80)
        qm.set_usage("t1", "api_calls", 50)
        u = qm.get_usage("t1", "api_calls")
        assert u.exceeded_soft is False
        assert u.exceeded_hard is False

        qm.set_usage("t1", "api_calls", 85)
        u = qm.get_usage("t1", "api_calls")
        assert u.exceeded_soft is True
        assert u.exceeded_hard is False

        qm.set_usage("t1", "api_calls", 100)
        u = qm.get_usage("t1", "api_calls")
        assert u.exceeded_soft is True
        assert u.exceeded_hard is True

    def test_daily_period_uses_date_key(self, qm: QuotaManager):
        """daily period 的使用量按日期隔离."""
        qm.set_quota("t1", "api_calls", 100, period="daily")
        qm.update_usage("t1", "api_calls", 50, period="daily")
        u = qm.get_usage("t1", "api_calls")
        assert u.used == 50
        assert u.period == "daily"


# ── QuotaManager: 配额检查 ────────────────────────────────────────────


class TestCheckQuota:
    def test_check_within_limits(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 100, soft_limit=80)
        r = qm.check_quota("t1", "api_calls", amount=10)
        assert r.allowed is True
        assert r.warning == ""
        assert r.reason == ""

    def test_check_soft_limit_exceeded_warns_but_allows(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 100, soft_limit=80)
        qm.set_usage("t1", "api_calls", 75)
        r = qm.check_quota("t1", "api_calls", amount=10)
        # 75 + 10 = 85 > 80 (soft) but <= 100 (hard)
        assert r.allowed is True
        assert r.warning != ""
        assert "soft" in r.warning.lower() or "approaching" in r.warning.lower()
        assert r.alert_id != ""  # alert recorded

    def test_check_hard_limit_exceeded_denies(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 100, soft_limit=80)
        qm.set_usage("t1", "api_calls", 95)
        r = qm.check_quota("t1", "api_calls", amount=10)
        # 95 + 10 = 105 > 100 (hard)
        assert r.allowed is False
        assert "exceeded" in r.reason.lower()
        assert r.alert_id != ""

    def test_check_at_hard_limit_denies(self, qm: QuotaManager):
        """used == hard_limit 时,任何 amount 都应拒绝."""
        qm.set_quota("t1", "api_calls", 100)
        qm.set_usage("t1", "api_calls", 100)
        r = qm.check_quota("t1", "api_calls", amount=1)
        assert r.allowed is False

    def test_check_no_quota_fail_open(self, qm: QuotaManager):
        """无配额设置(hard_limit=0) → 放行(fail-open)."""
        r = qm.check_quota("t1", "api_calls", amount=1)
        assert r.allowed is True
        assert r.reason == ""

    def test_check_unknown_resource_fail_open(self, qm: QuotaManager):
        """未知资源(无配额) → 放行."""
        r = qm.check_quota("t1", "unknown_resource", amount=999999)
        assert r.allowed is True

    def test_check_unlimited_quota(self, qm: QuotaManager):
        """hard_limit=0 表示不限 → 放行任意 amount."""
        qm.set_quota("t1", "api_calls", 0)
        r = qm.check_quota("t1", "api_calls", amount=999999)
        assert r.allowed is True

    def test_check_no_soft_limit(self, qm: QuotaManager):
        """soft_limit=0 时不触发软限制告警."""
        qm.set_quota("t1", "api_calls", 100, soft_limit=0)
        qm.set_usage("t1", "api_calls", 90)
        r = qm.check_quota("t1", "api_calls", amount=5)
        assert r.allowed is True
        assert r.warning == ""


# ── QuotaManager: consume ─────────────────────────────────────────────


class TestConsume:
    def test_consume_within_limits(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 100)
        r = qm.consume("t1", "api_calls", amount=10)
        assert r.allowed is True
        assert qm.get_usage("t1", "api_calls").used == 10

    def test_consume_at_hard_limit_not_recorded(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 100)
        qm.set_usage("t1", "api_calls", 95)
        r = qm.consume("t1", "api_calls", amount=10)
        assert r.allowed is False
        # 拒绝时不记录消费
        assert qm.get_usage("t1", "api_calls").used == 95

    def test_consume_soft_limit_records_usage(self, qm: QuotaManager):
        """软限制放行时仍记录使用量."""
        qm.set_quota("t1", "api_calls", 100, soft_limit=80)
        qm.set_usage("t1", "api_calls", 75)
        r = qm.consume("t1", "api_calls", amount=10)
        assert r.allowed is True
        assert qm.get_usage("t1", "api_calls").used == 85

    def test_consume_daily_period(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 100, period="daily")
        r = qm.consume("t1", "api_calls", amount=10)
        assert r.allowed is True
        assert qm.get_usage("t1", "api_calls").used == 10
        assert qm.get_usage("t1", "api_calls").period == "daily"


# ── QuotaManager: 告警 ────────────────────────────────────────────────


class TestAlerts:
    def test_alert_recorded_on_hard_exceed(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 100)
        qm.set_usage("t1", "api_calls", 95)
        qm.check_quota("t1", "api_calls", amount=10)
        alerts = qm.list_alerts("t1", resolved=False)
        assert len(alerts) == 1
        assert alerts[0].alert_type == "hard_exceeded"
        assert alerts[0].severity == "critical"

    def test_alert_recorded_on_soft_exceed(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 100, soft_limit=80)
        qm.set_usage("t1", "api_calls", 75)
        qm.check_quota("t1", "api_calls", amount=10)
        alerts = qm.list_alerts("t1", resolved=False)
        assert len(alerts) == 1
        assert alerts[0].alert_type == "soft_exceeded"
        assert alerts[0].severity == "warning"

    def test_alert_dedup(self, qm: QuotaManager):
        """同一 (tenant, resource, type) 在去重窗口内只记录一次."""
        qm.set_quota("t1", "api_calls", 100, soft_limit=80)
        qm.set_usage("t1", "api_calls", 75)
        qm.check_quota("t1", "api_calls", amount=10)  # first alert
        qm.check_quota("t1", "api_calls", amount=1)   # dedup'd
        alerts = qm.list_alerts("t1", resolved=False)
        assert len(alerts) == 1

    def test_list_alerts_resolved_filter(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 100)
        qm.set_usage("t1", "api_calls", 95)
        qm.check_quota("t1", "api_calls", amount=10)
        alerts_unresolved = qm.list_alerts("t1", resolved=False)
        assert len(alerts_unresolved) == 1
        alert_id = alerts_unresolved[0].alert_id

        assert qm.resolve_alert(alert_id) is True
        assert qm.list_alerts("t1", resolved=False) == []
        alerts_resolved = qm.list_alerts("t1", resolved=True)
        assert len(alerts_resolved) == 1

    def test_list_alerts_all(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 100)
        qm.set_usage("t1", "api_calls", 95)
        qm.check_quota("t1", "api_calls", amount=10)
        alerts = qm.list_alerts("t1", resolved=None)
        assert len(alerts) == 1

    def test_resolve_alert_not_found(self, qm: QuotaManager):
        assert qm.resolve_alert("nonexistent") is False

    def test_resolve_alert_already_resolved(self, qm: QuotaManager):
        qm.set_quota("t1", "api_calls", 100)
        qm.set_usage("t1", "api_calls", 95)
        qm.check_quota("t1", "api_calls", amount=10)
        alert_id = qm.list_alerts("t1")[0].alert_id
        assert qm.resolve_alert(alert_id) is True
        assert qm.resolve_alert(alert_id) is False  # already resolved

    def test_alerts_limit_offset(self, qm: QuotaManager):
        """告警列表支持 limit/offset 分页."""
        qm.set_quota("t1", "api_calls", 100, soft_limit=80)
        qm.set_usage("t1", "api_calls", 75)
        # 触发不同类型的告警(soft + hard)
        qm.check_quota("t1", "api_calls", amount=10)  # soft
        # 强制清除去重以记录第二个告警
        qm._alert_dedup.clear()
        qm.set_usage("t1", "api_calls", 95)
        qm.check_quota("t1", "api_calls", amount=10)  # hard
        all_alerts = qm.list_alerts("t1", resolved=None)
        assert len(all_alerts) == 2
        page = qm.list_alerts("t1", resolved=None, limit=1, offset=0)
        assert len(page) == 1


# ── QuotaManager: 缓存 ────────────────────────────────────────────────


class TestCache:
    def test_cache_hit(self, db_path: Path):
        """cache_ttl > 0 时,第二次读走缓存."""
        qm = QuotaManager(db_path, cache_ttl_s=60.0)
        qm.set_quota("t1", "api_calls", 100)
        u1 = qm.get_usage("t1", "api_calls")
        assert u1.hard_limit == 100
        assert qm.cache_size() == 1
        u2 = qm.get_usage("t1", "api_calls")
        assert u2.hard_limit == 100

    def test_cache_invalidate_on_set_quota(self, db_path: Path):
        qm = QuotaManager(db_path, cache_ttl_s=60.0)
        qm.set_quota("t1", "api_calls", 100)
        qm.get_usage("t1", "api_calls")
        assert qm.cache_size() == 1
        qm.set_quota("t1", "api_calls", 200)
        assert qm.cache_size() == 0

    def test_cache_invalidate_on_update_usage(self, db_path: Path):
        qm = QuotaManager(db_path, cache_ttl_s=60.0)
        qm.set_quota("t1", "api_calls", 100)
        qm.get_usage("t1", "api_calls")
        assert qm.cache_size() == 1
        qm.update_usage("t1", "api_calls", 10)
        assert qm.cache_size() == 0

    def test_cache_clear(self, db_path: Path):
        qm = QuotaManager(db_path, cache_ttl_s=60.0)
        qm.set_quota("t1", "api_calls", 100)
        qm.get_usage("t1", "api_calls")
        assert qm.cache_size() == 1
        qm.cache_clear()
        assert qm.cache_size() == 0


# ── QuotaMiddleware ───────────────────────────────────────────────────


class TestQuotaMiddleware:
    @pytest.fixture
    def app_with_mw(self, qm: QuotaManager) -> FastAPI:
        """FastAPI app with QuotaMiddleware + a dummy endpoint."""
        from maop.enterprise.quota_middleware import QuotaMiddleware
        app = FastAPI()

        @app.post("/api/agents/bot1/run")
        async def _run():
            return {"status": "ok"}

        @app.get("/api/health")
        async def _health():
            return {"status": "ok"}

        @app.post("/api/data/write")
        async def _write():
            return {"status": "ok"}

        app.add_middleware(QuotaMiddleware, quota_manager=qm, enabled=True)
        return app

    def test_public_path_bypasses(self, app_with_mw: FastAPI):
        client = TestClient(app_with_mw)
        r = client.get("/api/health")
        assert r.status_code == 200

    def test_get_method_bypasses(self, app_with_mw: FastAPI):
        """非写方法(GET)跳过配额检查."""
        client = TestClient(app_with_mw)
        # GET on a POST-only route → 405, but not 429
        r = client.get("/api/agents/bot1/run")
        assert r.status_code != 429

    def test_no_tenant_id_fail_open(self, app_with_mw: FastAPI):
        """无 tenant_id → fail-open 放行."""
        client = TestClient(app_with_mw)
        r = client.post("/api/agents/bot1/run")
        assert r.status_code == 200

    def test_hard_limit_denies_429(self, app_with_mw: FastAPI, qm: QuotaManager):
        """硬限制触发 → 429 + Retry-After."""
        qm.set_quota("t1", "api_calls", 1)
        qm.set_usage("t1", "api_calls", 1)
        # 模拟 AuthMiddleware 注入 tenant_id;
        # TestClient 不直接支持,我们用 middleware 注入.
        from fastapi import Request as _Req
        from starlette.middleware.base import BaseHTTPMiddleware

        class _InjectTenant(BaseHTTPMiddleware):
            async def dispatch(self, request: _Req, call_next):
                request.state.tenant_id = "t1"
                return await call_next(request)

        # 重新构建 app 以注入 tenant_id 在 QuotaMiddleware 之前
        from maop.enterprise.quota_middleware import QuotaMiddleware
        app2 = FastAPI()

        @app2.post("/api/agents/bot1/run")
        async def _run2():
            return {"status": "ok"}

        app2.add_middleware(QuotaMiddleware, quota_manager=qm, enabled=True)
        app2.add_middleware(_InjectTenant)
        client2 = TestClient(app2)
        r = client2.post("/api/agents/bot1/run")
        assert r.status_code == 429
        assert r.headers.get("Retry-After") == "60"
        assert r.headers.get("X-Quota-Resource") == "api_calls"
        assert "quota exceeded" in r.json()["error"].lower()

    def test_soft_limit_warns_but_allows(self, db_path: Path):
        """软限制触发 → 200 + X-Quota-Warning 头."""
        from fastapi import Request as _Req
        from maop.enterprise.quota_middleware import QuotaMiddleware
        from starlette.middleware.base import BaseHTTPMiddleware

        qm = QuotaManager(db_path, cache_ttl_s=0.0)
        qm.set_quota("t1", "api_calls", 100, soft_limit=80)
        qm.set_usage("t1", "api_calls", 80)  # used=80, amount=1 → projected=81 > 80 (soft)

        class _InjectTenant(BaseHTTPMiddleware):
            async def dispatch(self, request: _Req, call_next):
                request.state.tenant_id = "t1"
                return await call_next(request)

        app = FastAPI()

        @app.post("/api/agents/bot1/run")
        async def _run():
            return {"status": "ok"}

        app.add_middleware(QuotaMiddleware, quota_manager=qm, enabled=True)
        app.add_middleware(_InjectTenant)
        client = TestClient(app)
        r = client.post("/api/agents/bot1/run")
        assert r.status_code == 200
        assert "X-Quota-Warning" in r.headers

    def test_unmapped_path_bypasses(self, app_with_mw: FastAPI):
        """未映射的路径 → 不检查配额."""
        client = TestClient(app_with_mw)
        # /api/foo/bar 未在 path_patterns 中 → 放行
        r = client.post("/api/foo/bar")
        assert r.status_code in (200, 404)  # 404 if route not defined
        assert r.status_code != 429

    def test_disabled_middleware_noop(self, qm: QuotaManager):
        """enabled=False → 中间件 no-op."""
        from maop.enterprise.quota_middleware import QuotaMiddleware
        app = FastAPI()

        @app.post("/api/agents/bot1/run")
        async def _run():
            return {"status": "ok"}

        app.add_middleware(QuotaMiddleware, quota_manager=qm, enabled=False)
        client = TestClient(app)
        r = client.post("/api/agents/bot1/run")
        assert r.status_code == 200


# ── API 路由 smoke test ──────────────────────────────────────────────


class TestQuotaRouter:
    @pytest.fixture
    def client(self, qm: QuotaManager) -> TestClient:
        """TestClient with quotas router + admin role injected."""
        from maop.dashboard.routers import quotas as quotas_router
        # 注入 QuotaManager 单例
        quotas_router._quota_manager = qm
        app = FastAPI()
        app.include_router(quotas_router.router)

        # 注入 admin role 到 request.state
        from fastapi import Request as _Req
        from starlette.middleware.base import BaseHTTPMiddleware

        class _InjectAdmin(BaseHTTPMiddleware):
            async def dispatch(self, request: _Req, call_next):
                request.state.auth_roles = ["admin"]
                return await call_next(request)

        app.add_middleware(_InjectAdmin)
        return TestClient(app)

    def test_set_and_get_quota(self, client: TestClient):
        r = client.post("/api/quotas/t1/api_calls", json={
            "hard_limit": 1000, "soft_limit": 800, "period": "daily",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["quota"]["hard_limit"] == 1000

        r = client.get("/api/quotas/t1/api_calls")
        assert r.status_code == 200
        assert r.json()["quota"]["hard_limit"] == 1000

    def test_list_quotas(self, client: TestClient):
        client.post("/api/quotas/t1/api_calls", json={"hard_limit": 1000})
        client.post("/api/quotas/t1/storage_mb", json={"hard_limit": 5000})
        r = client.get("/api/quotas/t1")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 2

    def test_update_quota(self, client: TestClient):
        client.post("/api/quotas/t1/api_calls", json={"hard_limit": 1000})
        r = client.put("/api/quotas/t1/api_calls", json={"soft_limit": 900})
        assert r.status_code == 200
        assert r.json()["quota"]["soft_limit"] == 900
        assert r.json()["quota"]["hard_limit"] == 1000  # unchanged

    def test_delete_quota(self, client: TestClient):
        client.post("/api/quotas/t1/api_calls", json={"hard_limit": 1000})
        r = client.delete("/api/quotas/t1/api_calls")
        assert r.status_code == 200
        assert r.json()["deleted"] is True

    def test_get_quota_not_found(self, client: TestClient):
        r = client.get("/api/quotas/t1/api_calls")
        assert r.status_code == 404

    def test_usage_endpoints(self, client: TestClient):
        client.post("/api/quotas/t1/api_calls", json={"hard_limit": 1000})
        # increment
        r = client.post("/api/quotas/t1/api_calls/usage", json={"amount": 50})
        assert r.status_code == 200
        assert r.json()["used"] == 50
        # get
        r = client.get("/api/quotas/t1/api_calls/usage")
        assert r.status_code == 200
        assert r.json()["usage"]["used"] == 50
        # set absolute
        r = client.put("/api/quotas/t1/api_calls/usage", json={"value": 100})
        assert r.status_code == 200
        assert r.json()["used"] == 100
        # reset
        r = client.post("/api/quotas/t1/api_calls/reset-usage")
        assert r.status_code == 200

    def test_list_usage(self, client: TestClient):
        client.post("/api/quotas/t1/api_calls", json={"hard_limit": 1000})
        client.post("/api/quotas/t1/storage_mb", json={"hard_limit": 5000})
        r = client.get("/api/quotas/t1/usage")
        assert r.status_code == 200
        assert r.json()["count"] == 2

    def test_check_endpoint(self, client: TestClient):
        client.post("/api/quotas/t1/api_calls", json={
            "hard_limit": 100, "soft_limit": 80,
        })
        client.post("/api/quotas/t1/api_calls/usage", json={"amount": 75})
        r = client.post("/api/quotas/t1/api_calls/check", json={"amount": 10})
        assert r.status_code == 200
        result = r.json()["result"]
        assert result["allowed"] is True
        assert result["warning"] != ""

    def test_consume_endpoint(self, client: TestClient):
        client.post("/api/quotas/t1/api_calls", json={"hard_limit": 100})
        r = client.post("/api/quotas/t1/api_calls/consume", json={"amount": 10})
        assert r.status_code == 200
        assert r.json()["result"]["allowed"] is True
        # verify usage recorded
        r = client.get("/api/quotas/t1/api_calls/usage")
        assert r.json()["usage"]["used"] == 10

    def test_alerts_endpoint(self, client: TestClient):
        client.post("/api/quotas/t1/api_calls", json={"hard_limit": 100})
        client.post("/api/quotas/t1/api_calls/usage", json={"amount": 95})
        client.post("/api/quotas/t1/api_calls/check", json={"amount": 10})
        r = client.get("/api/quotas/t1/alerts")
        assert r.status_code == 200
        assert r.json()["count"] >= 1

    def test_resolve_alert_endpoint(self, client: TestClient):
        client.post("/api/quotas/t1/api_calls", json={"hard_limit": 100})
        client.post("/api/quotas/t1/api_calls/usage", json={"amount": 95})
        client.post("/api/quotas/t1/api_calls/check", json={"amount": 10})
        alerts = client.get("/api/quotas/t1/alerts").json()["alerts"]
        alert_id = alerts[0]["alert_id"]
        r = client.post(f"/api/quotas/alerts/{alert_id}/resolve")
        assert r.status_code == 200
        assert r.json()["resolved"] is True

    def test_alerts_filter_resolved(self, client: TestClient):
        client.post("/api/quotas/t1/api_calls", json={"hard_limit": 100})
        client.post("/api/quotas/t1/api_calls/usage", json={"amount": 95})
        client.post("/api/quotas/t1/api_calls/check", json={"amount": 10})
        # unresolved
        r = client.get("/api/quotas/t1/alerts?resolved=false")
        assert r.json()["count"] == 1
        # all
        r = client.get("/api/quotas/t1/alerts?resolved=all")
        assert r.json()["count"] == 1