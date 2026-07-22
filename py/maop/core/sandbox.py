"""MAOP Sandbox - Working-directory-scoped execution environment management.

SECURITY NOTICE: This module provides working-directory isolation and timeout
enforcement only. It does NOT provide OS-level sandboxing (no containers,
chroot, seccomp, or namespace isolation). Code running inside a "sandbox"
can still access the full filesystem, network, and OS resources of the host
process. For true isolation, use an external container runtime.

Sandboxed execution environment for plugin code. to pure Python with SQLite-backed index.
Actions: create, run, cleanup, list, info.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from maop.core.db_utils import sqlite_connect

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ── Models ──────────────────────────────────────────────────────

class SandboxInfo(BaseModel):
    """Sandbox metadata."""
    id: str = ""
    created: str = ""
    status: str = "active"  # active | expired | cleaned
    path: str = ""
    command: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    output_lines: int = 0


class SandboxResult(BaseModel):
    """Result of a sandbox run."""
    ok: bool = True
    exit_code: int = 0
    duration_ms: int = 0
    output_lines: int = 0
    log: str = ""
    error: str = ""


# ── SandboxManager ────────────────────────────────────────────

class SandboxManager:
    """Manage isolated execution sandboxes.

    Usage::

        mgr = SandboxManager(root_dir="/path/to/MAOP")
        sb = mgr.create()
        result = mgr.run(sb.id, command="echo hello")
        mgr.cleanup(sb.id)
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._sandbox_dir = self._root / "data" / "sandboxes"
        self._db_path = self._sandbox_dir / "sandbox_index.db"
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Create table if not exists."""
        self._sandbox_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sandboxes (
                    id TEXT PRIMARY KEY,
                    created TEXT NOT NULL,
                    status TEXT DEFAULT 'active',
                    path TEXT NOT NULL,
                    command TEXT DEFAULT '',
                    exit_code INTEGER DEFAULT 0,
                    duration_ms INTEGER DEFAULT 0,
                    output_lines INTEGER DEFAULT 0
                )
            """)

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    def _new_id(self) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"sb-{ts}-{uuid.uuid4().hex[:8]}"

    # ── Actions ──────────────────────────────────────────────

    def create(self, sandbox_id: str = "") -> SandboxInfo:
        """Create a new sandbox with isolated directories."""
        sb_id = sandbox_id or self._new_id()
        # Validate ID: alphanumeric + dash/underscore only
        if not re.match(r'^[A-Za-z0-9_-]+$', sb_id):
            raise ValueError(f"Invalid SandboxId: {sb_id}")

        sb_path = self._sandbox_dir / sb_id
        sb_path.mkdir(parents=True, exist_ok=True)
        (sb_path / "input").mkdir(exist_ok=True)
        (sb_path / "output").mkdir(exist_ok=True)
        (sb_path / "temp").mkdir(exist_ok=True)

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sandboxes (id, created, status, path) VALUES (?, ?, 'active', ?)",
                (sb_id, now, str(sb_path)),
            )

        return SandboxInfo(id=sb_id, created=now, status="active", path=str(sb_path))

    async def arun(
        self,
        sandbox_id: str,
        command: str,
        timeout_seconds: int = 30,
        max_output_lines: int = 500,
        work_dir: str = "",
    ) -> SandboxResult:
        """Run a command in the sandbox asynchronously (non-blocking).

        WARNING: This only enforces working-directory and timeout constraints.
        The command runs with full host OS permissions — no container/chroot
        isolation is applied. See module docstring for details.
        """
        sb = await asyncio.to_thread(self.get, sandbox_id)
        if sb is None:
            return SandboxResult(ok=False, error=f"sandbox not found: {sandbox_id}")

        exec_dir = Path(work_dir) if work_dir else Path(sb.path)
        exec_dir.mkdir(parents=True, exist_ok=True)

        log_file = exec_dir / f"sandbox-run-{datetime.now().strftime('%H%M%S')}.log"
        start = time.monotonic()

        try:

            import shlex
            cmd_parts = shlex.split(command)
            if not cmd_parts:
                return SandboxResult(ok=False, error="Empty command", duration_ms=int((time.monotonic() - start) * 1000))
            if sys.platform == "win32":
                _win_builtins = {"echo", "dir", "type", "copy", "move", "del", "mkdir", "md",
                    "rmdir", "rd", "cd", "chdir", "set", "path", "ver", "cls", "ren", "rename",
                    "call", "start", "find", "findstr", "sort", "more", "choice"}
                if os.path.basename(cmd_parts[0]).lower() in _win_builtins:
                    cmd_parts = ["cmd.exe", "/c", subprocess.list2cmdline(cmd_parts)]

            proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(exec_dir),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_seconds
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                elapsed_ms = int((time.monotonic() - start) * 1000)
                return SandboxResult(ok=False, error="timeout", duration_ms=elapsed_ms)

            elapsed_ms = int((time.monotonic() - start) * 1000)
            output = stdout.decode("utf-8", errors="replace")
            lines = output.splitlines()
            if len(lines) > max_output_lines:
                output = "\n".join(lines[:max_output_lines]) + f"\n... [truncated {len(lines) - max_output_lines} lines]"

            log_file.write_text(output, encoding="utf-8")

            def _update_db() -> None:
                with self._connect() as conn:
                    conn.execute(
                        "UPDATE sandboxes SET command=?, exit_code=?, duration_ms=?, output_lines=? WHERE id=?",
                        (command, proc.returncode, elapsed_ms, len(lines), sandbox_id),
                    )

            await asyncio.to_thread(_update_db)

            return SandboxResult(
                ok=True,
                exit_code=proc.returncode or 0,
                duration_ms=elapsed_ms,
                output_lines=len(lines),
                log=str(log_file),
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return SandboxResult(ok=False, error=str(exc), duration_ms=elapsed_ms)

    def run(
        self,
        sandbox_id: str,
        command: str,
        timeout_seconds: int = 30,
        max_output_lines: int = 500,
        work_dir: str = "",
    ) -> SandboxResult:
        """Run a command in the sandbox."""
        sb = self.get(sandbox_id)
        if sb is None:
            return SandboxResult(ok=False, error=f"sandbox not found: {sandbox_id}")

        exec_dir = Path(work_dir) if work_dir else Path(sb.path)
        exec_dir.mkdir(parents=True, exist_ok=True)

        log_file = exec_dir / f"sandbox-run-{datetime.now().strftime('%H%M%S')}.log"
        start = time.monotonic()

        try:
            # Security: use list form instead of shell=True to prevent command injection.
            # Handle Windows shell built-ins (echo, dir, etc.) via cmd.exe /c.
            import shlex
            cmd_parts = shlex.split(command)
            if not cmd_parts:
                return SandboxResult(ok=False, error="Empty command", duration_ms=int((time.monotonic() - start) * 1000))
            if sys.platform == "win32":
                _win_builtins = {"echo", "dir", "type", "copy", "move", "del", "mkdir", "md",
                    "rmdir", "rd", "cd", "chdir", "set", "path", "ver", "cls", "ren", "rename",
                    "call", "start", "find", "findstr", "sort", "more", "choice"}
                if os.path.basename(cmd_parts[0]).lower() in _win_builtins:
                    # Security: use subprocess.list2cmdline for safe Windows quoting
                    # instead of plain " ".join which doesn't handle special chars
                    cmd_parts = ["cmd.exe", "/c", subprocess.list2cmdline(cmd_parts)]

            proc = subprocess.run(
                cmd_parts,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=str(exec_dir),
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            output = proc.stdout
            lines = output.splitlines()
            if len(lines) > max_output_lines:
                output = "\n".join(lines[:max_output_lines]) + f"\n... [truncated {len(lines) - max_output_lines} lines]"

            log_file.write_text(output, encoding="utf-8")

            # Update sandbox record
            with self._connect() as conn:
                conn.execute(
                    "UPDATE sandboxes SET command=?, exit_code=?, duration_ms=?, output_lines=? WHERE id=?",
                    (command, proc.returncode, elapsed_ms, len(lines), sandbox_id),
                )

            return SandboxResult(
                ok=True,
                exit_code=proc.returncode,
                duration_ms=elapsed_ms,
                output_lines=len(lines),
                log=str(log_file),
            )
        except subprocess.TimeoutExpired:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return SandboxResult(ok=False, error="timeout", duration_ms=elapsed_ms)
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return SandboxResult(ok=False, error=str(exc), duration_ms=elapsed_ms)

    def cleanup(self, sandbox_id: str) -> bool:
        """Clean up a sandbox: remove files and mark as cleaned."""
        sb = self.get(sandbox_id)
        if sb is None:
            return False

        sb_path = Path(sb.path)
        if sb_path.exists():
            try:
                shutil.rmtree(sb_path)
            except Exception as exc:
                logger.warning("Failed to remove sandbox dir %s: %s", sb_path, exc)

        with self._connect() as conn:
            conn.execute(
                "UPDATE sandboxes SET status='cleaned' WHERE id=?",
                (sandbox_id,),
            )
        return True

    def list_all(self, status: str = "", limit: int = 50) -> list[SandboxInfo]:
        """List sandboxes, optionally filtered by status."""
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM sandboxes WHERE status=? ORDER BY created DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sandboxes ORDER BY created DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_info(r) for r in rows]

    def get(self, sandbox_id: str) -> SandboxInfo | None:
        """Get sandbox info by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sandboxes WHERE id=?", (sandbox_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_info(row)

    def cleanup_expired(self, hours: int = 24) -> int:
        """Clean up sandboxes older than N hours."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, path FROM sandboxes WHERE status='active' AND created < datetime('now', ?)",
                (f"-{hours} hours",),
            ).fetchall()

        count = 0
        for row in rows:
            if self.cleanup(row["id"]):
                count += 1
        return count

    def stats(self) -> dict[str, int]:
        """Get sandbox statistics."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM sandboxes GROUP BY status"
            ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    # ── Internal ─────────────────────────────────────────────

    def _row_to_info(self, row: sqlite3.Row) -> SandboxInfo:
        return SandboxInfo(
            id=row["id"],
            created=row["created"],
            status=row["status"],
            path=row["path"],
            command=row["command"],
            exit_code=row["exit_code"],
            duration_ms=row["duration_ms"],
            output_lines=row["output_lines"],
        )
