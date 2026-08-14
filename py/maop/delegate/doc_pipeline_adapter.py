r"""MAOP doc-pipeline Workflow Adapter

Bridges MAOP's dispatch system to doc-pipeline's PipelineOrchestrator.
Allows MAOP to run doc-pipeline as a first-class workflow agent.

Architecture:
  MAOP MaopLoop -> Dispatcher -> DocPipelineAdapter -> PipelineOrchestrator -> DAG execution
                                                       |
                                              emit_event() hooks -> MAOP EventHook subscribers

Usage in agents.yaml:
  doc_pipeline:
    script: maop.delegate.doc_pipeline_adapter
    routing_key: pipeline
    timeout: 600
    config:
      pipeline_name: technical-doc
      agents_dir: ${DOC_PIPELINE_ROOT}/agents  # Set DOC_PIPELINE_ROOT env var
"""
from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)

# doc-pipeline root (resolved lazily to avoid hard dependency)
_DOC_PIPELINE_ROOT: Path | None = None
_ORCHESTRATOR = None


def _resolve_doc_pipeline_root() -> Path:
    """Resolve doc-pipeline root directory."""
    global _DOC_PIPELINE_ROOT
    if _DOC_PIPELINE_ROOT is not None:
        return _DOC_PIPELINE_ROOT
    # Try standard locations. Besides $HOME, also derive from this repo's own
    # location (…/Nexus/MAOP/py/maop/delegate/ -> …/Nexus/Workflow/doc-pipeline)
    # so hosts whose code lives on a different drive than $HOME still resolve.
    repo_nexus = Path(__file__).resolve().parents[4]  # …/Nexus (MAOP's parent)
    candidates = [
        Path(os.environ.get("DOC_PIPELINE_ROOT", "")) if os.environ.get("DOC_PIPELINE_ROOT") else Path.home() / "Nexus" / "Workflow" / "doc-pipeline",
        Path.home() / "Nexus" / "Workflow" / "doc-pipeline",
        repo_nexus / "Workflow" / "doc-pipeline",
    ]
    for p in candidates:
        if (p / "pipeline_core" / "__init__.py").exists():
            _DOC_PIPELINE_ROOT = p
            return p
    raise FileNotFoundError("doc-pipeline not found. Set DOC_PIPELINE_ROOT env var or place at ~/Nexus/Workflow/doc-pipeline")


def _ensure_importable():
    """Add doc-pipeline to sys.path if not already importable."""
    root = _resolve_doc_pipeline_root()
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def get_orchestrator(agents_dir: str | None = None, checkpoint_dir: str = "checkpoints"):
    """Get or create a singleton PipelineOrchestrator instance."""
    global _ORCHESTRATOR
    if _ORCHESTRATOR is not None:
        return _ORCHESTRATOR

    _ensure_importable()
    from pipeline_core import PipelineOrchestrator

    root = _resolve_doc_pipeline_root()
    ad = agents_dir or str(root / "agents")
    _ORCHESTRATOR = PipelineOrchestrator(agents_dir=ad, checkpoint_dir=checkpoint_dir)
    logger.info("PipelineOrchestrator initialized (agents_dir=%s)", ad)
    return _ORCHESTRATOR


def register_maop_event_hooks() -> int:
    """Register MAOP-side callbacks for doc-pipeline events.

    Subscribes to all key event types and forwards them to MAOP's
    audit system and metrics collector.

    Returns number of hooks registered.
    """
    _ensure_importable()
    from pipeline_core.event_hook import get_hook_manager

    mgr = get_hook_manager()
    count = 0

    # Forward task.* events to MAOP audit
    def _on_task_event(event: str, payload: dict):
        logger.info("[doc-pipeline→MAOP] %s: %s", event, payload.get("task_id", "?"))
        try:
            from maop.core.monitoring.monitoring import metrics
            if event == "task.completed":
                getattr(metrics, "increment", lambda *a: None)("MAOP_doc_pipeline_completed", 1)
            elif event == "task.failed":
                getattr(metrics, "increment", lambda *a: None)("MAOP_doc_pipeline_failed", 1)
        except Exception:
            logger.debug('swallowed exception', exc_info=True)
            pass

    def _on_quality_gate(event: str, payload: dict):
        logger.info("[doc-pipeline→MAOP] quality_gate: score=%s passed=%s",
                     payload.get("score"), payload.get("passed"))

    def _on_circuit_breaker(event: str, payload: dict):
        logger.warning("[doc-pipeline→MAOP] circuit_breaker: agent=%s event=%s",
                        payload.get("agent"), event)

    for event, callback in [
        ("task.*", _on_task_event),
        ("quality_gate.*", _on_quality_gate),
        ("circuit_breaker.*", _on_circuit_breaker),
    ]:
        mgr.register(event=event, callback=callback)
        count += 1

    logger.info("Registered %d MAOP event hooks for doc-pipeline", count)
    return count


