"""Tests for maop.core.mcp_marketplace — remote registry discovery & install.

Phase δ-2: verifies the marketplace data models, registry management,
catalog fetching (with mocked HTTP), search, install/uninstall, and the
security constraints (untrusted registry + checksum rules).

No real network access — all HTTP calls are mocked via ``MCPMarketplace._fetch_url``.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from maop.core.mcp_hub import MCPServerConfig, TransportType
from maop.core.mcp_marketplace import (
    MCPMarketplace,
    MarketplaceConfig,
    MarketplaceRegistry,
    MarketplaceServer,
)


# ── Helpers ───────────────────────────────────────────────────


def _write_config(path: Path, registries: list[dict], cache_ttl_s: int = 3600) -> None:
    """Write a marketplace config YAML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"registries": registries, "cache_ttl_s": cache_ttl_s},
            default_flow_style=False,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _catalog_bytes(servers: list[dict]) -> bytes:
    """Build a JSON catalog response body."""
    return json.dumps({"servers": servers}).encode("utf-8")


def _make_fetch_side_effect(url_map: dict[str, bytes | Exception]):
    """Build a side-effect function for ``MCPMarketplace._fetch_url``.

    Each URL maps to either raw bytes (success) or an Exception (failure).
    Unknown URLs raise ``urllib.error.URLError``.
    """

    def _side_effect(url: str) -> bytes:
        if url not in url_map:
            raise urllib.error.URLError(f"unexpected URL {url}")
        value = url_map[url]
        if isinstance(value, Exception):
            raise value
        return value

    return _side_effect


# ── TestMarketplaceServer ─────────────────────────────────────


class TestMarketplaceServer:
    def test_defaults(self):
        s = MarketplaceServer(name="fs")
        assert s.name == "fs"
        assert s.description == ""
        assert s.version == ""
        assert s.author == ""
        assert s.homepage == ""
        assert s.download_url == ""
        assert s.transport_type == "stdio"
        assert s.default_config == {}
        assert s.checksum is None
        assert s.tags == []
        assert s.verified is False
        assert s.install_count == 0

    def test_full_fields(self):
        s = MarketplaceServer(
            name="github",
            description="GitHub MCP server",
            version="1.2.0",
            author="octocat",
            homepage="https://github.com/octocat/mcp-github",
            download_url="https://example.com/mcp-github-1.2.0.tar.gz",
            transport_type="sse",
            default_config={"url": "http://localhost:8080"},
            checksum="abc123",
            tags=["vcs", "github"],
            verified=True,
            install_count=42,
        )
        assert s.version == "1.2.0"
        assert s.transport_type == "sse"
        assert s.default_config == {"url": "http://localhost:8080"}
        assert s.checksum == "abc123"
        assert s.tags == ["vcs", "github"]
        assert s.verified is True
        assert s.install_count == 42

    def test_extra_fields_ignored(self):
        """Unknown fields in the manifest are ignored (forward-compat)."""
        s = MarketplaceServer(name="x", unknown_field="ignored")  # type: ignore[call-arg]
        assert s.name == "x"


# ── TestMarketplaceRegistry ───────────────────────────────────


class TestMarketplaceRegistry:
    def test_defaults(self):
        r = MarketplaceRegistry(name="official", url="https://example.com")
        assert r.name == "official"
        assert r.url == "https://example.com"
        assert r.trusted is False
        assert r.enabled is True

    def test_trusted_disabled(self):
        r = MarketplaceRegistry(
            name="legacy", url="https://old.example.com",
            trusted=True, enabled=False,
        )
        assert r.trusted is True
        assert r.enabled is False


class TestMarketplaceConfig:
    def test_defaults(self):
        c = MarketplaceConfig()
        assert c.registries == []
        assert c.cache_ttl_s == 3600


# ── TestMCPMarketplace ────────────────────────────────────────


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """Path to a marketplace config inside the tmp dir."""
    return tmp_path / "config" / "mcp_marketplace.yaml"


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "cache"


@pytest.fixture
def mp(config_path: Path, cache_dir: Path) -> MCPMarketplace:
    return MCPMarketplace(config_path=config_path, cache_dir=cache_dir)


