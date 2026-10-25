"""Run sharing service — publish a run's *summary* as a read-only link.

Why only the summary
--------------------
``run_service.get_run_detail`` returns ``events`` whose payload carries
``stdout`` / ``stderr`` / ``error``. Those routinely contain absolute paths,
env values and occasionally credentials echoed by an agent. A share link is
by definition readable by someone who was not authorized to see the run, so
this module **never stores or returns event payloads** — it stores the
``_summarize`` output only (task, agent, status, duration, step count, verify
verdict, cost). That is enough to answer "what happened and did it pass"
without leaking the internals.

Token model
-----------
* ``secrets.token_urlsafe(32)`` — 256 bits, not guessable, not sequential.
* Optional expiry (``expires_in_hours``); expired rows are treated as absent,
  so a leaked link dies on schedule rather than living forever.
* Revocation deletes the row outright — no "revoked" state to get wrong.

The public read path intentionally does **not** require admin: possession of
the token *is* the authorization. Everything that creates or revokes a link
does require admin.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from typing import Any

__all__ = ["create_share", "get_share", "list_shares", "revoke_share"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_shares (
    token       TEXT PRIMARY KEY,
    trace_id    TEXT NOT NULL,
    summary     TEXT NOT NULL,
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL DEFAULT 0
)
"""


def _connect() -> sqlite3.Connection:
    from maop.core.backends.db_utils import get_db_path

    db_path = get_db_path("run_shares")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    return conn


def create_share(
    trace_id: str,
    *,
    expires_in_hours: int = 168,
    created_by: str = "api",
) -> dict[str, Any]:
    """Create a share link for *trace_id*.

    Raises
    ------
    KeyError
        When the run has no recorded events (nothing to share).
    ValueError
        When ``expires_in_hours`` is negative.
    """
    if expires_in_hours < 0:
        raise ValueError("expires_in_hours must be >= 0")

    from maop.dashboard.services import run_service

    detail = run_service.get_run_detail(trace_id)
    if detail is None:
        raise KeyError(trace_id)

    # Only the summary crosses the boundary — see module docstring.
    summary = detail["summary"]
    token = secrets.token_urlsafe(32)
    now = time.time()
    expires_at = now + expires_in_hours * 3600 if expires_in_hours else 0.0

    import json

    with _connect() as conn:
        conn.execute(
            "INSERT INTO run_shares (token, trace_id, summary, created_by, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (token, trace_id, json.dumps(summary, ensure_ascii=False), created_by, now, expires_at),
        )

    return {
        "token": token,
        "trace_id": trace_id,
        "path": f"/share/{token}",
        "created_at": now,
        "expires_at": expires_at,
    }


def get_share(token: str) -> dict[str, Any] | None:
    """Return the shared summary, or ``None`` when unknown / expired."""
    import json

    with _connect() as conn:
        row = conn.execute(
            "SELECT token, trace_id, summary, created_at, expires_at"
            " FROM run_shares WHERE token = ?",
            (token,),
        ).fetchone()

    if row is None:
        return None
    if row["expires_at"] and row["expires_at"] < time.time():
        # Expired rows are reported as absent; the caller must not be able to
        # distinguish "never existed" from "expired".
        return None
    return {
        "token": row["token"],
        "trace_id": row["trace_id"],
        "summary": json.loads(row["summary"]),
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
    }


def revoke_share(token: str) -> bool:
    """Delete a share link. Returns ``False`` when the token was unknown."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM run_shares WHERE token = ?", (token,))
        return cur.rowcount > 0


def list_shares() -> list[dict[str, Any]]:
    """List active share links (newest first), excluding expired ones."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT token, trace_id, created_at, expires_at FROM run_shares"
            " ORDER BY created_at DESC"
        ).fetchall()
    now = time.time()
    return [
        {
            "token": r["token"],
            "trace_id": r["trace_id"],
            "created_at": r["created_at"],
            "expires_at": r["expires_at"],
        }
        for r in rows
        if not r["expires_at"] or r["expires_at"] >= now
    ]
