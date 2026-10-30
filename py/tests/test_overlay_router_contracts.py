"""sso / n8n / licenses 三条 overlay 路由的线缆契约。

这三条路由模块级 import 商业 overlay（ADR-017 双仓），纯净 CI 里连导入都不行——
``tests/dashboard_app_profiles`` 因此用 AST 源码视图给棘轮兜底。本文件补的是棘轮
看不到的那一半：``response_model`` 会**悄悄丢掉未声明的键**，也会在必填键缺失时
直接 500，而 OpenAPI 只说明"有 schema"。所以这里把路由真跑一遍，逐条比对键集合。

叠两件替身，理由不同：

* **overlay 替身**（``sso_store`` / ``n8n`` / ``license_manager`` 三个模块）：仅在
  overlay 不可导入时注入，只为让 ``from <overlay>.<module> import ...`` 这一步过得去；
  被测的路由、service、``response_model`` 全是真代码。
* **管理器/客户端替身**（SSO registry、n8n client、LicenseManager）：真实实现会连
  n8n HTTP、写 SQLite、签 Ed25519。替身只实现 router 用到的方法，返回值形状照抄
  真实实现（字段一致性由 ``test_mirrors_match_the_overlay_models`` 对账）。

条目的三个模型开 ``extra="allow"``：形状持有者在另一个仓库（或干脆是 n8n 的
自由格式 JSON），严格建模会静默丢键。``test_unknown_upstream_keys_survive`` 钉住
这条性质——删掉 ``extra="allow"`` 它就会红。

本文件不得出现 overlay 的导入路径字面量：``tests/conftest.py`` 的收集判据按 AST
看模块级 import，而文本断言也保留着同一条纪律（见文件末尾）。
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing_extensions import Self

from maop.dashboard.routers import licenses_schemas, n8n_schemas, sso_schemas
from maop.dashboard.services import auth_service, billing_service
from tests._overlay_rule import needs_overlay
from tests.dashboard_app_profiles import OVERLAY_IMPORT

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ALLOWLIST = FIXTURES / "enterprise_response_model_allowlist.txt"

#: (METHOD, PATH) of the 14 endpoints this batch modelled.
MODELLED = (
    ("GET", "/api/sso/providers"),
    ("POST", "/api/sso/providers"),
    ("PUT", "/api/sso/providers/{provider_id}"),
    ("DELETE", "/api/sso/providers/{provider_id}"),
    ("POST", "/api/sso/providers/{provider_id}/test"),
    ("GET", "/api/n8n/workflows"),
    ("POST", "/api/n8n/workflows/{workflow_id}/trigger"),
    ("GET", "/api/n8n/executions/{execution_id}"),
    ("GET", "/api/n8n/health"),
    ("GET", "/api/licenses/list"),
    ("POST", "/api/licenses/create"),
    ("GET", "/api/licenses/{license_id}"),
    ("POST", "/api/licenses/{license_id}/renew"),
    ("POST", "/api/licenses/{license_id}/revoke"),
    # 前端详情面板读"操作历史"的那条（2026-10-31 字段契约修复带出来的端点）。
    ("GET", "/api/licenses/{license_id}/audit"),
)

PROVIDER = {
    "id": 1,
    "name": "Corp Okta",
    "protocol": "oidc",
    "tenant_id": "t1",
    "enabled": True,
    "auto_redirect": False,
    "config": {"authorize_url": "https://idp.example/a", "client_secret": "******"},
    "attribute_mapping": {"email": "email"},
    "created_at": 1.0,
    "updated_at": 2.0,
}
PROBE_OIDC = {
    "reachable": True,
    "protocol": "oidc",
    "details": {
        "authorize_url_resolved": True,
        "token_url_resolved": True,
        "userinfo_url_resolved": None,
        "discovery_fetched": False,
        "latency_ms": 12.5,
    },
}
WORKFLOW = {
    "id": "wf-1",
    "name": "Sync Agents",
    "active": True,
    # 下面三个是 n8n 自由格式里前端确实在用的键，模型不声明、必须透传。
    "nodes": 5,
    "lastExecutedAt": "2026-09-12T00:00:00Z",
    "tags": ["ops"],
}
EXECUTION = {
    "execution_id": "ex-1",
    "workflow_id": "wf-1",
    "status": "running",
    "started_at": datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc),
    "finished_at": None,
    "data": {"input": 1},
    "error": None,
}
AUDIT_ENTRY = {
    "log_id": "log-1",
    "license_id": "maop_abc_secret",
    "action": "created",
    "actor": "admin",
    "timestamp": 1.0,
    "detail": "",
    "ip_address": "127.0.0.1",
    "metadata": {},
}
LICENSE = {
    "license_id": "maop_abc_secret",
    "customer": "Acme",
    "edition": "enterprise",
    "max_users": 10,
    "fingerprint": None,
    "features": ["max_agents:5"],
    "status": "active",
    "issued_at": 1.0,
    "expires_at": 2.0,
    "revoked_at": None,
    "revoked_reason": "",
    # 低熵占位：高熵假 key 会被 gitleaks 的 generic-api-key 规则判成泄漏。
    "license_key": "not-a-real-key",
    "issued_by": "admin",
    "notes": "email: ops@acme.test",
    "created_at": 1.0,
    "updated_at": 1.0,
    "deleted_at": None,
}


class _Dump:
    """Stand-in for the overlay's pydantic models (``model_dump`` + attribute access)."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def model_dump(self) -> dict:
        return dict(self._data)

    def __getattr__(self, item: str) -> object:
        try:
            return self._data[item]
        except KeyError:
            raise AttributeError(item) from None


