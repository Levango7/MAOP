"""Tests for MCP Stack B removal verification (δ-1: Stack A/B unification).

Verifies that:
  1. Stack B modules (mcp_client, mcp_registry, mcp_transport) are NOT importable
     — they have been removed in favor of the canonical MCPHub (Stack A).
  2. Dashboard MCP router uses MCPHub (not MCPRegistry/MCPClient).
  3. MCPHub provides the name-based compat shims required by legacy callers.
"""

from __future__ import annotations

import importlib
import sys

import pytest

# ── Stack B modules that must NOT be importable ─────────────────────────
REMOVED_MODULES = [
    "maop.core.mcp_transport",
    "maop.core.mcp_client",
    "maop.core.mcp_registry",
]


class TestStackBRemoved:
    """Each Stack B module must fail to import (removed in δ-1)."""

    @pytest.mark.parametrize("module_name", REMOVED_MODULES)
    def test_module_not_importable(self, module_name: str) -> None:
        # Ensure any stale entry is cleared from sys.modules so we exercise
        # the real import machinery (not a cached reference).
        sys.modules.pop(module_name, None)
        with pytest.raises(ImportError):
            importlib.import_module(module_name)

    def test_stack_b_classes_not_exported(self) -> None:
        """Stack B class names must not be importable from maop.core."""
        # These names lived in the removed Stack B modules.
        from maop.core.mcp import mcp_hub

        # Importing the canonical module must not re-expose Stack B classes.
        assert not hasattr(mcp_hub, "MCPRegistry"), (
            "MCPHub module must not expose removed MCPRegistry (Stack B)"
        )
        assert not hasattr(mcp_hub, "MCPClient"), (
            "MCPHub module must not expose removed MCPClient (Stack B)"
        )
        assert not hasattr(mcp_hub, "MCPToolDef"), (
            "MCPHub module must not expose removed MCPToolDef (Stack B); "
            "canonical model is MCPTool"
        )

    def test_no_legacy_test_client_file(self) -> None:
        """The Stack B unit-test file must have been removed."""
        from pathlib import Path

        legacy_test = Path(__file__).parent / "test_mcp_client.py"
        assert not legacy_test.exists(), (
            f"Stack B test file should be removed: {legacy_test}"
        )


class TestMCPHubCanonical:
    """MCPHub (Stack A) must provide the name-based compat shims used by
    legacy callers previously depending on MCPRegistry."""

    def test_mcp_hub_imports_cleanly(self) -> None:
        """The canonical mcp_hub module must import without warnings."""
        import warnings

        sys.modules.pop("maop.core.mcp.mcp_hub", None)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.import_module("maop.core.mcp.mcp_hub")
        deps = [
            w for w in caught
            if issubclass(w.category, DeprecationWarning)
            and "deprecated Stack B" in str(w.message)
        ]
        assert deps == [], (
            f"mcp_hub must not emit Stack B deprecation warnings: "
            f"{[str(w.message) for w in deps]}"
        )

    @pytest.mark.parametrize(
        "attr",
        [
            # Name-based compat shims required by legacy callers.
            "get_server_config",
            "find_server_id_by_name",
            "add_server",
            "remove_server",
            "all_tools",
            "find_tool",
            "call_tool_by_name",
            "list_servers",
            "health_check_all",
        ],
    )
    def test_mcp_hub_has_compat_shim(self, attr: str) -> None:
        from maop.core.mcp.mcp_hub import MCPHub

        assert hasattr(MCPHub, attr), (
            f"MCPHub must expose compat shim '{attr}' for legacy callers "
            f"(previously provided by MCPRegistry — Stack B)"
        )

    def test_mcp_hub_exposes_canonical_models(self) -> None:
        """Canonical Stack A model names must be importable from mcp_hub."""
        from maop.core.mcp.mcp_hub import (
            MCPTool,
        )

        # Sanity-check that these are real types (not Stack B aliases).
        assert MCPTool.__name__ == "MCPTool", (
            "Canonical tool model must be MCPTool (not MCPToolDef from Stack B)"
        )


