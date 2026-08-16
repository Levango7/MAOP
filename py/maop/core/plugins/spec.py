"""MAOP Plugin Specification — Plugin interface contract and metadata models.

Defines the abstract contract that every MAOP plugin must implement, plus the
supporting data models (metadata, runtime context, hook descriptors).

A *plugin* is a self-contained extension that registers hooks into the MAOP
orchestration lifecycle (pre-route, post-route, tool-call, agent-step, …)
without modifying core source code.

Design goals
------------
* **Stable contract** — plugins depend on ``PluginSpec`` / ``PluginContext``,
  not on internal MAOP APIs.  Breaking changes are gated by ``api_version``.
* **Typed hooks** — each hook point declares its callback signature so the
  manager can validate compatibility at load time.
* **No I/O in the interface** — ``PluginSpec`` is pure logic; persistence and
  discovery live in :mod:`maop.core.plugins.manager`.

Usage (plugin author)::

    from maop.core.plugins import PluginSpec, PluginMetadata, PluginContext

    class MyPlugin(PluginSpec):
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(name="my-plugin", version="1.0.0")

        def on_load(self, ctx: PluginContext) -> None:
            self._ctx = ctx

        def on_start(self) -> None:
            self._ctx.logger.info("started")

        def get_hooks(self) -> dict[str, callable]:
            return {"pre_route": self._pre_route}

        def _pre_route(self, request: dict) -> dict:
            return request
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

#: API version of the plugin contract.  Bumped on breaking changes to
#: ``PluginSpec`` / ``PluginContext``.  Plugins declare their required
#: ``api_version`` in :class:`PluginMetadata`; the manager rejects mismatches.
PLUGIN_API_VERSION: str = "1.0"


class HookPoint(str, Enum):
    """Well-known extension points in the MAOP lifecycle.

    Plugins register callbacks for one or more of these points.  The manager
    invokes them in registration order, passing a point-specific payload.
    """

    PRE_ROUTE = "pre_route"
    POST_ROUTE = "post_route"
    PRE_TOOL_CALL = "pre_tool_call"
    POST_TOOL_CALL = "post_tool_call"
    PRE_AGENT_STEP = "pre_agent_step"
    POST_AGENT_STEP = "post_agent_step"
    ON_ERROR = "on_error"
    ON_METRIC = "on_metric"
    ON_SHUTDOWN = "on_shutdown"


#: Type alias for a hook callback — accepts an arbitrary payload dict and
#: returns a (possibly mutated) payload dict.  Returning ``None`` is treated
#: as "no transformation".
HookCallback = Callable[[dict[str, Any]], dict[str, Any] | None]


class PluginMetadata(BaseModel):
    """Declarative metadata advertised by a plugin.

    Attributes
    ----------
    name : str
        Unique plugin identifier (lower-case, hyphen-separated).
    version : str
        SemVer version string.
    api_version : str
        Contract version the plugin was built against.  Must be compatible
        with :data:`PLUGIN_API_VERSION`.
    description, author, license, homepage, tags
        Human-readable metadata.
    dependencies : list[str]
        Names of other plugins that must be loaded first.
    config_schema : dict
        JSON-Schema-ish description of the plugin's config options.
    priority : int
        Loading priority (lower = earlier).  Used to order hook invocation.
    """

    name: str
    version: str = "0.1.0"
    api_version: str = PLUGIN_API_VERSION
    description: str = ""
    author: str = ""
    license: str = ""
    homepage: str = ""
    tags: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    priority: int = 100

    def is_api_compatible(self, host_version: str = PLUGIN_API_VERSION) -> bool:
        """Return True if the plugin's api_version is compatible with *host_version*.

        Compatibility rule (simple major.minor): the major component must match
        and the plugin's minor must be <= the host's minor.
        """
        return _semver_compatible(self.api_version, host_version)


def _semver_compatible(required: str, host: str) -> bool:
    """Check loose major.minor compatibility between two version strings."""
    try:
        r_major, r_minor = required.split(".")[:2]
        h_major, h_minor = host.split(".")[:2]
        return r_major == h_major and int(r_minor) <= int(h_minor)
    except (ValueError, IndexError):
        # Unparseable versions are treated as compatible to avoid blocking
        # legacy plugins; the manager logs a warning instead.
        return True


class PluginContext(BaseModel, arbitrary_types_allowed=True):  # type: ignore[call-arg]
    """Runtime context handed to a plugin during ``on_load``.

    Provides controlled access to host services without exposing internals.
    Plugins should store the context and use it for logging, config access,
    and emitting metrics.

    Attributes
    ----------
    logger : logging.Logger
        Pre-configured logger named ``maop.plugin.<name>``.
    config : dict
        Plugin-specific configuration merged from defaults + user overrides.
    data_dir : str
        Writable directory the plugin may use for persistent state.
    host_info : dict
        Read-only host metadata (version, edition, feature flags).
    """

    logger: Any = Field(default_factory=lambda: logging.getLogger("maop.plugin"))
    config: dict[str, Any] = Field(default_factory=dict)
    data_dir: str = ""
    host_info: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


class PluginSpec(ABC):
    """Abstract base class — the contract every MAOP plugin implements.

    Subclasses must override :meth:`metadata` and :meth:`on_load`.  The
    remaining lifecycle methods have default no-op implementations so a
    plugin only overrides what it needs.

    Lifecycle (driven by :class:`~maop.core.plugins.manager.PluginManager`)::

        discovered → on_load(ctx) → loaded
        loaded     → on_start()   → started
        started    → on_stop()    → stopped
        stopped    → on_unload()  → unloaded
        any        → on_error(exc) → errored
    """

    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """Return this plugin's metadata.  Called once at discovery time."""

    def on_load(self, ctx: PluginContext) -> None:
        """Called when the plugin is loaded into the host.  Default: no-op."""

    def on_unload(self) -> None:
        """Called when the plugin is unloaded.  Default: no-op.

        Implementations should release resources (file handles, threads,
        subscriptions) here.
        """

    def on_start(self) -> None:
        """Called when the host starts the plugin.  Default: no-op.

        Use this to begin background work or register runtime hooks.
        """

    def on_stop(self) -> None:
        """Called when the host stops the plugin.  Default: no-op."""

    def on_error(self, exc: BaseException) -> None:
        """Called when an exception occurs during a hook callback.

        Default implementation logs the error.  Plugins may override to
        implement custom recovery or alerting.
        """
        md = self.metadata()
        logger.error("plugin %s caught error: %s", md.name, exc)

    def get_hooks(self) -> dict[str, HookCallback]:
        """Return a mapping of hook-point name → callback.

        Keys should be :class:`HookPoint` values (e.g. ``"pre_route"``).
        Returning an empty dict (the default) means the plugin installs no
        hooks but may still participate in load/start/stop lifecycle.
        """
        return {}

    def get_config_defaults(self) -> dict[str, Any]:
        """Return default config values.  Merged with user overrides at load.

        Default: empty dict.  Plugins override to declare their config schema
        defaults without duplicating them in :class:`PluginMetadata`.
        """
        return {}