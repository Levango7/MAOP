"""Tests for maop.core.mcp.tool_signing, tool_discovery, and enhanced health check.

F2-03: verifies Ed25519 tool signing/verification, local+remote tool discovery
with signature checks, and the parallelized health_check_all with timeout /
exception isolation.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from maop.core.mcp.tool_discovery import (
    DiscoveredTool,

    DiscoverySource,
    ToolDiscovery,
)
from maop.core.mcp.tool_signing import (
    ToolSignatureError,
    ToolSigner,
    canonical_bytes,
    generate_keypair,
    sign_bytes,
    verify_bytes,
)


# ── TestToolSigning ───────────────────────────────────────────


class TestToolSigningLowLevel:
    def test_generate_keypair_returns_pem_strings(self):
        priv, pub = generate_keypair()
        assert isinstance(priv, str)
        assert isinstance(pub, str)
        assert "PRIVATE KEY" in priv
        assert "PUBLIC KEY" in pub

    def test_sign_and_verify_bytes(self):
        priv, pub = generate_keypair()
        data = b"hello world"
        sig = sign_bytes(data, priv)
        assert len(sig) == 64
        assert verify_bytes(data, sig, pub) is True

    def test_verify_tampered_data_fails(self):
        priv, pub = generate_keypair()
        sig = sign_bytes(b"original", priv)
        assert verify_bytes(b"tampered", sig, pub) is False

    def test_verify_tampered_signature_fails(self):
        priv, pub = generate_keypair()
        sig = sign_bytes(b"data", priv)
        bad_sig = bytes([sig[0] ^ 1]) + sig[1:]
        assert verify_bytes(b"data", bad_sig, pub) is False

    def test_canonical_bytes_deterministic(self):
        d1 = {"b": 2, "a": 1, "c": {"z": 3, "y": 2}}
        d2 = {"c": {"y": 2, "z": 3}, "a": 1, "b": 2}
        assert canonical_bytes(d1) == canonical_bytes(d2)

    def test_canonical_bytes_compact(self):
        data = canonical_bytes({"a": 1, "b": 2})
        assert data == b'{"a":1,"b":2}'


class TestToolSignerManifest:
    def test_sign_and_verify_manifest(self):
        signer = ToolSigner()
        priv, pub = signer.generate_keypair()
        manifest = {"name": "filesystem", "version": "1.0", "command": "npx fs"}
        signed = signer.sign_manifest(manifest, priv)
        assert "signature" in signed
        assert signed["signing_algorithm"] == "ed25519"
        assert signed["signing_key_id"] == "default"
        assert signer.verify_manifest(signed, pub) is True

    def test_verify_tampered_manifest_fails(self):
        signer = ToolSigner()
        priv, pub = signer.generate_keypair()
        signed = signer.sign_manifest({"name": "fs", "version": "1.0"}, priv)
        signed["version"] = "2.0"  # tamper
        assert signer.verify_manifest(signed, pub) is False

    def test_resigning_produces_consistent_signature(self):
        """Signing the same manifest twice yields the same signature (Ed25519 is deterministic)."""
        signer = ToolSigner()
        priv, _ = signer.generate_keypair()
        manifest = {"name": "fs", "version": "1.0"}
        s1 = signer.sign_manifest(manifest, priv)
        s2 = signer.sign_manifest(manifest, priv)
        assert s1["signature"] == s2["signature"]

    def test_signing_already_signed_manifest_replaces_signature(self):
        signer = ToolSigner()
        priv, _ = signer.generate_keypair()
        manifest = {"name": "fs", "version": "1.0"}
        signed = signer.sign_manifest(manifest, priv)
        # Re-signing should not sign the old signature field.
        resigned = signer.sign_manifest(signed, priv)
        assert resigned["signature"] == signed["signature"]

    def test_verify_missing_signature_raises(self):
        signer = ToolSigner()
        _, pub = signer.generate_keypair()
        with pytest.raises(ToolSignatureError, match="no 'signature'"):
            signer.verify_manifest({"name": "fs", "signing_algorithm": "ed25519"}, pub)

    def test_verify_wrong_algorithm_raises(self):
        signer = ToolSigner()
        _, pub = signer.generate_keypair()
        with pytest.raises(ToolSignatureError, match="Unsupported"):
            signer.verify_manifest(
                {"name": "fs", "signing_algorithm": "rsa", "signature": "00"}, pub
            )

    def test_verify_bad_hex_raises(self):
        signer = ToolSigner()
        _, pub = signer.generate_keypair()
        with pytest.raises(ToolSignatureError, match="not valid hex"):
            signer.verify_manifest(
                {"name": "fs", "signing_algorithm": "ed25519", "signature": "xyz"},
                pub,
            )

    def test_verify_wrong_length_raises(self):
        signer = ToolSigner()
        _, pub = signer.generate_keypair()
        with pytest.raises(ToolSignatureError, match="64 bytes"):
            signer.verify_manifest(
                {"name": "fs", "signing_algorithm": "ed25519", "signature": "00"},
                pub,
            )

    def test_custom_key_id(self):
        signer = ToolSigner(key_id="prod-key")
        priv, pub = signer.generate_keypair()
        signed = signer.sign_manifest({"name": "fs"}, priv)
        assert signed["signing_key_id"] == "prod-key"
        assert signer.verify_manifest(signed, pub) is True

    def test_key_id_property(self):
        signer = ToolSigner(key_id="my-key")
        assert signer.key_id == "my-key"


# ── TestToolDiscovery ────────────────────────────────────────


def _write_local_config(path: Path, servers: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"servers": servers}, default_flow_style=False),
        encoding="utf-8",
    )


def _catalog_bytes(tools: list[dict]) -> bytes:
    return json.dumps({"tools": tools}).encode("utf-8")


class TestToolDiscoveryLocal:
    def test_discover_local_from_config(self, tmp_path):
        config_path = tmp_path / "config" / "mcp_servers.yaml"
        _write_local_config(config_path, [
            {"name": "filesystem", "description": "fs tool", "transport": "stdio"},
            {"name": "git", "description": "git tool"},
        ])
        td = ToolDiscovery(root_dir=tmp_path)
        tools, report = td.discover_local()
        assert report.local_count == 2
        assert all(t.source == DiscoverySource.LOCAL for t in tools)
        assert all(t.verified for t in tools)  # local → trusted
        names = {t.name for t in tools}
        assert names == {"filesystem", "git"}

    def test_discover_local_missing_file(self, tmp_path):
        td = ToolDiscovery(root_dir=tmp_path)
        tools, report = td.discover_local()
        assert tools == []
        assert report.local_count == 0

    def test_discover_local_invalid_yaml(self, tmp_path):
        config_path = tmp_path / "config" / "mcp_servers.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(": not valid yaml: [", encoding="utf-8")
        td = ToolDiscovery(root_dir=tmp_path)
        tools, report = td.discover_local()
        assert tools == []
        assert len(report.errors) > 0

    def test_discover_local_skips_unnamed(self, tmp_path):
        config_path = tmp_path / "config" / "mcp_servers.yaml"
        _write_local_config(config_path, [
            {"name": "valid"},
            {"description": "no name"},
        ])
        td = ToolDiscovery(root_dir=tmp_path)
        tools, _ = td.discover_local()
        assert len(tools) == 1
        assert tools[0].name == "valid"


class TestToolDiscoveryRemote:
    def test_discover_remote_verified(self, tmp_path):
        signer = ToolSigner()
        priv, pub = signer.generate_keypair()
        manifest = {"name": "remote-tool", "version": "1.0", "publisher": "acme"}
        signed = signer.sign_manifest(manifest, priv)

        td = ToolDiscovery(
            root_dir=tmp_path,
            signer=signer,
            trusted_public_keys={"acme": pub},
        )
        catalog = _catalog_bytes([signed])

        with patch.object(td, "_fetch_url", return_value=catalog):
            tools, report = td.discover_remote(["https://registry.example.com/cat.json"])

        assert report.remote_count == 1
        assert tools[0].verified is True
        assert tools[0].source == DiscoverySource.REMOTE
        assert tools[0].publisher == "acme"

    def test_discover_remote_unverified_no_key(self, tmp_path):
        """Remote tool from publisher with no trusted key → unverified."""
        td = ToolDiscovery(root_dir=tmp_path)
        catalog = _catalog_bytes([{"name": "anon-tool", "publisher": "unknown"}])
        with patch.object(td, "_fetch_url", return_value=catalog):
            tools, _ = td.discover_remote(["https://reg.example.com/cat.json"])
        assert len(tools) == 1
        assert tools[0].verified is False

    def test_discover_remote_tampered_signature(self, tmp_path):
        signer = ToolSigner()
        priv, pub = signer.generate_keypair()
        signed = signer.sign_manifest(
            {"name": "tool", "version": "1.0", "publisher": "acme"}, priv
        )
        signed["version"] = "9.9"  # tamper after signing

        td = ToolDiscovery(
            root_dir=tmp_path,
            signer=signer,
            trusted_public_keys={"acme": pub},
        )
        catalog = _catalog_bytes([signed])
        with patch.object(td, "_fetch_url", return_value=catalog):
            tools, _ = td.discover_remote(["https://reg.example.com/cat.json"])
        assert len(tools) == 1
        assert tools[0].verified is False

    def test_discover_remote_fetch_error(self, tmp_path):
        td = ToolDiscovery(root_dir=tmp_path)
        with patch.object(td, "_fetch_url", side_effect=urllib.error.URLError("fail")):
            tools, report = td.discover_remote(["https://bad.example.com/cat.json"])
        assert tools == []
        assert len(report.errors) > 0

    def test_discover_remote_no_signature_with_key(self, tmp_path):
        """Publisher has a trusted key but tool has no signature → unverified."""
        signer = ToolSigner()
        _, pub = signer.generate_keypair()
        td = ToolDiscovery(
            root_dir=tmp_path,
            signer=signer,
            trusted_public_keys={"acme": pub},
        )
        catalog = _catalog_bytes([{"name": "unsigned", "publisher": "acme"}])
        with patch.object(td, "_fetch_url", return_value=catalog):
            tools, _ = td.discover_remote(["https://reg.example.com/cat.json"])
        assert len(tools) == 1
        assert tools[0].verified is False


class TestToolDiscoveryAll:
    def test_discover_all_merges_local_and_remote(self, tmp_path):
        # Local config
        config_path = tmp_path / "config" / "mcp_servers.yaml"
        _write_local_config(config_path, [{"name": "local-tool"}])

        # Remote catalog
        td = ToolDiscovery(root_dir=tmp_path)
        catalog = _catalog_bytes([{"name": "remote-tool", "publisher": "x"}])
        with patch.object(td, "_fetch_url", return_value=catalog):
            tools, report = td.discover_all(
                registries=["https://reg.example.com/cat.json"]
            )
        names = {t.name for t in tools}
        assert names == {"local-tool", "remote-tool"}
        assert report.local_count == 1
        assert report.remote_count == 1

    def test_discover_all_dedup_local_wins(self, tmp_path):
        config_path = tmp_path / "config" / "mcp_servers.yaml"
        _write_local_config(config_path, [{"name": "shared"}])

        td = ToolDiscovery(root_dir=tmp_path)
        catalog = _catalog_bytes([{"name": "shared", "publisher": "remote"}])
        with patch.object(td, "_fetch_url", return_value=catalog):
            tools, _ = td.discover_all(
                registries=["https://reg.example.com/cat.json"]
            )
        assert len(tools) == 1
        assert tools[0].source == DiscoverySource.LOCAL  # local wins
        assert tools[0].verified is True

    def test_discover_all_no_registries(self, tmp_path):
        config_path = tmp_path / "config" / "mcp_servers.yaml"
        _write_local_config(config_path, [{"name": "local-only"}])
        td = ToolDiscovery(root_dir=tmp_path)
        tools, report = td.discover_all()
        assert len(tools) == 1
        assert report.remote_count == 0

    def test_discover_all_verified_counts(self, tmp_path):
        config_path = tmp_path / "config" / "mcp_servers.yaml"
        _write_local_config(config_path, [{"name": "local1"}, {"name": "local2"}])
        td = ToolDiscovery(root_dir=tmp_path)
        tools, report = td.discover_all()
        assert report.verified_count == 2
        assert report.unverified_count == 0


class TestDiscoveredToolModel:
    def test_defaults(self):
        t = DiscoveredTool(name="x")
        assert t.source == DiscoverySource.LOCAL
        assert t.verified is False
        assert t.manifest == {}

    def test_with_fields(self):
        t = DiscoveredTool(
            name="x",
            source=DiscoverySource.REMOTE,
            verified=True,
            signature="abc",
            tags=["ai", "vision"],
        )
        assert t.source == DiscoverySource.REMOTE
        assert t.tags == ["ai", "vision"]


# ── TestEnhancedHealthCheck ───────────────────────────────────


class TestEnhancedHealthCheckAll:
    """Test the F2-03 enhanced parallel health_check_all with timeout
    and exception isolation."""

    async def test_timeout_marks_unhealthy(self, tmp_path):
        from maop.core.mcp.mcp_hub import MCPHub

        hub = MCPHub(root_dir=tmp_path)
        # Inject a fake transport + config so health_check has something to ping.
        # We make health_check slow by patching it to sleep.

        # Register a fake server by injecting into internal dicts.
        from maop.core.mcp.mcp_hub_types import MCPServerConfig, TransportType

        config = MCPServerConfig(name="slow-server", transport=TransportType.STDIO)
        hub._transports["slow"] = None  # type: ignore[assignment]
        hub._configs["slow"] = config


        async def slow_check(sid: str) -> bool:
            await asyncio.sleep(10)
            return True

        with patch.object(hub, "health_check", slow_check):
            results = await hub.health_check_all(timeout_s=0.1)

        assert "slow" in results
        assert results["slow"] is False

    async def test_exception_isolated(self, tmp_path):
        """One server raising should not cancel the others."""
        from maop.core.mcp.mcp_hub import MCPHub
        from maop.core.mcp.mcp_hub_types import MCPServerConfig, TransportType

        hub = MCPHub(root_dir=tmp_path)
        config = MCPServerConfig(name="srv", transport=TransportType.STDIO)
        hub._transports["bad"] = None  # type: ignore[assignment]
        hub._transports["good"] = None  # type: ignore[assignment]
        hub._configs["bad"] = config
        hub._configs["good"] = config

        call_count = {"n": 0}

        async def flaky_check(sid: str) -> bool:
            call_count["n"] += 1
            if sid == "bad":
                raise RuntimeError("boom")
            return True

        with patch.object(hub, "health_check", flaky_check):
            results = await hub.health_check_all()

        assert results["bad"] is False  # exception → unhealthy
        assert results["good"] is True
        assert call_count["n"] == 2  # both were called

    async def test_empty_returns_empty_dict(self, tmp_path):
        from maop.core.mcp.mcp_hub import MCPHub

        hub = MCPHub(root_dir=tmp_path)
        results = await hub.health_check_all()
        assert results == {}

    async def test_backward_compatible_no_timeout(self, tmp_path):
        """Without timeout_s, the method still works (backward compat)."""
        from maop.core.mcp.mcp_hub import MCPHub
        from maop.core.mcp.mcp_hub_types import MCPServerConfig, TransportType

        hub = MCPHub(root_dir=tmp_path)
        config = MCPServerConfig(name="srv", transport=TransportType.STDIO)
        hub._transports["s1"] = None  # type: ignore[assignment]
        hub._configs["s1"] = config

        async def ok_check(sid: str) -> bool:
            return True

        with patch.object(hub, "health_check", ok_check):
            results = await hub.health_check_all()

        assert results == {"s1": True}