class TestDashboardUsesMCPHub:
    """The Dashboard MCP router must depend solely on MCPHub (Stack A)."""

    def test_router_imports_mcp_hub(self) -> None:
        """dashboard/routers/mcp.py must import MCPHub, not Stack B."""
        from pathlib import Path

        router_path = (
            Path(__file__).resolve().parent.parent
            / "maop" / "dashboard" / "routers" / "mcp.py"
        )
        assert router_path.exists(), f"Dashboard MCP router missing: {router_path}"
        src = router_path.read_text(encoding="utf-8")

        # Must reference the canonical Stack A implementation.
        assert "from maop.core.mcp.mcp_hub import" in src, (
            "Dashboard MCP router must import from maop.core.mcp.mcp_hub (Stack A)"
        )
        assert "MCPHub" in src, "Dashboard MCP router must use MCPHub class"

        # Must NOT import any Stack B module.
        forbidden = [
            "from maop.core.mcp_registry",
            "from maop.core.mcp_client",
            "from maop.core.mcp_transport",
            "import maop.core.mcp_registry",
            "import maop.core.mcp_client",
            "import maop.core.mcp_transport",
        ]
        leaked = [token for token in forbidden if token in src]
        assert not leaked, (
            f"Dashboard MCP router must not import Stack B modules: {leaked}"
        )

    def test_router_endpoints_use_compat_shims(self) -> None:
        """Dashboard endpoints must call MCPHub compat shims, not Stack B."""
        from pathlib import Path

        router_path = (
            Path(__file__).resolve().parent.parent
            / "maop" / "dashboard" / "routers" / "mcp.py"
        )
        src = router_path.read_text(encoding="utf-8")

        # Compat shims introduced in δ-1 — these are what the router calls.
        expected_calls = [
            "hub.get_server_config(",
            "hub.find_server_id_by_name(",
            "hub.remove_server(",
            "hub.add_server(",
            "hub.all_tools()",
            "hub.list_servers()",
            "hub.call_tool_by_name(",
            "hub.health_check_all()",
        ]
        missing = [c for c in expected_calls if c not in src]
        assert not missing, (
            f"Dashboard MCP router missing MCPHub compat-shim calls: {missing}"
        )


class TestCallersMigrated:
    """function_call.py and tool_schema.py must use MCPHub (Stack A)."""

    def test_function_call_uses_mcp_hub(self) -> None:
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent
            / "maop" / "core" / "agent" / "llm_chat" / "function_call.py"
        )
        src = path.read_text(encoding="utf-8")

        assert "from maop.core.mcp.mcp_hub import MCPHub" in src, (
            "function_call.py must import MCPHub from maop.core.mcp.mcp_hub (Stack A)"
        )
        # Must not import Stack B modules at runtime.
        forbidden = [
            "from maop.core.mcp_registry import",
            "from maop.core.mcp_client import",
            "from maop.core.mcp_transport import",
        ]
        leaked = [t for t in forbidden if t in src]
        assert not leaked, (
            f"function_call.py must not import Stack B modules: {leaked}"
        )

    def test_tool_schema_uses_mcp_hub(self) -> None:
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent
            / "maop" / "core" / "agent" / "tools" / "tool_schema.py"
        )
        src = path.read_text(encoding="utf-8")

        assert "from maop.core.mcp.mcp_hub import" in src, (
            "tool_schema.py must import from maop.core.mcp.mcp_hub (Stack A)"
        )
        assert "MCPTool" in src, (
            "tool_schema.py must reference canonical MCPTool (not MCPToolDef)"
        )
        forbidden = [
            "from maop.core.mcp_registry import",
            "from maop.core.mcp_client import",
            "from maop.core.mcp_transport import",
        ]
        leaked = [t for t in forbidden if t in src]
        assert not leaked, (
            f"tool_schema.py must not import Stack B modules: {leaked}"
        )
