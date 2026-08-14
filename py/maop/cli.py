"""
MAOP - Agent Orchestration Framework CLI (Python entry point).
All commands use Python-native implementations. No PowerShell fallbacks.
"""

from __future__ import annotations

import logging
logger = logging.getLogger(__name__)

import argparse
import os
import sys
from pathlib import Path
from typing import Any

MAOP_ROOT = Path(__file__).resolve().parent.parent.parent


def cmd_start(port: int = 9079, host: str = "127.0.0.1") -> Any:
    """Start the FastAPI dashboard (Python-native)."""
    try:
        import uvicorn

        from maop.dashboard.server import app
    except ImportError:
        sys.exit(1)

    # Multi-worker support (MAOP_DASH_WORKERS / MAOP_WORKERS env var)
    workers = int(os.environ.get("MAOP_DASH_WORKERS", os.environ.get("MAOP_WORKERS", "1")))
    uvicorn_kwargs: dict[str, Any] = {"log_level": "info"}

    if workers > 1:
        uvicorn_kwargs["workers"] = workers
    else:
        # TLS support (single-worker only — uvicorn limitation)
        tls_enabled = os.environ.get("MAOP_TLS", "0") == "1"
        if tls_enabled:
            from maop.core.security.tls import TLSSettings, create_ssl_context
            cert_file = os.environ.get("MAOP_TLS_CERT", "")
            key_file = os.environ.get("MAOP_TLS_KEY", "")
            if cert_file and key_file:
                tls_settings = TLSSettings(enabled=True, cert_file=cert_file, key_file=key_file)
                ssl_ctx = create_ssl_context(tls_settings)
                if ssl_ctx:
                    uvicorn_kwargs["ssl"] = ssl_ctx

        _proto = "https" if "ssl" in uvicorn_kwargs else "http"

    uvicorn.run(app, host=host, port=port, **uvicorn_kwargs)


def cmd_stop() -> Any:
    """Stop MAOP using Python-native deploy.stop()."""
    try:
        from maop.deploy import stop
        result = stop(MAOP_ROOT)
        if result.pid:
            pass
    except Exception:
        sys.exit(1)


def cmd_status() -> Any:
    """Check MAOP status using Python-native deploy.status()."""
    try:
        from maop.deploy import status
        result = status(MAOP_ROOT)
        if result.pid:
            pass
        for _comp in result.components:
            pass
    except Exception:
        logger.debug('swallowed exception', exc_info=True)
        pass


def cmd_run(task: str) -> Any:
    """Run a task through Python-native MaopLoop."""
    try:
        import asyncio

        from maop.maop_loop import MaopLoop
        loop = MaopLoop(root_dir=str(MAOP_ROOT))
        result = asyncio.run(loop.run(task))
        if result.execution:
            if result.execution.stdout:
                pass
            if result.execution.error:
                pass
        if result.block_reason:
            pass
    except ImportError:
        sys.exit(1)
    except Exception:
        sys.exit(1)


def cmd_validate() -> Any:
    """Run config validation using Python-native deploy.validate_config()."""
    try:
        from maop.deploy import validate_config
        result = validate_config(MAOP_ROOT)
        if result.valid:
            pass
        else:
            for _err in result.errors:
                pass
        for _warn in result.warnings:
            pass
        if not result.valid:
            sys.exit(1)
    except Exception:
        sys.exit(1)


def cmd_health() -> Any:
    """Run health check using Python-native deploy.health_check()."""
    try:
        from maop.deploy import health_check
        results = health_check(MAOP_ROOT)
        all_healthy = True
        for r in results:
            _status_icon = {"healthy": "+", "degraded": "~", "unhealthy": "!"}[r.status.value]
            if r.status.value != "healthy":
                all_healthy = False
        if not all_healthy:
            sys.exit(1)
        else:
            pass
    except Exception:
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

    from maop.core.mcp.mcp_marketplace import MCPMarketplace
    # Use the project-root config dir so reads/writes stay local to the project
    # (not the package-shipped default, which is read-only).
    config_path = MAOP_ROOT / "config" / "mcp_marketplace.yaml"
    mp = MCPMarketplace(config_path=config_path)

    if parsed.subcommand == "list-registries":
        regs = mp.list_registries()
        if not regs:
            pass
        for r in regs:
            _trust = "trusted" if r.trusted else "untrusted"
            _status = "enabled" if r.enabled else "disabled"
    elif parsed.subcommand == "add-registry":
        mp.add_registry(parsed.name, parsed.url, trusted=parsed.trusted)
        _trust = "trusted" if parsed.trusted else "untrusted"
    elif parsed.subcommand == "remove-registry":
        mp.remove_registry(parsed.name)
    elif parsed.subcommand == "search":
        tags = (
            [t.strip() for t in parsed.tags.split(",") if t.strip()]
            if parsed.tags else None
        )
        results = mp.search(parsed.query, tags=tags)
        if not results:
            pass
        for s in results:
            _verified = " [verified]" if s.verified else ""
            _tags_str = f" tags={','.join(s.tags)}" if s.tags else ""
    elif parsed.subcommand == "install":
        try:
            _cfg = mp.install(
                parsed.name,
                registry_name=parsed.registry,
                verify_checksum=not parsed.no_verify,
                confirm_untrusted=parsed.confirm_untrusted,
            )
        except ValueError:
            sys.exit(1)
    elif parsed.subcommand == "uninstall":
        if mp.uninstall(parsed.name):
            pass
        else:
            sys.exit(1)
    elif parsed.subcommand == "list-installed":
        installed = mp.list_installed()
        if not installed:
            pass
        for s in installed:  # type: ignore[assignment]
            pass


