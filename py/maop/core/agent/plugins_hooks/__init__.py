"""Plugins and hooks subpackage."""
from maop.core.agent.plugins_hooks.plugin import PluginState, PluginInfo, PluginManifest
from maop.core.agent.plugins_hooks.plugin_manager import PluginManager
from maop.core.agent.plugins_hooks.plugin_sandbox import PluginSandbox, SandboxViolation
from maop.core.agent.plugins_hooks.hook_manager import (
    LifecycleEvent,
    HookType,
    HookPhase,
    HookDef,
    HookResult,
    HookTriggerStats,
    HookManager,
    get_hook_manager,
)
from maop.core.agent.plugins_hooks.protocol import ProtocolDef, ProtocolMessage, ProtocolRegistry