def run_pipeline(
    pipeline_name: str = "",
    input_file: str = "",
    config: dict | None = None,
    task_id: str | None = None,
    wait: bool = True,
) -> dict[str, Any]:
    """Run a doc-pipeline workflow from maop.

    This is the main entry point called by MAOP's Dispatcher when
    routing a task to the 'doc_pipeline' agent.

    Returns a dict with task_id, status, result, and error fields
    compatible with MAOP's DispatchResult format.
    """
    start = time.time()

    try:
        orch = get_orchestrator()

        # Ensure agents are discovered and registered
        if not orch.registry.list():
            orch.discover_agents()
            orch.register_agents()

        # Register MAOP event hooks (idempotent check)
        try:
            from pipeline_core.event_hook import get_hook_manager
            mgr = get_hook_manager()
            if not mgr.list_hooks():
                register_maop_event_hooks()
        except Exception:
            logger.debug('swallowed exception', exc_info=True)
            pass

        # Execute pipeline
        task = orch.run(
            task_id=task_id,
            pipeline_name=pipeline_name,
            input_file=input_file,
            config=config,
            wait=wait,
        )

        duration = time.time() - start
        result = {
            "task_id": task.id,
            "status": task.status.value,
            "pipeline": pipeline_name,
            "progress": task.progress,
            "result": task.result,
            "error": task.error,
            "duration_sec": round(duration, 2),
            "steps": len(task.steps),
        }
        logger.info("doc-pipeline task %s: %s in %.2fs", task.id, task.status.value, duration)
        return result

    except FileNotFoundError as e:
        return {"task_id": task_id or "", "status": "failed", "error": f"doc-pipeline not found: {e}"}
    except Exception as e:
        logger.exception("doc-pipeline execution failed")
        return {"task_id": task_id or "", "status": "failed", "error": str(e)}


def run_plan(
    pipeline_name: str = "",
    input_file: str = "",
    config: dict | None = None,
    task_id: str | None = None,
    wait: bool = True,
) -> dict[str, Any]:
    """Run doc-pipeline using Scheduler-generated ExecutionPlan.

    This uses the Scheduler to parse pipeline config, generate an
    ExecutionPlan with optimized parallelism, then execute via run_plan().
    """
    start = time.time()

    try:
        orch = get_orchestrator()

        if not orch.registry.list():
            orch.discover_agents()
            orch.register_agents()

        # Generate execution plan via Scheduler
        from pipeline_core.scheduler import Scheduler
        sched = Scheduler()
        plan = sched.parse(pipeline_name)

        task = orch.run_plan(plan, input_file=input_file, task_id=task_id, wait=wait)

        duration = time.time() - start
        return {
            "task_id": task.id,
            "status": task.status.value,
            "pipeline": pipeline_name,
            "progress": task.progress,
            "result": task.result,
            "error": task.error,
            "duration_sec": round(duration, 2),
            "steps": len(task.steps),
            "plan_id": plan.plan_id,
        }

    except Exception as e:
        logger.exception("doc-pipeline run_plan failed")
        return {"task_id": task_id or "", "status": "failed", "error": str(e)}


def get_status(task_id: str) -> dict[str, Any] | None:
    """Query doc-pipeline task status."""
    try:
        orch = get_orchestrator()
        task = orch.get_task(task_id)
        if task is None:
            return None
        return cast(dict[str, Any], task.to_dict())
    except Exception:
        return None


def list_hooks() -> list[dict]:
    """List registered event hooks."""
    try:
        _ensure_importable()
        from pipeline_core.event_hook import get_hook_manager
        return cast(list[dict], get_hook_manager().list_hooks())
    except Exception:
        return []


def shutdown():
    """Shutdown the orchestrator."""
    global _ORCHESTRATOR
    if _ORCHESTRATOR is not None:
        with contextlib.suppress(Exception):
            _ORCHESTRATOR.shutdown()
        _ORCHESTRATOR = None
