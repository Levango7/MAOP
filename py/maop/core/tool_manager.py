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

Version compatibility (2026-07-24): ``ToolDef`` now carries ``version``
and ``min_platform_version`` fields. ``register()`` rejects tools whose
``min_platform_version`` exceeds the running MAOP version, and ``call()``
re-checks compatibility before invocation. Version parsing never raises
— on parse failure the check fails open (treated as compatible) and a
warning is logged.
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


def _get_maop_version() -> str:
    """Return the running MAOP version, or '' if it cannot be determined."""
    try:
        from maop import __version__ as MAOP_VERSION
        return MAOP_VERSION
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("could not determine MAOP version: %s", exc)
        return ""


def _parse_version(v: str) -> Any:
    """Parse a version string into a comparable object.

    Prefers ``packaging.version.Version`` when available; falls back to a
    tuple of integers so two results of this function are always mutually
    comparable. Never raises — returns ``(0,)`` on total parse failure.
    """
    if not v:
        return (0,)
    try:
        from packaging.version import Version
        return Version(v)
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("packaging.version.Version(%r) failed: %s", v, exc)
    # Fallback: tuple of ints, stripping any non-digit suffix per segment.
    parts: list[int] = []
    for seg in str(v).split("."):
        num = ""
        for ch in seg:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts) or (0,)


def _is_version_compatible(min_platform_version: str) -> bool:
    """Return True if the running MAOP version satisfies
    ``min_platform_version``.

    Fails open (returns True) when the requirement is empty or when the
    comparison cannot be performed, logging a warning in the latter case
    so the system never crashes due to version checking.
    """
    if not min_platform_version:
        return True
    current = _get_maop_version()
    if not current:
        logger.warning(
            "skip version compatibility check: MAOP version unknown; "
            "tool requires >= %s",
            min_platform_version,
        )
        return True
    try:
        if _parse_version(current) >= _parse_version(min_platform_version):
            return True
        logger.warning(
            "version incompatible: MAOP %s < required %s",
            current, min_platform_version,
        )
        return False
    except Exception as exc:
        logger.warning(
            "version check failed for min_platform_version=%r: %s",
            min_platform_version, exc,
        )
        return True


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
    version: str = "1.0"
    min_platform_version: str = ""


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
                    call_count INTEGER DEFAULT 0,
                    version TEXT DEFAULT '1.0',
                    min_platform_version TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tools_category
                ON tools(category)
            """)
            # Best-effort migration for pre-existing databases: add the
            # new columns if they are missing. ALTER TABLE ADD COLUMN
            # fails if the column already exists, so guard with PRAGMA.
            existing = {
                r["name"]
                for r in conn.execute("PRAGMA table_info(tools)").fetchall()
            }
            if "version" not in existing:
                conn.execute(
                    "ALTER TABLE tools ADD COLUMN version TEXT DEFAULT '1.0'"
                )
            if "min_platform_version" not in existing:
                conn.execute(
                    "ALTER TABLE tools ADD COLUMN min_platform_version TEXT DEFAULT ''"
                )

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
        version: str = "1.0",
        min_platform_version: str = "",
    ) -> str:
        """Register a new tool or update an existing one.

        Version compatibility: if ``min_platform_version`` is set and the
        running MAOP version is older, registration is rejected (a
        ``ValueError`` is raised) and a warning is logged. Version parse
        failures fail open (the tool is registered).
        """
        if not tool_id or not tool_id.strip():
            raise ValueError("tool_id must be non-empty")
        if not command or not command.strip():
            raise ValueError("command must be non-empty")
        if not _is_version_compatible(min_platform_version):
            logger.warning(
                "rejecting registration of tool %r: requires MAOP >= %s",
                tool_id, min_platform_version,
            )
            raise ValueError(
                f"tool {tool_id!r} requires MAOP >= {min_platform_version}; "
                f"current MAOP version is incompatible"
            )
        name = name or tool_id
        params_json = json.dumps(params or {})
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO tools (id, name, description, command, category, params, enabled, created, version, min_platform_version)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name,
                     description=excluded.description,
                     command=excluded.command,
                     category=excluded.category,
                     params=excluded.params,
                     version=excluded.version,
                     min_platform_version=excluded.min_platform_version
                """,
                (tool_id, name, description, command, category, params_json, now, version, min_platform_version),
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

        Version compatibility (2026-07-24): re-checks the tool's
        ``min_platform_version`` before invocation and refuses to run
        incompatible tools (logging a warning) instead of crashing.
        """
        tool = self.info(tool_id)
        if tool is None:
            return ToolCallResult(ok=False, error=f"tool not found: {tool_id}")
        if not tool.enabled:
            return ToolCallResult(ok=False, error=f"tool disabled: {tool_id}")
        if not _is_version_compatible(tool.min_platform_version):
            return ToolCallResult(
                ok=False,
                error=(
                    f"tool {tool_id} requires MAOP >= {tool.min_platform_version}; "
                    f"current MAOP version is incompatible"
                ),
            )

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

        Version compatibility (2026-07-24): re-checks
        ``min_platform_version`` before invocation, mirroring ``call()``.
        """
        tool = self.info(tool_id)
        if tool is None:
            return ToolCallResult(ok=False, error=f"tool not found: {tool_id}")
        if not tool.enabled:
            return ToolCallResult(ok=False, error=f"tool disabled: {tool_id}")
        if not _is_version_compatible(tool.min_platform_version):
            return ToolCallResult(
                ok=False,
                error=(
                    f"tool {tool_id} requires MAOP >= {tool.min_platform_version}; "
                    f"current MAOP version is incompatible"
                ),
            )

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

        Version compatibility (2026-07-24): populates ``version`` and
        ``min_platform_version``; uses ``dict(row)`` lookup so legacy
        rows (or non-migrated DBs) do not raise KeyError.
        """
        params = {}
        try:
            params = json.loads(row["params"] or "{}")
        except (json.JSONDecodeError, ValueError):
            pass
        cols = set(row.keys()) if hasattr(row, "keys") else set()
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
            version=row["version"] if "version" in cols else "1.0",
            min_platform_version=row["min_platform_version"] if "min_platform_version" in cols else "",
        )