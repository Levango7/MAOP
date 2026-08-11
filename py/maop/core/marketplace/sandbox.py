"""Marketplace sandbox — isolated execution with whitelist environment.

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

logger = logging.getLogger(__name__)

# ── Environment whitelist policy (G-02) ────────────────────────────

# Variables whose name starts with this prefix are forwarded.
_SANDBOX_ENV_PREFIX = "MAOP_SANDBOX_"

# A minimal set of "safe" variables required for the subprocess to run.
# These are system-level variables that do not contain secrets.
#
# Classification (see .env.sandbox for user-tunable overrides):
#   - 必需变量 (required): PATH, HOME, USER, SYSTEMROOT, TEMP, TMP
#   - 安全变量 (safe):     LANG, LC_ALL, LC_CTYPE, TMPDIR, COMSPEC,
#                          APPDATA, LOCALAPPDATA, PROGRAMDATA
#   - 业务变量 (business): MAOP_* — forwarded only when listed in
#                          .env.sandbox or matching MAOP_SANDBOX_*
_SAFE_ENV_VARS: frozenset[str] = frozenset({
    # ── 必需变量（系统运行必需，不建议禁用）──────────────────
    "PATH",
    "HOME",
    "USER",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    # ── 安全变量（不影响安全性）──────────────────────────────
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    # Windows runtime / system DLL resolution helpers (safe, no secrets).
    "COMSPEC",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
})

# Variables that must NEVER be forwarded even if they match the prefix.
# This is a defence-in-depth deny-list; the whitelist already excludes them.
_BLOCKED_ENV_VARS: frozenset[str] = frozenset({
    "JWT_SECRET",
    "DB_PASSWORD",
    "API_KEY",
    "SECRET_KEY",
    "MAOP_JWT_SECRET",
    "MAOP_DB_PASSWORD",
    "MAOP_API_KEY",
    "MAOP_SECRET_KEY",
    "DATABASE_URL",
    "REDIS_URL",
    "MAOP_DATABASE_URL",
    "MAOP_REDIS_URL",
})


# ── Custom whitelist via .env.sandbox ──────────────────────────────

# Module-level cache: {path: (mtime, whitelist_or_None)} to avoid
# re-reading the config file on every build_sandbox_env() call.
_whitelist_cache: dict[Path, tuple[float, frozenset[str] | None]] = {}


def _resolve_sandbox_config_path(
    config_file: str | Path | None = None,
) -> Path:
    """Resolve the path to the ``.env.sandbox`` config file.

    Priority:
      1. Explicit *config_file* argument.
      2. ``MAOP_SANDBOX_ENV_FILE`` environment variable.
      3. Project-root ``.env.sandbox`` (auto-discovered).
    """
    if config_file is not None:
        return Path(config_file)
    env_file = os.environ.get("MAOP_SANDBOX_ENV_FILE")
    if env_file:
        return Path(env_file)
    # Auto-discover: sandbox.py lives at <root>/py/maop/core/marketplace/
    project_root = Path(__file__).resolve().parents[4]
    return project_root / ".env.sandbox"


def _load_sandbox_whitelist(
    config_file: str | Path | None = None,
) -> frozenset[str] | None:
    """Load a custom variable whitelist from ``.env.sandbox``.

    Returns
    -------
    frozenset[str] | None
        The set of variable names marked ``yes``/``true``/``1``, or
        ``None`` when the file is absent (caller should fall back to
        the built-in :data:`_SAFE_ENV_VARS`).
    """
    path = _resolve_sandbox_config_path(config_file)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        # File does not exist or is inaccessible → use defaults.
        return None

    # Return cached result if the file hasn't changed.
    cached = _whitelist_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    enabled: set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().lower()
            if val in ("yes", "true", "1", "on"):
                enabled.add(key)
        result: frozenset[str] | None = frozenset(enabled)
    except OSError as exc:
        logger.warning("[sandbox] failed to read %s: %s", path, exc)
        result = None

    _whitelist_cache[path] = (mtime, result)
    return result


def build_sandbox_env(
    base_env: dict[str, str] | None = None,
    *,
    extra_safe: frozenset[str] = frozenset(),
    config_file: str | Path | None = None,
) -> dict[str, str]:
    """Build a sandbox-safe environment dict.

    G-02 fix: only forwards variables matching the whitelist policy.

    Parameters
    ----------
    base_env : dict[str, str] | None
        The source environment (defaults to ``os.environ``).
    extra_safe : frozenset[str]
        Additional variable names to consider safe (merged with the
        default :data:`_SAFE_ENV_VARS`). Use sparingly.
    config_file : str | Path | None
        Path to a ``.env.sandbox`` file that overrides the built-in
        safe set. When ``None`` the path is resolved via
        :func:`_resolve_sandbox_config_path` (env var or project-root
        auto-discovery). If the file does not exist the built-in
        defaults are used.

    Returns
    -------
    dict[str, str]
        A new dict containing only whitelisted variables.
    """
    if base_env is None:
        base_env = dict(os.environ)

    # Use custom whitelist from .env.sandbox if available, else defaults.
    custom_whitelist = _load_sandbox_whitelist(config_file)
    if custom_whitelist is not None:
        safe = custom_whitelist | extra_safe
    else:
        safe = _SAFE_ENV_VARS | extra_safe
    result: dict[str, str] = {}

    for key, value in base_env.items():
        # Defence-in-depth: never forward blocked variables.
        if key in _BLOCKED_ENV_VARS:
            logger.debug("[sandbox] blocked env var %s stripped", key)
            continue
        # Forward safe variables.
        if key in safe:
            result[key] = value
            continue
        # Forward MAOP_SANDBOX_* variables (explicit sandbox config).
        if key.startswith(_SANDBOX_ENV_PREFIX):
            result[key] = value
            continue
        # Everything else is stripped.

    logger.debug(
        "[sandbox] built env with %d vars (source had %d)",
        len(result), len(base_env),
    )
    return result


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