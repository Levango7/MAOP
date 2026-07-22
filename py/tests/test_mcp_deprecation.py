"""Tests for MCP Stack B deprecation warnings (t14).

Verifies that importing the deprecated MCP modules (mcp_transport,
mcp_client, mcp_registry) emits DeprecationWarning pointing to
mcp_hub as the canonical implementation.
"""

from __future__ import annotations

import importlib
import warnings

import pytest


DEPRECATED_MODULES = [
    "maop.core.mcp_transport",
    "maop.core.mcp_client",
    "maop.core.mcp_registry",
]


class TestStackBDeprecation:
    """Each Stack B module must warn on import."""

    @pytest.mark.parametrize("module_name", DEPRECATED_MODULES)
    def test_import_emits_deprecation_warning(self, module_name: str) -> None:
        # Force re-import so the warning fires again.
        # Clear from sys.modules so importlib.reload re-executes module body.
        import sys
        mod = sys.modules.pop(module_name, None)
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                importlib.import_module(module_name)
            # Find the DeprecationWarning from this module.
            deps = [
                w for w in caught
                if issubclass(w.category, DeprecationWarning)
                and "deprecated Stack B" in str(w.message)
                and module_name in str(w.message)
            ]
            assert len(deps) >= 1, (
                f"Expected DeprecationWarning from {module_name}, "
                f"got: {[str(w.message) for w in caught]}"
            )
        finally:
            # Restore the module in sys.modules (re-imported version).
            if mod is not None:
                sys.modules[module_name] = mod

    def test_warning_mentions_mcp_hub_as_canonical(self) -> None:
        import sys
        mod = sys.modules.pop("maop.core.mcp_registry", None)
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                importlib.import_module("maop.core.mcp_registry")
            deps = [
                w for w in caught
                if issubclass(w.category, DeprecationWarning)
            ]
            assert len(deps) >= 1
            assert "mcp_hub" in str(deps[0].message)
        finally:
            if mod is not None:
                sys.modules["maop.core.mcp_registry"] = mod

    def test_mcp_hub_not_deprecated(self) -> None:
        """The canonical mcp_hub module must NOT emit DeprecationWarning."""
        import sys
        mod = sys.modules.pop("maop.core.mcp_hub", None)
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                importlib.import_module("maop.core.mcp_hub")
            deps = [
                w for w in caught
                if issubclass(w.category, DeprecationWarning)
                and "deprecated Stack B" in str(w.message)
            ]
            assert len(deps) == 0, (
                f"mcp_hub should not be deprecated, but got: "
                f"{[str(w.message) for w in deps]}"
            )
        finally:
            if mod is not None:
                sys.modules["maop.core.mcp_hub"] = mod
