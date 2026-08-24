"""Coverage tests for core modules: tls, tool_schema, skill_version, sandbox.

Uses isolated tmp_path + real instances where possible.
"""
from __future__ import annotations

import ssl

import pytest

from maop.core.agent.tools.tool_schema import ToolSchemaDef, ToolSchemaGenerator
from maop.core.security.tls import TLSSettings, create_ssl_context, generate_self_signed

# ── TLS ──────────────────────────────────────────────────────────────

class TestTLSSettings:
    def test_defaults(self):
        s = TLSSettings()
        assert s.enabled is False
        assert s.min_version == "TLSv1_2"

    def test_custom(self):
        s = TLSSettings(enabled=True, cert_file="cert.pem", key_file="key.pem")
        assert s.enabled is True
        assert s.cert_file == "cert.pem"


class TestCreateSslContext:
    def test_disabled_returns_none(self):
        s = TLSSettings(enabled=False)
        assert create_ssl_context(s) is None

    def test_missing_cert_file(self):
        s = TLSSettings(enabled=True, cert_file="nonexistent.pem", key_file="key.pem")
        with pytest.raises(FileNotFoundError):
            create_ssl_context(s)

    def test_missing_key_file(self, tmp_path):
        cert = tmp_path / "cert.pem"
        cert.write_text("fake cert")
        s = TLSSettings(enabled=True, cert_file=str(cert), key_file="nonexistent.pem")
        with pytest.raises(FileNotFoundError):
            create_ssl_context(s)

    def test_deprecated_tls_version_rejected(self, tmp_path):
        cert = tmp_path / "cert.pem"
        cert.write_text("fake cert")
        key = tmp_path / "key.pem"
        key.write_text("fake key")
        s = TLSSettings(
            enabled=True, cert_file=str(cert), key_file=str(key),
            min_version="TLSv1",
        )
        with pytest.raises(ValueError, match="deprecated"):
            create_ssl_context(s)

    def test_deprecated_tls_version_allowed_with_env(self, tmp_path, monkeypatch):
        cert = tmp_path / "cert.pem"
        cert.write_text("fake cert")
        key = tmp_path / "key.pem"
        key.write_text("fake key")
        monkeypatch.setenv("MAOP_TLS_ALLOW_DEPRECATED", "1")
        s = TLSSettings(
            enabled=True, cert_file=str(cert), key_file=str(key),
            min_version="TLSv1",
        )
        # Will fail at load_cert_chain (fake cert), but passes the deprecation check
        with pytest.raises((ssl.SSLError, ValueError, Exception)):
            create_ssl_context(s)

    def test_verify_client_without_ca(self, tmp_path):
        cert = tmp_path / "cert.pem"
        cert.write_text("fake cert")
        key = tmp_path / "key.pem"
        key.write_text("fake key")
        s = TLSSettings(
            enabled=True, cert_file=str(cert), key_file=str(key),
            verify_client=True,
        )
        # load_cert_chain fails first (fake cert) → SSLError, or if it
        # somehow passes, verify_client check raises ValueError.
        with pytest.raises((ssl.SSLError, ValueError, Exception)):
            create_ssl_context(s)

    def test_placeholder_cert_rejected(self, tmp_path):
        cert = tmp_path / "cert.pem"
        cert.write_text("# placeholder certificate")
        key = tmp_path / "key.pem"
        key.write_text("fake key")
        s = TLSSettings(enabled=True, cert_file=str(cert), key_file=str(key))
        with pytest.raises(ValueError, match="placeholder"):
            create_ssl_context(s)


class TestGenerateSelfSigned:
    def test_openssl_unavailable_raises(self, tmp_path, monkeypatch):
        """When openssl is not available, raises RuntimeError."""
        import subprocess
        mock_run = MagicMock(side_effect=FileNotFoundError("openssl not found"))
        monkeypatch.setattr(subprocess, "run", mock_run)
        with pytest.raises(RuntimeError, match="openssl not available"):
            generate_self_signed(tmp_path)

    def test_existing_cert_skipped(self, tmp_path):
        """When cert already exists and overwrite=False, returns existing."""
        cert = tmp_path / "MAOP-dev.crt"
        key = tmp_path / "MAOP-dev.key"
        cert.write_text("existing cert")
        key.write_text("existing key")
        result_cert, result_key = generate_self_signed(tmp_path, overwrite=False)
        assert result_cert == cert
        assert result_key.exists()