def cmd_mcp(args: list[str]) -> Any:
    """Handle ``mcp <subcommand>`` — currently only ``marketplace`` is supported."""
    if not args:
        sys.exit(1)
    sub = args[0]
    rest = args[1:]
    if sub == "marketplace":
        cmd_mcp_marketplace(rest)
    else:
        sys.exit(1)


# ── worker subcommand (F1-01 distributed execution) ──────────────

def cmd_worker_start(
    redis_url: str = "redis://localhost:6379/0",
    concurrency: int = 4,
    capabilities: str = "",
    heartbeat_interval: float = 5.0,
) -> None:
    """Start a distributed worker that consumes tasks from Redis Streams.

    F1-01 (分布式执行): the worker registers with the
    :class:`~maop.core.scheduling.worker_pool.WorkerRegistry`, sends
    periodic heartbeats, and executes DAG node tasks dispatched by the
    :class:`~maop.core.scheduling.distributed_scheduler.DistributedScheduler`.

    Parameters
    ----------
    redis_url : str
        Redis connection URL.
    concurrency : int
        Maximum concurrent task executions.
    capabilities : str
        Comma-separated affinity tags (e.g. ``"gpu,linux"``).
    heartbeat_interval : float
        Seconds between heartbeat refreshes.
    """
    from maop.worker.distributed_worker import run_worker

    caps = {t.strip() for t in capabilities.split(",") if t.strip()} if capabilities else set()
    run_worker(
        redis_url=redis_url,
        concurrency=concurrency,
        capabilities=caps,
        heartbeat_interval=heartbeat_interval,
    )


def cmd_worker(args: list[str]) -> None:
    """``maop worker <subcommand>`` dispatcher.

    Subcommands
    -----------
        start   Start a distributed worker
    """
    if not args:
        import sys as _sys
        _sys.stderr.write(
            "usage: maop worker <subcommand>\n"
            "  start   Start a distributed worker (Redis Streams consumer)\n"
        )
        _sys.exit(1)

    sub = args[0]
    if sub == "start":
        parser = argparse.ArgumentParser(
            prog="maop worker start",
            description="Start a distributed worker that consumes tasks from Redis Streams",
        )
        parser.add_argument(
            "--redis-url", default="redis://localhost:6379/0",
            help="Redis connection URL (default: redis://localhost:6379/0)",
        )
        parser.add_argument(
            "--concurrency", type=int, default=4,
            help="Maximum concurrent task executions (default: 4)",
        )
        parser.add_argument(
            "--capabilities", default="",
            help="Comma-separated affinity tags (e.g. 'gpu,linux')",
        )
        parser.add_argument(
            "--heartbeat-interval", type=float, default=5.0,
            help="Seconds between heartbeat refreshes (default: 5.0)",
        )
        parsed = parser.parse_args(args[1:])
        cmd_worker_start(
            redis_url=parsed.redis_url,
            concurrency=parsed.concurrency,
            capabilities=parsed.capabilities,
            heartbeat_interval=parsed.heartbeat_interval,
        )
    else:
        import sys as _sys
        _sys.stderr.write(f"Unknown worker subcommand: {sub}\n")
        _sys.exit(1)


# ── migrate subcommand ──────────────────────────────────────────

def cmd_migrate_pg_init() -> None:
    """Run alembic upgrade head against the PG ini."""
    import subprocess
    from pathlib import Path

    pg_ini = Path(__file__).resolve().parent / "migrations" / "pg" / "alembic.ini"
    subprocess.call(["alembic", "-c", str(pg_ini), "upgrade", "head"])


def cmd_migrate_sqlite_to_pg(args: list[str]) -> None:
    """Dispatch sqlite-to-pg migration with parsed args."""
    dry_run = "--dry-run" in args
    tables: list[str] = []
    if "--tables" in args:
        idx = args.index("--tables")
        if idx + 1 < len(args):
            tables = [t.strip() for t in args[idx + 1].split(",") if t.strip()]

    from maop.migrations.sqlite_to_pg import migrate
    migrate(dry_run=dry_run, tables=tables)


def cmd_migrate(args: list[str]) -> None:
    """`maop migrate <subcommand>` dispatcher."""
    if not args:
        import sys as _sys
        _sys.stderr.write(
            "usage: maop migrate <subcommand>\n"
            "  pg-init        Initialize PostgreSQL schema via Alembic\n"
            "  sqlite-to-pg   Migrate SQLite data to PostgreSQL\n"
            "  status         Show migration status\n"
        )
        _sys.exit(1)

    sub = args[0]
    if sub == "pg-init":
        cmd_migrate_pg_init()
    elif sub == "sqlite-to-pg":
        cmd_migrate_sqlite_to_pg(args[1:])
    elif sub == "status":
        from maop.core.backends.db_utils import get_db_path
        print(f"SQLite DB: {get_db_path('maop')}")
    else:
        import sys as _sys
        _sys.stderr.write(f"Unknown migrate subcommand: {sub}\n")
        _sys.exit(1)


