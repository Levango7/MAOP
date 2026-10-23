"""HTTP contracts for ``/api/model-gateway/*`` — envelope vs real producers.

Same two directions as ``test_model_router_contracts.py``, plus the two defects
this batch fixed, each pinned by a test that fails on the old behaviour:

* ``PUT /api/model-gateway/config`` used to replace the whole config, and the
  dashboard's form only sends two of the four fields — so every save deleted
  every permission rule (``_sync_permissions_to_db`` runs
  ``DELETE FROM model_gateway_permissions``);
* the usage table read a ``{model: tokens}`` map as if it were a list of rows,
  and there was no endpoint at all that could hydrate the config form.

Tests run against a real ``ModelGateway`` on an isolated tmp SQLite, not a stub:
these are precisely the paths where a stub would have agreed with the old bug.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.agent.llm_chat.model_gateway import (
    ModelAccessDecision,
    ModelGateway,
    ModelGatewayConfig,
    ModelPermission,
)
from maop.dashboard.routers import model_gateway as gateway_router
from maop.dashboard.routers.model_gateway_schemas import (
    CheckEnvelope,
    PermissionListEnvelope,
    UsageGetEnvelope,
)
from maop.dashboard.services import model_service

CONTRACT_ROUTES: list[tuple[str, str]] = [
    ("GET", "/api/model-gateway/permissions"),
    ("POST", "/api/model-gateway/permissions"),
    ("PUT", "/api/model-gateway/permissions/{model_pattern}"),
    ("DELETE", "/api/model-gateway/permissions/{model_pattern}"),
    ("POST", "/api/model-gateway/check"),
    ("GET", "/api/model-gateway/usage"),
    ("POST", "/api/model-gateway/usage"),
    ("DELETE", "/api/model-gateway/usage"),
    ("GET", "/api/model-gateway/config"),
    ("PUT", "/api/model-gateway/config"),
]


@pytest.fixture
def gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModelGateway:
    """A real ModelGateway on an isolated DB, injected into the service singleton."""
    gw = ModelGateway(db_path=tmp_path / "gateway.db")
    monkeypatch.setattr(model_service, "_model_gateway", gw)
    return gw


@pytest.fixture
def client(gateway: ModelGateway) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)

    app.include_router(gateway_router.router)
    return TestClient(app)


def _assert_keeps_every_payload_field(body: dict[str, Any], payload: dict[str, Any]) -> None:
    for key, value in payload.items():
        assert key in body, f"dropped by response_model: {key}"
        assert body[key] == value, f"altered by response_model: {key}"


# ── envelopes vs the real producers ────────────────────────────────


def test_permission_envelope_matches_domain_model_fields() -> None:
    """The list rows are ``ModelPermission`` dumps — one source, no drift."""
    assert set(PermissionListEnvelope.model_fields) == {
        "status", "permissions", "count", "error",
    }
    assert set(ModelPermission.model_fields) == {
        "model_pattern", "allowed", "max_tokens_per_call", "daily_token_limit", "priority",
    }


def test_check_decision_keeps_every_domain_field(client: TestClient) -> None:
    """The UI reads ``matched_permission`` / ``remaining_daily_tokens`` off the
    decision — the frontend previously read the envelope itself, so every check
    rendered as "denied"."""
    client.post("/api/model-gateway/permissions", json={
        "model_pattern": "gpt-4*", "allowed": True, "daily_token_limit": 1000, "priority": 10,
    })
    body = client.post("/api/model-gateway/check", json={"model": "gpt-4o"}).json()
    decision = body["decision"]
    assert set(decision) == set(ModelAccessDecision.model_fields)
    assert decision["allowed"] is True
    assert decision["model"] == "gpt-4o"
    assert decision["matched_permission"]["model_pattern"] == "gpt-4*"
    assert decision["remaining_daily_tokens"] == 1000
    assert "matched_rule" not in decision, "frontend must read the real key, not a legend"


def test_check_denied_by_default_policy_has_null_matched_permission(client: TestClient) -> None:
    body = client.post("/api/model-gateway/check", json={"model": "anything"}).json()
    assert body["decision"]["allowed"] is True
    assert body["decision"]["matched_permission"] is None
    assert body["decision"]["remaining_daily_tokens"] == -1


def test_usage_envelope_exposes_agent_rows(client: TestClient) -> None:
    """``usage`` is a collapsed map; the table needs ``rows`` with the agent."""
    client.post("/api/model-gateway/usage", json={"model": "gpt-4", "tokens": 300, "agent": "coder"})
    client.post("/api/model-gateway/usage", json={"model": "gpt-4", "tokens": 100, "agent": "reviewer"})
    body = client.get("/api/model-gateway/usage").json()
    assert set(body) == set(UsageGetEnvelope.model_fields)
    assert body["usage"] == {"gpt-4": 400}
    assert body["total_tokens"] == 400
    assert body["rows"] == [
        {"model": "gpt-4", "agent": "coder", "tokens": 300},
        {"model": "gpt-4", "agent": "reviewer", "tokens": 100},
    ]


def test_usage_rows_follow_the_model_filter(client: TestClient) -> None:
    client.post("/api/model-gateway/usage", json={"model": "gpt-4", "tokens": 10, "agent": "a"})
    client.post("/api/model-gateway/usage", json={"model": "claude", "tokens": 20, "agent": "b"})
    body = client.get("/api/model-gateway/usage", params={"model": "claude"}).json()
    assert body["rows"] == [{"model": "claude", "agent": "b", "tokens": 20}]


def test_get_daily_usage_rows_matches_sqlite_granularity(gateway: ModelGateway) -> None:
    gateway.record_usage("gpt-4", 5, agent="x")
    gateway.record_usage("gpt-4", 7, agent="y")
    assert gateway.get_daily_usage_rows() == [
        {"model": "gpt-4", "agent": "y", "tokens": 7},
        {"model": "gpt-4", "agent": "x", "tokens": 5},
    ]


# ── the config-wipe regression ─────────────────────────────────────


def test_partial_config_put_preserves_permission_rules(client: TestClient, gateway: ModelGateway) -> None:
    """The exact payload the dashboard sends must not delete anything."""
    client.post("/api/model-gateway/permissions", json={
        "model_pattern": "gpt-4*", "allowed": False, "priority": 10,
    })
    resp = client.put("/api/model-gateway/config", json={
        "global_daily_token_limit": 50000,
        "default_policy": "allow",
    })
    assert resp.status_code == 200
    assert client.get("/api/model-gateway/permissions").json()["count"] == 1, (
        "partial config PUT wiped the permission rules"
    )
    assert gateway.list_permissions()[0].model_pattern == "gpt-4*"


def test_partial_config_put_preserves_unset_fields(client: TestClient, gateway: ModelGateway) -> None:
    """``enable_quota_check`` was silently reset to True by the whole-object replace."""
    client.put("/api/model-gateway/config", json={"enable_quota_check": False})
    resp = client.put("/api/model-gateway/config", json={"default_policy": "deny"})
    assert resp.status_code == 200
    config = resp.json()["config"]
    assert config["enable_quota_check"] is False, "a field the client never sent got reset"
    assert config["default_policy"] == "deny"


def test_explicit_empty_permissions_still_replace(client: TestClient) -> None:
    """Presence is the signal: an explicit ``[]`` means "clear", and still works."""
    client.post("/api/model-gateway/permissions", json={"model_pattern": "a-*"})
    client.put("/api/model-gateway/config", json={"permissions": []})
    assert client.get("/api/model-gateway/permissions").json()["count"] == 0


def test_unknown_config_field_is_rejected_not_swallowed(client: TestClient) -> None:
    """``extra='ignore'`` would answer 200 while changing nothing."""
    assert client.put("/api/model-gateway/config", json={"defualt_policy": "deny"}).status_code == 422


def test_service_rejects_unknown_update_key(gateway: ModelGateway) -> None:
    with pytest.raises(ValueError, match="unknown gateway config field"):
        model_service.patch_gateway_config(defualt_policy="deny")


def test_repeated_partial_patches_do_not_disturb_existing_rules(client: TestClient) -> None:
    """每个 PUT 只带一个键，规则表必须原样留着（老实现第二次起就在删了）。"""
    client.post("/api/model-gateway/permissions", json={"model_pattern": "keep-*", "priority": 1})
    for value in (1000, 2000):
        cfg = client.put("/api/model-gateway/config",
                         json={"global_daily_token_limit": value}).json()["config"]
        assert [p["model_pattern"] for p in cfg["permissions"]] == ["keep-*"]
        assert cfg["global_daily_token_limit"] == value


# ── config read path ───────────────────────────────────────────────


def test_get_config_returns_the_live_values(client: TestClient) -> None:
    """Without this endpoint the form always displayed defaults, i.e. a lie."""
    client.put("/api/model-gateway/config", json={"global_daily_token_limit": 123456,
                                                  "default_policy": "deny"})
    body = client.get("/api/model-gateway/config").json()
    assert body["status"] == "ok"
    assert set(body["config"]) == set(ModelGatewayConfig.model_fields)
    assert body["config"]["global_daily_token_limit"] == 123456
    assert body["config"]["default_policy"] == "deny"


def test_config_snapshot_is_not_a_live_reference(gateway: ModelGateway) -> None:
    """改快照不能改到网关本体，否则调用点拿到的是共享可变状态。"""
    gateway.add_permission(ModelPermission(model_pattern="live-*"))
    snapshot = gateway.config
    snapshot.permissions.clear()
    assert [p.model_pattern for p in gateway.config.permissions] == ["live-*"]


# ── error branch + wiring ──────────────────────────────────────────


def test_error_fallback_is_labelled_error_not_ok(client: TestClient, monkeypatch) -> None:
    def _boom() -> dict[str, Any]:
        raise RuntimeError("gateway down")

    monkeypatch.setattr(model_service, "list_gateway_permissions", _boom)
    body = client.get("/api/model-gateway/permissions").json()
    assert body["status"] == "error"
    assert body["error"] == "List failed"
    assert body["permissions"] == []


def test_check_error_branch_leaves_decision_null(client: TestClient, monkeypatch) -> None:
    def _boom(*_a, **_kw) -> dict[str, Any]:
        raise RuntimeError("nope")

    monkeypatch.setattr(model_service, "check_model_access", _boom)
    body = client.post("/api/model-gateway/check", json={"model": "gpt-4"}).json()
    assert set(body) == set(CheckEnvelope.model_fields)
    assert body["decision"] is None
    assert body["status"] == "error"


def test_all_contract_routes_declare_a_response_model(client: TestClient) -> None:
    schema = client.app.openapi()
    problems: list[str] = []
    for method, template in CONTRACT_ROUTES:
        op = (schema["paths"].get(template) or {}).get(method.lower())
        if op is None:
            problems.append(f"MISSING {method} {template}")
            continue
        body = ((op.get("responses") or {}).get("200", {}).get("content") or {}).get(
            "application/json", {},
        ).get("schema") or {}
        if "$ref" not in body and "properties" not in body:
            problems.append(f"UNtyped {method} {template}")
    assert problems == []
