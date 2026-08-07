"""Tests for maop.core.mcp_discovery.MCPDiscovery.

E1 (2026-07-22, Phase E): verifies auto-discovery of MCP server configs
from standard locations (project YAML + Claude Desktop JSON format).

Uses tmp_path to create isolated config files — no real home directory
or real subprocess is touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from maop.core.mcp.mcp_discovery import DiscoveryReport, MCPDiscovery, _claude_desktop_config_path
from maop.core.mcp.mcp_hub import TransportType

# ── _claude_desktop_config_path ────────────────────────────────


def test_claude_desktop_config_path_returns_none_on_unknown_os():
    """On an unknown OS, the path helper returns None."""
    with patch("maop.core.mcp.mcp_discovery.platform.system", return_value="UnknownOS"):
        assert _claude_desktop_config_path() is None


def test_claude_desktop_config_path_windows_uses_appdata():
    """On Windows, the path is derived from %APPDATA%."""
    with patch("maop.core.mcp.mcp_discovery.platform.system", return_value="Windows"), \
         patch.dict("os.environ", {"APPDATA": "C:/Users/test/AppData/Roaming"}):
        p = _claude_desktop_config_path()
        assert p is not None
        assert p.name == "claude_desktop_config.json"
        assert "Claude" in p.parts


def test_claude_desktop_config_path_windows_no_appdata_returns_none():
    """On Windows without %APPDATA%, returns None."""
    with patch("maop.core.mcp.mcp_discovery.platform.system", return_value="Windows"), \
         patch.dict("os.environ", {"APPDATA": ""}, clear=True):
        # Re-import to pick up patched env (module-level reads at call time).
        assert _claude_desktop_config_path() is None


# ── DiscoveryReport model ──────────────────────────────────────


def test_discovery_report_defaults():
    r = DiscoveryReport()
    assert r.sources_scanned == 0
    assert r.servers_found == 0
    assert r.servers_registered == 0
    assert r.errors == []


# ── discover() with empty root ─────────────────────────────────


def test_discover_no_config_files_returns_empty(tmp_path):
    """When no config files exist, discover() returns empty list + zeroed report."""
    disc = MCPDiscovery(root_dir=tmp_path)
    configs, report = disc.discover()
    assert configs == []
    assert isinstance(report, DiscoveryReport)
    assert report.servers_found == 0
    # sources_scanned counts all scan_paths() regardless of existence.
    assert report.sources_scanned >= 1
    assert report.errors == []


# ── discover() with YAML config ────────────────────────────────


def test_discover_yaml_servers_dict_format(tmp_path):
    """MAOP YAML config with `servers` as a dict is parsed correctly."""
    cfg = tmp_path / "config" / "mcp_servers.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("""
servers:
  filesystem:
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
  remote:
    transport: sse
    url: http://localhost:8080
""", encoding="utf-8")
    disc = MCPDiscovery(root_dir=tmp_path)
    # Patch user-level + Claude paths to non-existent so only the
    # project-local file is scanned.
    with patch.object(MCPDiscovery, "_scan_paths", lambda self: [cfg]):
        configs, report = disc.discover()
    assert len(configs) == 2
    names = {c.name for c in configs}
    assert "filesystem" in names
    assert "remote" in names
    fs = next(c for c in configs if c.name == "filesystem")
    assert fs.transport == TransportType.STDIO
    assert fs.command == "npx"
    assert fs.args == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    rm = next(c for c in configs if c.name == "remote")
    assert rm.transport == TransportType.SSE
    assert rm.url == "http://localhost:8080"
    assert report.servers_found == 2
    assert report.errors == []


def test_discover_yaml_servers_list_format(tmp_path):
    """MAOP YAML config with `servers` as a list is coerced to configs."""
    cfg = tmp_path / "config" / "mcp_servers.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("""
servers:
  - name: list-server
    transport: stdio
    command: python
    args: ["-m", "mcp_server"]
""", encoding="utf-8")
    disc = MCPDiscovery(root_dir=tmp_path)
    with patch.object(MCPDiscovery, "_scan_paths", lambda self: [cfg]):
        configs, _ = disc.discover()
    assert len(configs) == 1
    assert configs[0].name == "list-server"
    assert configs[0].command == "python"


def test_discover_yaml_mcp_servers_key(tmp_path):
    """The alternative `mcp_servers` key is also recognized."""
    cfg = tmp_path / "config" / "mcp_servers.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("""
