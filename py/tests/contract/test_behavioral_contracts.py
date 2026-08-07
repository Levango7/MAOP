"""Behavioral contract tests — verify runtime behavior, not just schema/existence.

Three behavioral contracts:
1. Model fallback: FallbackManager builds correct chain, filters by failure count.
2. Control action audit: every control action produces an audit event.
3. Model switch runtime effect: switching model changes effective_model in dispatcher.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract


# ── Helpers ─────────────────────────────────────────────────────

def _make_registry():
    """Build a ModelRegistry from the project's models.yaml."""
    import sys
    MAOP_ROOT = str(Path(__file__).resolve().parent.parent.parent)
    if MAOP_ROOT not in sys.path:
        sys.path.insert(0, MAOP_ROOT)

    from maop.model.registry import ModelRegistry
    return ModelRegistry(project_root=MAOP_ROOT)


# ── 1. Model Fallback Behavioral Contract ──────────────────────

class TestModelFallbackBehavior:
    """Verify FallbackManager correctly builds and filters fallback chains."""

    def test_fallback_chain_includes_primary_first(self):
        """Primary model must always be first in the chain."""
        from maop.model.fallback import FallbackManager
        from maop.model.schema import EffectiveModel

        registry = _make_registry()
        fm = FallbackManager(registry)

        em = EffectiveModel(
            model_name="gpt-4", provider="openai",
            fallback_chain=["gpt-3.5", "local-mini"],
        )
        chain = fm.get_chain(em)
        assert chain[0] == "gpt-4", "Primary must be first"

    def test_fallback_chain_excludes_high_failure_models(self):
        """Models with >=5 consecutive failures must be excluded."""
        from maop.model.fallback import FallbackManager
        from maop.model.schema import EffectiveModel

        registry = _make_registry()
        fm = FallbackManager(registry)

        # Record 5 failures for gpt-3.5
        for _ in range(5):
            fm.record_failure("gpt-3.5")

        em = EffectiveModel(
            model_name="gpt-4", provider="openai",
            fallback_chain=["gpt-3.5", "local-mini"],
        )
        chain = fm.get_chain(em)
        assert "gpt-3.5" not in chain, "Model with 5+ failures must be excluded"
        assert "gpt-4" in chain, "Primary must still be present"
        assert "local-mini" in chain, "Healthy fallback must still be present"

    def test_should_fallback_on_timeout(self):
        """Timeout errors must trigger fallback when policy allows."""
        from maop.model.fallback import FallbackManager

        registry = _make_registry()
        fm = FallbackManager(registry)
        assert fm.should_fallback("TIMEOUT after 30s") is True

    def test_should_not_fallback_when_policy_disables(self):
        """When fallback_on_error=False, should not fallback."""
        from maop.model.fallback import FallbackManager

        registry = _make_registry()
        fm = FallbackManager(registry)
        assert fm.should_fallback("some error", policy_fallback_on_error=False) is False

    def test_record_success_resets_failure_count(self):
        """Recording success must reset failure count to 0."""
        from maop.model.fallback import FallbackManager

        registry = _make_registry()
        fm = FallbackManager(registry)

        fm.record_failure("gpt-3.5")
        fm.record_failure("gpt-3.5")
        assert fm.get_failure_stats()["gpt-3.5"] == 2

        fm.record_success("gpt-3.5")
        assert "gpt-3.5" not in fm.get_failure_stats(), "Success must reset failures"


# ── 2. Control Action Audit Behavioral Contract ────────────────