# ── Config loading ────────────────────────────────────────────


class TestMCPMarketplaceConfig:
    def test_load_default_when_no_config_file(self, config_path: Path, cache_dir: Path):
        mp = MCPMarketplace(config_path=config_path, cache_dir=cache_dir)
        assert mp.list_registries() == []

    def test_load_custom_config(self, config_path: Path, cache_dir: Path):
        _write_config(config_path, [
            {"name": "official", "url": "https://r1.example.com", "trusted": True, "enabled": True},
            {"name": "community", "url": "https://r2.example.com", "trusted": False, "enabled": True},
        ], cache_ttl_s=600)
        mp = MCPMarketplace(config_path=config_path, cache_dir=cache_dir)
        regs = mp.list_registries()
        assert len(regs) == 2
        assert regs[0].name == "official"
        assert regs[0].trusted is True
        assert regs[1].name == "community"
        assert regs[1].trusted is False

    def test_load_malformed_yaml_returns_empty(self, config_path: Path, cache_dir: Path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("{ invalid: yaml: content:", encoding="utf-8")
        mp = MCPMarketplace(config_path=config_path, cache_dir=cache_dir)
        assert mp.list_registries() == []

    def test_load_non_dict_root_returns_empty(self, config_path: Path, cache_dir: Path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("- just\n- a\n- list", encoding="utf-8")
        mp = MCPMarketplace(config_path=config_path, cache_dir=cache_dir)
        assert mp.list_registries() == []

    def test_load_skips_malformed_registry_entries(self, config_path: Path, cache_dir: Path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(yaml.safe_dump({
            "registries": [
                {"name": "good", "url": "https://good.example.com", "trusted": True},
                "not-a-dict",
                {"url": "missing-name"},
            ],
        }), encoding="utf-8")
        mp = MCPMarketplace(config_path=config_path, cache_dir=cache_dir)
        regs = mp.list_registries()
        # "good" is kept; "not-a-dict" skipped; missing-name has name="" but is kept
        names = [r.name for r in regs]
        assert "good" in names
        assert "not-a-dict" not in names

    def test_creates_cache_dir(self, config_path: Path, tmp_path: Path):
        cache = tmp_path / "new_cache"
        assert not cache.exists()
        MCPMarketplace(config_path=config_path, cache_dir=cache)
        assert cache.exists()


# ── Registry management ───────────────────────────────────────


class TestMCPMarketplaceRegistries:
    def test_add_registry_persists(self, mp: MCPMarketplace, config_path: Path):
        mp.add_registry("official", "https://r.example.com", trusted=True)
        # Re-read from disk to verify persistence.
        mp2 = MCPMarketplace(config_path=config_path, cache_dir=mp._cache_dir)
        regs = mp2.list_registries()
        assert len(regs) == 1
        assert regs[0].name == "official"
        assert regs[0].url == "https://r.example.com"
        assert regs[0].trusted is True
        assert regs[0].enabled is True

    def test_add_registry_replaces_existing(self, mp: MCPMarketplace):
        mp.add_registry("official", "https://old.example.com")
        mp.add_registry("official", "https://new.example.com", trusted=True)
        regs = mp.list_registries()
        assert len(regs) == 1
        assert regs[0].url == "https://new.example.com"
        assert regs[0].trusted is True

    def test_add_registry_default_untrusted(self, mp: MCPMarketplace):
        mp.add_registry("community", "https://c.example.com")
        regs = mp.list_registries()
        assert regs[0].trusted is False

    def test_remove_registry(self, mp: MCPMarketplace):
        mp.add_registry("official", "https://r.example.com")
        mp.add_registry("community", "https://c.example.com")
        mp.remove_registry("official")
        regs = mp.list_registries()
        assert len(regs) == 1
        assert regs[0].name == "community"

    def test_remove_registry_noop_if_missing(self, mp: MCPMarketplace):
        mp.add_registry("official", "https://r.example.com")
        mp.remove_registry("nonexistent")
        assert len(mp.list_registries()) == 1

    def test_remove_registry_persists(self, mp: MCPMarketplace, config_path: Path):
        mp.add_registry("official", "https://r.example.com")
        mp.remove_registry("official")
        mp2 = MCPMarketplace(config_path=config_path, cache_dir=mp._cache_dir)
        assert mp2.list_registries() == []


# ── fetch_catalog ─────────────────────────────────────────────


class TestMCPMarketplaceFetchCatalog:
    def test_fetch_single_registry_success(self, mp: MCPMarketplace):
        mp.add_registry("official", "https://r.example.com")
        catalog = _catalog_bytes([
            {"name": "fs", "version": "1.0.0", "description": "Filesystem"},
            {"name": "git", "version": "2.0.0", "tags": ["vcs"]},
        ])
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": catalog,
            }),
        ):
            servers = mp.fetch_catalog()
        assert len(servers) == 2
        assert servers[0].name == "fs"
        assert servers[1].tags == ["vcs"]

    def test_fetch_aggregates_all_enabled_registries(self, mp: MCPMarketplace):
        mp.add_registry("r1", "https://r1.example.com")
        mp.add_registry("r2", "https://r2.example.com")
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r1.example.com": _catalog_bytes([{"name": "a"}]),
                "https://r2.example.com": _catalog_bytes([{"name": "b"}, {"name": "c"}]),
            }),
        ):
            servers = mp.fetch_catalog()
        names = [s.name for s in servers]
        assert names == ["a", "b", "c"]

    def test_fetch_by_name_filters_to_single_registry(self, mp: MCPMarketplace):
        mp.add_registry("r1", "https://r1.example.com")
        mp.add_registry("r2", "https://r2.example.com")
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r1.example.com": _catalog_bytes([{"name": "a"}]),
                "https://r2.example.com": _catalog_bytes([{"name": "b"}]),
            }),
        ):
            servers = mp.fetch_catalog(registry_name="r2")
        assert len(servers) == 1
        assert servers[0].name == "b"

    def test_fetch_skips_disabled_registries(self, mp: MCPMarketplace, config_path: Path):
        _write_config(config_path, [
            {"name": "enabled-r", "url": "https://e.example.com", "enabled": True},
            {"name": "disabled-r", "url": "https://d.example.com", "enabled": False},
        ])
        mp2 = MCPMarketplace(config_path=config_path, cache_dir=mp._cache_dir)
        with patch.object(
            mp2, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://e.example.com": _catalog_bytes([{"name": "a"}]),
            }),
        ):
            servers = mp2.fetch_catalog()
        assert len(servers) == 1

    def test_fetch_unknown_registry_returns_empty(self, mp: MCPMarketplace):
        mp.add_registry("r1", "https://r1.example.com")
        with patch.object(mp, "_fetch_url") as mock_fetch:
            servers = mp.fetch_catalog(registry_name="nonexistent")
        assert servers == []
        mock_fetch.assert_not_called()

    def test_fetch_failure_skips_registry(self, mp: MCPMarketplace):
        """A network failure on one registry is logged and skipped."""
        mp.add_registry("bad", "https://bad.example.com")
        mp.add_registry("good", "https://good.example.com")
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://bad.example.com": urllib.error.URLError("timeout"),
                "https://good.example.com": _catalog_bytes([{"name": "ok"}]),
            }),
        ):
            servers = mp.fetch_catalog()
        assert len(servers) == 1
        assert servers[0].name == "ok"

    def test_fetch_invalid_json_skips_registry(self, mp: MCPMarketplace):
        mp.add_registry("r1", "https://r.example.com")
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": b"not json",
            }),
        ):
            servers = mp.fetch_catalog()
        assert servers == []

    def test_fetch_bare_list_catalog(self, mp: MCPMarketplace):
        """A catalog that is a bare JSON list (not wrapped in {servers:...})."""
        mp.add_registry("r1", "https://r.example.com")
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": json.dumps([{"name": "a"}, {"name": "b"}]).encode(),
            }),
        ):
            servers = mp.fetch_catalog()
        assert len(servers) == 2

    def test_fetch_skips_malformed_server_entries(self, mp: MCPMarketplace):
        mp.add_registry("r1", "https://r.example.com")
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": _catalog_bytes([
                    {"name": "good"},
                    "not-a-dict",
                    {"name": "also-good"},
                ]),
            }),
        ):
            servers = mp.fetch_catalog()
        assert len(servers) == 2
        assert {s.name for s in servers} == {"good", "also-good"}


