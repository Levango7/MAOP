"""Contract tests for Dispatcher — verify ModelSelector integration contract."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract


class TestDispatcherModelIntegration:
    """Verify Dispatcher correctly integrates with ModelSelector."""

    def test_dispatcher_accepts_model_selector(self):
        """Dispatcher.__init__ must accept model_selector parameter."""
        import inspect

        from maop.delegate.dispatcher import Dispatcher
        sig = inspect.signature(Dispatcher.__init__)
        assert "model_selector" in sig.parameters

    def test_dispatcher_has_effective_model_property(self):
        """Dispatcher must expose effective_model property."""
        from maop.delegate.dispatcher import Dispatcher
        d = Dispatcher()
        assert hasattr(d, "effective_model")
        assert d.effective_model is None  # None before any dispatch

    def test_dispatch_result_has_driver_used(self):
        from maop.delegate.dispatcher import DispatchResult
        fields = DispatchResult.model_fields
        assert "driver_used" in fields
        assert "breaker_tripped" in fields

    def test_agent_config_has_model_field(self):
        from maop.delegate.dispatcher import AgentConfig
        fields = AgentConfig.model_fields
        assert "model" in fields


class TestModelSelectorContract:
    """Verify ModelSelector interface contract."""

    def test_select_method_exists(self):
        from maop.model.selector import ModelSelector
        assert hasattr(ModelSelector, "select")

    def test_select_for_routing_key_exists(self):
        from maop.model.selector import ModelSelector
        assert hasattr(ModelSelector, "select_for_routing_key")

    def test_select_returns_effective_model(self, tmp_path):
        from maop.model.registry import ModelRegistry
        from maop.model.schema import EffectiveModel
        from maop.model.selector import ModelSelector

        reg = ModelRegistry(project_root=str(
            Path(__file__).resolve().parent.parent.parent.parent
        ))
        selector = ModelSelector(reg)
        em = selector.select(capability="codegen")
        assert isinstance(em, EffectiveModel)
        assert em.model_name  # non-empty
        assert em.provider  # non-empty
