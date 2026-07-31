"""Tests for MAOP ↔ doc-pipeline workflow adapter and event hooks."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _doc_pipeline_available() -> bool:
    try:
        from maop.delegate.doc_pipeline_adapter import _resolve_doc_pipeline_root
        root = _resolve_doc_pipeline_root()
        return root.exists() and (root / "pipeline_core" / "__init__.py").exists()
    except Exception:
        return False


_DOC_PIPELINE_AVAILABLE = _doc_pipeline_available()


# ── Adapter import & path resolution ──────────────────────

class TestAdapterImport:
    """Test that the adapter module imports correctly."""

    def test_module_importable(self):
        from maop.delegate.doc_pipeline_adapter import get_status, run_pipeline, run_plan
        assert callable(run_pipeline)
        assert callable(run_plan)
        assert callable(get_status)

    @pytest.mark.skipif(not _DOC_PIPELINE_AVAILABLE, reason="doc-pipeline project not available")
    def test_resolve_doc_pipeline_root(self):
        from maop.delegate.doc_pipeline_adapter import _resolve_doc_pipeline_root
        root = _resolve_doc_pipeline_root()
        assert root.exists(), f"doc-pipeline root {root} does not exist"
        assert (root / "pipeline_core" / "__init__.py").exists()

    def test_adapter_functions_exist(self):
        from maop.delegate.doc_pipeline_adapter import (
            get_orchestrator,
            get_status,
            list_hooks,
            register_maop_event_hooks,
            run_pipeline,
            run_plan,
            shutdown,
        )
        assert all(callable(f) for f in [
            get_orchestrator, register_maop_event_hooks,
            list_hooks, shutdown, run_pipeline, run_plan, get_status
        ])


# ── Event hook integration ──────────────────────

class TestEventHooks:
    """Test that emit_event hooks are wired into doc-pipeline."""

    def test_emit_event_importable_from_pipeline(self):
        """pipeline.py should import emit_event from event_hook."""
        root = Path(r"F:\Nexus\Workflow\doc-pipeline")
        pipeline_py = (root / "pipeline_core" / "pipeline.py").read_text(encoding="utf-8")
        assert "from .event_hook import emit_event" in pipeline_py

    def test_emit_event_calls_in_pipeline(self):
        """pipeline.py should call emit_event at key lifecycle points."""
        root = Path(r"F:\Nexus\Workflow\doc-pipeline")
        pipeline_py = (root / "pipeline_core" / "pipeline.py").read_text(encoding="utf-8")
        # Task lifecycle
        assert 'emit_event("task.created"' in pipeline_py
        assert 'emit_event("task.started"' in pipeline_py
        assert 'emit_event("task.completed"' in pipeline_py
        assert 'emit_event("task.failed"' in pipeline_py
        assert 'emit_event("task.cancelled"' in pipeline_py

    def test_emit_event_in_circuit_breaker(self):
        """circuit_breaker.py should emit events on state transitions."""
        root = Path(r"F:\Nexus\Workflow\doc-pipeline")
        cb_py = (root / "pipeline_core" / "circuit_breaker.py").read_text(encoding="utf-8")
        assert 'emit_event("circuit_breaker.open"' in cb_py
        assert 'emit_event("circuit_breaker.close"' in cb_py

    def test_emit_event_in_quality_gate(self):
        """quality_gate.py should emit events after scoring."""
        root = Path(r"F:\Nexus\Workflow\doc-pipeline")
        qg_py = (root / "agents" / "quality_gate.py").read_text(encoding="utf-8")
        assert 'emit_event("quality_gate.evaluated"' in qg_py
        assert 'emit_event("quality_gate.regenerate"' in qg_py

    def test_event_hook_module_exists(self):
        """event_hook.py should exist and expose emit_event."""
        root = Path(r"F:\Nexus\Workflow\doc-pipeline")
        eh_py = (root / "pipeline_core" / "event_hook.py").read_text(encoding="utf-8")
        assert "def emit_event" in eh_py
        assert "class EventHookManager" in eh_py


# ── MAOP adapter behavior ──────────────────────

class TestAdapterBehavior:
    """Test adapter behavior with mocking."""

    def test_run_pipeline_returns_dict(self):
        """run_pipeline should return a dict with expected keys."""
        from maop.delegate.doc_pipeline_adapter import run_pipeline

        with patch("maop.delegate.doc_pipeline_adapter.get_orchestrator") as mock_get:
            mock_task = MagicMock()
            mock_task.id = "test-001"
            mock_task.status.value = "done"
            mock_task.progress = 100
            mock_task.result = {"output": "ok"}
            mock_task.error = ""
            mock_task.steps = []

            mock_orch = MagicMock()
            mock_orch.registry.list.return_value = ["fetcher", "writer"]
            mock_orch.run.return_value = mock_task
            mock_get.return_value = mock_orch

            result = run_pipeline(pipeline_name="test", input_file="test.md")

        assert isinstance(result, dict)
        assert result["task_id"] == "test-001"
        assert result["status"] == "done"
        assert "duration_sec" in result

    def test_run_pipeline_handles_file_not_found(self):
        """run_pipeline should handle FileNotFoundError gracefully."""
        from maop.delegate.doc_pipeline_adapter import run_pipeline

        with patch("maop.delegate.doc_pipeline_adapter.get_orchestrator",
                   side_effect=FileNotFoundError("not found")):
            result = run_pipeline(pipeline_name="test")

        assert result["status"] == "failed"
        assert "not found" in result["error"]

    def test_run_pipeline_handles_generic_exception(self):
        """run_pipeline should handle generic exceptions gracefully."""
        from maop.delegate.doc_pipeline_adapter import run_pipeline

        with patch("maop.delegate.doc_pipeline_adapter.get_orchestrator",
                   side_effect=RuntimeError("boom")):
            result = run_pipeline(pipeline_name="test")

        assert result["status"] == "failed"
        assert "boom" in result["error"]

    def test_get_status_returns_none_for_missing_task(self):
        """get_status should return None for non-existent task."""
        from maop.delegate.doc_pipeline_adapter import get_status

        with patch("maop.delegate.doc_pipeline_adapter.get_orchestrator") as mock_get:
            mock_orch = MagicMock()
            mock_orch.get_task.return_value = None
            mock_get.return_value = mock_orch

            result = get_status("nonexistent")

        assert result is None

    def test_shutdown_resets_orchestrator(self):
        """shutdown should reset the global orchestrator."""
        from maop.delegate import doc_pipeline_adapter as adapter

        # Set a mock orchestrator
        mock_orch = MagicMock()
        adapter._ORCHESTRATOR = mock_orch

        adapter.shutdown()

        mock_orch.shutdown.assert_called_once()
        assert adapter._ORCHESTRATOR is None


# ── agents.yaml configuration ──────────────────────

class TestAgentsYamlConfig:
    """Test that agents.yaml has doc-pipeline configured correctly."""

    def test_doc_pipeline_agent_exists(self):
        import yaml
        config_path = Path(r"F:\Nexus\MAOP\config\agents.yaml")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert "doc-pipeline" in config["agents"], "doc-pipeline agent not in agents.yaml"

    def test_doc_pipeline_has_pipeline_capability(self):
        import yaml
        config_path = Path(r"F:\Nexus\MAOP\config\agents.yaml")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        agent = config["agents"]["doc-pipeline"]
        assert "pipeline" in agent["capabilities"]
        assert "docgen" in agent["capabilities"]

    def test_doc_pipeline_routing_configured(self):
        import yaml
        config_path = Path(r"F:\Nexus\MAOP\config\agents.yaml")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        # docgen routing should point to doc-pipeline
        assert config["routing"]["docgen"]["primary"] == "doc-pipeline"
        assert config["routing"]["pipeline"]["primary"] == "doc-pipeline"