# ── config subcommand (v5.0.0 env var migration) ─────────────────

# Short-name → long-name environment variable mappings (v5.0.0 migration).
_CONFIG_MIGRATE_MAPPINGS: dict[str, str] = {
    "MAOP_PORT": "MAOP_DASH_PORT",
    "MAOP_WORKERS": "MAOP_DASH_WORKERS",
    "MAOP_TLS": "MAOP_TLS_ENABLED",
    "MAOP_AUTH": "MAOP_AUTH_ENABLED",
}


def cmd_config_migrate(dry_run: bool = False, file: str = ".env") -> None:
    """Migrate deprecated short-name environment variables to long names.

    Rewrites the given ``.env`` file in place, replacing deprecated
    short names (``MAOP_PORT``, ``MAOP_WORKERS``, ``MAOP_TLS``,
    ``MAOP_AUTH``) with their v5.0.0 canonical long names. A comment
    recording the migration date is prepended. When *dry_run* is True
    the changes are only printed, not written.

    Parameters
    ----------
    dry_run : bool
        Preview changes without modifying the file.
    file : str
        Path to the ``.env`` file (default: ``.env`` in CWD).
    """
    from datetime import date

    env_path = Path(file)
    if not env_path.is_file():
        print(f"[config migrate] file not found: {env_path}")
        sys.exit(1)

    # utf-8-sig transparently strips a leading BOM if present.
    text = env_path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    changes: list[tuple[str, str, str]] = []  # (old_name, new_name, value)
    new_lines: list[str] = []

    # First pass: collect existing keys to avoid duplicate long-name entries.
    existing_keys: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            existing_keys.add(key)

    today = date.today().isoformat()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, sep, value = stripped.partition("=")
            key = key.strip()
            if key in _CONFIG_MIGRATE_MAPPINGS:
                new_key = _CONFIG_MIGRATE_MAPPINGS[key]
                if new_key in existing_keys:
                    # Long name already present; drop the short-name line
                    # to avoid duplicate / conflicting values.
                    changes.append((key, new_key, value))
                    new_lines.append(
                        f"# [migrated {today}] {key}={value}"
                        f"  (superseded by existing {new_key})"
                    )
                    continue
                changes.append((key, new_key, value))
                new_lines.append(f"{new_key}{sep}{value}")
                existing_keys.add(new_key)
                continue
        new_lines.append(line)

    if not changes:
        print(f"[config migrate] no deprecated variables found in {env_path}")
        return

    migration_header = (
        f"# [migrated {today}] "
        f"short-name env vars -> long names (maop config migrate)"
    )
    # Prepend header only if not already present.
    if migration_header not in lines:
        new_lines.insert(0, migration_header)

    print(f"[config migrate] {len(changes)} variable(s) to migrate:")
    for old, new, val in changes:
        print(f"  {old}={val}  ->  {new}={val}")

    if dry_run:
        print("[config migrate] --dry-run: no changes written.")
        return

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"[config migrate] wrote {env_path}")


def cmd_config(args: list[str]) -> None:
    """``maop config <subcommand>`` dispatcher.

    Subcommands
    -----------
        migrate   Migrate deprecated short-name env vars (.env)
    """
    if not args:
        sys.stderr.write(
            "usage: maop config <subcommand>\n"
            "  migrate   Migrate deprecated short-name env vars (.env)\n"
        )
        sys.exit(1)

    sub = args[0]
    if sub == "migrate":
        parser = argparse.ArgumentParser(
            prog="maop config migrate",
            description="Migrate deprecated short-name environment variables to long names",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Preview changes without writing the file",
        )
        parser.add_argument(
            "--file", default=".env",
            help="Path to the .env file (default: .env)",
        )
        parsed = parser.parse_args(args[1:])
        cmd_config_migrate(dry_run=parsed.dry_run, file=parsed.file)
    else:
        sys.stderr.write(f"Unknown config subcommand: {sub}\n")
        sys.exit(1)


def main() -> Any:
    # Enable JSON structured logging when MAOP_JSON_LOG=1 (for ELK / Loki).
    if os.environ.get("MAOP_JSON_LOG", "0") == "1":
        from maop.core.monitoring.monitoring import setup_json_logging
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
    if argv and argv[0] == "migrate":
        cmd_migrate(argv[1:])
        return
    if argv and argv[0] == "config":
        cmd_config(argv[1:])
        return
    if argv and argv[0] == "worker":
        cmd_worker(argv[1:])
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
            sys.exit(1)
        cmd_run(args.task)
    elif args.action == "validate":
        cmd_validate()
    elif args.action == "health":
        cmd_health()


if __name__ == "__main__":
    main()