mcp_servers:
  alt-server:
    transport: stdio
    command: node
""", encoding="utf-8")
    disc = MCPDiscovery(root_dir=tmp_path)
    with patch.object(MCPDiscovery, "_scan_paths", lambda self: [cfg]):
        configs, _ = disc.discover()
    assert len(configs) == 1
    assert configs[0].name == "alt-server"


def test_discover_yaml_invalid_transport_falls_back_to_stdio(tmp_path):
    """An unknown transport string falls back to STDIO."""
    cfg = tmp_path / "config" / "mcp_servers.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("""
servers:
  bad-transport:
    transport: invalid_transport
    command: echo
""", encoding="utf-8")
    disc = MCPDiscovery(root_dir=tmp_path)
    with patch.object(MCPDiscovery, "_scan_paths", lambda self: [cfg]):
        configs, _ = disc.discover()
    assert len(configs) == 1
    assert configs[0].transport == TransportType.STDIO


def test_discover_yaml_non_dict_root_returns_empty(tmp_path):
    """A YAML file whose root is not a dict yields no configs."""
    cfg = tmp_path / "config" / "mcp_servers.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("- just\n- a\n- list", encoding="utf-8")
    disc = MCPDiscovery(root_dir=tmp_path)
    with patch.object(MCPDiscovery, "_scan_paths", lambda self: [cfg]):
        configs, _ = disc.discover()
    assert configs == []


# ── discover() with Claude Desktop JSON ────────────────────────


def test_discover_claude_desktop_json(tmp_path):
    """Claude Desktop JSON format is parsed into MCPServerConfig."""
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({
        "mcpServers": {
            "fs": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                "env": {"NODE_PATH": "/usr/lib"},
            },
            "remote": {
                "url": "http://remote:8080",
            },
        },
    }), encoding="utf-8")
    disc = MCPDiscovery(root_dir=tmp_path)
    with patch.object(MCPDiscovery, "_scan_paths", lambda self: [cfg]):
        configs, report = disc.discover()
    assert len(configs) == 2
    fs = next(c for c in configs if c.name == "fs")
    assert fs.transport == TransportType.STDIO
    assert fs.command == "npx"
    assert fs.env == {"NODE_PATH": "/usr/lib"}
    rm = next(c for c in configs if c.name == "remote")
    # URL without command → SSE
    assert rm.transport == TransportType.SSE
    assert rm.url == "http://remote:8080"
    assert report.servers_found == 2


def test_discover_claude_desktop_skips_non_dict_server(tmp_path):
    """A non-dict entry in mcpServers is skipped without error."""
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({
        "mcpServers": {
            "good": {"command": "echo"},
            "bad": "not-a-dict",
        },
    }), encoding="utf-8")
    disc = MCPDiscovery(root_dir=tmp_path)
    with patch.object(MCPDiscovery, "_scan_paths", lambda self: [cfg]):
        configs, _ = disc.discover()
    assert len(configs) == 1
    assert configs[0].name == "good"


# ── Deduplication across sources ──────────────────────────────


def test_discover_deduplicates_by_name_across_sources(tmp_path):
    """A server name found in an earlier source shadows later sources."""
    yaml_cfg = tmp_path / "config" / "mcp_servers.yaml"
    yaml_cfg.parent.mkdir(parents=True)
    yaml_cfg.write_text("""
servers:
  shared:
    command: from-yaml
