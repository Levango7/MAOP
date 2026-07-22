"""MAOP MCP Auto-Discovery — Automatically find and register MCP servers.

Scans standard locations for MCP server configurations and automatically
registers them with MCPHub:

1. Project-local: ``<root_dir>/config/mcp_servers.yaml``
2. User-global: ``~/.config/maop/mcp_servers.yaml``
3. Claude Desktop format: ``~/.claude/claude_desktop_config.json`` (macOS/Linux)
   or ``%APPDATA%\\Claude\\claude_desktop_config.json`` (Windows)

Usage::

    from maop.core.mcp_discovery import MCPDiscovery

    discovery = MCPDiscovery(root_dir="/path/to/MAOP")
    configs = discovery.discover()

    # Register with MCPHub
    for cfg in configs:
        hub.add_server(cfg)
"""

from __future__ import annotations

import json
import logging
import os
import platform
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class DiscoveryReport(BaseModel):
    """Report from an auto-discovery scan."""
    sources_scanned: int = 0
    servers_found: int = 0
    servers_registered: int = 0
    errors: list[str] = []


def _claude_desktop_config_path() -> Path | None:
    """Find the Claude Desktop config file path for the current OS."""
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "Claude" / "claude_desktop_config.json"
    elif system == "Linux":
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    return None


class MCPDiscovery:
    """Auto-discover MCP server configurations from standard locations.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)

    def discover(self) -> tuple[list[Any], DiscoveryReport]:
        """Scan all standard locations and return discovered server configs.

        Returns
        -------
        tuple of (list of MCPServerConfig, DiscoveryReport)
        """
        from maop.core.mcp_hub import MCPServerConfig

        report = DiscoveryReport()
        configs: list[MCPServerConfig] = []
        seen_names: set[str] = set()

        scan_paths = self._scan_paths()
        report.sources_scanned = len(scan_paths)

        for path in scan_paths:
            if not path.exists():
                continue

            try:
                if path.suffix == ".json":
                    found = self._parse_claude_desktop(path, seen_names)
                elif path.suffix in (".yaml", ".yml"):
                    found = self._parse_yaml_config(path, seen_names)
                else:
                    continue

                configs.extend(found)
                report.servers_found += len(found)
            except Exception as exc:
                report.errors.append(f"{path}: {exc}")
                logger.warning("[mcp_discovery] Error parsing %s: %s", path, exc)

        return configs, report

    def _scan_paths(self) -> list[Path]:
        """Get all paths to scan for MCP server configs."""
        paths: list[Path] = []

        paths.append(self._root / "config" / "mcp_servers.yaml")
        paths.append(self._root / "config" / "mcp_servers.yml")

        user_config = Path.home() / ".config" / "maop" / "mcp_servers.yaml"
        paths.append(user_config)

        claude_path = _claude_desktop_config_path()
        if claude_path is not None:
            paths.append(claude_path)

        return paths

    def _parse_claude_desktop(
        self, path: Path, seen_names: set[str]
    ) -> list[Any]:
        """Parse Claude Desktop format config."""
        from maop.core.mcp_hub import MCPServerConfig, TransportType

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        servers_data = data.get("mcpServers", {})
        configs: list[MCPServerConfig] = []

        for name, server_info in servers_data.items():
            if name in seen_names:
                continue
            if not isinstance(server_info, dict):
                continue

            command = server_info.get("command", "")
            args = server_info.get("args", [])
            env = server_info.get("env", {})
            url = server_info.get("url", "")

            transport = TransportType.STDIO if command else (
                TransportType.SSE if url else TransportType.STDIO
            )

            configs.append(MCPServerConfig(
                name=name,
                transport=transport,
                command=command,
                args=args if isinstance(args, list) else [],
                env=env if isinstance(env, dict) else {},
                url=url,
            ))
            seen_names.add(name)

        return configs

    def _parse_yaml_config(
        self, path: Path, seen_names: set[str]
    ) -> list[Any]:
        """Parse MAOP-format YAML config."""
        from maop.core.mcp_hub import MCPServerConfig, TransportType

        try:
            import yaml
        except ImportError:
            logger.debug("[mcp_discovery] PyYAML not installed, skipping %s", path)
            return []

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            return []

        servers_data = data.get("servers", data.get("mcp_servers", {}))
        if isinstance(servers_data, list):
            servers_data = {s.get("name", f"server_{i}"): s for i, s in enumerate(servers_data) if isinstance(s, dict)}
        elif not isinstance(servers_data, dict):
            return []

        configs: list[MCPServerConfig] = []

        for name, server_info in servers_data.items():
            if name in seen_names:
                continue
            if not isinstance(server_info, dict):
                continue

            transport_str = server_info.get("transport", "stdio")
            try:
                transport = TransportType(transport_str)
            except ValueError:
                transport = TransportType.STDIO

            configs.append(MCPServerConfig(
                name=name,
                transport=transport,
                command=server_info.get("command", ""),
                args=server_info.get("args", []),
                env=server_info.get("env", {}),
                url=server_info.get("url", ""),
            ))
            seen_names.add(name)

        return configs