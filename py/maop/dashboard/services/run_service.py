"""Run review service — projects the append-only ``run_events`` ledger.

M0 could only show a control job's captured stdout. This service turns the
per-run event log into the object the UI needs to answer "what happened in
this run": plan, every execution attempt with its output, the gate verdicts,
cost, and the routing decisions that chose the agent.

Framework-agnostic on purpose (no FastAPI import), matching the other
service modules.
"""

from __future__ import annotations

from typing import Any

from maop.core.run_events import fetch_recent_runs, fetch_run_events
from maop.dashboard.services import routing_service


def _summarize(trace_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    started = next((e for e in events if e["kind"] == "run.started"), None)
    finished = next((e for e in events if e["kind"] == "run.finished"), None)
    plan = next((e for e in events if e["kind"] == "run.plan"), None)
    verify = next((e for e in reversed([e for e in events if e["kind"] == "run.verify"])), None)
    cost = next((e for e in reversed([e for e in events if e["kind"] == "run.cost"])), None)
    steps = [e for e in events if e["kind"] == "run.step"]
    actor = finished or plan or (steps[-1] if steps else None) or {}
    return {
        "trace_id": trace_id,
        "task": str((started or {}).get("payload", {}).get("task", "")),
        "agent": actor.get("agent", ""),
        "routing_key": str((finished or {}).get("payload", {}).get("routing_key", "")),
        "status": "finished" if finished else "incomplete",
        "success": bool((finished or {}).get("payload", {}).get("success", False)) if finished else None,
        "duration_ms": int((finished or {}).get("duration_ms", 0) or 0),
        "steps": len(steps),
        "started_at": events[0]["timestamp"] if events else "",
        "updated_at": events[-1]["timestamp"] if events else "",
        "verify_status": (verify or {}).get("status", ""),
        "verify_summary": str((verify or {}).get("payload", {}).get("summary", "")),
        "cost": (cost or {}).get("payload", {}),
    }


def get_run_detail(trace_id: str) -> dict[str, Any] | None:
    """Return the timeline for one run, or ``None`` when nothing was stored."""
    events = fetch_run_events(trace_id)
    if not events:
        return None
    routing = routing_service.query_decisions_by_trace(trace_id)
    return {
        "summary": _summarize(trace_id, events),
        "events": events,
        "decisions": routing.get("decisions", []),
    }


def list_runs(limit: int = 20) -> dict[str, Any]:
    """Return run summaries, newest first."""
    items = fetch_recent_runs(limit=limit)
    return {"items": items, "total": len(items)}
