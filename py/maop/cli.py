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


def cmd_mcp_marketplace(args: list[str]) -> Any:
    """Handle ``mcp marketplace <subcommand>`` — registry and server management.

    Subcommands:
        list-registries                         List configured registries
        add-registry <name> <url> [--trusted]   Add a registry
        remove-registry <name>                  Remove a registry
        search <query> [--tags t1,t2]           Search the catalog
        install <name> [--registry R] [--no-verify] [--confirm-untrusted]
        uninstall <name>                        Remove an installed server
        list-installed                          List installed marketplace servers
    """
    parser = argparse.ArgumentParser(
        prog="maop mcp marketplace",
        description="MCP Marketplace — discover and install MCP servers from remote registries",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    sub.add_parser("list-registries", help="List all configured registries")

    p_add = sub.add_parser("add-registry", help="Add a registry")
    p_add.add_argument("name", help="Registry name")
    p_add.add_argument("url", help="Registry URL (HTTP/HTTPS)")
    p_add.add_argument("--trusted", action="store_true", help="Mark registry as trusted")

    p_remove = sub.add_parser("remove-registry", help="Remove a registry")
    p_remove.add_argument("name", help="Registry name")

    p_search = sub.add_parser("search", help="Search servers in the catalog")
    p_search.add_argument("query", help="Search query (matches name/description)")
    p_search.add_argument("--tags", default="", help="Comma-separated tags to filter by")

    p_install = sub.add_parser("install", help="Install a server from the marketplace")
    p_install.add_argument("name", help="Server name to install")
    p_install.add_argument("--registry", default=None, help="Install from a specific registry")
    p_install.add_argument("--no-verify", action="store_true", help="Skip checksum verification")
    p_install.add_argument(
        "--confirm-untrusted", action="store_true",
        help="Confirm installing from an untrusted registry without a checksum",
    )

    p_uninstall = sub.add_parser("uninstall", help="Uninstall a marketplace server")
    p_uninstall.add_argument("name", help="Server name to uninstall")

    sub.add_parser("list-installed", help="List installed marketplace servers")

    parsed = parser.parse_args(args)

    from maop.core.mcp_marketplace import MCPMarketplace
    # Use the project-root config dir so reads/writes stay local to the project
    # (not the package-shipped default, which is read-only).
    config_path = MAOP_ROOT / "config" / "mcp_marketplace.yaml"
    mp = MCPMarketplace(config_path=config_path)

    if parsed.subcommand == "list-registries":
        regs = mp.list_registries()
        if not regs:
            print("No registries configured.")
        for r in regs:
            trust = "trusted" if r.trusted else "untrusted"
            status = "enabled" if r.enabled else "disabled"
            print(f"  {r.name}: {r.url} [{trust}, {status}]")
    elif parsed.subcommand == "add-registry":
        mp.add_registry(parsed.name, parsed.url, trusted=parsed.trusted)
        trust = "trusted" if parsed.trusted else "untrusted"
        print(f"Added {trust} registry '{parsed.name}' -> {parsed.url}")
    elif parsed.subcommand == "remove-registry":
        mp.remove_registry(parsed.name)
        print(f"Removed registry '{parsed.name}'")
    elif parsed.subcommand == "search":
        tags = (
            [t.strip() for t in parsed.tags.split(",") if t.strip()]
            if parsed.tags else None
        )
        results = mp.search(parsed.query, tags=tags)
        if not results:
            print("No servers found.")
        for s in results:
            verified = " [verified]" if s.verified else ""
            tags_str = f" tags={','.join(s.tags)}" if s.tags else ""
            print(f"  {s.name} v{s.version}{verified}{tags_str}: {s.description}")
    elif parsed.subcommand == "install":
        try:
            cfg = mp.install(
                parsed.name,
                registry_name=parsed.registry,
                verify_checksum=not parsed.no_verify,
                confirm_untrusted=parsed.confirm_untrusted,
            )
            print(f"Installed '{parsed.name}' (transport={cfg.transport.value})")
        except ValueError as e:
            print(f"ERROR: {e}")
            sys.exit(1)
    elif parsed.subcommand == "uninstall":
        if mp.uninstall(parsed.name):
            print(f"Uninstalled '{parsed.name}'")
        else:
            print(f"ERROR: '{parsed.name}' is not installed")
            sys.exit(1)
    elif parsed.subcommand == "list-installed":
        installed = mp.list_installed()
        if not installed:
            print("No marketplace servers installed.")
        for s in installed:
            print(
                f"  {s.get('name', '?')} v{s.get('version', '?')} "
                f"(from {s.get('registry', '?')})"
            )


def cmd_mcp(args: list[str]) -> Any:
    """Handle ``mcp <subcommand>`` — currently only ``marketplace`` is supported."""
    if not args:
        print("ERROR: 'mcp' requires a subcommand. Available: marketplace")
        sys.exit(1)
    sub = args[0]
    rest = args[1:]
    if sub == "marketplace":
        cmd_mcp_marketplace(rest)
    else:
        print(f"ERROR: Unknown mcp subcommand '{sub}'. Available: marketplace")
        sys.exit(1)


def main() -> Any:
    # Enable JSON structured logging when MAOP_JSON_LOG=1 (for ELK / Loki).
    if os.environ.get("MAOP_JSON_LOG", "0") == "1":
        from maop.core.monitoring import setup_json_logging
        setup_json_logging(
            level=os.environ.get("MAOP_LOG_LEVEL", "INFO"),
            log_file=os.environ.get("MAOP_JSON_LOG_FILE") or None,
        )

    # Handle nested `mcp marketplace ...` commands with their own subparser,
    # separate from the flat top-level action model. Dispatched before the
    # main parser so the two structures don't interfere.
    argv = sys.argv[1:]
    if argv and argv[0] == "mcp":
        cmd_mcp(argv[1:])
        return

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