class _FakeSsoStore:
    def create(self, payload: object) -> _Dump:
        return _Dump(PROVIDER)

    def list(self, **kwargs: object) -> tuple[list[_Dump], int]:
        return [_Dump(PROVIDER)], 1

    def update(self, provider_id: int, payload: object) -> _Dump:
        return _Dump({**PROVIDER, "enabled": False})

    def delete(self, provider_id: int) -> bool:
        return True


class _FakeSsoRegistry:
    def __init__(self) -> None:
        self.store = _FakeSsoStore()

    def invalidate(self, provider_id: int) -> None:
        pass

    @staticmethod
    def to_masked_response(resp: object) -> dict:
        data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)  # type: ignore[arg-type]
        data["config"] = {**data["config"], "client_secret": "******"}
        # 真实 registry 会原样带上企业包模型新增的字段——这里也模拟一个。
        data["last_login_at"] = None
        return data

    def test_connection(self, provider_id: int) -> dict:
        return dict(PROBE_OIDC)


class _FakeN8nClient:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def list_workflows(self) -> list[dict]:
        return [dict(WORKFLOW)]

    def trigger_workflow(self, workflow_id: str, **kwargs: object) -> _Dump:
        return _Dump({**EXECUTION, "workflow_id": workflow_id})

    def get_execution(self, execution_id: str) -> _Dump:
        return _Dump({**EXECUTION, "execution_id": execution_id})

    def health_check(self) -> bool:
        return True


class _FakeLicenseManager:
    def list_licenses(self, status: str = "") -> list[_Dump]:
        return [_Dump({**LICENSE, "history": []})]

    def create_license(self, **kwargs: object) -> _Dump:
        return _Dump(LICENSE)

    def get_license(self, license_id: str) -> _Dump:
        return _Dump({**LICENSE, "license_id": license_id})

    def renew_license(self, license_id: str, **kwargs: object) -> _Dump:
        return _Dump({**LICENSE, "license_id": license_id})

    def revoke_license(self, license_id: str, **kwargs: object) -> _Dump:
        return _Dump({**LICENSE, "license_id": license_id, "status": "revoked"})

    def get_audit_logs(self, license_id: str = "", **kwargs: object) -> list[_Dump]:
        return [_Dump(AUDIT_ENTRY)]


# ── overlay stand-ins (only installed when the real package is absent) ──


class _StandInProviderCreate(BaseModel):
    name: str
    protocol: str
    tenant_id: str = ""
    enabled: bool = True
    auto_redirect: bool = False
    config: dict = {}
    attribute_mapping: dict = {}


