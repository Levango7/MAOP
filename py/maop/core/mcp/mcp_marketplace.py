"""MAOP MCP Marketplace — remote registry discovery and server installation.

Extends the local-only ``mcp_discovery.py`` with the ability to fetch server
catalogs from remote HTTP/HTTPS registries, search them, and install servers
into the local ``mcp_installed.yaml`` (kept separate from the user-edited
``mcp_servers.yaml`` so manual config is never overwritten).

Security model (aligned with project_memory.md Plugin manifest SHA-256 rule):

  - ``trusted: true`` registries may auto-install servers even without a
    checksum, although checksums are still verified when present.
  - ``trusted: false`` registries require either a valid SHA-256 checksum on
    the server entry OR an explicit ``confirm_untrusted=True`` opt-in from
    the caller. A missing checksum on an untrusted registry without opt-in
    raises ``ValueError`` and the install is refused.
  - All network requests use a 10-second timeout and the standard-library
    ``urllib`` (no new dependencies).

Usage::

    from maop.core.mcp.mcp_marketplace import MCPMarketplace

    mp = MCPMarketplace()
    servers = mp.fetch_catalog()
    results = mp.search("filesystem")
    cfg = mp.install("filesystem", confirm_untrusted=True)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from maop.core.mcp.mcp_hub import MCPServerConfig, TransportType

logger = logging.getLogger(__name__)


class MarketplaceServer(BaseModel):
    """Metadata for an MCP server advertised in a marketplace registry.

    Fields mirror the manifest constraints documented in project_memory.md
    (Plugin manifest must carry a SHA-256 checksum).
    """

    name: str
    description: str = ""
    version: str = ""
    author: str = ""
    homepage: str = ""
    download_url: str = ""
    transport_type: str = "stdio"  # stdio | sse | websocket
    default_config: dict[str, Any] = Field(default_factory=dict)
    checksum: str | None = None  # expected SHA-256 hex digest of download_url payload
    tags: list[str] = Field(default_factory=list)
    verified: bool = False
    install_count: int = 0


class MarketplaceRegistry(BaseModel):
    """Configuration for a single remote registry endpoint."""

    name: str
    url: str
    trusted: bool = False
    enabled: bool = True


class MarketplaceConfig(BaseModel):
    """Top-level marketplace configuration loaded from YAML."""

    registries: list[MarketplaceRegistry] = Field(default_factory=list)
    cache_ttl_s: int = 3600


class MCPMarketplace:
    """Discover and install MCP servers from remote registries.

    Parameters
    ----------
    config_path : Path | None
        Path to the marketplace config YAML. If ``None``, falls back to the
        package-shipped default at ``py/maop/config/mcp_marketplace.yaml``.
    cache_dir : Path | None
        Directory for caching fetched catalogs. If ``None``, a
        ``marketplace_cache`` directory is created next to the config file.
    """

    #: Package-shipped default config (example registries).
    DEFAULT_CONFIG_PATH: Path = (
        Path(__file__).resolve().parent.parent / "config" / "mcp_marketplace.yaml"
    )

    #: Network timeout for all registry HTTP requests (seconds).
    NETWORK_TIMEOUT_S: float = 10.0

    def __init__(
        self,
        config_path: Path | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self._config_path: Path = (
            Path(config_path) if config_path is not None else self.DEFAULT_CONFIG_PATH
        )
        if cache_dir is not None:
            self._cache_dir: Path = Path(cache_dir)
        else:
            self._cache_dir = self._config_path.parent.parent / "cache" / "marketplace"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._config: MarketplaceConfig = self._load_config()
        # Installed servers live next to the active config so writes stay local
        # to the project (never the package default unless explicitly pointed at).
        self._installed_path: Path = self._config_path.parent / "mcp_installed.yaml"

    # ── Config persistence ───────────────────────────────────────

    def _load_config(self) -> MarketplaceConfig:
        """Load marketplace config from YAML, returning empty config on failure."""
        if not self._config_path.exists():
            return MarketplaceConfig()
        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning(
                "[mcp_marketplace] Failed to load config %s: %s", self._config_path, exc
            )
            return MarketplaceConfig()

        if not isinstance(data, dict):
            return MarketplaceConfig()

        registries: list[MarketplaceRegistry] = []
        for r in data.get("registries", []) or []:
            if not isinstance(r, dict):
                continue
            try:
                registries.append(MarketplaceRegistry(
                    name=r.get("name", ""),
                    url=r.get("url", ""),
                    trusted=bool(r.get("trusted", False)),
                    enabled=bool(r.get("enabled", True)),
                ))
            except Exception as exc:
                logger.debug("[mcp_marketplace] Skipping malformed registry entry: %s", exc)
        return MarketplaceConfig(
            registries=registries,
            cache_ttl_s=int(data.get("cache_ttl_s", 3600)),
        )

    def _save_config(self) -> None:
        """Persist the current config back to YAML."""
        data = {
            "registries": [r.model_dump() for r in self._config.registries],
            "cache_ttl_s": self._config.cache_ttl_s,
        }
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
        except Exception as exc:
            logger.warning(
                "[mcp_marketplace] Failed to save config %s: %s", self._config_path, exc
            )

    # ── Registry management ──────────────────────────────────────

    def list_registries(self) -> list[MarketplaceRegistry]:
        """Return all configured registries (enabled and disabled)."""
        return list(self._config.registries)

    def add_registry(self, name: str, url: str, trusted: bool = False) -> None:
        """Add or replace a registry entry and persist the config."""
        self._config.registries = [
            r for r in self._config.registries if r.name != name
        ]
        self._config.registries.append(
            MarketplaceRegistry(name=name, url=url, trusted=trusted, enabled=True)
        )
        self._save_config()

    def remove_registry(self, name: str) -> None:
        """Remove a registry by name. No-op if the name is unknown."""
        before = len(self._config.registries)
        self._config.registries = [
            r for r in self._config.registries if r.name != name
        ]
        if len(self._config.registries) != before:
            self._save_config()

    # ── Network ──────────────────────────────────────────────────

    def _fetch_url(self, url: str) -> bytes:
        """Fetch raw bytes from a URL with the standard timeout.

        Security (C-2 fix): only ``http`` / ``https`` schemes are allowed
        and the resolved host must NOT be a private / loopback / link-local
        address — this prevents SSRF via crafted registry URLs pointing at
        internal services (e.g. ``http://169.254.169.254/...`` metadata
        endpoints, ``http://127.0.0.1:9079/...`` internal APIs, or
        ``file:///etc/passwd`` exfiltration).

        Raises ``ValueError`` for disallowed URLs, otherwise whatever
        ``urllib`` raises (``URLError`` / ``HTTPError`` / ``socket.timeout``)
        so callers can distinguish failure modes.
        """
        self._assert_safe_url(url)
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self.NETWORK_TIMEOUT_S) as resp:
            return resp.read()  # type: ignore[no-any-return]

    @staticmethod
    def _assert_safe_url(url: str) -> None:
        """Validate ``url`` against SSRF rules.

        Allowed schemes: ``http``, ``https``. The hostname (after DNS
        resolution) must NOT resolve to a private / loopback / link-local
        / multicast address. This is enforced by resolving the hostname
        and checking each returned address with :func:`ipaddress.ip_address`
        against :attr:`ipaddress.ip_address.is_private` /
        :attr:`is_loopback` / :attr:`is_link_local` /
        :attr:`is_multicast` / :attr:`is_reserved`.
        """
        import ipaddress
        import socket
        from urllib.parse import urlsplit

        try:
            parts = urlsplit(url)
        except Exception as exc:
            raise ValueError(f"Invalid URL '{url}': {exc}") from exc

        scheme = (parts.scheme or "").lower()
        if scheme not in ("http", "https"):
            raise ValueError(
                f"URL scheme '{scheme}' not allowed (only http/https permitted): {url}"
            )

        host = parts.hostname or ""
        if not host:
            raise ValueError(f"URL '{url}' has no hostname")

        # Literal IP — check directly without DNS lookup.
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                raise ValueError(
                    f"URL '{url}' points to a non-routable IP ({ip})"
                )
            return
        except ValueError:
            # Not a literal IP — fall through to DNS resolution.
            if "://" in host:
                raise

        # Hostname — resolve and check ALL returned addresses.
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise ValueError(f"Could not resolve host '{host}': {exc}") from exc

        for _family, _stype, _proto, _canon, sockaddr in infos:
            addr_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(addr_str)
            except ValueError:
                continue
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                raise ValueError(
                    f"URL '{url}' resolves to non-routable IP {ip} (host '{host}')"
                )

    def _fetch_registry_catalog(
        self, registry: MarketplaceRegistry
    ) -> list[MarketplaceServer]:
        """Fetch and parse one registry's catalog.

        Any network/parse failure logs a warning and returns an empty list
        so a single broken registry never aborts an aggregate fetch.
        """
        try:
            raw = self._fetch_url(registry.url)
            parsed = json.loads(raw.decode("utf-8"))
        except urllib.error.URLError as exc:
            logger.warning(
                "[mcp_marketplace] Registry '%s' unreachable: %s", registry.name, exc
            )
            return []
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning(
                "[mcp_marketplace] Registry '%s' returned invalid JSON: %s",
                registry.name, exc,
            )
            return []
        except Exception as exc:
            logger.warning(
                "[mcp_marketplace] Registry '%s' fetch error: %s", registry.name, exc
            )
            return []

        if isinstance(parsed, dict):
            servers_data = parsed.get("servers", []) or []
        elif isinstance(parsed, list):
            servers_data = parsed
        else:
            return []

        servers: list[MarketplaceServer] = []
        for item in servers_data:
            if not isinstance(item, dict):
                continue
            try:
                servers.append(MarketplaceServer(**item))
            except Exception as exc:
                logger.debug(
                    "[mcp_marketplace] Skipping malformed server entry in '%s': %s",
                    registry.name, exc,
                )
        return servers

    def fetch_catalog(
        self, registry_name: str | None = None
    ) -> list[MarketplaceServer]:
        """Fetch the server catalog from one or all enabled registries.

        Parameters
        ----------
        registry_name : str | None
            If given, fetch only from that registry (must be enabled).
            If ``None``, aggregate results from all enabled registries.

        Failed registries are skipped with a warning — they never abort the
        aggregate fetch.
        """
        if registry_name is not None:
            registries = [
                r for r in self._config.registries
                if r.name == registry_name and r.enabled
            ]
        else:
            registries = [r for r in self._config.registries if r.enabled]

        all_servers: list[MarketplaceServer] = []
        for reg in registries:
            all_servers.extend(self._fetch_registry_catalog(reg))
        return all_servers

    # ── Search ───────────────────────────────────────────────────

    def search(
        self,
        query: str,
        tags: list[str] | None = None,
    ) -> list[MarketplaceServer]:
        """Search the aggregated catalog by name/description/tags.

        A server matches if the query (case-insensitive) appears in its name
        or description, OR if any of the requested tags are present on the
        server (when ``tags`` is provided). When ``query`` is empty only tag
        matching is used.
        """
        query_lower = query.lower()
        tags_lower = {t.lower() for t in tags} if tags else set()
        results: list[MarketplaceServer] = []
        for srv in self.fetch_catalog():
            name_match = bool(query_lower) and query_lower in srv.name.lower()
            desc_match = bool(query_lower) and query_lower in srv.description.lower()
            tag_match = False
            if tags_lower:
                srv_tags_lower = {t.lower() for t in srv.tags}
                tag_match = bool(tags_lower & srv_tags_lower)
            if name_match or desc_match or (tags_lower and tag_match):
                results.append(srv)
        return results

    # ── Install / uninstall ──────────────────────────────────────

    def _find_in_catalog(
        self, name: str, registry_name: str | None = None
    ) -> tuple[MarketplaceServer, MarketplaceRegistry] | None:
        """Locate a server by name across enabled registries.

        Returns the first ``(server, registry)`` pair whose server name matches.
        """
        if registry_name is not None:
            registries = [
                r for r in self._config.registries
                if r.name == registry_name and r.enabled
            ]
        else:
            registries = [r for r in self._config.registries if r.enabled]

        for reg in registries:
            for srv in self._fetch_registry_catalog(reg):
                if srv.name == name:
                    return srv, reg
        return None

    def _load_installed(self) -> dict[str, dict[str, Any]]:
        """Load the installed-servers index from ``mcp_installed.yaml``."""
        if not self._installed_path.exists():
            return {}
        try:
            with open(self._installed_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning(
                "[mcp_marketplace] Failed to load installed index %s: %s",
                self._installed_path, exc,
            )
            return {}
        servers = data.get("servers", {}) if isinstance(data, dict) else {}
        if isinstance(servers, list):
            return {
                s.get("name", ""): s for s in servers if isinstance(s, dict) and s.get("name")
            }
        return servers if isinstance(servers, dict) else {}

    def _save_installed(self, installed: dict[str, dict[str, Any]]) -> None:
        """Persist the installed-servers index."""
        try:
            self._installed_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._installed_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    {"servers": installed}, f, default_flow_style=False, sort_keys=False
                )
        except Exception as exc:
            logger.warning(
                "[mcp_marketplace] Failed to save installed index %s: %s",
                self._installed_path, exc,
            )

    def _to_server_config(self, server: MarketplaceServer) -> MCPServerConfig:
        """Build an :class:`MCPServerConfig` from a marketplace server entry.

        ``default_config`` may carry ``command``, ``args``, ``env``, ``url``,
        ``headers`` — all valid ``MCPServerConfig`` fields. ``transport`` is
        derived from ``transport_type`` and overrides any value in
        ``default_config`` to keep the manifest authoritative.
        """
        cfg = dict(server.default_config)
        cfg.setdefault("name", server.name)
        try:
            transport = TransportType(server.transport_type)
        except ValueError:
            transport = TransportType.STDIO
        cfg["transport"] = transport
        return MCPServerConfig(**cfg)

    def install(
        self,
        name: str,
        registry_name: str | None = None,
        verify_checksum: bool = True,
        confirm_untrusted: bool = False,
    ) -> MCPServerConfig:
        """Install a server from the marketplace into ``mcp_installed.yaml``.

        Parameters
        ----------
        name : str
            Server name to install (must exist in a enabled registry).
        registry_name : str | None
            Restrict lookup to a specific registry. If ``None`` all enabled
            registries are searched in order.
        verify_checksum : bool
            When ``True`` (default) and the server carries a ``checksum``,
            the downloaded payload is verified via SHA-256. A mismatch raises
            ``ValueError``.
        confirm_untrusted : bool
            Opt-in flag required to install from an untrusted registry when
            the server has no checksum. Without this flag such installs are
            refused (security constraint).

        Returns
        -------
        MCPServerConfig
            The server config, ready to be passed to ``MCPHub.connect``.

        Raises
        ------
        ValueError
            If the server is not found, if an untrusted registry server has
            no checksum and ``confirm_untrusted`` is ``False``, or if a
            checksum verification fails.
        """
        found = self._find_in_catalog(name, registry_name)
        if found is None:
            raise ValueError(f"Server '{name}' not found in any enabled registry")
        server, registry = found

        # Security gate: untrusted + no checksum + no opt-in → refuse.
        if not registry.trusted and verify_checksum and server.checksum is None and not confirm_untrusted:
            raise ValueError(
                f"Server '{name}' is from untrusted registry '{registry.name}' "
                f"and has no SHA-256 checksum. Pass confirm_untrusted=True to "
                f"install anyway, or install from a trusted registry."
            )

        # Checksum verification (only when a checksum is present and requested).
        if verify_checksum and server.checksum is not None and server.download_url:
            try:
                payload = self._fetch_url(server.download_url)
            except Exception as exc:
                raise ValueError(
                    f"Failed to download '{name}' from {server.download_url}: {exc}"
                ) from exc
            actual = hashlib.sha256(payload).hexdigest()
            if actual.lower() != server.checksum.lower():
                raise ValueError(
                    f"Checksum mismatch for '{name}': expected {server.checksum}, "
                    f"got {actual}"
                )

        # Record in the installed index (never touches mcp_servers.yaml).
        installed = self._load_installed()
        installed[name] = {
            "name": server.name,
            "version": server.version,
            "registry": registry.name,
            "transport": server.transport_type,
            "config": server.default_config,
            "checksum": server.checksum,
            "installed_at": time.time(),
        }
        self._save_installed(installed)

        logger.info(
            "[mcp_marketplace] Installed '%s' v%s from registry '%s'",
            server.name, server.version, registry.name,
        )
        return self._to_server_config(server)

    def uninstall(self, name: str) -> bool:
        """Remove a server from the installed index.

        Returns
        -------
        bool
            ``True`` if the server was present and removed, ``False`` if it
            was not installed.
        """
        installed = self._load_installed()
        if name not in installed:
            return False
        del installed[name]
        self._save_installed(installed)
        logger.info("[mcp_marketplace] Uninstalled '%s'", name)
        return True

    def list_installed(self) -> list[dict[str, Any]]:
        """Return the list of installed marketplace server records."""
        installed = self._load_installed()
        return list(installed.values())
