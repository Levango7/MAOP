"""Contract tests for Model Management API endpoints.

Validates that /api/model/* endpoints return correct schemas
matching what the frontend app.js expects.
"""
from __future__ import annotations

import pytest

# Mark all tests as contract tests
pytestmark = pytest.mark.contract


# ── Endpoint existence contracts ────────────────────────────────

EXPECTED_MODEL_ENDPOINTS = [
    "/api/model/agents",       # GET — list agents with model info
    "/api/model/quota",        # GET — agent quota/availability
    "/api/model/switch",       # POST — switch agent model
    "/api/model/registry",     # GET — full registry stats
    "/api/model/list",         # GET — all registered models
    "/api/model/providers",    # GET — provider list with health
    "/api/model/select",       # GET — select best model
    "/api/model/budget",       # GET — budget status
    "/api/model/quota/status", # GET — quota usage per provider
    "/api/model/policies",     # GET — selection policies
]


class TestModelAPIContracts:
    """Verify all model API endpoints are defined in server.py."""


    def test_all_model_endpoints_exist(self, server_routes):
        for ep in EXPECTED_MODEL_ENDPOINTS:
            assert ep in server_routes, f"Missing endpoint: {ep}"

    # These used to be ``assert expected_keys == {…}`` — a comparison against a
    # literal in the same statement, so they could never fail. They now read the
    # app's real OpenAPI document, which is generated from the response models.

    @staticmethod
    def _response_properties(path: str, method: str = "get") -> set[str]:
        from maop.dashboard.server import app

        schema = app.openapi()
        op = (schema["paths"].get(path) or {})[method]
        body = ((op.get("responses") or {}).get("200", {}).get("content") or {}).get(
            "application/json", {},
        ).get("schema") or {}
        ref = body.get("$ref")
        if ref:
            name = ref.rsplit("/", 1)[-1]
            body = schema["components"]["schemas"][name]
        return set(body.get("properties") or {})

    @staticmethod
    def _component_properties(path: str, prop: str, method: str = "get") -> set[str]:
        from maop.dashboard.server import app

        schema = app.openapi()
        op = (schema["paths"].get(path) or {})[method]
        body = ((op.get("responses") or {}).get("200", {}).get("content") or {}).get(
            "application/json", {},
        ).get("schema") or {}
        if "$ref" in body:
            body = schema["components"]["schemas"][body["$ref"].rsplit("/", 1)[-1]]
        props = body.get("properties") or {}
        target = props.get(prop) or {}
        refs: list[str] = []
        for candidate in (target.get("$ref"), (target.get("items") or {}).get("$ref")):
            if candidate:
                refs.append(candidate)
        # Optional object fields are emitted as anyOf [$ref, null].
        for branch in target.get("anyOf") or []:
            if branch.get("$ref"):
                refs.append(branch["$ref"])
        if refs:
            name = refs[0].rsplit("/", 1)[-1]
            return set(schema["components"]["schemas"][name].get("properties") or {})
        return set(target.get("properties") or {})

    def test_model_list_response_schema(self):
        """/api/model/list must return {models: [...], count: int}."""
        props = self._response_properties("/api/model/list")
        assert {"status", "models", "count"} <= props
        assert self._component_properties("/api/model/list", "models") == {
            "name", "provider", "family", "context_window", "max_output",
            "cost_per_1k_input", "cost_per_1k_output", "capabilities",
            "latency_tier", "quality_tier", "enabled", "provider_healthy",
        }

    def test_model_registry_response_schema(self):
        """/api/model/registry must return {status, stats}."""
        assert {"status", "stats"} <= self._response_properties("/api/model/registry")
        assert "total_providers" in self._component_properties("/api/model/registry", "stats")

    def test_model_select_response_schema(self):
        """/api/model/select must not expose the resolved API key."""
        assert {"status", "effective_model"} <= self._response_properties("/api/model/select")
        fields = self._component_properties("/api/model/select", "effective_model")
        assert "api_key" not in fields
        assert "has_api_key" in fields

    def test_model_providers_response_schema(self):
        """/api/model/providers must return {providers, status}."""
        assert {"status", "providers"} <= self._response_properties("/api/model/providers")
        assert self._component_properties("/api/model/providers", "providers") == {
            "name", "type", "enabled", "protocol", "healthy", "has_api_key", "base_url",
        }

    def test_model_policies_response_schema(self):
        """/api/model/policies must return {policies, count, status}."""
        assert {"status", "policies", "count"} <= self._response_properties("/api/model/policies")

    def test_model_budget_response_schema(self):
        """/api/model/budget must return {status, budget}."""
        assert {"status", "budget"} <= self._response_properties("/api/model/budget")
        assert "daily_spend" in self._component_properties("/api/model/budget", "budget")

    def test_model_quota_status_response_schema(self):
        """/api/model/quota/status returns ``quotas`` as a LIST (usage_all())."""
        assert {"status", "quotas"} <= self._response_properties("/api/model/quota/status")
        assert self._component_properties("/api/model/quota/status", "quotas") == {
            "provider", "requests_used", "requests_limit",
            "tokens_used", "tokens_limit", "window_s",
        }


# ── Model schema contracts ──────────────────────────────────────

class TestModelSchemaContracts:
    """Verify Pydantic model schemas match YAML config structure."""

    def test_model_def_has_required_fields(self):
        from maop.model.schema import ModelDef
        fields = ModelDef.model_fields
        required = {"name", "provider", "family", "context_window"}
        assert required.issubset(set(fields.keys()))

    def test_provider_def_has_required_fields(self):
        from maop.model.schema import ProviderDef
        fields = ProviderDef.model_fields
        required = {"type", "base_url", "enabled"}
        assert required.issubset(set(fields.keys()))

    def test_effective_model_has_required_fields(self):
        from maop.model.schema import EffectiveModel
        fields = EffectiveModel.model_fields
        required = {"model_name", "provider", "policy_name"}
        assert required.issubset(set(fields.keys()))

    def test_model_policy_has_required_fields(self):
        from maop.model.schema import ModelPolicy
        fields = ModelPolicy.model_fields
        required = {"strategy", "fallback_on_error", "fallback_on_timeout"}
        assert required.issubset(set(fields.keys()))

    def test_budget_config_has_required_fields(self):
        from maop.model.schema import BudgetConfig
        fields = BudgetConfig.model_fields
        required = {"daily_limit", "monthly_limit", "alert_threshold"}
        assert required.issubset(set(fields.keys()))

    def test_quota_config_has_required_fields(self):
        from maop.model.schema import QuotaConfig
        fields = QuotaConfig.model_fields
        required = {"requests_per_minute", "tokens_per_minute"}
        assert required.issubset(set(fields.keys()))
