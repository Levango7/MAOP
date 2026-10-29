"""企业版 tenant / rbac / quotas 三条路由的线缆契约。

为什么需要这一层：``test_enterprise_response_models.py`` 只断言 OpenAPI 里
"有 schema"，不断言 schema 与服务层**实际返回的键**一致。``response_model``
的两种静默失效正好都藏在这个缝里——多出来的键被悄悄丢掉，必填键缺失则 500。
所以这里把三条路由跑起来，逐条比对信封与条目的键集合。

叠三件替身，各自的理由不同：

* **管理器替身**（tenant / rbac / quota）：真实 ``QuotaManager`` 等在商业 overlay
  里，纯净 CI 装不上。替身只实现 router 用到的那几个方法，服务层、路由层、
  ``response_model`` 全是真代码。
* **枚举替身**（``TenantStatus`` / ``Role`` / ``Permission``）：handler 在**函数体内**
  导入 overlay，CI 下连 200 都拿不到。仅在真模块不可导入时注入，装了企业包的
  机器走真模块，字段一致性由 ``test_mirrors_match_the_overlay_models`` 对账。
* **DB 路径替身**：``quota_audit`` 会写变更历史，落真实 ``data/maop.db`` 会污染
  开发机并让断言依赖历史数据。改指 ``tmp_path``，代码路径仍是生产那条。

本文件不得出现 overlay 的导入路径字面量：``tests/conftest.py`` 的旧判据按文本
匹配，纯净 CI 会把整份文件静默移出收集——那样这批契约一次都不会跑。
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from enum import Enum
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from maop.dashboard.routers import quotas as quotas_router
from maop.dashboard.routers import quotas_schemas, rbac_schemas, tenant_schemas
from maop.dashboard.routers import rbac as rbac_router
from maop.dashboard.routers import tenant as tenant_router
from maop.dashboard.services import billing_service, quota_audit, rbac_service, tenant_service
from tests.dashboard_app_profiles import OVERLAY_IMPORT, enterprise_app

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ALLOWLIST = FIXTURES / "enterprise_response_model_allowlist.txt"

#: (METHOD, PATH) of the 17 endpoints this batch modelled.
MODELLED = (
    ("GET", "/api/tenant/list"),
    ("POST", "/api/tenant/create"),
    ("POST", "/api/tenant/{tenant_id}/suspend"),
    ("POST", "/api/tenant/{tenant_id}/activate"),
    ("DELETE", "/api/tenant/{tenant_id}"),
    ("GET", "/api/rbac/grants"),
    ("POST", "/api/rbac/grant"),
    ("POST", "/api/rbac/revoke"),
    ("GET", "/api/rbac/roles"),
    ("GET", "/api/rbac/permissions"),
    ("GET", "/api/quotas/history"),
    ("POST", "/api/quotas/alerts/{alert_id}/resolve"),
    ("GET", "/api/quotas/{tenant_id}/alerts"),
    ("GET", "/api/quotas/{tenant_id}/usage"),
    ("GET", "/api/quotas/overview"),
    ("GET", "/api/quotas/{tenant_id}/trend"),
    ("POST", "/api/quotas/{tenant_id}/{resource}"),
)

TENANT = {
    "tenant_id": "t1",
    "name": "Acme",
    "status": "active",
    "plan": "pro",
    "quota": {
        "max_api_calls_per_day": 1,
        "max_storage_mb": 2,
        "max_agents": 3,
        "max_concurrent_tasks": 4,
        "max_users": 5,
    },
    "created_at": 1.0,
    "updated_at": 2.0,
    "expires_at": None,
    "metadata": {"region": "cn"},
}
TENANT_USAGE = {
    "api_calls_today": 7,
    "storage_mb": 1.5,
    "active_agents": 2,
    "concurrent_tasks": 1,
    "active_users": 3,
}
GRANT = {
    "user_id": "u1",
    "role": "viewer",
    "tenant_id": "t1",
    "granted_by": "admin",
    "granted_at": 3.0,
    "expires_at": None,
}
QUOTA = {
    "tenant_id": "t1",
    "resource": "api_calls",
    "hard_limit": 100,
    "soft_limit": 80,
    "period": "total",
    "created_at": 4.0,
    "updated_at": 5.0,
}
USAGE_ROW = {
    "tenant_id": "t1",
    "resource": "api_calls",
    "period": "total",
    "used": 10,
    "hard_limit": 100,
    "soft_limit": 80,
    "remaining": 90,
    "exceeded_soft": False,
    "exceeded_hard": False,
}
ALERT = {
    "alert_id": "a1",
    "tenant_id": "t1",
    "resource": "api_calls",
    "alert_type": "soft_limit",
    "current_value": 81,
    "limit_value": 80,
    "severity": "warning",
    "message": "over soft limit",
    "created_at": 6.0,
    "resolved": False,
    "resolved_at": None,
}


class _Dump:
    """Stand-in for the overlay's pydantic models.

    服务层既用 ``model_dump()`` 也直接读属性（``t.tenant_id``），所以两样都要给。
    """

    def __init__(self, data: dict) -> None:
        self._data = data

    def model_dump(self) -> dict:
        return dict(self._data)

    def __getattr__(self, item: str) -> object:
        try:
            return self._data[item]
        except KeyError:
            raise AttributeError(item) from None


class _FakeTenantManager:
    def list_tenants(self, status: object = None) -> list[_Dump]:
        return [_Dump(TENANT)]

    def get_usage(self, tenant_id: str) -> _Dump:
        return _Dump(TENANT_USAGE)

    def create_tenant(self, tenant_id: str, name: str, plan: str = "starter", quota: object = None) -> _Dump:
        return _Dump({**TENANT, "tenant_id": tenant_id, "name": name, "plan": plan})

    def get_tenant(self, tenant_id: str) -> _Dump:
        return _Dump(TENANT)

    def suspend_tenant(self, tenant_id: str) -> bool:
        return True

    def activate_tenant(self, tenant_id: str) -> bool:
        return True

    def delete_tenant(self, tenant_id: str) -> bool:
        return True


class _FakeRbacManager:
    def list_grants(self, user_id: str = "", tenant_id: str = "") -> list[_Dump]:
        return [_Dump(GRANT)]

    def grant_role(self, user_id: str, role: object, *, granted_by: str = "", tenant_id: str = "") -> _Dump:
        return _Dump({**GRANT, "user_id": user_id, "role": getattr(role, "value", role)})

    def revoke_role(self, user_id: str, role: object, tenant_id: str = "") -> bool:
        return True

    def user_roles(self, user_id: str) -> list[object]:
        return []

    def user_permissions(self, user_id: str) -> set[object]:
        return set()


class _FakeQuotaManager:
    def get_quota(self, tenant_id: str, resource: str) -> _Dump | None:
        # 返回 None = 该资源还没有配额，这样 set_quota 会真的写出一条变更历史。
        return None

    def set_quota(self, tenant_id: str, resource: str, hard_limit: int, *, soft_limit: int = 0, period: str = "total") -> _Dump:
        return _Dump({**QUOTA, "tenant_id": tenant_id, "resource": resource, "hard_limit": hard_limit})

    def list_alerts(self, tenant_id: str, resolved: bool | None = False, limit: int = 100, offset: int = 0) -> list[_Dump]:
        return [_Dump(ALERT)]

    def list_usage(self, tenant_id: str) -> list[_Dump]:
        return [_Dump(USAGE_ROW)]

    def resolve_alert(self, alert_id: str) -> bool:
        return True


# ── overlay stand-ins (only installed when the real package is absent) ──


class _StandInTenantStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIAL = "trial"
    TERMINATED = "terminated"


class _StandInTenantQuota(BaseModel):
    max_api_calls_per_day: int = 10000
    max_storage_mb: int = 5120
    max_agents: int = 50
    max_concurrent_tasks: int = 10
    max_users: int = 20


class _StandInRole(str, Enum):
    SUPERADMIN = "superadmin"
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class _StandInPermission(str, Enum):
    AGENTS_READ = "agents:read"
    TENANT_READ = "tenant:read"


_STAND_IN_CACHE: dict[str, types.ModuleType] = {}


def _stand_in(name: str) -> types.ModuleType:
    if name in _STAND_IN_CACHE:
        return _STAND_IN_CACHE[name]
    module = types.ModuleType(name)
    if name.endswith(".tenant"):
        module.TenantStatus = _StandInTenantStatus
        module.TenantQuota = _StandInTenantQuota
    elif name.endswith(".rbac"):
        module.Role = _StandInRole
        module.Permission = _StandInPermission
        module.ROLE_PERMISSIONS = {_StandInRole.VIEWER: frozenset({_StandInPermission.AGENTS_READ})}
    else:  # pragma: no cover - programming error
        raise AssertionError(name)
    _STAND_IN_CACHE[name] = module
    return module


def _overlay_present() -> bool:
    try:
        return importlib.util.find_spec(OVERLAY_IMPORT) is not None
    except (ImportError, ValueError):
        return False


@pytest.fixture
def _prepared_app(monkeypatch, tmp_path):
    for module in (tenant_router, rbac_router):
        monkeypatch.setattr(module, "has_feature", lambda *a, **k: True)
        monkeypatch.setattr(module, "require_admin", lambda *a, **k: None)
    monkeypatch.setattr(quotas_router, "require_admin", lambda *a, **k: None)
    monkeypatch.setattr(billing_service, "has_feature", lambda *a, **k: True)

    db_file = tmp_path / "data" / "maop.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(billing_service, "unified_db_path", lambda: str(db_file))
    monkeypatch.setattr(quota_audit, "unified_db_path", lambda: str(db_file))

    monkeypatch.setattr(tenant_service, "_tenant_manager", _FakeTenantManager())
    monkeypatch.setattr(rbac_service, "_rbac_manager", _FakeRbacManager())
    monkeypatch.setattr(billing_service, "_quota_manager", _FakeQuotaManager())

    if not _overlay_present():
        for suffix in (".tenant", ".rbac"):
            monkeypatch.setitem(sys.modules, OVERLAY_IMPORT + suffix, _stand_in(OVERLAY_IMPORT + suffix))

    return enterprise_app()


@pytest.fixture
def client(_prepared_app):
    return TestClient(_prepared_app)


def _fields(model: type[BaseModel]) -> set[str]:
    return set(model.model_fields)


# ── 1. OpenAPI: every modelled route declares a schema ─────────────


def test_every_modelled_route_is_typed_in_openapi() -> None:
    ops = enterprise_app().openapi()["paths"]
    untyped = []
    for method, path in MODELLED:
        op = ops.get(path, {}).get(method.lower())
        assert op is not None, f"{method} {path} missing from OpenAPI"
        schema = ((op.get("responses", {}).get("200") or {}).get("content") or {}).get(
            "application/json", {}
        ).get("schema") or {}
        if "$ref" not in schema:
            untyped.append(f"{method} {path}")
    assert untyped == [], f"routes still untyped: {untyped}"


def test_no_modelled_route_is_still_allowlisted() -> None:
    text = ALLOWLIST.read_text(encoding="utf-8")
    placeholders = ("{tenant_id}", "{alert_id}", "{resource}")
    stale = []
    for method, path in MODELLED:
        fixture_line = f"{method} {path}"
        for placeholder in placeholders:
            fixture_line = fixture_line.replace(placeholder, "{}")
        if fixture_line in text:
            stale.append(fixture_line)
    assert stale == [], f"already typed but still in the burn-down list: {stale}"


# ── 1b. 生产端对账：服务层吐出的键 vs 镜像模型 ────────────────────


def test_service_payloads_fit_the_mirror_models(client) -> None:
    """``response_model`` 会把未声明的键**悄悄丢掉**，线缆断言看不见这种漂移。

    所以这里绕开 FastAPI，直接拿服务层（真代码）的返回值与镜像模型逐键比。
    服务端哪天多吐一个键而模型没跟上，这条就会红 —— 否则前端永远等不到那个键，
    而所有门禁都是绿的。
    """
    listed = tenant_service.list_tenants()
    assert set(listed) == _fields(tenant_schemas.TenantListEnvelope)
    assert set(listed["tenants"][0]) == _fields(tenant_schemas.TenantListItem)
    assert set(listed["tenants"][0]["quota"]) == _fields(tenant_schemas.TenantQuota)
    assert set(listed["tenants"][0]["usage"]) == _fields(tenant_schemas.TenantUsage)

    created = tenant_service.create_tenant("t2", "Beta", "pro", 10, 20)
    assert set(created) == _fields(tenant_schemas.TenantEnvelope)
    assert set(created["tenant"]) == _fields(tenant_schemas.TenantItem)

    for fn, model, flag in (
        (tenant_service.suspend_tenant, tenant_schemas.TenantEnvelopeSuspended, "suspended"),
        (tenant_service.activate_tenant, tenant_schemas.TenantEnvelopeActivated, "activated"),
        (tenant_service.delete_tenant, tenant_schemas.TenantEnvelopeDeleted, "deleted"),
    ):
        payload = fn("t1")
        assert set(payload) == _fields(model)
        assert payload[flag] is True

    assert {"status", *billing_service.list_quota_history()} == _fields(quotas_schemas.QuotaHistoryEnvelope)
    billing_service.set_quota("t1", "api_calls", 100)
    recorded = billing_service.list_quota_history()["history"]
    assert recorded and set(recorded[0]) == _fields(quotas_schemas.QuotaHistoryItem)

    assert set(billing_service.set_quota("t1", "api_calls", 100)) == _fields(quotas_schemas.QuotaItem)
    assert set(billing_service.list_quota_alerts("t1")[0]) == _fields(quotas_schemas.QuotaAlertItem)
    assert set(billing_service.list_quota_usage("t1")[0]) == _fields(quotas_schemas.QuotaUsageItem)

    overview = quota_audit.quota_overview(tenant_service.list_tenants()["tenants"])
    assert {"status", *overview} == _fields(quotas_schemas.QuotaOverviewEnvelope)
    assert set(overview["allocation"]) == _fields(quotas_schemas.QuotaAllocation)

    trend = quota_audit.usage_trend("t1", hours=3)
    assert {"status", "tenant_id", *trend} == _fields(quotas_schemas.QuotaTrendEnvelope)


# ── 2. 线缆形状：信封信封与条目逐键比对 ───────────────────────────


def test_tenant_list_wire_shape(client) -> None:
    body = client.get("/api/tenant/list").json()
    assert set(body) == _fields(tenant_schemas.TenantListEnvelope)
    assert body["status"] == "ok"
    assert body["count"] == 1
    assert set(body["tenants"][0]) == _fields(tenant_schemas.TenantListItem)
    assert set(body["tenants"][0]["quota"]) == _fields(tenant_schemas.TenantQuota)
    assert set(body["tenants"][0]["usage"]) == _fields(tenant_schemas.TenantUsage)


def test_tenant_create_wire_shape(client) -> None:
    body = client.post("/api/tenant/create", json={"tenant_id": "t2", "name": "Beta"}).json()
    assert set(body) == _fields(tenant_schemas.TenantEnvelope)
    assert set(body["tenant"]) == _fields(tenant_schemas.TenantItem)
    # 单条端点不带 usage —— 列表行才带，两个模型是故意分开的。
    assert "usage" not in body["tenant"]


@pytest.mark.parametrize(
    ("method", "path", "field", "model"),
    [
        ("post", "/api/tenant/t1/suspend", "suspended", tenant_schemas.TenantEnvelopeSuspended),
        ("post", "/api/tenant/t1/activate", "activated", tenant_schemas.TenantEnvelopeActivated),
        ("delete", "/api/tenant/t1", "deleted", tenant_schemas.TenantEnvelopeDeleted),
    ],
)
def test_tenant_lifecycle_wire_shape(client, method, path, field, model) -> None:
    body = getattr(client, method)(path).json()
    assert set(body) == _fields(model)
    assert body == {"status": "ok", field: True}


def test_rbac_grants_wire_shape(client) -> None:
    body = client.get("/api/rbac/grants").json()
    assert set(body) == _fields(rbac_schemas.GrantListEnvelope)
    assert set(body["grants"][0]) == _fields(rbac_schemas.RoleGrantItem)
    assert body["count"] == 1


def test_rbac_grant_and_revoke_wire_shape(client) -> None:
    granted = client.post("/api/rbac/grant", json={"user_id": "u1", "role": "viewer"}).json()
    assert set(granted) == _fields(rbac_schemas.GrantEnvelope)
    assert set(granted["grant"]) == _fields(rbac_schemas.RoleGrantItem)

    revoked = client.post("/api/rbac/revoke", json={"user_id": "u1", "role": "viewer"}).json()
    assert set(revoked) == _fields(rbac_schemas.RevokeEnvelope)
    assert revoked == {"status": "ok", "revoked": True}


def test_rbac_roles_wire_shape(client) -> None:
    body = client.get("/api/rbac/roles").json()
    assert set(body) == _fields(rbac_schemas.RoleListEnvelope)
    assert body["roles"], "expected at least one role"
    assert set(body["roles"][0]) == _fields(rbac_schemas.RoleItem)


def test_rbac_permissions_wire_shape(client) -> None:
    body = client.get("/api/rbac/permissions").json()
    assert set(body) == _fields(rbac_schemas.PermissionListEnvelope)
    assert body["permissions"], "expected at least one permission"
    assert set(body["permissions"][0]) == _fields(rbac_schemas.PermissionItem)


def test_quota_history_wire_shape(client) -> None:
    body = client.get("/api/quotas/history").json()
    assert set(body) == _fields(quotas_schemas.QuotaHistoryEnvelope)
    # 空库时只有信封，没有条目可断言；改写一条再读回。
    client.post("/api/quotas/t1/api_calls", json={"hard_limit": 100})
    body = client.get("/api/quotas/history").json()
    assert body["history"], "quota change was not recorded"
    assert set(body["history"][0]) == _fields(quotas_schemas.QuotaHistoryItem)
    assert body["count"] == len(body["history"])


def test_quota_alerts_wire_shape(client) -> None:
    body = client.get("/api/quotas/t1/alerts").json()
    assert set(body) == _fields(quotas_schemas.QuotaAlertListEnvelope)
    assert set(body["alerts"][0]) == _fields(quotas_schemas.QuotaAlertItem)

    resolved = client.post("/api/quotas/alerts/a1/resolve").json()
    assert set(resolved) == _fields(quotas_schemas.QuotaAlertResolvedEnvelope)
    assert resolved == {"status": "ok", "resolved": True}


def test_quota_usage_wire_shape(client) -> None:
    body = client.get("/api/quotas/t1/usage").json()
    assert set(body) == _fields(quotas_schemas.QuotaUsageListEnvelope)
    assert set(body["usages"][0]) == _fields(quotas_schemas.QuotaUsageItem)


def test_quota_overview_wire_shape(client) -> None:
    body = client.get("/api/quotas/overview").json()
    assert set(body) == _fields(quotas_schemas.QuotaOverviewEnvelope)
    assert set(body["allocation"]) == _fields(quotas_schemas.QuotaAllocation)


def test_quota_trend_wire_shape(client) -> None:
    body = client.get("/api/quotas/t1/trend").json()
    assert set(body) == _fields(quotas_schemas.QuotaTrendEnvelope)
    assert body["tenant_id"] == "t1"
    assert len(body["labels"]) == body["hours"]
    for series in body["series"]:
        assert set(series) == _fields(quotas_schemas.QuotaTrendSeries)
        assert len(series["data"]) == body["hours"]


def test_quota_set_wire_shape(client) -> None:
    body = client.post("/api/quotas/t1/api_calls", json={"hard_limit": 100}).json()
    assert set(body) == _fields(quotas_schemas.QuotaEnvelope)
    assert set(body["quota"]) == _fields(quotas_schemas.QuotaItem)


def test_missing_required_payload_key_is_a_500_not_a_silent_empty(
    _prepared_app, monkeypatch,
) -> None:
    """反向守卫：服务少给一个必填键时，模型没有默认值可补 —— 应当是 500。

    这条钉的是 response_model 的第二种静默失效方向（必填键缺失被当成空值）。
    若哪天有人给条目模型的必填键补上默认值，这条会变绿，从而暴露"空值冒充
    数据"的风险。
    """
    incomplete = {k: v for k, v in QUOTA.items() if k != "resource"}

    class _BrokenManager(_FakeQuotaManager):
        def set_quota(self, *a, **k):
            return _Dump(incomplete)

    monkeypatch.setattr(billing_service, "_quota_manager", _BrokenManager())
    lenient = TestClient(_prepared_app, raise_server_exceptions=False)
    assert lenient.post("/api/quotas/t1/api_calls", json={"hard_limit": 100}).status_code == 500


# ── 3. 镜像 vs 企业包模型（装了包才跑）────────────────────────────


@pytest.mark.skipif(not _overlay_present(), reason="commercial overlay not installed (ADR-017 second repo)")
def test_mirrors_match_the_overlay_models() -> None:
    tenant_mod = importlib.import_module(OVERLAY_IMPORT + ".tenant")
    rbac_mod = importlib.import_module(OVERLAY_IMPORT + ".rbac")
    quota_mod = importlib.import_module(OVERLAY_IMPORT + ".quota")

    pairs = [
        (tenant_schemas.TenantQuota, tenant_mod.TenantQuota),
        (tenant_schemas.TenantUsage, tenant_mod.TenantUsage),
        (tenant_schemas.TenantItem, tenant_mod.Tenant),
        (rbac_schemas.RoleGrantItem, rbac_mod.RoleGrant),
        (quotas_schemas.QuotaItem, quota_mod.QuotaResponse),
        (quotas_schemas.QuotaUsageItem, quota_mod.UsageResponse),
        (quotas_schemas.QuotaAlertItem, quota_mod.QuotaAlertResponse),
    ]
    mismatched = {
        mine.__name__: {
            "missing": sorted(set(theirs.model_fields) - set(mine.model_fields)),
            "extra": sorted(set(mine.model_fields) - set(theirs.model_fields)),
        }
        for mine, theirs in pairs
        if set(mine.model_fields) != set(theirs.model_fields)
    }
    assert mismatched == {}, mismatched


def test_this_file_stays_collectible() -> None:
    """旧收集判据按文本匹配：出现 overlay 导入路径字面量 ⇒ 纯净 CI 整份被丢。"""
    markers = (OVERLAY_IMPORT, OVERLAY_IMPORT.replace(".", "-"))
    source = Path(__file__).read_text(encoding="utf-8")
    assert [m for m in markers if m in source] == []
