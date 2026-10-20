"""Append-only run events: the per-run source of truth (M1).

Before this module a run existed only as an in-memory ``LoopResult`` plus
text lines in ``logs/MAOP-structured.log``, so "what happened in run X"
could not be answered once the process exited — ``delegations`` had the
right columns but was only ever written by tests.

Events are appended, never updated, and keyed by ``trace_id``, the id
``MaopLoop`` already threads through routing decisions, memory and cost.
UI projections and statistics read this table instead of scraping logs.

Bookkeeping must never kill a run, so storage failures are logged rather
than raised; readers fail to an empty list for the same reason.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)

#: Text fields longer than this are truncated before storage, so one chatty
#: agent cannot grow ``maop.db`` without bound.
MAX_TEXT = 2000

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  kind TEXT NOT NULL,
  agent TEXT DEFAULT '',
  step INTEGER DEFAULT 0,
  status TEXT DEFAULT '',
  duration_ms INTEGER DEFAULT 0,
  payload TEXT DEFAULT '{}',
  timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_events_trace ON run_events(trace_id, seq);
CREATE INDEX IF NOT EXISTS idx_run_events_timestamp ON run_events(timestamp);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clip_value(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[:MAX_TEXT] + f"…(截断，原长 {len(value)})"
    if isinstance(value, dict):
        return {key: _clip_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clip_value(item) for item in value]
    return value


def _payload_json(payload: dict[str, Any] | None) -> str:
    return json.dumps(_clip_value(payload or {}), ensure_ascii=False, default=str)


def record_run_event(
    trace_id: str,
    kind: str,
    *,
    agent: str = "",
    step: int = 0,
    status: str = "",
    duration_ms: int = 0,
    payload: dict[str, Any] | None = None,
) -> bool:
    """Append one event for *trace_id*; ``False`` means it was not stored."""
    if not trace_id:
        logger.warning("[run_events] dropping %s event without trace_id", kind)
        return False
    db_path = get_db_path("run_events", legacy_fallback="maop")
    try:
        with sqlite_connect(db_path) as conn:
            conn.executescript(SCHEMA_DDL)
            last = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM run_events WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO run_events (trace_id, seq, kind, agent, step, status,"
                " duration_ms, payload, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    trace_id, int(last) + 1, kind, agent[:200], step, status[:200],
                    int(duration_ms), _payload_json(payload), _now(),
                ),
            )
        return True
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[run_events] failed to store %s for %s: %s", kind, trace_id, exc)
        return False


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload"] or "{}")
    except json.JSONDecodeError:
        logger.warning("[run_events] corrupt payload on event id=%s", row["id"])
        payload = {}
    return {
        "trace_id": row["trace_id"],
        "seq": row["seq"],
        "kind": row["kind"],
        "agent": row["agent"],
        "step": row["step"],
        "status": row["status"],
        "duration_ms": row["duration_ms"],
        "timestamp": row["timestamp"],
        "payload": payload,
    }


def fetch_run_events(trace_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
    """Return one run's events in write order (empty when nothing was stored)."""
    db_path = get_db_path("run_events", legacy_fallback="maop")
    try:
        with sqlite_connect(db_path) as conn:
            conn.executescript(SCHEMA_DDL)
            rows = conn.execute(
                "SELECT * FROM run_events WHERE trace_id = ? ORDER BY seq LIMIT ?",
                (trace_id, int(limit)),
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("[run_events] read failed for %s: %s", trace_id, exc)
        return []
    return [_row_to_event(row) for row in rows]


def fetch_recent_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Summarize the newest runs, one entry per trace_id, newest first."""
    db_path = get_db_path("run_events", legacy_fallback="maop")
    try:
        with sqlite_connect(db_path) as conn:
            conn.executescript(SCHEMA_DDL)
            traces = conn.execute(
                "SELECT trace_id, MIN(timestamp) AS started_at, MAX(timestamp) AS updated_at"
                " FROM run_events GROUP BY trace_id"
                " ORDER BY MAX(id) DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("[run_events] recent-run read failed: %s", exc)
        return []
    summaries: list[dict[str, Any]] = []
    for trace in traces:
        events = fetch_run_events(trace["trace_id"])
        started = next((e for e in events if e["kind"] == "run.started"), None)
        finished = next((e for e in events if e["kind"] == "run.finished"), None)
        steps = [e for e in events if e["kind"] == "run.step"]
        agent = (finished or started or {}).get("agent", "") or ""
        summaries.append({
            "trace_id": trace["trace_id"],
            "task": str((started or {}).get("payload", {}).get("task", "")),
            "agent": agent,
            "steps": len(steps),
            "event_count": len(events),
            "duration_ms": int((finished or {}).get("duration_ms", 0) or 0),
            "success": bool((finished or {}).get("payload", {}).get("success", False)) if finished else None,
            "started_at": trace["started_at"],
            "updated_at": trace["updated_at"],
            "status": "finished" if finished else "incomplete",
        })
    return summaries


#: One ``run.step`` event is one delegation attempt — the row the
#: ``delegations`` table was supposed to hold but never did (no production
#: writer ever called ``insert_delegation``).
STEP_KIND = "run.step"

#: Days of history ``prune_run_events`` keeps when the caller has no opinion.
DEFAULT_RETENTION_DAYS = 30


def agent_rollup(since: str | None = None) -> list[dict[str, Any]]:
    """Per-agent attempt totals, read from the event spine.

    ``since`` is compared lexicographically against the seconds-precision
    timestamps written here, so callers must format it with
    ``timespec="seconds"``; ``None`` means all history.
    """
    sql = (
        "SELECT agent, COUNT(*) AS total,"
        " SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS successes,"
        " AVG(duration_ms) AS avg_duration_ms"
        " FROM run_events WHERE kind = ? AND agent <> ?"
    )
    params: list[Any] = [STEP_KIND, ""]
    if since is not None:
        sql += " AND timestamp >= ?"
        params.append(since)
    sql += " GROUP BY agent"
    db_path = get_db_path("run_events", legacy_fallback="maop")
    try:
        with sqlite_connect(db_path) as conn:
            conn.executescript(SCHEMA_DDL)
            rows = conn.execute(sql, tuple(params)).fetchall()
    except sqlite3.Error as exc:
        logger.warning("[run_events] agent rollup failed: %s", exc)
        return []
    rollup: list[dict[str, Any]] = []
    for row in rows:
        total = int(row["total"] or 0)
        successes = int(row["successes"] or 0)
        rollup.append({
            "agent": row["agent"],
            "total": total,
            "successes": successes,
            "success_rate": (successes / total) if total else 0.0,
            "avg_duration_ms": int(row["avg_duration_ms"] or 0),
        })
    return rollup


def recent_steps(limit: int = 20, since: str | None = None) -> list[dict[str, Any]]:
    """Newest delegation attempts, flattened for the live panel."""
    sql = (
        "SELECT * FROM run_events WHERE kind = ? AND agent <> ?"
    )
    params: list[Any] = [STEP_KIND, ""]
    if since is not None:
        sql += " AND timestamp >= ?"
        params.append(since)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    db_path = get_db_path("run_events", legacy_fallback="maop")
    try:
        with sqlite_connect(db_path) as conn:
            conn.executescript(SCHEMA_DDL)
            rows = conn.execute(sql, tuple(params)).fetchall()
    except sqlite3.Error as exc:
        logger.warning("[run_events] recent-step read failed: %s", exc)
        return []
    steps: list[dict[str, Any]] = []
    for row in rows:
        event = _row_to_event(row)
        payload = event["payload"]
        steps.append({
            "agent": event["agent"],
            "task": str(payload.get("task", "")),
            "exit_code": int(payload.get("exit_code", 0) or 0),
            "duration_ms": event["duration_ms"],
            "timestamp": event["timestamp"],
            "status": event["status"],
            "trace_id": event["trace_id"],
        })
    return steps


def prune_run_events(older_than_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """Delete events older than the retention window; returns rows removed.

    A bounded window is deliberate: ``run_events`` is append-only, so without a
    prune one chatty agent grows ``maop.db`` forever — while the window still has
    to stay wide enough that a run is reviewable after the fact.
    """
    days = max(int(older_than_days), 1)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds"
    )
    db_path = get_db_path("run_events", legacy_fallback="maop")
    try:
        with sqlite_connect(db_path) as conn:
            conn.executescript(SCHEMA_DDL)
            cursor = conn.execute(
                "DELETE FROM run_events WHERE timestamp < ?", (cutoff,)
            )
            return int(cursor.rowcount or 0)
    except sqlite3.Error as exc:
        logger.warning("[run_events] prune failed: %s", exc)
        return 0
