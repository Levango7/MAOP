"""MAOP ToolManager — Register, discover, and invoke external tools.

Each tool is identified by a unique ``tool_id`` and backed by a shell
command (parsed with ``shlex`` to prevent injection). Tools are persisted
in the unified MAOP SQLite database.

F7 (2026-07-22, Phase F): ``call()`` is now ``async`` and uses
``asyncio.create_subprocess_exec`` instead of the blocking
``subprocess.run``. ``stderr`` is now captured into
``ToolCallResult.error`` for debugging. A backward-compatible sync
wrapper ``call_sync()`` is preserved for non-async callers (e.g.
dashboard data_bridge). See ``docs/adr/013-agent-llm-direct-cli-fallback.md``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, cast

from maop.core.db_utils import get_db_path, sqlite_connect

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Models ──────────────────────────────────────────────────────

class ToolDef(BaseModel):
    """A registered tool."""
    id: str = ""
    name: str = ""
    description: str = ""
    command: str = ""
    category: str = "general"
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    created: str = ""
    last_called: str | None = None
    call_count: int = 0


class ToolCallResult(BaseModel):
    """Result of a tool call."""
    ok: bool = True
    exit_code: int = 0
    duration_ms: int = 0
    output: str = ""
    error: str = ""


# ── ToolManager ──────────────────────────────────────────────

class ToolManager:
    """Register, discover, and invoke external tools.

    Usage::

        mgr = ToolManager(root_dir="/path/to/MAOP")
        mgr.register("lint", command="ruff check", category="quality")
        tools = mgr.list()
        result = await mgr.call("lint", args=["src/"])  # F7: now async
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._db_path = get_db_path("tool_manager")
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Create table if not exists."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tools (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    command TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    params TEXT DEFAULT '{}',
                    enabled INTEGER DEFAULT 1,
                    created TEXT NOT NULL,
                    last_called TEXT,
                    call_count INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tools_category
                ON tools(category)
            """)

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    # ── Actions ──────────────────────────────────────────────

    def register(
        self,
        tool_id: str,
        command: str,
        name: str = "",
        description: str = "",
        category: str = "general",
        params: dict[str, Any] | None = None,
    ) -> str:
        """Register a new tool or update an existing one."""
        if not tool_id or not tool_id.strip():
            raise ValueError("tool_id must be non-empty")
        if not command or not command.strip():
            raise ValueError("command must be non-empty")
        name = name or tool_id
        params_json = json.dumps(params or {})
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO tools (id, name, description, command, category, params, enabled, created)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name,
                     description=excluded.description,
                     command=excluded.command,
                     category=excluded.category,
                     params=excluded.params
                """,
                (tool_id, name, description, command, category, params_json, now),
            )
        return tool_id

    def list(self, category: str = "") -> List[dict[str, Any]]:
        """List tools grouped by category."""
        with self._connect() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM tools WHERE category=? ORDER BY name", (category,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tools ORDER BY category, name"
                ).fetchall()

        # Group by category
        by_category: dict[str, list] = {}
        for r in rows:
            cat = r["category"]
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append({
                "id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "enabled": bool(r["enabled"]),
                "category": r["category"],
            })

        return [{"category": cat, "tools": tools} for cat, tools in sorted(by_category.items())]

    def find(self, query: str) -> List[ToolDef]:
        """Find tools by free-text query (matches id, name, description, category)."""
        pattern = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tools WHERE id LIKE ? OR name LIKE ? OR description LIKE ? OR category LIKE ?",
                (pattern, pattern, pattern, pattern),
            ).fetchall()
        return [self._row_to_tool(r) for r in rows]

    def info(self, tool_id: str) -> ToolDef | None:
        """Get info for a specific tool."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tools WHERE id=?", (tool_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_tool(row)

    def enable(self, tool_id: str) -> bool:
        """Enable a tool."""
        with self._connect() as conn:
            cursor = conn.execute("UPDATE tools SET enabled=1 WHERE id=?", (tool_id,))
            return cast(bool, cursor.rowcount > 0)

    def disable(self, tool_id: str) -> bool:
        """Disable a tool."""
        with self._connect() as conn:
            cursor = conn.execute("UPDATE tools SET enabled=0 WHERE id=?", (tool_id,))
            return cast(bool, cursor.rowcount > 0)

    async def call(
        self,
        tool_id: str,
        args: List[str] | None = None,
        timeout_seconds: int = 30,
    ) -> ToolCallResult:
        """Invoke a registered tool.

        F7 (2026-07-22, Phase F): now ``async`` — uses
        ``asyncio.create_subprocess_exec`` instead of blocking
        ``subprocess.run`` so the event loop is not blocked during
        long-running tool invocations. ``stderr`` is captured into
        ``ToolCallResult.error`` for debugging (previously dropped).
        See ADR-013.
        """
        tool = self.info(tool_id)
        if tool is None:
            return ToolCallResult(ok=False, error=f"tool not found: {tool_id}")
        if not tool.enabled:
            return ToolCallResult(ok=False, error=f"tool disabled: {tool_id}")

        # Parse command safely with shlex (no shell injection) and append args as list elements
        cmd_parts = shlex.split(tool.command) + (args or [])
        start = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_seconds
                )
            except asyncio.TimeoutError:
                # Kill the timed-out process to avoid orphaned children
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
                elapsed_ms = int((time.monotonic() - start) * 1000)
                return ToolCallResult(
                    ok=False, exit_code=-1,
                    duration_ms=elapsed_ms, error="timeout",
                )

            elapsed_ms = int((time.monotonic() - start) * 1000)
            stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
            stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""

            # Update call stats
            now = datetime.now(timezone.utc).isoformat()
            with self._connect() as conn:
                conn.execute(
                    "UPDATE tools SET last_called=?, call_count=call_count+1 WHERE id=?",
                    (now, tool_id),
                )

            # F7b: capture stderr into error field for debugging
            return ToolCallResult(
                ok=proc.returncode == 0,
                exit_code=proc.returncode if proc.returncode is not None else -1,
                duration_ms=elapsed_ms,
                output=stdout,
                error=stderr if proc.returncode != 0 else "",
            )
        except FileNotFoundError as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return ToolCallResult(
                ok=False, exit_code=-2,
                duration_ms=elapsed_ms,
                error=f"command not found: {exc}",
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return ToolCallResult(
                ok=False, exit_code=-3,
                duration_ms=elapsed_ms, error=str(exc),
            )

    def call_sync(
        self,
        tool_id: str,
        args: List[str] | None = None,
        timeout_seconds: int = 30,
    ) -> ToolCallResult:
        """Backward-compatible sync wrapper around ``call()``.

        F7 (2026-07-22, Phase F): for non-async callers (e.g. dashboard
        data_bridge). Internally runs the async ``call()`` via
        ``asyncio.run()``. Prefer ``await mgr.call(...)`` in async code.
        """
        try:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    self.call(tool_id, args=args, timeout_seconds=timeout_seconds)
                )
            finally:
                loop.close()
        except RuntimeError:
            # Already inside a running loop (e.g. called from sync within async)
            # — fall back to direct subprocess.run to avoid nested loop errors.
            return self._call_sync_fallback(tool_id, args, timeout_seconds)

    def _call_sync_fallback(
        self,
        tool_id: str,
        args: List[str] | None,
        timeout_seconds: int,
    ) -> ToolCallResult:
        """Legacy synchronous subprocess.run path — used only when a
        running event loop blocks ``call_sync()``.
        """
        tool = self.info(tool_id)
        if tool is None:
            return ToolCallResult(ok=False, error=f"tool not found: {tool_id}")
        if not tool.enabled:
            return ToolCallResult(ok=False, error=f"tool disabled: {tool_id}")

        cmd_parts = shlex.split(tool.command) + (args or [])
        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd_parts, capture_output=True, text=True,
                timeout=timeout_seconds, shell=False,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            now = datetime.now(timezone.utc).isoformat()
            with self._connect() as conn:
                conn.execute(
                    "UPDATE tools SET last_called=?, call_count=call_count+1 WHERE id=?",
                    (now, tool_id),
                )
            return ToolCallResult(
                ok=proc.returncode == 0,
                exit_code=proc.returncode,
                duration_ms=elapsed_ms,
                output=proc.stdout,
                error=proc.stderr if proc.returncode != 0 else "",
            )
        except subprocess.TimeoutExpired:
            return ToolCallResult(ok=False, error="timeout")
        except Exception as exc:
            return ToolCallResult(ok=False, error=str(exc))

    def delete(self, tool_id: str) -> bool:
        """Delete a registered tool."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM tools WHERE id=?", (tool_id,))
            return cast(bool, cursor.rowcount > 0)

    def stats(self) -> dict[str, Any]:
        """Get tool statistics.

        F7 (2026-07-22, Phase F): preserved the pre-Phase-F return
        schema (``total / enabled / disabled / total_calls /
        by_category``) for backward compatibility with dashboard
        data_bridge and existing tests.
        """
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) as cnt FROM tools").fetchone()["cnt"]
            enabled = conn.execute("SELECT COUNT(*) as cnt FROM tools WHERE enabled=1").fetchone()["cnt"]
            by_category = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM tools GROUP BY category"
            ).fetchall()
            total_calls = conn.execute("SELECT SUM(call_count) as cnt FROM tools").fetchone()["cnt"] or 0

        return {
            "total": total,
            "enabled": enabled,
            "disabled": total - enabled,
            "total_calls": total_calls,
            "by_category": {r["category"]: r["cnt"] for r in by_category},
        }

    # ── Row converters ────────────────────────────────────────

    @staticmethod
    def _row_to_tool(row: Any) -> ToolDef:
        """Convert a SQLite Row to a ToolDef model.

        F7 (2026-07-22, Phase F): renamed from ``_row_to_model`` to
        preserve the pre-Phase-F method name (callers in dashboard
        data_bridge rely on it).
        """
        params = {}
        try:
            params = json.loads(row["params"] or "{}")
        except (json.JSONDecodeError, ValueError):
            pass
        return ToolDef(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            command=row["command"],
            category=row["category"],
            params=params,
            enabled=bool(row["enabled"]),
            created=row["created"],
            last_called=row["last_called"],
            call_count=row["call_count"],
        )
