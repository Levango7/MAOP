"""MAOP Dashboard — Engine execution endpoints (M0 L0.1).

Provides REST endpoints for the workflow engine, including distributed
execution via the Redis Streams backend (requires Redis / Enterprise).

Endpoints
---------
- ``POST /api/engine/run``     — submit a single task for local execution
  (multi-loop via WorkerPool, backward-compatible).
- ``POST /api/engine/run-distributed`` — submit a DAG of steps for
  distributed execution via Redis Streams. Falls back to local backend
  when Redis is unavailable.

Authentication: all endpoints require ``admin`` role (``require_admin``).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/engine", tags=["engine"])


# ── Request models ────────────────────────────────────────────


class EngineStepRequest(BaseModel):
    """A single workflow step for the engine."""

    id: str = Field(..., max_length=200)
    type: str = Field(default="agent")
    agent: str = Field(default="", max_length=200)
    task: str = Field(default="")
    depends_on: list[str] = Field(default_factory=list)
    retry: int = Field(default=0, ge=0)
    timeout: int = Field(default=120, ge=1)
    on_failure: str = Field(default="", max_length=50)


class EngineRunRequest(BaseModel):
    """POST /api/engine/run-distributed request body."""

    steps: list[EngineStepRequest] = Field(..., min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
    workdir: str = Field(default="", max_length=500)
    trace_id: str = Field(default="")


# ── Singleton (for dependency injection in tests) ──────────────

_engine_instance: Any = None


def _get_engine(
    redis_client: Any = None,
    step_executor: Any = None,
) -> Any:
    """Return the process-wide Engine instance (singleton).

    When ``redis_client`` is not provided but Redis is explicitly
    configured (``MAOP_REDIS_URL`` / settings ``redis_url``), a Redis
    client is created automatically so the engine can use the
    distributed TaskBackend.
    """
    global _engine_instance
    if _engine_instance is None:
        from maop.engine import Engine

        if redis_client is None:
            redis_client = _create_redis_client_from_env()
        _engine_instance = Engine(
            redis_client=redis_client,
            step_executor=step_executor,
        )
    return _engine_instance


def _create_redis_client_from_env() -> Any:
    """Create a Redis client from the settings module (single source).

    Reads ``MAOPSettings.redis_url`` (env ``MAOP_REDIS_URL``). A client is
    only built when the operator **explicitly** configured ``redis_url``
    (``model_fields_set``); the built-in default
    (``redis://localhost:6379/0``) is treated as "not configured" so
    Personal-edition deployments without Redis keep the clean local
    fallback instead of wiring a backend that cannot connect.

    Returns ``None`` when Redis is not configured or explicitly blanked
    (set ``MAOP_REDIS_URL=`` as the escape hatch).
    """
    from maop.config.settings import get_settings

    settings = get_settings()
    if "redis_url" not in settings.model_fields_set:
        return None
    url = (settings.redis_url or "").strip()
    if not url:
        return None
    try:
        import redis

        return redis.from_url(url, decode_responses=False)
    except Exception as exc:
        logger.warning("[engine_router] redis client init failed: %s", exc)
        return None


def set_engine_for_tests(engine: Any) -> None:
    """Inject a mock Engine for unit tests."""
    global _engine_instance
    _engine_instance = engine


# ── Endpoints ──────────────────────────────────────────────────


@router.post("/run")
@handle_api_errors("Engine run (local)")
async def api_engine_run(
    body: EngineStepRequest,
    request: Request,
) -> dict[str, Any]:
    """Submit a single task for local multi-loop execution.

    Wraps the step in a single-node DAG and executes it locally.
    """
    require_admin(request)
    engine = _get_engine()

    from maop.engine_types import StepType, WorkflowStep
    step = WorkflowStep(
        id=body.id or f"step-{uuid.uuid4().hex[:8]}",
        type=StepType.AGENT,
        agent=body.agent,
        task=body.task,
    )
    result = await engine.run(
        steps=[step],
        context={},
        workdir="",
    )
    return {
        "status": "ok" if result.success else "error",
        "trace_id": result.trace_id,
        "result": result.model_dump(),
    }


@router.post("/run-distributed")
@handle_api_errors("Engine run (distributed)")
async def api_engine_run_distributed(
    body: EngineRunRequest,
    request: Request,
) -> dict[str, Any]:
    """Submit a DAG of steps for distributed execution via Redis Streams.

    Steps are dispatched to the Redis Streams task queue
    (``maop:sched:tasks``) and executed by one or more workers in the
    ``maop_workers`` consumer group.

    The endpoint accepts an optional ``trace_id``; when omitted a UUID is
    generated. The returned ``trace_id`` can be used to query execution
    status (see ``GET /api/scheduling/results/{trace_id}`` — M1).

    **Fallback**: if no Redis client is configured (Personal edition),
    the endpoint falls back to local execution and logs a warning.
    """
    require_admin(request)
    engine = _get_engine()

    from maop.engine_types import StepType, WorkflowStep
    steps = []
    for s in body.steps:
        steps.append(WorkflowStep(
            id=s.id,
            type=StepType(s.type) if s.type in ("plan", "agent", "dag", "verify", "condition", "terminal") else StepType.AGENT,
            agent=s.agent,
            task=s.task,
            depends_on=list(s.depends_on),
            retry=s.retry,
            timeout=s.timeout,
            on_failure=s.on_failure,
        ))

    result = await engine.run(
        steps=steps,
        context=dict(body.context),
        workdir=body.workdir,
        trace_id=body.trace_id or uuid.uuid4().hex,
        distributed=True,
    )
    return {
        "status": "ok" if result.success else "error",
        "trace_id": result.trace_id,
        "result": result.model_dump(),
    }


__all__ = ["router", "set_engine_for_tests"]
