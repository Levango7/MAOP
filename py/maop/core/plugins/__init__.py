"""MAOP Plugin System — Typed plugin contract, lifecycle manager, hook dispatch.

This package provides the **modern** plugin API for MAOP:

* :class:`PluginSpec` — abstract contract plugins implement.
* :class:`PluginMetadata` / :class:`PluginContext` — declarative metadata and
  runtime context handed to plugins.
* :class:`PluginManager` — registry + lifecycle state machine + hook dispatch.
* :class:`PluginState` / :class:`HookPoint` — enums for lifecycle and hooks.

It complements :mod:`maop.core.plugin` (the filesystem sandbox loader) by
offering a stable, typed, in-process contract.  Plugins written against
``PluginSpec`` are registered with ``PluginManager.register()``; the manager
handles dependency resolution, ordered hook dispatch, and graceful error
isolation.

Example::

    from maop.core.plugins import PluginManager, PluginSpec, PluginMetadata

    class Greeter(PluginSpec):
        def metadata(self):
            return PluginMetadata(name="greeter", version="1.0.0")
        def on_load(self, ctx):
            self.ctx = ctx
        def on_start(self):
            self.ctx.logger.info("greeter ready")

    mgr = PluginManager()
    mgr.register(Greeter())
    mgr.load("greeter")
    mgr.start("greeter")
"""

from __future__ import annotations

from maop.core.plugins.manager import (
    PluginError,
    PluginManager,
    PluginRecord,
    PluginState,
)
from maop.core.plugins.spec import (
    PLUGIN_API_VERSION,
    HookCallback,
    HookPoint,
    PluginContext,
    PluginMetadata,
    PluginSpec,
)

__all__ = [
    "PLUGIN_API_VERSION",
    "HookCallback",
    "HookPoint",
    "PluginContext",
    "PluginError",
    "PluginManager",
    "PluginMetadata",
    "PluginRecord",
    "PluginSpec",
    "PluginState",
]