class _StandInProviderUpdate(BaseModel):
    name: str | None = None
    tenant_id: str | None = None
    enabled: bool | None = None
    auto_redirect: bool | None = None
    config: dict | None = None
    attribute_mapping: dict | None = None


class _StandInLicenseCreate(BaseModel):
    customer: str
    expires_at: str
    max_users: int | None = None
    fingerprint: str | None = None
    features: list[str] | None = None
    issued_by: str = ""
    notes: str = ""


class _StandInLicenseRenew(BaseModel):
    new_expires_at: str
    actor: str = ""


class _StandInLicenseRevoke(BaseModel):
    reason: str = ""
    actor: str = ""


class _StandInLicenseUpdate(BaseModel):
    customer: str | None = None
    max_users: int | None = None
    fingerprint: str | None = None
    features: list[str] | None = None
    notes: str | None = None


class _StandInLicenseValidate(BaseModel):
    license_key: str


class _N8nIntegrationError(Exception):
    pass


_STAND_INS: dict[str, dict[str, object]] = {
    OVERLAY_IMPORT + ".sso_store": {
        "SSOProviderCreate": _StandInProviderCreate,
        "SSOProviderUpdate": _StandInProviderUpdate,
    },
    OVERLAY_IMPORT + ".n8n": {
        "N8nClient": object,
        "N8nIntegrationError": _N8nIntegrationError,
        "handle_n8n_webhook": lambda *a, **k: {"status": "ok"},
        "N8nWorkflowExecution": _Dump,
    },
    OVERLAY_IMPORT + ".license_manager": {
        "LicenseCreateRequest": _StandInLicenseCreate,
        "LicenseRenewRequest": _StandInLicenseRenew,
        "LicenseRevokeRequest": _StandInLicenseRevoke,
        "LicenseUpdateRequest": _StandInLicenseUpdate,
        "LicenseValidateRequest": _StandInLicenseValidate,
        "LicenseManager": object,
        "LicenseNotFoundError": type("LicenseNotFoundError", (Exception,), {}),
    },
}


def _overlay_present() -> bool:
    try:
        return importlib.util.find_spec(OVERLAY_IMPORT) is not None
    except (ImportError, ValueError):
        return False


def _install_stand_ins(monkeypatch) -> None:
    if _overlay_present():
        return
    for name, attrs in _STAND_INS.items():
        module = types.ModuleType(name)
        for attr, value in attrs.items():
            setattr(module, attr, value)
        monkeypatch.setitem(sys.modules, name, module)


def _router(name: str):
    return importlib.import_module(f"maop.dashboard.routers.{name}")


@pytest.fixture
def _prepared_app(monkeypatch):
    _install_stand_ins(monkeypatch)

    sso_router = _router("sso")
    n8n_router = _router("n8n")
    licenses_router = _router("licenses")
    integration_service = importlib.import_module(
        "maop.dashboard.services.integration_service"
    )

    monkeypatch.setattr(sso_router, "has_feature", lambda *a, **k: True)
    monkeypatch.setattr(sso_router, "_audit", lambda *a, **k: None)
    monkeypatch.setattr("maop.core.security.middleware.require_admin", lambda *a, **k: None)
    monkeypatch.setattr(auth_service, "get_sso_registry", _FakeSsoRegistry)

    monkeypatch.setattr(n8n_router, "has_feature", lambda *a, **k: True)
    monkeypatch.setattr(n8n_router, "require_admin", lambda *a, **k: None)
    monkeypatch.setattr(integration_service, "_get_client", _FakeN8nClient)
    monkeypatch.setenv("N8N_BASE_URL", "http://n8n.test:5678")

    monkeypatch.setattr(licenses_router, "require_admin", lambda *a, **k: None)
    monkeypatch.setattr(billing_service, "has_feature", lambda *a, **k: True)
    monkeypatch.setattr(billing_service, "_license_manager", _FakeLicenseManager())

    app = FastAPI()
    for module in (sso_router, n8n_router, licenses_router):
        app.include_router(module.router)
    return app


