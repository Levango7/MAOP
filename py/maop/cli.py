"""
MAOP - Agent Orchestration Framework CLI (Python entry point).
All commands use Python-native implementations. No PowerShell fallbacks.
"""

from __future__ import annotations

from typing import Any

import argparse
import os
import sys
from pathlib import Path

MAOP_ROOT = Path(__file__).resolve().parent.parent.parent


def cmd_start(port: int = 9079, host: str = "127.0.0.1") -> Any:
    """Start the FastAPI dashboard (Python-native)."""
    try:
        from maop.dashboard.server import app
        import uvicorn
    except ImportError as e:
        print(f"ERROR: Cannot import maop dashboard: {e}")
        print("Install dependencies: pip install fastapi uvicorn")
        sys.exit(1)

    # Multi-worker support (MAOP_WORKERS env var)
    workers = int(os.environ.get("MAOP_WORKERS", "1"))
    uvicorn_kwargs: dict[str, Any] = {"log_level": "info"}

    if workers > 1:
        uvicorn_kwargs["workers"] = workers
        print(f"MAOP Dashboard -> http://{host}:{port} (workers={workers})")
    else:
        # TLS support (single-worker only — uvicorn limitation)
        tls_enabled = os.environ.get("MAOP_TLS", "0") == "1"
        if tls_enabled:
            from maop.core.tls import TLSSettings, create_ssl_context
            cert_file = os.environ.get("MAOP_TLS_CERT", "")
            key_file = os.environ.get("MAOP_TLS_KEY", "")
            if cert_file and key_file:
                tls_settings = TLSSettings(enabled=True, cert_file=cert_file, key_file=key_file)
                ssl_ctx = create_ssl_context(tls_settings)
                if ssl_ctx:
                    uvicorn_kwargs["ssl"] = ssl_ctx

        proto = "https" if "ssl" in uvicorn_kwargs else "http"
        print(f"MAOP Dashboard -> {proto}://{host}:{port}")

    uvicorn.run(app, host=host, port=port, **uvicorn_kwargs)


def cmd_stop() -> Any:
    """Stop MAOP using Python-native deploy.stop()."""
    try:
        from maop.deploy import stop
        result = stop(MAOP_ROOT)
        print(f"[MAOP] Status: {result.status.value}")
        if result.pid:
            print(f"[MAOP] Stopped PID {result.pid}")
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def cmd_status() -> Any:
    """Check MAOP status using Python-native deploy.status()."""
    try:
        from maop.deploy import status
        result = status(MAOP_ROOT)
        print(f"[MAOP] Status: {result.status.value}")
        if result.pid:
            print(f"[MAOP] PID: {result.pid}")
        for comp in result.components:
            print(f"  {comp.name}: {comp.status.value} ({comp.latency_ms:.1f}ms) {comp.message}")
    except Exception as e:
        print(f"[MAOP] Status check failed: {e}")


def cmd_run(task: str) -> Any:
    """Run a task through Python-native MaopLoop."""
    try:
        import asyncio
        from maop.maop_loop import MaopLoop
        loop = MaopLoop(root_dir=str(MAOP_ROOT))
        result = asyncio.run(loop.run(task))
        print(f"[MAOP] Agent: {result.selected_agent}")
        print(f"[MAOP] Success: {result.success}")
        print(f"[MAOP] Duration: {result.total_duration_ms}ms")
        if result.execution:
            print(f"[MAOP] Exit code: {result.execution.exit_code}")
            if result.execution.stdout:
                print(f"[MAOP] Output:\n{result.execution.stdout[:2000]}")
            if result.execution.error:
                print(f"[MAOP] Error: {result.execution.error}")
        if result.block_reason:
            print(f"[MAOP] Blocked: {result.block_reason}")
    except ImportError as e:
        print(f"ERROR: Cannot import MaopLoop: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Task execution failed: {e}")
        sys.exit(1)


def cmd_validate() -> Any:
    """Run config validation using Python-native deploy.validate_config()."""
    try:
        from maop.deploy import validate_config
        result = validate_config(MAOP_ROOT)
        if result.valid:
            print("[MAOP] Configuration: VALID")
        else:
            print("[MAOP] Configuration: INVALID")
            for err in result.errors:
                print(f"  ERROR: {err}")
        for warn in result.warnings:
            print(f"  WARNING: {warn}")
        if not result.valid:
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: Validation failed: {e}")
        sys.exit(1)


def cmd_health() -> Any:
    """Run health check using Python-native deploy.health_check()."""
    try:
        from maop.deploy import health_check
        results = health_check(MAOP_ROOT)
        all_healthy = True
        for r in results:
            status_icon = {"healthy": "+", "degraded": "~", "unhealthy": "!"}[r.status.value]
            print(f"  [{status_icon}] {r.name}: {r.status.value} ({r.latency_ms:.1f}ms) {r.message}")
            if r.status.value != "healthy":
                all_healthy = False
        if not all_healthy:
            print("[MAOP] Health: DEGRADED")
            sys.exit(1)
        else:
            print("[MAOP] Health: OK")
    except Exception as e:
        print(f"ERROR: Health check failed: {e}")
        sys.exit(1)


def main() -> Any:
    # Enable JSON structured logging when MAOP_JSON_LOG=1 (for ELK / Loki).
    if os.environ.get("MAOP_JSON_LOG", "0") == "1":
        from maop.core.monitoring import setup_json_logging
        setup_json_logging(
            level=os.environ.get("MAOP_LOG_LEVEL", "INFO"),
            log_file=os.environ.get("MAOP_JSON_LOG_FILE") or None,
        )

    parser = argparse.ArgumentParser(description="MAOP - Agent Orchestration Framework")
    parser.add_argument("action", nargs="?", default="start",
                        choices=["start", "stop", "status", "run", "validate", "health"])
    parser.add_argument("--task", "-t", default="", help="Task description (for run)")
    parser.add_argument("--port", "-p", type=int, default=9079, help="Dashboard port (default 9079)")
    parser.add_argument("--host", default="127.0.0.1", help="Dashboard host (default 127.0.0.1)")
    args = parser.parse_args()

    if args.action == "start":
        cmd_start(args.port, args.host)
    elif args.action == "stop":
        cmd_stop()
    elif args.action == "status":
        cmd_status()
    elif args.action == "run":
        if not args.task:
            print("ERROR: --task required for run")
            sys.exit(1)
        cmd_run(args.task)
    elif args.action == "validate":
        cmd_validate()
    elif args.action == "health":
        cmd_health()


if __name__ == "__main__":
    main()
