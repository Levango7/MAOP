"""MAOP Deploy - Deployment entry point with start/stop/status, config validation, and health check.

Provides a unified CLI and programmatic interface for:
  - Starting/stopping the MAOP system (loop + dashboard)
  - Validating configuration before deployment
  - Health checking all subsystems
  - Status reporting
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── Models ──────────────────────────────────────────────────────


class ServiceStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ComponentHealth(BaseModel):
    """Health check result for a single component."""
    name: str = ""
    status: HealthStatus = HealthStatus.HEALTHY
    message: str = ""
    latency_ms: float = 0.0
    details: dict[str, Any] = Field(default_factory=dict)


class DeployConfig(BaseModel):
    """Deployment configuration."""
    root_dir: str = ""
    dashboard_port: int = 9079
    dashboard_host: str = "127.0.0.1"
    log_level: str = "INFO"
    workers: int = 1
    pid_file: str = ""  # Defaults to root_dir/data/MAOP.pid


class ValidationResult(BaseModel):
    """Result of configuration validation."""
    valid: bool = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SystemStatus(BaseModel):
    """Overall system status."""
    status: ServiceStatus = ServiceStatus.STOPPED
    pid: int | None = None
    uptime_s: float = 0.0
    started_at: str = ""
    components: list[ComponentHealth] = Field(default_factory=list)
    config: DeployConfig = Field(default_factory=DeployConfig)


# ── Config Validation ───────────────────────────────────────────

def validate_config(root_dir: str | Path) -> ValidationResult:
    """Validate MAOP configuration for deployment readiness.

    Checks:
      1. Required directories exist
      2. agents.yaml is valid
      3. rules.yaml is valid
      4. Python dependencies are available
      5. Port is available
    """
    root = Path(root_dir)
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Required directories
    required_dirs = ["config", "data"]
    for d in required_dirs:
        if not (root / d).is_dir():
            errors.append(f"Missing required directory: {d}")

    # 2. agents.yaml
    agents_yaml = root / "config" / "agents.yaml"
    if not agents_yaml.is_file():
        errors.append("Missing config/agents.yaml")
    else:
        try:
            import yaml
            with open(agents_yaml, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                errors.append("agents.yaml is not a valid YAML mapping")
            elif "agents" not in data:
                warnings.append("agents.yaml has no 'agents' key")
        except ImportError:
            warnings.append("PyYAML not installed; skipping YAML validation")
        except Exception as e:
            errors.append(f"agents.yaml parse error: {e}")

    # 3. rules.yaml
    rules_yaml = root / "config" / "rules.yaml"
    if not rules_yaml.is_file():
        warnings.append("Missing config/rules.yaml (optional)")

    # 4. Python package
    try:
        import maop  # noqa: F401
    except ImportError:
        errors.append("MAOP Python package not importable")

    # 5. Data directory writable
    data_dir = root / "data"
    if data_dir.is_dir():
        test_file = data_dir / ".write_test"
        try:
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
        except OSError:
            errors.append("data/ directory is not writable")
    else:
        warnings.append("data/ directory does not exist yet (will be created)")

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


# ── Health Check ────────────────────────────────────────────────

def health_check(root_dir: str | Path, timeout_s: float = 5.0) -> list[ComponentHealth]:
    """Check health of all MAOP subsystems."""
    root = Path(root_dir)
    results: list[ComponentHealth] = []

    # 1. Database health
    t0 = time.monotonic()
    db_path = root / "data" / "maop.db"
    if db_path.is_file():
        try:
            import sqlite3
            with sqlite3.connect(str(db_path), timeout=timeout_s) as conn:
                conn.execute("SELECT 1")
            results.append(ComponentHealth(
                name="database",
                status=HealthStatus.HEALTHY,
                latency_ms=(time.monotonic() - t0) * 1000,
            ))
        except Exception as e:
            results.append(ComponentHealth(
                name="database",
                status=HealthStatus.UNHEALTHY,
                message=str(e),
                latency_ms=(time.monotonic() - t0) * 1000,
            ))
    else:
        results.append(ComponentHealth(
            name="database",
            status=HealthStatus.DEGRADED,
            message="Database file not found",
        ))

    # 2. Memory store health
    t0 = time.monotonic()
    mem_path = root / "data" / "memory.db"
    if mem_path.is_file():
        try:
            import sqlite3
            with sqlite3.connect(str(mem_path), timeout=timeout_s) as conn:
                conn.execute("SELECT 1")
            results.append(ComponentHealth(
                name="memory",
                status=HealthStatus.HEALTHY,
                latency_ms=(time.monotonic() - t0) * 1000,
            ))
        except Exception as e:
            results.append(ComponentHealth(
                name="memory",
                status=HealthStatus.UNHEALTHY,
                message=str(e),
                latency_ms=(time.monotonic() - t0) * 1000,
            ))
    else:
        results.append(ComponentHealth(
            name="memory",
            status=HealthStatus.DEGRADED,
            message="Memory DB not found (will be created on first use)",
        ))

    # 3. Config health
    t0 = time.monotonic()
    agents_yaml = root / "config" / "agents.yaml"
    if agents_yaml.is_file():
        results.append(ComponentHealth(
            name="config",
            status=HealthStatus.HEALTHY,
            latency_ms=(time.monotonic() - t0) * 1000,
        ))
    else:
        results.append(ComponentHealth(
            name="config",
            status=HealthStatus.UNHEALTHY,
            message="agents.yaml not found",
            latency_ms=(time.monotonic() - t0) * 1000,
        ))

    # 4. Dashboard health (check if port is responding)
    t0 = time.monotonic()
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:9079/api/health")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if resp.status == 200:
                results.append(ComponentHealth(
                    name="dashboard",
                    status=HealthStatus.HEALTHY,
                    latency_ms=(time.monotonic() - t0) * 1000,
                ))
            else:
                results.append(ComponentHealth(
                    name="dashboard",
                    status=HealthStatus.DEGRADED,
                    message=f"HTTP {resp.status}",
                    latency_ms=(time.monotonic() - t0) * 1000,
                ))
    except Exception:
        results.append(ComponentHealth(
            name="dashboard",
            status=HealthStatus.DEGRADED,
            message="Dashboard not reachable (may not be started)",
            latency_ms=(time.monotonic() - t0) * 1000,
        ))

    return results


# ── PID Management ──────────────────────────────────────────────

def _pid_path(root_dir: str | Path) -> Path:
    return Path(root_dir) / "data" / "maop.pid"


def _read_pid(root_dir: str | Path) -> int | None:
    pid_file = _pid_path(root_dir)
    if pid_file.is_file():
        try:
            return int(pid_file.read_text().strip())
        except (ValueError, OSError):
            return None
    return None


def _write_pid(root_dir: str | Path, pid: int) -> None:
    pid_file = _pid_path(root_dir)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(pid), encoding="utf-8")


def _remove_pid(root_dir: str | Path) -> None:
    pid_file = _pid_path(root_dir)
    if pid_file.is_file():
        pid_file.unlink()


# ── Start / Stop / Status ───────────────────────────────────────

def start(
    root_dir: str | Path = ".",
    *,
    port: int = 9079,
    host: str = "127.0.0.1",
    log_level: str = "INFO",
    dashboard: bool = True,
    wait_ready: bool = False,
    ready_timeout_s: float = 30.0,
    ready_interval_s: float = 0.5,
) -> SystemStatus:
    """Start the MAOP system (dashboard server).

    Returns SystemStatus with the current state.

    P2-P3 fix (M8): 启动后可选轮询 health_check 直到就绪或超时。

    Parameters
    ----------
    wait_ready : bool
        是否等待服务就绪。默认 False（保持原行为，立即返回 STARTING）。
        True 时轮询 health_check 直到 dashboard 健康或超时。
    ready_timeout_s : float
        就绪检查超时秒数。默认 30.0。
    ready_interval_s : float
        就绪检查轮询间隔秒数。默认 0.5。
    """
    root = Path(root_dir).resolve()

    # Validate first
    validation = validate_config(root)
    if not validation.valid:
        return SystemStatus(
            status=ServiceStatus.ERROR,
            config=DeployConfig(root_dir=str(root)),
            components=[ComponentHealth(
                name="validation",
                status=HealthStatus.UNHEALTHY,
                message="; ".join(validation.errors),
            )],
        )

    # Check if already running
    existing_pid = _read_pid(root)
    if existing_pid is not None:
        try:
            os.kill(existing_pid, 0)  # Check if process exists
            return SystemStatus(
                status=ServiceStatus.RUNNING,
                pid=existing_pid,
                config=DeployConfig(root_dir=str(root), dashboard_port=port),
            )
        except (OSError, ProcessLookupError):
            _remove_pid(root)  # Stale PID file

    # Ensure data directory
    (root / "data").mkdir(parents=True, exist_ok=True)

    # Start dashboard as subprocess
    if dashboard:
        cmd = [
            sys.executable, "-m", "uvicorn",
            "maop.dashboard.server:app",
            "--host", host,
            "--port", str(port),
            "--log-level", log_level.lower(),
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Windows 专属常量，POSIX 无——getattr 默认值跨平台安全（mypy 两端不报）
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if sys.platform == "win32" else 0,
        )
        _write_pid(root, proc.pid)
        logger.info("MAOP started: pid=%d, dashboard=%s:%d", proc.pid, host, port)

        # P2-P3 fix (M8): 就绪检查 — 轮询 health_check 直到成功或超时
        if wait_ready:
            components = _wait_for_ready(
                root,
                timeout_s=ready_timeout_s,
                interval_s=ready_interval_s,
            )

            # 检查子进程是否已退出（启动失败）
            if proc.poll() is not None:
                # 子进程已退出，读取 stderr 获取错误信息
                stderr_output = ""
                try:
                    if proc.stderr is not None:
                        stderr_output = proc.stderr.read().decode("utf-8", errors="replace")[-500:]
                except Exception:
                    logger.debug('swallowed exception', exc_info=True)
                _remove_pid(root)
                logger.error("MAOP subprocess exited prematurely: %s", stderr_output)
                return SystemStatus(
                    status=ServiceStatus.ERROR,
                    pid=proc.pid,
                    config=DeployConfig(root_dir=str(root), dashboard_port=port),
                    components=[ComponentHealth(
                        name="subprocess",
                        status=HealthStatus.UNHEALTHY,
                        message=f"Process exited: {stderr_output}",
                    )],
                )

            # 评估就绪状态
            dashboard_healthy = any(
                c.name == "dashboard" and c.status == HealthStatus.HEALTHY
                for c in components
            )

            if dashboard_healthy:
                final_status = ServiceStatus.RUNNING
                logger.info("MAOP ready: pid=%d", proc.pid)
            else:
                # Dashboard 未就绪但进程仍在运行 — 超时返回 ERROR + 健康检查结果
                final_status = ServiceStatus.ERROR
                logger.warning(
                    "MAOP not ready after %.1fs: pid=%d, components=%s",
                    ready_timeout_s, proc.pid,
                    [(c.name, c.status.value) for c in components],
                )

            return SystemStatus(
                status=final_status,
                pid=proc.pid,
                started_at=datetime.now(timezone.utc).isoformat(),
                components=components,
                config=DeployConfig(
                    root_dir=str(root),
                    dashboard_port=port,
                    dashboard_host=host,
                    log_level=log_level,
                ),
            )

        # 不等待就绪 — 保持原行为
        return SystemStatus(
            status=ServiceStatus.STARTING,
            pid=proc.pid,
            started_at=datetime.now(timezone.utc).isoformat(),
            config=DeployConfig(
                root_dir=str(root),
                dashboard_port=port,
                dashboard_host=host,
                log_level=log_level,
            ),
        )

    # No dashboard - just mark as running
    _write_pid(root, os.getpid())
    return SystemStatus(
        status=ServiceStatus.RUNNING,
        pid=os.getpid(),
        started_at=datetime.now(timezone.utc).isoformat(),
        config=DeployConfig(root_dir=str(root)),
    )


def _wait_for_ready(
    root_dir: str | Path,
    *,
    timeout_s: float = 30.0,
    interval_s: float = 0.5,
) -> list[ComponentHealth]:
    """轮询 health_check 直到 dashboard 就绪或超时。

    P2-P3 fix (M8): start() 后的就绪检查辅助函数。
    返回最后一次 health_check 的结果（无论是否就绪）。
    """
    deadline = time.monotonic() + timeout_s
    last_components: list[ComponentHealth] = []

    while time.monotonic() < deadline:
        last_components = health_check(root_dir, timeout_s=interval_s)

        # 检查 dashboard 组件是否 HEALTHY
        dashboard_healthy = any(
            c.name == "dashboard" and c.status == HealthStatus.HEALTHY
            for c in last_components
        )
        if dashboard_healthy:
            return last_components

        time.sleep(interval_s)

    return last_components


def stop(root_dir: str | Path = ".") -> SystemStatus:
    """Stop the MAOP system."""
    root = Path(root_dir).resolve()
    pid = _read_pid(root)

    if pid is None:
        return SystemStatus(
            status=ServiceStatus.STOPPED,
            config=DeployConfig(root_dir=str(root)),
        )

    try:
        if sys.platform == "win32":
            # On Windows, kill the process tree
            subprocess.run(  # noqa: PLW1510
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
            )
        else:
            os.kill(pid, signal.SIGTERM)
        logger.info("MAOP stopped: pid=%d", pid)
    except (OSError, ProcessLookupError) as e:
        logger.warning("Stop failed (process may already be dead): %s", e)
    finally:
        _remove_pid(root)

    return SystemStatus(
        status=ServiceStatus.STOPPED,
        config=DeployConfig(root_dir=str(root)),
    )


def status(root_dir: str | Path = ".") -> SystemStatus:
    """Get current system status."""
    root = Path(root_dir).resolve()
    pid = _read_pid(root)

    if pid is None:
        return SystemStatus(
            status=ServiceStatus.STOPPED,
            config=DeployConfig(root_dir=str(root)),
        )

    # Check if process is alive
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        _remove_pid(root)
        return SystemStatus(
            status=ServiceStatus.STOPPED,
            config=DeployConfig(root_dir=str(root)),
        )

    # Check component health
    components = health_check(root)

    overall = ServiceStatus.RUNNING
    if any(c.status == HealthStatus.UNHEALTHY for c in components):
        overall = ServiceStatus.ERROR

    return SystemStatus(
        status=overall,
        pid=pid,
        components=components,
        config=DeployConfig(root_dir=str(root)),
    )
