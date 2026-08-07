"""MAOP Runtime - Unified execution environment abstraction.

Provides a common interface for running commands in different environments:
  - Local: Direct subprocess execution on the host
  - Isolated: Sandbox directory with restricted access
  - Container: Docker container execution (if available)

This decouples the execution logic from the environment, allowing
seamless switching between local dev and containerized production.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Windows cmd.exe built-in commands that cannot be executed with shell=False.
_WIN_BUILTINS = frozenset({
    "echo", "dir", "type", "copy", "move", "del", "erase", "mkdir", "md",
    "rmdir", "rd", "cd", "chdir", "set", "path", "ver", "cls", "prompt",
    "title", "date", "time", "ren", "rename", "assoc", "ftype", "break",
    "call", "chcp", "exit", "goto", "if", "for", "shift", "start", "setlocal",
    "endlocal", "pushd", "popd", "mklink", "where", "color", "sort", "more",
    "find", "findstr", "cmd", "choice",
})


def _resolve_cmd(command: str, args: list[str] | None = None) -> list[str]:
    """Parse command into a list, handling Windows shell built-ins safely.

    On Windows, commands like 'echo' are cmd.exe built-ins and cannot be
    executed with shell=False. This function detects them and prefixes
    with ['cmd.exe', '/c'] to preserve functionality without shell injection.
    """
    cmd_list = shlex.split(command)
    if args:
        cmd_list.extend(args)
    if not cmd_list:
        return cmd_list
    if sys.platform == "win32":
        first = os.path.basename(cmd_list[0]).lower()
        if first in _WIN_BUILTINS:
            return ["cmd.exe", "/c", subprocess.list2cmdline(cmd_list)]
    return cmd_list

# ── Models ──────────────────────────────────────────────────────


class RuntimeType(str, Enum):
    LOCAL = "local"           # Direct subprocess
    ISOLATED = "isolated"     # Sandbox directory
    CONTAINER = "container"   # Docker container


class RuntimeConfig(BaseModel):
    """Configuration for a runtime environment."""
    type: RuntimeType = RuntimeType.LOCAL
    workdir: str = ""
    timeout_s: float = 300.0
    env: dict[str, str] = Field(default_factory=dict)
    # Container-specific
    image: str = ""           # Docker image name
    container_name: str = ""
    # Isolated-specific
    sandbox_dir: str = ""
    readonly_paths: list[str] = Field(default_factory=list)
    network_access: bool = True


class ExecutionResult(BaseModel):
    """Result of a command execution."""
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    runtime_type: RuntimeType = RuntimeType.LOCAL
    command: str = ""
    started_at: str = ""
    timed_out: bool = False


class RuntimeInfo(BaseModel):
    """Information about the runtime environment."""
    type: RuntimeType = RuntimeType.LOCAL
    available: bool = True
    version: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


# ── Base Runtime ────────────────────────────────────────────────

class BaseRuntime(ABC):
    """Abstract base for execution environments."""

    def __init__(self, config: RuntimeConfig):
        self.config = config

    @abstractmethod
    def info(self) -> RuntimeInfo:
        raise NotImplementedError

    @abstractmethod
    def execute(self, command: str, *, args: list[str] | None = None) -> ExecutionResult:
        raise NotImplementedError

    def is_available(self) -> bool:
        return True


# ── Local Runtime ───────────────────────────────────────────────

class LocalRuntime(BaseRuntime):
    """Execute commands directly on the host via subprocess."""

    def info(self) -> RuntimeInfo:
        return RuntimeInfo(
            type=RuntimeType.LOCAL,
            available=True,
            version=sys.version,
            details={"platform": sys.platform, "pid": os.getpid()},
        )

    def execute(self, command: str, *, args: list[str] | None = None) -> ExecutionResult:
        """Execute a command locally."""
        # Security: use list form instead of shell=True to prevent command injection.
        # _resolve_cmd handles Windows shell built-ins (echo, dir, etc.) safely.
        cmd_list = _resolve_cmd(command, args)
        full_cmd = " ".join(cmd_list) if cmd_list else command

        env = {**os.environ, **self.config.env}
        workdir = self.config.workdir or None
        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.monotonic()

        try:
            proc = subprocess.run(  # noqa: PLW1510
                cmd_list,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_s,
                cwd=workdir,
                env=env,
            )
            duration = time.monotonic() - t0

            return ExecutionResult(
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_s=round(duration, 3),
                runtime_type=RuntimeType.LOCAL,
                command=full_cmd,
                started_at=started_at,
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - t0
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {self.config.timeout_s}s",
                duration_s=round(duration, 3),
                runtime_type=RuntimeType.LOCAL,
                command=full_cmd,
                started_at=started_at,
                timed_out=True,
            )
        except Exception as e:
            duration = time.monotonic() - t0
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_s=round(duration, 3),
                runtime_type=RuntimeType.LOCAL,
                command=full_cmd,
                started_at=started_at,
            )


# ── Isolated Runtime ───────────────────────────────────────────

class IsolatedRuntime(BaseRuntime):
    """Execute commands in a sandbox directory with restricted access."""

    def __init__(self, config: RuntimeConfig):
        super().__init__(config)
        if not config.sandbox_dir:
            config.sandbox_dir = str(Path("data/sandboxes") / f"rt-{int(time.time())}")

    def info(self) -> RuntimeInfo:
        sandbox = Path(self.config.sandbox_dir)
        return RuntimeInfo(
            type=RuntimeType.ISOLATED,
            available=True,
            details={
                "sandbox_dir": str(sandbox),
                "sandbox_exists": sandbox.is_dir(),
                "network_access": self.config.network_access,
            },
        )

    def execute(self, command: str, *, args: list[str] | None = None) -> ExecutionResult:
        """Execute a command in the sandbox directory."""
        sandbox = Path(self.config.sandbox_dir)
        sandbox.mkdir(parents=True, exist_ok=True)

        # Create subdirs if needed
        for d in ("input", "output", "temp"):
            (sandbox / d).mkdir(exist_ok=True)

        # Security: use list form instead of shell=True to prevent command injection.
        # _resolve_cmd handles Windows shell built-ins (echo, dir, etc.) safely.
        cmd_list = _resolve_cmd(command, args)
        full_cmd = " ".join(cmd_list) if cmd_list else command

        env = {**os.environ, **self.config.env}
        env["MAOP_SANDBOX_DIR"] = str(sandbox)
        env["MAOP_RUNTIME"] = "isolated"

        # Restrict network if configured
        if not self.config.network_access and sys.platform != "win32":
            # On Linux, we could use unshare; on Windows, skip
            pass

        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.monotonic()

        try:
            proc = subprocess.run(  # noqa: PLW1510
                cmd_list,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_s,
                cwd=str(sandbox),
                env=env,
            )
            duration = time.monotonic() - t0

            return ExecutionResult(
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_s=round(duration, 3),
                runtime_type=RuntimeType.ISOLATED,
                command=full_cmd,
                started_at=started_at,
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - t0
            return ExecutionResult(
                exit_code=-1,
                stderr=f"Command timed out after {self.config.timeout_s}s",
                duration_s=round(duration, 3),
                runtime_type=RuntimeType.ISOLATED,
                command=full_cmd,
                started_at=started_at,
                timed_out=True,
            )
        except Exception as e:
            duration = time.monotonic() - t0
            return ExecutionResult(
                exit_code=-1,
                stderr=str(e),
                duration_s=round(duration, 3),
                runtime_type=RuntimeType.ISOLATED,
                command=full_cmd,
                started_at=started_at,
            )


# ── Container Runtime ──────────────────────────────────────────

class ContainerRuntime(BaseRuntime):
    """Execute commands inside a Docker container."""

    def is_available(self) -> bool:
        """Check if Docker is available."""
        try:
            result = subprocess.run(  # noqa: PLW1510
                ["docker", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def info(self) -> RuntimeInfo:
        available = self.is_available()
        version = ""
        if available:
            try:
                result = subprocess.run(
                    ["docker", "--version"],
                    capture_output=True, text=True, timeout=5,
                    check=True,
                )
                version = result.stdout.strip()
            except Exception as e:
                logger.debug("ignored: %s", e, exc_info=True)

        return RuntimeInfo(
            type=RuntimeType.CONTAINER,
            available=available,
            version=version,
            details={"image": self.config.image},
        )

    def execute(self, command: str, *, args: list[str] | None = None) -> ExecutionResult:
        """Execute a command inside a Docker container."""
        if not self.is_available():
            return ExecutionResult(
                exit_code=-1,
                stderr="Docker is not available",
                runtime_type=RuntimeType.CONTAINER,
                command=command,
            )

        image = self.config.image or "python:3.11-slim"
        full_cmd = command
        if args:
            full_cmd = f"{command} {' '.join(shlex.quote(a) for a in args)}"

        docker_cmd = ["docker", "run", "--rm"]

        if self.config.container_name:
            docker_cmd.extend(["--name", self.config.container_name])

        if self.config.workdir:
            docker_cmd.extend(["-v", f"{self.config.workdir}:/workspace"])
            docker_cmd.extend(["-w", "/workspace"])

        for k, v in self.config.env.items():
            docker_cmd.extend(["-e", f"{k}={v}"])

        if not self.config.network_access:
            docker_cmd.append("--network=none")

        docker_cmd.extend([image, "sh", "-c", full_cmd])

        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.monotonic()

        try:
            proc = subprocess.run(  # noqa: PLW1510
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_s,
            )
            duration = time.monotonic() - t0

            return ExecutionResult(
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_s=round(duration, 3),
                runtime_type=RuntimeType.CONTAINER,
                command=full_cmd,
                started_at=started_at,
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - t0
            return ExecutionResult(
                exit_code=-1,
                stderr=f"Container timed out after {self.config.timeout_s}s",
                duration_s=round(duration, 3),
                runtime_type=RuntimeType.CONTAINER,
                command=full_cmd,
                started_at=started_at,
                timed_out=True,
            )
        except Exception as e:
            duration = time.monotonic() - t0
            return ExecutionResult(
                exit_code=-1,
                stderr=str(e),
                duration_s=round(duration, 3),
                runtime_type=RuntimeType.CONTAINER,
                command=full_cmd,
                started_at=started_at,
            )


# ── Factory ─────────────────────────────────────────────────────

_RUNTIME_MAP: dict[RuntimeType, type[BaseRuntime]] = {
    RuntimeType.LOCAL: LocalRuntime,
    RuntimeType.ISOLATED: IsolatedRuntime,
    RuntimeType.CONTAINER: ContainerRuntime,
}


def create_runtime(config: RuntimeConfig | None = None) -> BaseRuntime:
    """Create a runtime instance from configuration.

    Falls back to LocalRuntime if the requested runtime is unavailable.
    """
    if config is None:
        config = RuntimeConfig()

    runtime_cls = _RUNTIME_MAP.get(config.type, LocalRuntime)
    runtime = runtime_cls(config)

    # Fallback if unavailable
    if not runtime.is_available():
        logger.warning(
            "Runtime %s unavailable, falling back to local",
            config.type.value,
        )
        fallback_config = config.model_copy(update={"type": RuntimeType.LOCAL})
        runtime = LocalRuntime(fallback_config)

    return runtime