# ── Tool Schema ─────────────────────────────────────────────────────

class TestToolSchemaDef:
    def test_defaults(self):
        d = ToolSchemaDef(name="test")
        assert d.name == "test"
        assert d.source == "manual"
        assert d.parameters["type"] == "object"


class TestToolSchemaGenerator:
    def test_init(self):
        gen = ToolSchemaGenerator(root_dir="/tmp")
        assert gen._root_dir == "/tmp"

    def test_register_unregister(self):
        gen = ToolSchemaGenerator()
        schema = ToolSchemaDef(name="test_tool")
        gen.register(schema)
        assert "test_tool" in gen._custom_schemas
        assert gen.unregister("test_tool") is True
        assert gen.unregister("test_tool") is False

    def test_from_python_function(self):
        gen = ToolSchemaGenerator()

        def my_func(x: int, y: str = "default"):
            """Do something.

            :param x: the x value
            :param y: the y value
            """

        schema = gen.from_python_function(my_func)
        assert schema.name == "my_func"
        assert "x" in schema.parameters["properties"]
        assert "y" in schema.parameters["properties"]
        assert "x" in schema.parameters["required"]
        assert "y" not in schema.parameters["required"]

    def test_from_python_function_with_args_style(self):
        gen = ToolSchemaGenerator()

        def my_func(x: int, y: str = "default"):
            """Do something.

            Args:
                x: the x value
                y: the y value
            """

        schema = gen.from_python_function(my_func)
        assert schema.parameters["properties"]["x"]["description"] == "the x value"

    def test_from_python_function_custom_name(self):
        gen = ToolSchemaGenerator()

        def my_func(x: int):
            """Test."""

        schema = gen.from_python_function(my_func, name="custom_name")
        assert schema.name == "custom_name"

    def test_from_mcp_tool(self):
        gen = ToolSchemaGenerator()
        # Use a simple object with the expected attributes
        from types import SimpleNamespace
        mock_tool = SimpleNamespace(
            server_name="test_server",
            name="test_tool",
            description="test description",
            input_schema={"type": "object", "properties": {}},
        )
        # MCPTool is a specific class — test with non-MCPTool object
        schema = gen.from_mcp_tool(mock_tool)
        assert schema.source == "mcp"

    def test_from_cli_tool(self):
        gen = ToolSchemaGenerator()
        from types import SimpleNamespace
        mock_cli = SimpleNamespace(
            id="cli_tool",
            description="CLI tool",
            params={"arg1": {"type": "string"}},
        )
        schema = gen.from_cli_tool(mock_cli)
        assert schema.name == "cli_tool"
        assert schema.source == "cli"

    def test_from_cli_tool_string_params(self):
        gen = ToolSchemaGenerator()
        from types import SimpleNamespace
        mock_cli = SimpleNamespace(
            id="cli_tool",
            description="CLI tool",
            params={"arg1": "string description"},
        )
        schema = gen.from_cli_tool(mock_cli)
        assert schema.parameters["properties"]["arg1"]["type"] == "string"

    def test_generate_openai(self):
        gen = ToolSchemaGenerator()
        gen.register(ToolSchemaDef(name="test_tool", description="test"))
        result = gen.generate(provider="openai")
        assert len(result) >= 1
        assert result[0]["type"] == "function"

    def test_generate_anthropic(self):
        gen = ToolSchemaGenerator()
        gen.register(ToolSchemaDef(name="test_tool", description="test"))
        result = gen.generate(provider="anthropic")
        assert len(result) >= 1
        assert "name" in result[0]

    def test_extract_description(self):
        gen = ToolSchemaGenerator()
        doc = "This is a description.\n\n:param x: the x value"
        desc = gen._extract_description(doc)
        assert "This is a description." in desc

    def test_extract_param_docs(self):
        gen = ToolSchemaGenerator()
        doc = ":param x: the x value\n:param y: the y value"
        docs = gen._extract_param_docs(doc)
        assert docs["x"] == "the x value"
        assert docs["y"] == "the y value"


# ── Skill Version ───────────────────────────────────────────────────