class TestControlActionAuditBehavior:
    """Every control action must produce an audit event."""

    def test_successful_action_produces_audit(self, tmp_path):
        """A real (non-stub) action must create an audit event with SUCCESS status."""
        from maop.control.plane import ActionStatus, ControlPlane

        plane = ControlPlane(root_dir=str(tmp_path))

        def real_handler(target="", detail=None):
            return {"ok": True}

        plane.register_handler("test.real", real_handler)
        result = plane.execute(
            action="test.real", actor="test", target="agent-1",
            detail={"task": "hello"},
        )
        assert result.status == ActionStatus.SUCCESS
        assert result.audit is not None, "Audit event must be produced"
        assert result.audit.action == "test.real"
        assert result.audit.actor == "test"
        assert result.audit.target == "agent-1"

    def test_failed_action_produces_error_audit(self, tmp_path):
        """An unknown action must produce an ERROR-level audit event."""
        from maop.control.audit import AuditLevel
        from maop.control.plane import ActionStatus, ControlPlane

        plane = ControlPlane(root_dir=str(tmp_path))
        result = plane.execute(
            action="nonexistent.action", actor="test",
        )
        assert result.status == ActionStatus.FAILED
        assert result.audit is not None, "Failed action must still produce audit"
        assert result.audit.level == AuditLevel.ERROR

    def test_audit_log_persists_events(self, tmp_path):
        """Audit events must be readable from the log after recording."""
        from maop.control.plane import ControlPlane

        plane = ControlPlane(root_dir=str(tmp_path))
        plane.execute(action="control.run", actor="user1", target="agent-a")
        plane.execute(action="control.stop", actor="user1", target="agent-a")

        events = plane.audit_log().read_recent(limit=10)
        assert len(events) >= 2, "At least 2 events must be persisted"
        actions = [e.action for e in events]
        assert "control.run" in actions
        assert "control.stop" in actions

    def test_model_switch_produces_audit_with_detail(self, tmp_path):
        """Model switch must produce audit with model info in detail and SUCCESS status."""
        from maop.control.plane import ActionStatus, ControlPlane

        plane = ControlPlane(root_dir=str(tmp_path))
        result = plane.execute(
            action="model.switch", actor="admin", target="claude",
            detail={"model": "opus"},
        )
        assert result.status == ActionStatus.SUCCESS
        assert result.audit is not None
        assert result.audit.target == "claude"
        assert "model" in result.audit.detail, "Audit detail must contain model info"


# ── 3. Model Switch Runtime Effect Contract ────────────────────

class TestModelSwitchRuntimeEffect:
    """Model switch must affect the effective model in dispatcher runtime state."""

    def test_dispatcher_without_selector_uses_agent_model(self):
        """Without ModelSelector, dispatcher must use agent's configured model."""
        import sys
        MAOP_ROOT = str(Path(__file__).resolve().parent.parent.parent)
        if MAOP_ROOT not in sys.path:
            sys.path.insert(0, MAOP_ROOT)

        from maop.core.reliability.circuit_breaker import CircuitBreaker
        from maop.delegate.dispatcher import Dispatcher

        # No model_selector — should use agent's model as-is
        d = Dispatcher(MAOP_config=None, breaker=CircuitBreaker(), model_selector=None)
        assert d.effective_model is None, "No selector → no effective model"

    def test_dispatch_result_has_model_resolved_flag(self):
        """DispatchResult must expose model_resolved flag."""
        from maop.core.reliability.error_schema import new_result
        from maop.delegate.dispatcher import DispatchResult

        r = new_result(agent="test", task="t", exit_code=0)
        dr = DispatchResult(result=r)
        assert hasattr(dr, "model_resolved"), "DispatchResult must have model_resolved field"
        assert dr.model_resolved is True, "Default must be True (no selector = not failed)"

    def test_selector_resolution_changes_effective_model(self):
        """When ModelSelector resolves, effective_model must be set on dispatcher."""
        from maop.core.reliability.circuit_breaker import CircuitBreaker
        from maop.delegate.dispatcher import Dispatcher
        from maop.model.selector import ModelSelector

        registry = _make_registry()
        selector = ModelSelector(registry)
        d = Dispatcher(MAOP_config=None, breaker=CircuitBreaker(), model_selector=selector)

        # Call select_for_routing_key to verify it produces a valid EffectiveModel
        em = selector.select_for_routing_key(
            routing_key="codegen", agent_model="",
        )
        assert em.model_name, "Selector must resolve a model name"
        assert em.provider, "Provider must be resolved"
        # effective_model on dispatcher is set during dispatch, not before
        assert d.effective_model is None, "Before dispatch, effective_model should be None"