# ── search ────────────────────────────────────────────────────


class TestMCPMarketplaceSearch:
    @pytest.fixture
    def mp_with_catalog(self, mp: MCPMarketplace) -> MCPMarketplace:
        mp.add_registry("r1", "https://r.example.com")
        catalog = _catalog_bytes([
            {"name": "filesystem", "description": "File system access", "tags": ["fs", "io"]},
            {"name": "github", "description": "GitHub integration", "tags": ["vcs"]},
            {"name": "gitlab", "description": "GitLab source control", "tags": ["vcs"]},
        ])
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": catalog,
            }),
        ):
            yield mp

    def test_search_by_name(self, mp_with_catalog: MCPMarketplace):
        results = mp_with_catalog.search("file")
        assert len(results) == 1
        assert results[0].name == "filesystem"

    def test_search_by_description(self, mp_with_catalog: MCPMarketplace):
        results = mp_with_catalog.search("integration")
        assert len(results) == 1
        assert results[0].name == "github"

    def test_search_case_insensitive(self, mp_with_catalog: MCPMarketplace):
        results = mp_with_catalog.search("GITHUB")
        assert len(results) == 1
        assert results[0].name == "github"

    def test_search_by_tag(self, mp_with_catalog: MCPMarketplace):
        results = mp_with_catalog.search("", tags=["vcs"])
        names = {r.name for r in results}
        assert names == {"github", "gitlab"}

    def test_search_by_query_and_tags(self, mp_with_catalog: MCPMarketplace):
        """Query OR tags — a server matching either is returned."""
        results = mp_with_catalog.search("file", tags=["vcs"])
        names = {r.name for r in results}
        assert "filesystem" in names
        assert "github" in names

    def test_search_no_results(self, mp_with_catalog: MCPMarketplace):
        results = mp_with_catalog.search("nonexistent-query")
        assert results == []

    def test_search_empty_query_no_tags_returns_empty(self, mp_with_catalog: MCPMarketplace):
        results = mp_with_catalog.search("")
        assert results == []


