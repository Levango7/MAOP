"""MAOP Plugin System — Discovery, loading, lifecycle, and Hook extension points.

This module is a backward-compatibility façade that re-exports the public
symbols of the plugin subsystem, which has been split into focused
submodules:

  - ``plugin_hooks``    — hook declaration models & lifecycle state
    (``PluginState``, ``PluginManifest``)
  - ``plugin_sandbox``  — restricted execution environment for untrusted
    plugin code (``PluginSandbox``, ``SandboxViolation``, builtins
    whitelists, dunder AST scan, import guard, timeout)
  - ``plugin_manager``  — plugin lifecycle manager with discovery,
    loading, and Hook integration (``PluginManager``, ``PluginInfo``)

Existing imports such as
``from maop.core.agent.plugins_hooks.plugin import PluginManager``
continue to work unchanged.

Plugin directory layout::

    plugins/
      my_plugin/
        MAOP-plugin.yaml   # manifest
        main.py           # entry point (must export MAOP_plugin_init)

Security model (defense in depth):
  - Path whitelist: only files under ``plugins/`` may be loaded
  - SHA-256 checksum: mandatory integrity verification per manifest
  - Static AST scan: forbidden dunder attribute access
    (__class__, __subclasses__, __globals__, __code__, ...) is rejected
    at parse time, blocking the classic
    ``().__class__.__bases__[0].__subclasses__()`` escape chain
  - Restricted builtins (pure whitelist): only explicitly-listed safe
    names are exposed; dangerous builtins (globals, locals, getattr,
    type, vars, dir, eval, exec, compile, open, __import__, ...) are
    either omitted or replaced with stubs that raise SandboxViolation
  - Import guard: plugin code may only import from a configurable allowlist
  - Timeout: plugin init functions are capped by a configurable wall-clock limit
"""

from __future__ import annotations

import logging

# Hook declaration models & lifecycle state
from .plugin_hooks import PluginManifest, PluginState

# Manager: discovery, loading, lifecycle, Hook integration
from .plugin_manager import PluginInfo, PluginManager

# Sandbox: restricted execution environment + builtins whitelists
from .plugin_sandbox import (
    _DANGEROUS_BUILTINS,
    _DEFAULT_ALLOWED_IMPORTS,
    _DUNDER_DENYLIST,
    _SAFE_BUILTIN_CONSTS,
    _SAFE_BUILTIN_NAMES,
    PluginSandbox,
    SandboxViolation,
    _mp_init_target,
)

logger = logging.getLogger(__name__)

__all__ = [
    "_DANGEROUS_BUILTINS",
    "_DEFAULT_ALLOWED_IMPORTS",
    "_DUNDER_DENYLIST",
    "_SAFE_BUILTIN_CONSTS",
    "_SAFE_BUILTIN_NAMES",
    "PluginInfo",
    "PluginManager",
    "PluginManifest",
    "PluginSandbox",
    "PluginState",
    "SandboxViolation",
    "_mp_init_target",
]