@pytest.fixture
def client(_prepared_app):
    return TestClient(_prepared_app)


def _fields(model: type[BaseModel]) -> set[str]:
    return set(model.model_fields)


# ── 1. OpenAPI: every modelled route declares a schema ─────────────


def test_every_modelled_route_is_typed_in_openapi(_prepared_app) -> None:
    ops = _prepared_app.openapi()["paths"]
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
    stale = [
        f"{method} {path}".replace("{provider_id}", "{}").replace(
            "{workflow_id}", "{}"
        ).replace("{execution_id}", "{}").replace("{license_id}", "{}")
        for method, path in MODELLED
    ]
    still_there = [line for line in stale if line in text]
    assert still_there == [], f"already typed but still in the burn-down list: {still_there}"


# ── 2. 线缆形状 ────────────────────────────────────────────────────


def test_sso_provider_list_wire_shape(client) -> None:
    body = client.get("/api/sso/providers").json()
    assert set(body) == _fields(sso_schemas.SSOProviderListEnvelope)
    assert body["count"] == 1
    assert body["total"] == 1
    provider = body["providers"][0]
    assert set(provider) == _fields(sso_schemas.SSOProviderItem) | {"last_login_at"}


def test_sso_provider_mutations_wire_shape(client) -> None:
    created = client.post(
        "/api/sso/providers",
        json={"name": "Corp Okta", "protocol": "oidc", "config": {"authorize_url": "x"}},
    ).json()
    assert set(created) == _fields(sso_schemas.SSOProviderEnvelope)
    assert set(created["provider"]) == _fields(sso_schemas.SSOProviderItem) | {"last_login_at"}

    updated = client.put("/api/sso/providers/1", json={"enabled": False}).json()
    assert set(updated) == _fields(sso_schemas.SSOProviderEnvelope)
    assert updated["provider"]["enabled"] is False

    deleted = client.delete("/api/sso/providers/1").json()
    assert deleted == {"status": "ok", "deleted": True}


def test_sso_provider_test_wire_shape(client) -> None:
    body = client.post("/api/sso/providers/1/test").json()
    assert set(body) == _fields(sso_schemas.SSOProviderTestEnvelope)
    assert body["reachable"] is True
    assert set(body["details"]) == _fields(sso_schemas.SSOProbeDetails)


def test_n8n_workflows_wire_shape(client) -> None:
    body = client.get("/api/n8n/workflows").json()
    assert set(body) == _fields(n8n_schemas.N8nWorkflowListEnvelope)
    assert body["count"] == 1
    assert set(body["workflows"][0]) == _fields(n8n_schemas.N8nWorkflowItem) | {
        "nodes", "lastExecutedAt", "tags",
    }


def test_n8n_execution_responses_carry_the_execution_status(client) -> None:
    """``{"status": "ok", **execution}`` 的后展开键会覆盖字面量 —— 线上如此。

    前端把 ``status`` 当执行状态读（``running`` / ``success`` / ...）。这条钉住
    当前语义：若哪天有人给它套上信封或改掉合并顺序，这里会红。
    """
    trigger = client.post("/api/n8n/workflows/wf-1/trigger", json={"data": {}}).json()
    assert set(trigger) == _fields(n8n_schemas.N8nExecutionItem)
    assert trigger["status"] == "running"

    execution = client.get("/api/n8n/executions/ex-1").json()
    assert set(execution) == _fields(n8n_schemas.N8nExecutionItem)
    assert execution["status"] == "running"


def test_n8n_health_wire_shape(client) -> None:
    body = client.get("/api/n8n/health").json()
    assert set(body) == _fields(n8n_schemas.N8nHealthEnvelope)
    assert body["n8n_reachable"] is True
    assert body["base_url"] == "http://n8n.test:5678"


def test_license_list_wire_shape(client) -> None:
    body = client.get("/api/licenses/list").json()
    assert set(body) == _fields(licenses_schemas.LicenseListEnvelope)
    assert body["count"] == 1
    assert set(body["licenses"][0]) == _fields(licenses_schemas.LicenseItem) | {"history"}


