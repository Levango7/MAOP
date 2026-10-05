"""MAOP Sandbox - Working-directory-scoped execution environment management.

SECURITY NOTICE — IMPORTANT: READ BEFORE USING THIS MODULE
=========================================================

This module provides **working-directory isolation** and **timeout enforcement**
only. It does **NOT** provide OS-level sandboxing. Specifically:

  - **No filesystem isolation**: Code running inside a "sandbox" can still
    access the full filesystem of the host process (``/etc``, ``~/.ssh``,
    other users' files, etc.). The workdir boundary is advisory, not enforced
    by the OS.
  - **No network isolation**: There is no firewall, network namespace, or
    egress filtering. Sandboxed code can make arbitrary outbound connections.
  - **No process isolation**: Sandboxed code runs in the same process space
    and can ``fork``, ``exec``, inspect/modify host process memory, and
    access shared resources (IPC, signals, environment variables).
  - **No syscall filtering**: There is no seccomp-bpf, AppArmor, or SELinux
    profile. All syscalls available to the host process are available to
    sandboxed code.
  - **Timeout is best-effort**: The timeout limits wall-clock duration but
    does not guarantee timely termination if the code blocks on an
    uninterruptible operation (e.g., disk I/O, network wait).

**When this sandbox IS appropriate:**
  - Running trusted/first-party plugin code that needs a clean working
    directory and a time budget.
  - Isolating file output between concurrent agent runs.
  - Enforcing cleanup of temporary artifacts.

**When this sandbox is NOT appropriate (use an external container runtime):**
  - Executing untrusted or third-party code.
  - Running code that may attempt filesystem traversal, network exfiltration,
    or privilege escalation.
  - Any scenario requiring strong isolation guarantees.

For true OS-level isolation, use Docker, gVisor, Firecracker, or similar
container runtimes with appropriate security profiles.

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

from pydantic import BaseModel

from maop.core.backends.db_utils import sqlite_connect

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

    def __init__(
        self,
        root_dir: str | Path,
        allowed_commands: set[str] | list[str] | None = None,
    ) -> None:
        self._root = Path(root_dir)
        self._sandbox_dir = self._root / "data" / "sandboxes"
        self._db_path = self._sandbox_dir / "sandbox_index.db"
        # H-3 fix: 可选命令白名单。None 表示不限制（向后兼容）；
        # 配置后只允许白名单内的命令（按命令名匹配，忽略路径前缀和参数）。
        self._allowed_commands: set[str] | None = (
            {c.lower() for c in allowed_commands} if allowed_commands else None
        )
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

    def _check_command_allowed(self, command: str) -> str | None:
        """H-3 fix: 检查命令是否在白名单内。

        Returns
        -------
        str | None
            被拒绝时返回原因字符串；允许时返回 None。
        """
        if self._allowed_commands is None:
            return None  # 未配置白名单，允许所有（向后兼容）
        try:
            import shlex
            parts = shlex.split(command)
            if not parts:
                return "Empty command"
            cmd_name = os.path.basename(parts[0]).lower()
            if cmd_name not in self._allowed_commands:
                return f"Command not in allowlist: {cmd_name}"
            return None
        except ValueError as exc:
            return f"Invalid command syntax: {exc}"

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

        # H-3 fix: 命令白名单检查
        denied = self._check_command_allowed(command)
        if denied:
            return SandboxResult(ok=False, error=denied)

        exec_dir = Path(work_dir) if work_dir else Path(sb.path)
        exec_dir.mkdir(parents=True, exist_ok=True)

        log_file = exec_dir / f"sandbox-run-{datetime.now(timezone.utc).strftime('%H%M%S')}.log"
        start = time.monotonic()

        try:

            import shlex
            cmd_parts = shlex.split(command)
            if not cmd_parts:
                return SandboxResult(ok=False, error="Empty command", duration_ms=max(1, int((time.monotonic() - start) * 1000)))
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
                # G-02: 只转发白名单内的环境变量。不传 env= 时子进程会继承**整份**
                # 服务器环境（含密钥），沙箱就只约束了工作目录、没约束凭据面。
                env=build_sandbox_env(),
            )
            try:
                stdout, _stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_seconds
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                elapsed_ms = max(1, int((time.monotonic() - start) * 1000))
                return SandboxResult(ok=False, error="timeout", duration_ms=elapsed_ms)

            elapsed_ms = max(1, int((time.monotonic() - start) * 1000))
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
            elapsed_ms = max(1, int((time.monotonic() - start) * 1000))
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

        # H-3 fix: 命令白名单检查
        denied = self._check_command_allowed(command)
        if denied:
            return SandboxResult(ok=False, error=denied)

        exec_dir = Path(work_dir) if work_dir else Path(sb.path)
        exec_dir.mkdir(parents=True, exist_ok=True)

        log_file = exec_dir / f"sandbox-run-{datetime.now(timezone.utc).strftime('%H%M%S')}.log"
        start = time.monotonic()

        try:
            # Security: use list form instead of shell=True to prevent command injection.
            # Handle Windows shell built-ins (echo, dir, etc.) via cmd.exe /c.
            import shlex
            cmd_parts = shlex.split(command)
            if not cmd_parts:
                return SandboxResult(ok=False, error="Empty command", duration_ms=max(1, int((time.monotonic() - start) * 1000)))
            if sys.platform == "win32":
                _win_builtins = {"echo", "dir", "type", "copy", "move", "del", "mkdir", "md",
                    "rmdir", "rd", "cd", "chdir", "set", "path", "ver", "cls", "ren", "rename",
                    "call", "start", "find", "findstr", "sort", "more", "choice"}
                if os.path.basename(cmd_parts[0]).lower() in _win_builtins:
                    # Security: use subprocess.list2cmdline for safe Windows quoting
                    # instead of plain " ".join which doesn't handle special chars
                    cmd_parts = ["cmd.exe", "/c", subprocess.list2cmdline(cmd_parts)]

            proc = subprocess.run(  # noqa: PLW1510
                cmd_parts,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=str(exec_dir),
                # G-02: 同 arun() —— 不传 env= 会继承整份服务器环境（含密钥）。
                # 注意本模块**有两套并行实现**（同步 run 走 subprocess.run，
                # 异步 arun 走 create_subprocess_exec），安全相关的改动必须两边都做：
                # 我第一版只补了 arun，同步路径照旧泄漏，是行为用例（不是结构守卫）
                # 把它抓出来的 —— 守卫当时只枚举 create_subprocess_exec。
                env=build_sandbox_env(),
            )
            elapsed_ms = max(1, int((time.monotonic() - start) * 1000))
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
            elapsed_ms = max(1, int((time.monotonic() - start) * 1000))
            return SandboxResult(ok=False, error="timeout", duration_ms=elapsed_ms)
        except Exception as exc:
            elapsed_ms = max(1, int((time.monotonic() - start) * 1000))
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

# ── 子进程环境白名单（G-02）──────────────────────────────────────
#
# 2026-10-05 **从 core/marketplace/sandbox.py 移植过来**：那份实现从来没有接线
# （该模块零生产导入方），而**真正在跑的**沙箱（本模块）在 create_subprocess_exec
# 时根本没传 env= —— 于是沙箱里的命令继承整份服务器环境，包括 MAOP_JWT_SECRET /
# MAOP_PG_PASSWORD 等。sandbox 却把服务端密钥一并递给了被沙箱隔离的进程，
# 等于只约束了工作目录、没约束凭据面。
#
# 白名单可用项目根 `.env.sandbox`（或 MAOP_SANDBOX_ENV_FILE 指向的文件）逐行覆盖：
# `KEY=yes` 才转发；不在白名单且不匹配 MAOP_SANDBOX_ 前缀的一律剥离。
# PATH/HOME/SYSTEMROOT/TEMP 等运行必需项默认在白名单里，保证命令仍能跑起来。

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
