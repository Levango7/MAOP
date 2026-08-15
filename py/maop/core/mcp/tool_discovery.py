"""MAOP Tool Discovery — unified local + remote tool discovery with signature checks.

``ToolDiscovery`` aggregates MCP tool definitions from two sources:

  1. **Local** — scans the project's ``config/mcp_servers.yaml`` and the
     user-global ``~/.config/maop/mcp_servers.yaml`` for installed servers,
     then reads each server's tool list from the MCP hub database.
  2. **Remote** — fetches a JSON catalog from one or more HTTP/HTTPS
     registry URLs (the same format used by ``mcp_marketplace.py``).

Each discovered tool is wrapped in a :class:`DiscoveredTool` record that
carries its source, an optional Ed25519 signature, and a ``verified`` flag.
When a public key is provided (per-publisher or global), the discovery
service verifies the signature on remote tools and marks unverified /
tampered tools accordingly — local tools are trusted by default (they were
installed by the operator).

Usage::

    from maop.core.mcp.tool_discovery import ToolDiscovery

    td = ToolDiscovery(root_dir="/path/to/MAOP")
    tools = td.discover_all(registries=["https://registry.example.com/catalog.json"])
    verified = [t for t in tools if t.verified]
"""

from __future__ import annotations

import json
import logging
import os
import platform
import urllib.error
import urllib.request
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from maop.core.mcp.tool_signing import ToolSignatureError, ToolSigner

logger = logging.getLogger(__name__)


# ── Enums & data models ───────────────────────────────────────


class DiscoverySource(str, Enum):
    """Where a discovered tool came from."""

    LOCAL = "local"
    REMOTE = "remote"


class DiscoveredTool(BaseModel):
    """A tool discovered from a local or remote source.

    The ``verified`` flag is ``True`` when:
      - the source is local (operator-installed → trusted), or
      - the source is remote AND an Ed25519 signature is present AND
        verifies against the configured public key for the tool's
        ``publisher``.
    """

    name: str
    description: str = ""
    version: str = ""
    publisher: str = ""
    source: DiscoverySource = DiscoverySource.LOCAL
    source_url: str = ""
    transport_type: str = "stdio"
    # Hex-encoded Ed25519 signature over the canonical manifest.
    signature: str = ""
    signing_algorithm: str = ""
    signing_key_id: str = ""
    verified: bool = False
    # Raw manifest fields for install / inspection.
    manifest: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class DiscoveryReport(BaseModel):
    """Aggregate result of a discovery sweep."""

    local_count: int = 0
    remote_count: int = 0
    verified_count: int = 0
    unverified_count: int = 0
    errors: list[str] = Field(default_factory=list)


# ── Discovery service ────────────────────────────────────────