# ── install / uninstall / list_installed ──────────────────────


class TestMCPMarketplaceInstall:
    def test_install_trusted_no_checksum(self, mp: MCPMarketplace):
        mp.add_registry("official", "https://r.example.com", trusted=True)
        catalog = _catalog_bytes([{
            "name": "fs",
            "version": "1.0.0",
            "transport_type": "stdio",
            "default_config": {"command": "npx", "args": ["-y", "fs-server"]},
        }])
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": catalog,
            }),
        ):
            cfg = mp.install("fs")
        assert isinstance(cfg, MCPServerConfig)
        assert cfg.name == "fs"
        assert cfg.transport == TransportType.STDIO
        assert cfg.command == "npx"
        assert cfg.args == ["-y", "fs-server"]
        # Verify written to mcp_installed.yaml
        installed = mp.list_installed()
        assert len(installed) == 1
        assert installed[0]["name"] == "fs"
        assert installed[0]["registry"] == "official"

    def test_install_writes_separate_from_mcp_servers(self, mp: MCPMarketplace):
        """Installed servers go to mcp_installed.yaml, never mcp_servers.yaml."""
        mp.add_registry("official", "https://r.example.com", trusted=True)
        catalog = _catalog_bytes([{"name": "fs", "default_config": {"command": "x"}}])
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": catalog,
            }),
        ):
            mp.install("fs")
        # mcp_servers.yaml should not exist (we never write to it).
        mcp_servers_path = mp._config_path.parent / "mcp_servers.yaml"
        assert not mcp_servers_path.exists()
        # mcp_installed.yaml should exist.
        assert mp._installed_path.exists()

    def test_install_with_valid_checksum(self, mp: MCPMarketplace):
        mp.add_registry("official", "https://r.example.com", trusted=True)
        payload = b"server-binary-content"
        checksum = hashlib.sha256(payload).hexdigest()
        catalog = _catalog_bytes([{
            "name": "fs",
            "version": "1.0.0",
            "download_url": "https://r.example.com/download/fs.tar.gz",
            "checksum": checksum,
            "default_config": {"command": "fs"},
        }])
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": catalog,
                "https://r.example.com/download/fs.tar.gz": payload,
            }),
        ):
            cfg = mp.install("fs", verify_checksum=True)
        assert cfg.name == "fs"

    def test_install_with_specific_registry(self, mp: MCPMarketplace):
        mp.add_registry("r1", "https://r1.example.com", trusted=True)
        mp.add_registry("r2", "https://r2.example.com", trusted=True)
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r1.example.com": _catalog_bytes([{"name": "a"}]),
                "https://r2.example.com": _catalog_bytes([{"name": "b"}]),
            }),
        ):
            cfg = mp.install("b", registry_name="r2")
        assert cfg.name == "b"

    def test_install_not_found_raises(self, mp: MCPMarketplace):
        mp.add_registry("official", "https://r.example.com", trusted=True)
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": _catalog_bytes([{"name": "other"}]),
            }),
        ):
            with pytest.raises(ValueError, match="not found"):
                mp.install("nonexistent")

    def test_install_transport_type_sse(self, mp: MCPMarketplace):
        mp.add_registry("official", "https://r.example.com", trusted=True)
        catalog = _catalog_bytes([{
            "name": "remote",
            "transport_type": "sse",
            "default_config": {"url": "http://localhost:8080/sse"},
        }])
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": catalog,
            }),
        ):
            cfg = mp.install("remote")
        assert cfg.transport == TransportType.SSE
        assert cfg.url == "http://localhost:8080/sse"

    def test_install_invalid_transport_falls_back_to_stdio(self, mp: MCPMarketplace):
        mp.add_registry("official", "https://r.example.com", trusted=True)
        catalog = _catalog_bytes([{
            "name": "bad",
            "transport_type": "invalid-transport",
            "default_config": {"command": "echo"},
        }])
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": catalog,
            }),
        ):
            cfg = mp.install("bad")
        assert cfg.transport == TransportType.STDIO

    def test_install_overwrites_existing(self, mp: MCPMarketplace):
        """Re-installing a server overwrites the previous record."""
        mp.add_registry("official", "https://r.example.com", trusted=True)
        v1 = _catalog_bytes([{"name": "fs", "version": "1.0.0", "default_config": {"command": "v1"}}])
        v2 = _catalog_bytes([{"name": "fs", "version": "2.0.0", "default_config": {"command": "v2"}}])
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": v1,
            }),
        ):
            mp.install("fs")
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": v2,
            }),
        ):
            mp.install("fs")
        installed = mp.list_installed()
        assert len(installed) == 1
        assert installed[0]["version"] == "2.0.0"

    def test_uninstall_removes_server(self, mp: MCPMarketplace):
        mp.add_registry("official", "https://r.example.com", trusted=True)
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": _catalog_bytes([{"name": "fs"}]),
            }),
        ):
            mp.install("fs")
        assert mp.uninstall("fs") is True
        assert mp.list_installed() == []

    def test_uninstall_not_installed_returns_false(self, mp: MCPMarketplace):
        assert mp.uninstall("nonexistent") is False

    def test_list_installed_empty(self, mp: MCPMarketplace):
        assert mp.list_installed() == []

    def test_list_installed_after_install(self, mp: MCPMarketplace):
        mp.add_registry("official", "https://r.example.com", trusted=True)
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": _catalog_bytes([{
                    "name": "fs",
                    "version": "1.0.0",
                    "default_config": {"command": "x"},
                }]),
            }),
        ):
            mp.install("fs")
        installed = mp.list_installed()
        assert len(installed) == 1
        record = installed[0]
        assert record["name"] == "fs"
        assert record["version"] == "1.0.0"
        assert record["registry"] == "official"
        assert "installed_at" in record


