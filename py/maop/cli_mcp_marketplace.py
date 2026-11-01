"""``maop mcp marketplace`` subcommand handling (extracted from cli.py, M-X1).

拆分动因：cli.py 在 file-size 门禁 800 行边缘（M1/M2 先后触线）；按
``cli_evolution`` / ``cli_run`` 先例把 ``mcp marketplace`` 子命令族迁出，
``maop.cli`` 继续 re-export ``cmd_mcp_marketplace``（patch 面不变）。
"""

from __future__ import annotations

import argparse
import sys
from typing import Any


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
    p_install.add_argument(
        "--require-signature", action="store_true",
        help="Refuse installs without a valid Ed25519 signature (M-X1)",
    )

    p_uninstall = sub.add_parser("uninstall", help="Uninstall a marketplace server")
    p_uninstall.add_argument("name", help="Server name to uninstall")

    sub.add_parser("list-installed", help="List installed marketplace servers")

    parsed = parser.parse_args(args)

    from maop.cli import _runtime_root  # 延迟导入：cli.py 是本模块的门面（re-export）
    from maop.config.settings import marketplace_verify_key_path
    from maop.core.marketplace.signing import PackageVerifier
    from maop.core.mcp.mcp_marketplace import MCPMarketplace
    # Use the project-root config dir so reads/writes stay local to the project
    # (not the package-shipped default, which is read-only).
    config_path = _runtime_root() / "config" / "mcp_marketplace.yaml"
    verify_key = marketplace_verify_key_path()
    mp = MCPMarketplace(
        config_path=config_path,
        verifier=PackageVerifier(verify_key) if verify_key else None,
        require_signature=getattr(parsed, "require_signature", False),
    )

    if parsed.subcommand == "list-registries":
        regs = mp.list_registries()
        if not regs:
            print("[INFO] 未配置任何 registry")
        for r in regs:
            trust = "trusted" if r.trusted else "untrusted"
            status = "enabled" if r.enabled else "disabled"
            print(f"  {r.name}  {r.url}  [{trust}] [{status}]")
    elif parsed.subcommand == "add-registry":
        mp.add_registry(parsed.name, parsed.url, trusted=parsed.trusted)
        trust = "trusted" if parsed.trusted else "untrusted"
        print(f"[OK] add-registry 完成 — {parsed.name} {parsed.url} [{trust}]")
    elif parsed.subcommand == "remove-registry":
        mp.remove_registry(parsed.name)
        print(f"[OK] remove-registry 完成 — 已移除 {parsed.name}")
    elif parsed.subcommand == "search":
        tags = (
            [t.strip() for t in parsed.tags.split(",") if t.strip()]
            if parsed.tags else None
        )
        results = mp.search(parsed.query, tags=tags)
        if not results:
            print(f"[INFO] 未找到匹配 '{parsed.query}' 的服务器")
        for s in results:
            verified = " [verified]" if s.verified else ""
            tags_str = f" tags={','.join(s.tags)}" if s.tags else ""
            print(f"  {s.name}  {s.description}{verified}{tags_str}")
    elif parsed.subcommand == "install":
        try:
            _cfg = mp.install(
                parsed.name,
                registry_name=parsed.registry,
                verify_checksum=not parsed.no_verify,
                confirm_untrusted=parsed.confirm_untrusted,
            )
            print(f"[OK] install 完成 — 已安装 {parsed.name}")
        except ValueError:
            sys.exit(1)
    elif parsed.subcommand == "uninstall":
        if mp.uninstall(parsed.name):
            print(f"[OK] uninstall 完成 — 已卸载 {parsed.name}")
        else:
            print(f"[FAIL] uninstall 失败 — {parsed.name} 未安装或卸载失败", file=sys.stderr)
            sys.exit(1)
    elif parsed.subcommand == "list-installed":
        installed = mp.list_installed()
        if not installed:
            print("[INFO] 未安装任何 marketplace 服务器")
        for s in installed:  # type: ignore
            print(f"  {s.name}  {s.version}")
