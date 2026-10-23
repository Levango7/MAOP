"""HTTP contracts for ``/api/model/*`` plus the model↔producer key pins.

Two directions, because either one alone has already been wrong in this repo:

* ``response_model`` must not drop or invent a key the service emits — asserted
  by comparing the HTTP body with the service payload;
* the declared fields must match what the *real* producers build
  (``ModelRegistry.stats``, ``ProviderRegistry.list_providers``,
  ``BudgetGuard.stats``) — asserted against those classes, so a rename on either
  side fails here instead of blanking a dashboard column.

``status`` defaults to ``"error"`` on purpose: the success paths always write
``{"status": "ok", ...}`` while several ``error_value=`` fallbacks omit the key
entirely — an ``"ok"`` default would label a failure as a success.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.dashboard.routers import model as model_router
from maop.dashboard.routers.model_schemas import (
    BudgetStats,
    ModelListEnvelope,
    ProviderRow,
    RegistryStats,
)
from maop.dashboard.services import model_service

CONTRACT_ROUTES: list[tuple[str, str]] = [
    ("GET", "/api/model/agents"),
    ("GET", "/api/model/quota"),
    ("POST", "/api/model/switch"),
    ("GET", "/api/model/registry"),
    ("GET", "/api/model/list"),
    ("GET", "/api/model/providers"),
    ("GET", "/api/model/budget"),
    ("GET", "/api/model/policies"),
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(model_router, "require_admin", lambda request: None)
    app = FastAPI()
    app.include_router(model_router.router)
    return TestClient(app)


def _assert_keeps_every_payload_field(body: dict[str, Any], payload: dict[str, Any]) -> None:
    """The envelopes declare an ``error`` slot for the fallback branch, so the
    response may be a superset — but it must never drop, rename or retype a
    field the service produced."""
    for key, value in payload.items():
        assert key in body, f"dropped by response_model: {key}"
        assert body[key] == value, f"altered by response_model: {key}"


# ── model fields vs the real producers ────────────────────────────


def test_registry_stats_model_matches_real_registry_keys() -> None:
    """Compare against the real registry built from the repo's own config."""
    from maop.model.registry import ModelRegistry

    real = ModelRegistry()
    assert set(RegistryStats.model_fields) == set(real.stats())


def test_provider_row_model_matches_real_registry_keys() -> None:
    """``has_api_key`` / ``base_url`` are UI columns; api_key itself never leaves."""
    from maop.model.registry import ModelRegistry

    rows = ModelRegistry().providers.list_providers()
    assert rows, "repo config/models.yaml should define at least one provider"
    assert set(ProviderRow.model_fields) == set(rows[0])
    assert not {"api_key", "api_key_env", "extra_headers"} & set(rows[0])


def test_budget_model_matches_guard_keys(tmp_path) -> None:
    from maop.model.budget import BudgetGuard

    guard = BudgetGuard(root_dir=str(tmp_path))
    assert set(BudgetStats.model_fields) == set(guard.stats())


# ── envelopes vs the service payloads ────────────────────────────


def test_models_envelope_keeps_every_service_field(client, monkeypatch) -> None:
    row = {
        "name": "claude-3", "provider": "anthropic", "family": "claude",
        "context_window": 200000, "max_output": 4096,
        "cost_per_1k_input": 0.003, "cost_per_1k_output": 0.015,
        "capabilities": ["codegen"], "latency_tier": "fast", "quality_tier": "excellent",
        "enabled": True, "provider_healthy": True,
    }
    payload = {"models": [row], "count": 1}
    monkeypatch.setattr(model_service, "list_models", lambda: dict(payload))
    body = client.get("/api/model/list").json()
    _assert_keeps_every_payload_field(body, {**payload, "status": "ok"})
    assert set(ModelListEnvelope.model_fields) == {"status", "models", "count", "error"}


def test_providers_envelope_keeps_has_api_key_and_base_url(client, monkeypatch) -> None:
    rows = [{"name": "anthropic", "type": "api", "enabled": True, "protocol": "claude_code",
             "healthy": True, "has_api_key": True, "base_url": "https://api.anthropic.com"}]
    monkeypatch.setattr(model_service, "list_model_providers", lambda: {"providers": rows})
    body = client.get("/api/model/providers").json()
    _assert_keeps_every_payload_field(body, {"providers": rows, "status": "ok"})
    # The UI column is derived from has_api_key; a silent default would fake it.
    assert body["providers"][0]["has_api_key"] is True


def test_registry_envelope_carries_real_stats(client, monkeypatch) -> None:
    stats = {
        "total_models": 2, "enabled_models": 1, "total_providers": 2, "enabled_providers": 1,
        "by_quality": {"excellent": 1}, "by_latency": {"fast": 1},
        "by_provider": {"anthropic": 2}, "by_protocol": {"claude_code": 1},
        "thinking_capable": 1, "multimodal_models": 1, "tool_calling_models": 2,
        "streaming_models": 2, "image_gen_models": 0,
    }
    monkeypatch.setattr(model_service, "get_model_registry_stats", lambda: {"stats": stats})
    body = client.get("/api/model/registry").json()
    _assert_keeps_every_payload_field(body, {"stats": stats, "status": "ok"})
    assert body["stats"]["by_provider"] == {"anthropic": 2}