# ── Security ──────────────────────────────────────────────────


class TestMarketplaceSecurity:
    def test_untrusted_no_checksum_rejected_by_default(self, mp: MCPMarketplace):
        mp.add_registry("community", "https://c.example.com", trusted=False)
        catalog = _catalog_bytes([{
            "name": "risky",
            "version": "1.0.0",
            # no checksum field
            "default_config": {"command": "rm"},
        }])
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://c.example.com": catalog,
            }),
        ):
            with pytest.raises(ValueError, match="untrusted"):
                mp.install("risky")
        # Nothing should be written.
        assert mp.list_installed() == []

    def test_untrusted_no_checksum_allowed_with_confirm(self, mp: MCPMarketplace):
        mp.add_registry("community", "https://c.example.com", trusted=False)
        catalog = _catalog_bytes([{
            "name": "risky",
            "version": "1.0.0",
            "default_config": {"command": "echo"},
        }])
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://c.example.com": catalog,
            }),
        ):
            cfg = mp.install("risky", confirm_untrusted=True)
        assert cfg.name == "risky"
        assert len(mp.list_installed()) == 1

    def test_untrusted_with_checksum_allowed(self, mp: MCPMarketplace):
        """Untrusted registry + valid checksum → install succeeds (checksum proves integrity)."""
        mp.add_registry("community", "https://c.example.com", trusted=False)
        payload = b"safe-content"
        checksum = hashlib.sha256(payload).hexdigest()
        catalog = _catalog_bytes([{
            "name": "verified",
            "version": "1.0.0",
            "download_url": "https://c.example.com/d/verified.tar.gz",
            "checksum": checksum,
            "default_config": {"command": "verified"},
        }])
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://c.example.com": catalog,
                "https://c.example.com/d/verified.tar.gz": payload,
            }),
        ):
            cfg = mp.install("verified")
        assert cfg.name == "verified"

    def test_checksum_mismatch_rejected(self, mp: MCPMarketplace):
        mp.add_registry("official", "https://r.example.com", trusted=True)
        payload = b"actual-content"
        wrong_checksum = hashlib.sha256(b"different-content").hexdigest()
        catalog = _catalog_bytes([{
            "name": "tampered",
            "version": "1.0.0",
            "download_url": "https://r.example.com/d/t.tar.gz",
            "checksum": wrong_checksum,
            "default_config": {"command": "x"},
        }])
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": catalog,
                "https://r.example.com/d/t.tar.gz": payload,
            }),
        ):
            with pytest.raises(ValueError, match="Checksum mismatch"):
                mp.install("tampered")
        assert mp.list_installed() == []

    def test_checksum_case_insensitive(self, mp: MCPMarketplace):
        """Checksum comparison is case-insensitive (hex digests)."""
        mp.add_registry("official", "https://r.example.com", trusted=True)
        payload = b"content"
        checksum = hashlib.sha256(payload).hexdigest().upper()
        catalog = _catalog_bytes([{
            "name": "ok",
            "download_url": "https://r.example.com/d/ok.tar.gz",
            "checksum": checksum,
            "default_config": {"command": "x"},
        }])
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": catalog,
                "https://r.example.com/d/ok.tar.gz": payload,
            }),
        ):
            cfg = mp.install("ok")
        assert cfg.name == "ok"

    def test_no_verify_skips_checksum_check(self, mp: MCPMarketplace):
        """verify_checksum=False skips both the checksum fetch and the security gate."""
        mp.add_registry("community", "https://c.example.com", trusted=False)
        catalog = _catalog_bytes([{
            "name": "unverified",
            "default_config": {"command": "x"},
        }])
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://c.example.com": catalog,
            }),
        ):
            cfg = mp.install("unverified", verify_checksum=False)
        assert cfg.name == "unverified"
        assert len(mp.list_installed()) == 1

    def test_download_failure_raises(self, mp: MCPMarketplace):
        mp.add_registry("official", "https://r.example.com", trusted=True)
        payload = b"x"
        checksum = hashlib.sha256(payload).hexdigest()
        catalog = _catalog_bytes([{
            "name": "dl-fail",
            "download_url": "https://r.example.com/d/fail.tar.gz",
            "checksum": checksum,
            "default_config": {"command": "x"},
        }])
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": catalog,
                "https://r.example.com/d/fail.tar.gz": urllib.error.URLError("net error"),
            }),
        ):
            with pytest.raises(ValueError, match="Failed to download"):
                mp.install("dl-fail")
        assert mp.list_installed() == []

    def test_network_timeout_skips_registry(self, mp: MCPMarketplace):
        """A URLError (e.g. timeout) on one registry doesn't abort the aggregate fetch."""
        import socket
        mp.add_registry("slow", "https://slow.example.com")
        mp.add_registry("fast", "https://fast.example.com", trusted=True)
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://slow.example.com": socket.timeout("timed out"),
                "https://fast.example.com": _catalog_bytes([{"name": "ok"}]),
            }),
        ):
            servers = mp.fetch_catalog()
        assert len(servers) == 1
        assert servers[0].name == "ok"

    def test_installed_path_separate_from_user_config(self, mp: MCPMarketplace, config_path: Path):
        """mcp_installed.yaml is a sibling of mcp_marketplace.yaml, never mcp_servers.yaml."""
        mp.add_registry("official", "https://r.example.com", trusted=True)
        with patch.object(
            mp, "_fetch_url", side_effect=_make_fetch_side_effect({
                "https://r.example.com": _catalog_bytes([{"name": "fs"}]),
            }),
        ):
            mp.install("fs")
        assert mp._installed_path == config_path.parent / "mcp_installed.yaml"
        assert mp._installed_path.exists()