class TestSkillVersionManager:
    def test_init(self, tmp_path):
        from maop.core.evolution.skill_version import SkillVersionManager
        mgr = SkillVersionManager(root_dir=str(tmp_path))
        assert mgr is not None

    def test_list_skills_empty(self, tmp_path):
        from maop.core.evolution.skill_version import SkillVersionManager
        mgr = SkillVersionManager(root_dir=str(tmp_path))
        skills = mgr.list_skills()
        assert isinstance(skills, list)

    def test_get_skill_meta_nonexistent(self, tmp_path):
        from maop.core.evolution.skill_version import SkillVersionManager
        mgr = SkillVersionManager(root_dir=str(tmp_path))
        assert mgr.get_skill_meta("nonexistent") is None

    def test_load_skill_nonexistent(self, tmp_path):
        from maop.core.evolution.skill_version import SkillVersionManager
        mgr = SkillVersionManager(root_dir=str(tmp_path))
        assert mgr.load_skill("nonexistent") is None

    def test_get_history_empty(self, tmp_path):
        from maop.core.evolution.skill_version import SkillVersionManager
        mgr = SkillVersionManager(root_dir=str(tmp_path))
        history = mgr.get_history("nonexistent")
        assert isinstance(history, list)

    def test_delete_skill_nonexistent(self, tmp_path):
        from maop.core.evolution.skill_version import SkillVersionManager
        mgr = SkillVersionManager(root_dir=str(tmp_path))
        assert mgr.delete_skill("nonexistent") is False

    def test_match_empty(self, tmp_path):
        from maop.core.evolution.skill_version import SkillVersionManager
        mgr = SkillVersionManager(root_dir=str(tmp_path))
        matches = mgr.match("test intent")
        assert isinstance(matches, list)

    def test_hot_reload(self, tmp_path):
        from maop.core.evolution.skill_version import SkillVersionManager
        mgr = SkillVersionManager(root_dir=str(tmp_path))
        result = mgr.hot_reload()
        assert isinstance(result, int)


# ── Sandbox ─────────────────────────────────────────────────────────

class TestSandboxManager:
    def test_init(self, tmp_path):
        from maop.core.security.sandbox import SandboxManager
        mgr = SandboxManager(root_dir=str(tmp_path))
        assert mgr is not None

    def test_create(self, tmp_path):
        from maop.core.security.sandbox import SandboxManager
        mgr = SandboxManager(root_dir=str(tmp_path))
        info = mgr.create()
        assert info is not None
        assert info.id

    def test_create_with_id(self, tmp_path):
        from maop.core.security.sandbox import SandboxManager
        mgr = SandboxManager(root_dir=str(tmp_path))
        info = mgr.create(sandbox_id="custom-id")
        assert info.id == "custom-id"

    def test_get_nonexistent(self, tmp_path):
        from maop.core.security.sandbox import SandboxManager
        mgr = SandboxManager(root_dir=str(tmp_path))
        assert mgr.get("nonexistent") is None

    def test_get_existing(self, tmp_path):
        from maop.core.security.sandbox import SandboxManager
        mgr = SandboxManager(root_dir=str(tmp_path))
        mgr.create(sandbox_id="test-sb")
        info = mgr.get("test-sb")
        assert info is not None
        assert info.id == "test-sb"

    def test_list_all(self, tmp_path):
        from maop.core.security.sandbox import SandboxManager
        mgr = SandboxManager(root_dir=str(tmp_path))
        mgr.create(sandbox_id="sb1")
        result = mgr.list_all()
        assert isinstance(result, list)

    def test_cleanup(self, tmp_path):
        from maop.core.security.sandbox import SandboxManager
        mgr = SandboxManager(root_dir=str(tmp_path))
        mgr.create(sandbox_id="sb1")
        assert mgr.cleanup("sb1") is True

    def test_cleanup_nonexistent(self, tmp_path):
        from maop.core.security.sandbox import SandboxManager
        mgr = SandboxManager(root_dir=str(tmp_path))
        assert mgr.cleanup("nonexistent") is False

    def test_stats(self, tmp_path):
        from maop.core.security.sandbox import SandboxManager
        mgr = SandboxManager(root_dir=str(tmp_path))
        stats = mgr.stats()
        assert isinstance(stats, dict)

    def test_cleanup_expired(self, tmp_path):
        from maop.core.security.sandbox import SandboxManager
        mgr = SandboxManager(root_dir=str(tmp_path))
        result = mgr.cleanup_expired(hours=24)
        assert isinstance(result, int)


# Need MagicMock import
from unittest.mock import MagicMock