def test_license_mutations_wire_shape(client) -> None:
    created = client.post(
        "/api/licenses/create", json={"customer": "Acme", "expires_at": "2027-01-01"},
    ).json()
    assert set(created) == _fields(licenses_schemas.LicenseEnvelope)
    assert set(created["license"]) == _fields(licenses_schemas.LicenseItem)

    detail = client.get("/api/licenses/maop_abc_secret").json()
    assert set(detail) == _fields(licenses_schemas.LicenseEnvelope)
    assert detail["license"]["license_id"] == "maop_abc_secret"

    renewed = client.post(
        "/api/licenses/maop_abc_secret/renew", json={"new_expires_at": "2027-06-01"},
    ).json()
    assert set(renewed) == _fields(licenses_schemas.LicenseEnvelope)

    revoked = client.post("/api/licenses/maop_abc_secret/revoke", json={"reason": ""}).json()
    assert revoked["license"]["status"] == "revoked"


def test_license_audit_wire_shape(client) -> None:
    body = client.get("/api/licenses/maop_abc_secret/audit").json()
    assert set(body) == _fields(licenses_schemas.LicenseAuditListEnvelope)
    assert body["count"] == 1
    assert set(body["audit_logs"][0]) == _fields(licenses_schemas.LicenseAuditItem)


# ── 3. extra="allow"：上游新增字段必须原样到前端 ──────────────────


def test_unknown_upstream_keys_survive_the_response_model(client) -> None:
    """严格模型会把这些键悄悄吃掉：n8n 的 ``nodes`` / license 的 ``history`` /
    企业包模型新增字段。三条路由都开 ``extra="allow"``，这里逐条钉住。"""
    workflow = client.get("/api/n8n/workflows").json()["workflows"][0]
    assert workflow["nodes"] == 5
    assert workflow["tags"] == ["ops"]

    license_row = client.get("/api/licenses/list").json()["licenses"][0]
    assert license_row["history"] == []

    provider = client.get("/api/sso/providers").json()["providers"][0]
    assert "last_login_at" in provider


def test_missing_required_payload_key_is_a_500_not_a_silent_empty(
    _prepared_app, monkeypatch,
) -> None:
    """必填键缺失是 500，不是"空值冒充数据"（与 tenant 批次同款反向守卫）。"""
    incomplete = {k: v for k, v in LICENSE.items() if k != "license_key"}

    class _BrokenManager(_FakeLicenseManager):
        def get_license(self, license_id: str) -> _Dump:
            return _Dump(incomplete)

    monkeypatch.setattr(billing_service, "_license_manager", _BrokenManager())
    lenient = TestClient(_prepared_app, raise_server_exceptions=False)
    assert lenient.get("/api/licenses/maop_abc_secret").status_code == 500


# ── 4. 镜像 vs 企业包模型（装了包才跑）────────────────────────────


@pytest.mark.skipif(
    not _overlay_present(), reason="commercial overlay not installed (ADR-017 second repo)"
)
def test_mirrors_match_the_overlay_models() -> None:
    sso_store = importlib.import_module(OVERLAY_IMPORT + ".sso_store")
    n8n_mod = importlib.import_module(OVERLAY_IMPORT + ".n8n")
    license_mod = importlib.import_module(OVERLAY_IMPORT + ".license_manager")

    pairs = [
        (sso_schemas.SSOProviderItem, sso_store.SSOProviderResponse),
        (n8n_schemas.N8nExecutionItem, n8n_mod.N8nWorkflowExecution),
        (licenses_schemas.LicenseItem, license_mod.LicenseRecord),
        (licenses_schemas.LicenseAuditItem, license_mod.LicenseAuditEntry),
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
    """纯净 CI 里本文件必须仍被收集：模块级 import overlay 会让它整份消失。"""
    source = Path(__file__).read_text(encoding="utf-8")
    assert not needs_overlay(source), "this contract suite would stop being collected in CI"
    assert [m for m in (OVERLAY_IMPORT, OVERLAY_IMPORT.replace(".", "-")) if m in source] == []