""", encoding="utf-8")
    json_cfg = tmp_path / "claude.json"
    json_cfg.write_text(json.dumps({
        "mcpServers": {"shared": {"command": "from-json"}},
    }), encoding="utf-8")
    disc = MCPDiscovery(root_dir=tmp_path)
    with patch.object(MCPDiscovery, "_scan_paths", lambda self: [yaml_cfg, json_cfg]):
        configs, report = disc.discover()
    assert len(configs) == 1
    # YAML is scanned first, so its command wins.
    assert configs[0].command == "from-yaml"
    assert report.servers_found == 1


# ── Error handling ────────────────────────────────────────────


def test_discover_records_parse_error_in_report(tmp_path):
    """A malformed YAML/JSON file is recorded in report.errors, not raised."""
    cfg = tmp_path / "config" / "mcp_servers.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{ invalid: yaml: content:", encoding="utf-8")
    disc = MCPDiscovery(root_dir=tmp_path)
    with patch.object(MCPDiscovery, "_scan_paths", lambda self: [cfg]):
        configs, report = disc.discover()
    assert configs == []
    assert len(report.errors) == 1
    assert "mcp_servers.yaml" in report.errors[0]


def test_discover_malformed_json_recorded(tmp_path):
    """Malformed JSON is caught and recorded, not propagated."""
    cfg = tmp_path / "claude.json"
    cfg.write_text("{not valid json", encoding="utf-8")
    disc = MCPDiscovery(root_dir=tmp_path)
    with patch.object(MCPDiscovery, "_scan_paths", lambda self: [cfg]):
        configs, report = disc.discover()
    assert configs == []
    assert len(report.errors) == 1


# ── _scan_paths ───────────────────────────────────────────────


def test_scan_paths_includes_project_yaml_and_yml(tmp_path):
    """Both .yaml and .yml project config paths are included."""
    disc = MCPDiscovery(root_dir=tmp_path)
    paths = disc._scan_paths()
    yaml_path = tmp_path / "config" / "mcp_servers.yaml"
    yml_path = tmp_path / "config" / "mcp_servers.yml"
    assert yaml_path in paths
    assert yml_path in paths


def test_scan_paths_includes_user_global_config(tmp_path):
    """User-global ~/.config/maop/mcp_servers.yaml is included."""
    disc = MCPDiscovery(root_dir=tmp_path)
    paths = disc._scan_paths()
    user_path = Path.home() / ".config" / "maop" / "mcp_servers.yaml"
    assert user_path in paths


def test_scan_paths_includes_claude_desktop_on_supported_os(tmp_path):
    """On Windows/macOS/Linux, the Claude Desktop config path is included."""
    disc = MCPDiscovery(root_dir=tmp_path)
    with patch("maop.core.mcp.mcp_discovery.platform.system", return_value="Linux"):
        paths = disc._scan_paths()
    claude_path = Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    assert claude_path in paths


# ── Edge cases ────────────────────────────────────────────────


def test_discover_unknown_suffix_skipped(tmp_path):
    """A file with an unknown suffix is skipped (not parsed, not errored)."""
    cfg = tmp_path / "config.txt"
    cfg.write_text("some text", encoding="utf-8")
    disc = MCPDiscovery(root_dir=tmp_path)
    with patch.object(MCPDiscovery, "_scan_paths", lambda self: [cfg]):
        configs, report = disc.discover()
    assert configs == []
    assert report.errors == []
    assert report.servers_found == 0


def test_discover_empty_yaml_file_returns_empty(tmp_path):
    """An empty YAML file yields no configs and no errors."""
    cfg = tmp_path / "config" / "mcp_servers.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("", encoding="utf-8")
    disc = MCPDiscovery(root_dir=tmp_path)
    with patch.object(MCPDiscovery, "_scan_paths", lambda self: [cfg]):
        configs, report = disc.discover()
    assert configs == []
    assert report.errors == []


def test_discover_yml_suffix_also_parsed(tmp_path):
    """The .yml suffix is treated the same as .yaml."""
    cfg = tmp_path / "config" / "mcp_servers.yml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("servers:\n  yml-server:\n    command: echo\n", encoding="utf-8")
    disc = MCPDiscovery(root_dir=tmp_path)
    with patch.object(MCPDiscovery, "_scan_paths", lambda self: [cfg]):
        configs, _ = disc.discover()
    assert len(configs) == 1
    assert configs[0].name == "yml-server"


def test_discover_skips_non_dict_server_in_yaml(tmp_path):
    """A non-dict server entry in YAML is skipped without error."""
    cfg = tmp_path / "config" / "mcp_servers.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("""
servers:
  good:
    command: echo
  bad: "just-a-string"
""", encoding="utf-8")
    disc = MCPDiscovery(root_dir=tmp_path)
    with patch.object(MCPDiscovery, "_scan_paths", lambda self: [cfg]):
        configs, _ = disc.discover()
    assert len(configs) == 1
    assert configs[0].name == "good"
