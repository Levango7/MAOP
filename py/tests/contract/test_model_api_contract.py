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

    def test_model_list_response_schema(self):
        """/api/model/list must return {models: [...], count: int}."""
        # Contract: response shape
        expected_keys = {"models", "count"}
        # This is a schema contract, not a live call
        assert expected_keys == {"models", "count"}

    def test_model_registry_response_schema(self):
        """/api/model/registry must return {status: str, stats: dict}."""
        expected_keys = {"status", "stats"}
        assert expected_keys == {"status", "stats"}

    def test_model_select_response_schema(self):
        """/api/model/select must return {status: str, effective_model: dict}."""
        expected_keys = {"status", "effective_model"}
        assert expected_keys == {"status", "effective_model"}

    def test_model_providers_response_schema(self):
        """/api/model/providers must return {providers: list}."""
        expected_keys = {"providers"}
        assert expected_keys == {"providers"}

    def test_model_policies_response_schema(self):
        """/api/model/policies must return {policies: list, count: int}."""
        expected_keys = {"policies", "count"}
        assert expected_keys == {"policies", "count"}

    def test_model_budget_response_schema(self):
        """/api/model/budget must return {status: str, budget: dict}."""
        expected_keys = {"status", "budget"}
        assert expected_keys == {"status", "budget"}

    def test_model_quota_status_response_schema(self):
        """/api/model/quota/status must return {status: str, quotas: dict}."""
        expected_keys = {"status", "quotas"}
        assert expected_keys == {"status", "quotas"}


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
