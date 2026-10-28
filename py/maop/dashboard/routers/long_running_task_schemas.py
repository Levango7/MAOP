"""Response models for ``/api/long-running-tasks/*`` (JSON endpoints).

``LongRunningTask`` (``maop/core/agent/router/long_running_task.py``) is the
domain model and ``session_service`` already returns ``model_dump()`` of it, so
it is reused verbatim: the manager is the only thing that knows the task's key
set.

``GET /api/long-running-tasks/{task_id}/progress`` is deliberately **not**
covered here — the handler returns a ``StreamingResponse`` with
``media_type="text/event-stream"``, so a JSON ``response_model`` would be a
false contract (same policy as ``fixtures/frontend_non_json_endpoints.txt``).
"""

from __future__ import annotations

from pydantic import BaseModel

from maop.core.agent.router.long_running_task import LongRunningTask


class LongRunningTaskListEnvelope(BaseModel):
    """``GET /api/long-running-tasks`` — active (pending/running) tasks."""

    status: str = "ok"
    count: int
    tasks: list[LongRunningTask]


class LongRunningTaskCancelEnvelope(BaseModel):
    """``POST /api/long-running-tasks/{task_id}/cancel`` — echoes the task id."""

    status: str = "ok"
    task_id: str