# ── Regression tests for security Critical fixes (C-2 SSRF) ──

import pytest
from maop.core.mcp_marketplace import MCPMarketplace


class TestSSRFProtection:
    """C-2: _fetch_url must reject non-http(s) schemes and private IPs.

    Without this check, a malicious registry URL like
    ``http://169.254.169.254/latest/meta-data/`` (cloud metadata) or
    ``file:///etc/passwd`` could be fetched server-side.
    """

    def test_rejects_file_scheme(self):
        with pytest.raises(ValueError, match="scheme"):
            MCPMarketplace._assert_safe_url("file:///etc/passwd")

    def test_rejects_ftp_scheme(self):
        with pytest.raises(ValueError, match="scheme"):
            MCPMarketplace._assert_safe_url("ftp://example.com/file")

    def test_rejects_empty_scheme(self):
        with pytest.raises(ValueError, match="scheme"):
            MCPMarketplace._assert_safe_url("//example.com/file")

    def test_rejects_loopback_ipv4(self):
        with pytest.raises(ValueError, match="non-routable"):
            MCPMarketplace._assert_safe_url("http://127.0.0.1:9079/api/admin")

    def test_rejects_loopback_ipv6(self):
        with pytest.raises(ValueError, match="non-routable"):
            MCPMarketplace._assert_safe_url("http://[::1]:9079/api/admin")

    def test_rejects_private_ip_10(self):
        with pytest.raises(ValueError, match="non-routable"):
            MCPMarketplace._assert_safe_url("http://10.0.0.1/internal")

    def test_rejects_private_ip_192_168(self):
        with pytest.raises(ValueError, match="non-routable"):
            MCPMarketplace._assert_safe_url("http://192.168.1.1/router")

    def test_rejects_link_local_metadata(self):
        """Cloud metadata endpoint must be blocked (AWS/Azure/GCP)."""
        with pytest.raises(ValueError, match="non-routable"):
            MCPMarketplace._assert_safe_url("http://169.254.169.254/latest/meta-data/")

    def test_accepts_public_https(self):
        # Should NOT raise — public HTTPS is allowed.
        # Use ``example.com`` (IANA reserved, has real DNS A records) so
        # the test works in environments with internet access. Skip
        # gracefully when offline — the SSRF *rejection* tests above
        # don't need DNS and always run.
        import socket
        try:
            socket.getaddrinfo("example.com", None)
        except socket.gaierror:
            pytest.skip("offline: cannot resolve example.com")
        MCPMarketplace._assert_safe_url("https://example.com/catalog.json")

    def test_accepts_public_http(self):
        import socket
        try:
            socket.getaddrinfo("example.com", None)
        except socket.gaierror:
            pytest.skip("offline: cannot resolve example.com")
        MCPMarketplace._assert_safe_url("http://example.com/catalog.json")

    def test_rejects_no_hostname(self):
        with pytest.raises(ValueError, match="hostname"):
            MCPMarketplace._assert_safe_url("http:///path")
