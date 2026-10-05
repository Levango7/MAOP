"""Marketplace sandbox — isolated execution with whitelist environment.

.. warning::

   **本模块未接入任何运行时路径（2026-09-24 取证）。**

   AST 导入分析确认：全仓**零运行时导入方**，仅被
   ``tests/test_marketplace_f301.py`` 导入（测试全绿，覆盖白名单剥离
   JWT_SECRET / DB_PASSWORD / API_KEY）。

   **已复核：当前无实际密钥泄漏。** 全仓搜索 ``os.environ.copy()`` 仅命中
   本模块自身的注释（说明它替换了什么），线上代码并未使用该危险写法。
   换言之：**没有漏洞，但这份防护也没生效** —— 它是为未来的沙箱执行路径
   准备的，而那条路径尚未接入本模块。

   登记于 ``scripts/check_wiring.py`` 的 ``KNOWN_UNWIRED``。

G-02 security fix: replaces ``os.environ.copy()`` (which leaks *all*
environment variables including JWT_SECRET, DB_PASSWORD, API_KEY, etc.
into sandboxed subprocesses) with a strict whitelist policy.

Policy
------
Only environment variables whose name starts with ``MAOP_SANDBOX_`` are
forwarded to the sandboxed subprocess. All other variables — including
sensitive secrets — are stripped.

Additionally, a fixed set of "safe" variables (``PATH``, ``HOME``,
``LANG``, ``SYSTEMROOT`` on Windows) are forwarded so that the
subprocess can actually run basic commands. These safe variables do
not include any secrets.

Custom whitelist
----------------
A project-root ``.env.sandbox`` file (or the path pointed to by the
``MAOP_SANDBOX_ENV_FILE`` environment variable) can override the
built-in safe set. Each line is ``KEY=yes`` / ``KEY=no``; variables
marked ``yes`` are forwarded, everything else is stripped. If the
file is absent the built-in :data:`_SAFE_ENV_VARS` defaults apply.

Usage
-----
::

    mgr = SandboxManager(root_dir="/path/to/maop")
    sb = mgr.create()
    result = mgr.run(sb.id, command="python plugin.py")

The :meth:`SandboxManager.run` method uses :func:`build_sandbox_env`
to construct the child process environment.
"""

from __future__ import annotations

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

from pydantic import BaseModel

from maop.core.backends.db_utils import sqlite_connect

# 白名单实现已统一到 core/security/sandbox.py（本模块原先那份从未接线）。
from maop.core.security.sandbox import _BLOCKED_ENV_VARS, build_sandbox_env

logger = logging.getLogger(__name__)

# ── Environment whitelist policy (G-02) ────────────────────────────

# Variables whose name starts with this prefix are forwarded.


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
    """Manage isolated execution sandboxes with whitelist env.

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

    def run(
        self,
        sandbox_id: str,
        command: str,
        timeout_seconds: int = 30,
        max_output_lines: int = 500,
        work_dir: str = "",
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        """Run a command in the sandbox.

        G-02 fix: the child process environment is built via
        :func:`build_sandbox_env` — only MAOP_SANDBOX_* and a minimal
        set of safe system variables are forwarded. Sensitive variables
        (JWT_SECRET, DB_PASSWORD, API_KEY, …) are never leaked.

        Parameters
        ----------
        env : dict[str, str] | None
            Additional environment overrides. These are merged *after*
            the whitelist filter, so callers can explicitly inject
            specific variables. Keys in *env* that are in the blocked
            list are still rejected.
        """
        sb = self.get(sandbox_id)
        if sb is None:
            return SandboxResult(ok=False, error=f"sandbox not found: {sandbox_id}")

        exec_dir = Path(work_dir) if work_dir else Path(sb.path)
        exec_dir.mkdir(parents=True, exist_ok=True)

        log_file = exec_dir / f"sandbox-run-{datetime.now(timezone.utc).strftime('%H%M%S')}.log"
        start = time.monotonic()

        # G-02: build whitelist env instead of os.environ.copy()
        child_env = build_sandbox_env()
        if env:
            for k, v in env.items():
                if k in _BLOCKED_ENV_VARS:
                    logger.warning("[sandbox] refused to inject blocked env var %s", k)
                    continue
                child_env[k] = v

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

            proc = subprocess.run(  # noqa: PLW1510
                cmd_parts,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=str(exec_dir),
                env=child_env,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            output = proc.stdout
            lines = output.splitlines()
            if len(lines) > max_output_lines:
                output = "\n".join(lines[:max_output_lines]) + f"\n... [truncated {len(lines) - max_output_lines} lines]"

            log_file.write_text(output, encoding="utf-8")

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