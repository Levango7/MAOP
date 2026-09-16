"""MAOP Plugin System — Typed plugin contract, lifecycle manager, hook dispatch.

.. warning::
   **当前为预留扩展点，尚未接入主流程**（2026-09-17 核实）：全仓库除本包
   内部与文档字符串外，**生产代码零引用**——没有调用点构造 ``PluginManager``
   或注册 ``PluginSpec``。实际的插件/工具加载走
   :mod:`maop.core.plugin`（文件系统沙箱加载器）与
   :mod:`maop.core.agent.plugins_hooks.hook_manager`。

   因此本包的“依赖解析 / 有序 hook 分发 / 错误隔离”能力**不生效**，
   不要据其推断系统的插件隔离保证。若要用它，需先在主流程接入（注册点 +
   加载时机），并补充接入后的集成测试。

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