def test_budget_and_policies_and_switch_envelopes(client, monkeypatch) -> None:
    budget = {"daily_spend": 1.0, "daily_limit": 5.0, "monthly_spend": 9.0, "monthly_limit": 90.0,
              "alert_threshold": 0.8, "hard_stop": True, "daily_remaining": 4.0,
              "daily_utilization": 0.2, "alerted": False}
    monkeypatch.setattr(model_service, "get_model_budget", lambda: {"budget": budget})
    body = client.get("/api/model/budget").json()
    _assert_keeps_every_payload_field(body, {"budget": budget, "status": "ok"})
    assert body["budget"]["daily_spend"] == 1.0

    policies = [{"name": "cheap", "strategy": "cheapest", "max_cost_per_task": 0.01,
                 "prefer_low_latency": False, "fallback_on_error": True, "fallback_on_timeout": True}]
    monkeypatch.setattr(model_service, "list_model_policies",
                        lambda: {"policies": policies, "count": 1})
    body = client.get("/api/model/policies").json()
    _assert_keeps_every_payload_field(body, {"policies": policies, "count": 1, "status": "ok"})
    # ModelPolicy.fallback_on_quota_exceeded is not in the payload and must not
    # be invented by the envelope.
    assert "fallback_on_quota_exceeded" not in body["policies"][0]

    monkeypatch.setattr(model_service, "switch_model", _async_result({"agent": "a1", "model": "m2"}))
    body = client.post("/api/model/switch", json={"agent": "a1", "model": "m2"}).json()
    _assert_keeps_every_payload_field(body, {"agent": "a1", "model": "m2", "status": "ok"})


def test_agents_and_quota_envelopes_keep_their_own_key_names(client, monkeypatch) -> None:
    agents = [{"name": "coder", "cli": "claude", "driver": "cli", "model": "claude-3",
               "timeout_s": 60, "capabilities": ["codegen"], "description": "d",
               "cli_available": True, "cli_path": "/usr/bin/claude"}]
    monkeypatch.setattr(model_service, "list_model_agents", lambda: {"agents": agents, "count": 1})
    body = client.get("/api/model/agents").json()
    _assert_keeps_every_payload_field(body, {"agents": agents, "count": 1, "status": "ok"})

    # /quota rows are keyed by ``agent``, not ``name``.
    quota = [{"agent": "coder", "cli": "claude", "model": "claude-3", "available": True,
              "cli_path": "/usr/bin/claude", "driver": "cli"}]
    monkeypatch.setattr(model_service, "get_model_quota", lambda: {"agents": quota, "count": 1})
    body = client.get("/api/model/quota").json()
    _assert_keeps_every_payload_field(body, {"agents": quota, "count": 1, "status": "ok"})
    assert body["agents"][0]["agent"] == "coder"


def test_error_fallback_is_labelled_error_not_ok(client, monkeypatch) -> None:
    """The fallback dicts omit ``status``; the model must not print "ok"."""

    def _boom() -> dict[str, Any]:
        raise RuntimeError("registry down")

    monkeypatch.setattr(model_service, "list_models", _boom)
    body = client.get("/api/model/list").json()
    assert body["status"] == "error"
    assert body["error"] == "Model list failed"


def _async_result(payload: dict[str, Any]):
    async def _inner(*_a, **_kw) -> dict[str, Any]:
        return payload

    return _inner


def test_select_never_returns_the_resolved_api_key(client, monkeypatch) -> None:
    """``EffectiveModel.api_key`` is a live credential for the dispatcher only."""
    payload = {
        "effective_model": {
            "model_name": "claude-3", "provider": "anthropic", "model_id": "claude-3-opus",
            "protocol": "claude_code", "cli_model_arg": "", "cost_estimate": 0.01,
            "fallback_chain": ["gpt-4"], "policy_name": "default",
            "capability_matrix": {"tool_calling": True}, "thinking": {"supported": True},
            "base_url": "https://api.anthropic.com", "api_key": "sk-live-secret",
        },
    }
    monkeypatch.setattr(model_service, "select_model", lambda *a, **kw: dict(payload))
    body = client.get("/api/model/select").json()
    model = body["effective_model"]
    assert "api_key" not in model, "credential leaked through the response model"
    assert "sk-live-secret" not in body["effective_model"].get("base_url", "")
    assert model["has_api_key"] is True  # presence reported, value never leaves
    assert model["model_name"] == "claude-3"
    assert model["fallback_chain"] == ["gpt-4"]


def test_quota_status_quotas_is_a_list_of_rows(client, monkeypatch) -> None:
    rows = [{"provider": "anthropic", "requests_used": 3, "requests_limit": 60,
             "tokens_used": 1200, "tokens_limit": 90000, "window_s": 60}]
    monkeypatch.setattr(model_service, "get_model_quota_status", lambda: {"quotas": rows})
    body = client.get("/api/model/quota/status").json()
    assert isinstance(body["quotas"], list)
    _assert_keeps_every_payload_field(body, {"quotas": rows, "status": "ok"})


def test_all_contract_routes_declare_a_response_model(client) -> None:
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