class ToolDiscovery:
    """Discover MCP tools from local config and remote registries.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root (where ``config/mcp_servers.yaml`` lives).
    signer : ToolSigner | None
        Signer instance for signature verification.  A default one is
        created when ``None``.
    trusted_public_keys : dict[str, str] | None
        Mapping of ``publisher → PEM public key``.  Remote tools whose
        publisher has no key here are left unverified (not an error).
    timeout_s : float
        Network timeout for remote registry fetches.
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        signer: ToolSigner | None = None,
        trusted_public_keys: dict[str, str] | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._root = Path(root_dir)
        self._signer = signer or ToolSigner()
        self._trusted_keys = trusted_public_keys or {}
        self._timeout = timeout_s

    # ── local discovery ───────────────────────────────────────

    def _local_scan_paths(self) -> list[Path]:
        """Yield local config file paths to scan (project + user-global)."""
        paths = [self._root / "config" / "mcp_servers.yaml"]
        # User-global config location per OS.
        system = platform.system()
        if system == "Windows":
            appdata = os.environ.get("APPDATA", "")
            if appdata:
                paths.append(Path(appdata) / "maop" / "mcp_servers.yaml")
        else:
            paths.append(Path.home() / ".config" / "maop" / "mcp_servers.yaml")
        return paths

    def discover_local(self) -> tuple[list[DiscoveredTool], DiscoveryReport]:
        """Discover tools from local MCP server config files.

        Reads each ``mcp_servers.yaml``, treats every configured server as
        a locally-installed tool source, and marks all results as
        ``verified=True`` (local installs are operator-trusted).
        """
        report = DiscoveryReport()
        tools: list[DiscoveredTool] = []

        for path in self._local_scan_paths():
            if not path.exists():
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception as exc:
                report.errors.append(f"Failed to read {path}: {exc}")
                continue
            servers = data.get("servers", [])
            if isinstance(servers, dict):
                servers = list(servers.values())
            for srv in servers:
                if not isinstance(srv, dict):
                    continue
                name = srv.get("name", "")
                if not name:
                    continue
                tools.append(DiscoveredTool(
                    name=name,
                    description=srv.get("description", ""),
                    version=srv.get("version", ""),
                    publisher=srv.get("publisher", "local"),
                    source=DiscoverySource.LOCAL,
                    source_url=str(path),
                    transport_type=srv.get("transport", "stdio"),
                    verified=True,  # local → trusted
                    manifest=srv,
                    tags=srv.get("tags", []),
                ))

        report.local_count = len(tools)
        return tools, report

    # ── remote discovery ──────────────────────────────────────

    def _fetch_url(self, url: str) -> bytes:
        """Fetch *url* with a timeout; raises ``urllib.error.URLError`` on failure."""
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data: bytes = resp.read()
            return data

    def discover_remote(
        self, registries: list[str]
    ) -> tuple[list[DiscoveredTool], DiscoveryReport]:
        """Discover tools from remote registry URLs.

        Each registry is expected to return JSON of the form::

            {"tools": [{"name": ..., "signature": ..., "publisher": ...}, ...]}

        Tools with a valid signature from a trusted publisher are marked
        ``verified=True``; the rest are still returned but with
        ``verified=False`` so callers can decide whether to install them.
        """
        report = DiscoveryReport()
        tools: list[DiscoveredTool] = []

        for url in registries:
            try:
                raw = self._fetch_url(url)
                catalog = json.loads(raw)
            except Exception as exc:
                report.errors.append(f"Failed to fetch {url}: {exc}")
                continue
            for entry in catalog.get("tools", []):
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name", "")
                if not name:
                    continue
                publisher = entry.get("publisher", "")
                verified = self._verify_remote_tool(entry, publisher)
                tools.append(DiscoveredTool(
                    name=name,
                    description=entry.get("description", ""),
                    version=entry.get("version", ""),
                    publisher=publisher,
                    source=DiscoverySource.REMOTE,
                    source_url=url,
                    transport_type=entry.get("transport_type", "stdio"),
                    signature=entry.get("signature", ""),
                    signing_algorithm=entry.get("signing_algorithm", ""),
                    signing_key_id=entry.get("signing_key_id", ""),
                    verified=verified,
                    manifest=entry,
                    tags=entry.get("tags", []),
                ))

        report.remote_count = len(tools)
        return tools, report

    def _verify_remote_tool(self, entry: dict[str, Any], publisher: str) -> bool:
        """Verify a remote tool's signature against the trusted key for *publisher*.

        Returns ``False`` (not an error) when:
          - the publisher has no trusted key configured, or
          - the entry has no signature.
        Returns ``False`` and logs a warning when the signature is present
        but invalid (potential tampering).
        """
        pub_key = self._trusted_keys.get(publisher)
        if not pub_key:
            return False
        if not entry.get("signature"):
            return False
        try:
            return self._signer.verify_manifest(entry, pub_key)
        except ToolSignatureError as exc:
            logger.warning("Signature verification failed for %s: %s", entry.get("name"), exc)
            return False

    # ── combined discovery ────────────────────────────────────

    def discover_all(
        self,
        registries: list[str] | None = None,
    ) -> tuple[list[DiscoveredTool], DiscoveryReport]:
        """Discover tools from all sources (local + remote).

        De-duplicates by ``name``: when the same tool name appears in both
        local and remote sources, the local (trusted) entry wins.
        """
        local_tools, report = self.discover_local()
        remote_tools: list[DiscoveredTool] = []
        if registries:
            remote_tools, remote_report = self.discover_remote(registries)
            report.remote_count = remote_report.remote_count
            report.errors.extend(remote_report.errors)

        # Merge with local taking precedence on name collisions.
        seen: set[str] = set()
        merged: list[DiscoveredTool] = []
        for tool in (*local_tools, *remote_tools):
            if tool.name in seen:
                continue
            seen.add(tool.name)
            merged.append(tool)

        report.local_count = len(local_tools)
        report.verified_count = sum(1 for t in merged if t.verified)
        report.unverified_count = len(merged) - report.verified_count
        return merged, report