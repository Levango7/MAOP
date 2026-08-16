"""MAOP Plugin Manager — Discovery, loading, lifecycle, and hook dispatch.

Orchestrates plugins that implement :class:`maop.core.plugins.spec.PluginSpec`.
The manager owns the plugin state machine, enforces dependency ordering, and
provides a single dispatch entry-point for each :class:`HookPoint`.

State machine::

    REGISTERED ──load()──► LOADED ──start()──► STARTED
        │                     │                   │
        │                     │                  stop()
        │                     ▼                   ▼
        │                  unload()            STOPPED
        ▼                     │
      (gone)                   ▼
                          UNLOADED

Any exception during a lifecycle transition moves the plugin to ``ERRORED``
and records the exception; the manager continues operating other plugins.

The manager is intentionally storage-agnostic: it works with in-process
:class:`PluginSpec` instances.  Filesystem discovery (finding plugin classes
on disk) is delegated to :mod:`maop.core.plugin` (the legacy sandbox loader)
or supplied by the caller via :meth:`register`.
"""

from __future__ import annotations

import logging
import threading
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from maop.core.plugins.spec import (
    PLUGIN_API_VERSION,
    HookCallback,
    HookPoint,
    PluginContext,
    PluginMetadata,
    PluginSpec,
)

logger = logging.getLogger(__name__)


class PluginState(str, Enum):
    """Lifecycle states tracked by :class:`PluginManager`."""

    REGISTERED = "registered"   # spec known, not yet loaded
    LOADED = "loaded"           # on_load() succeeded
    STARTED = "started"         # on_start() succeeded
    STOPPED = "stopped"         # on_stop() succeeded
    UNLOADED = "unloaded"       # on_unload() succeeded
    ERRORED = "errored"         # last transition raised


class PluginRecord(BaseModel, arbitrary_types_allowed=True):  # type: ignore
    """Internal bookkeeping for one plugin instance."""

    spec: Any = None            # PluginSpec instance (arbitrary type for mypy)
    state: PluginState = PluginState.REGISTERED
    context: PluginContext | None = None
    error: str = ""
    load_order: int = 0

    model_config = {"arbitrary_types_allowed": True}

    @property
    def name(self) -> str:
        return str(self.spec.metadata().name)


class PluginError(Exception):
    """Raised when a plugin operation fails (load/start/dependency/etc)."""


class PluginManager:
    """Central registry and lifecycle controller for MAOP plugins.

    Thread-safe: all public methods acquire an internal RLock.  Hook dispatch
    is also guarded so a callback raising will not corrupt the registry.

    Parameters
    ----------
    data_dir : str | Path | None
        Base directory for per-plugin writable state.  Each plugin gets a
        sub-directory ``<data_dir>/<plugin_name>/``.
    host_info : dict
        Read-only metadata exposed to plugins via :class:`PluginContext`.
    strict_api : bool
        If True (default), reject plugins whose ``api_version`` is
        incompatible with :data:`PLUGIN_API_VERSION`.  If False, log a
        warning and proceed.
    """

    def __init__(
        self,
        *,
        data_dir: str | Path | None = None,
        host_info: dict[str, Any] | None = None,
        strict_api: bool = True,
    ) -> None:
        self._data_dir = Path(data_dir) if data_dir else None
        self._host_info = host_info or {}
        self._strict_api = strict_api
        self._plugins: dict[str, PluginRecord] = {}
        self._lock = threading.RLock()
        self._load_counter = 0

    # ── registration ────────────────────────────────────────────────

    def register(self, spec: PluginSpec) -> PluginMetadata:
        """Register a plugin spec without loading it.

        Returns the plugin's metadata.  Raises :class:`PluginError` if a
        plugin with the same name is already registered.
        """
        md = spec.metadata()
        with self._lock:
            if md.name in self._plugins:
                raise PluginError(f"plugin {md.name!r} already registered")
            if self._strict_api and not md.is_api_compatible():
                raise PluginError(
                    f"plugin {md.name!r} api_version {md.api_version!r} "
                    f"incompatible with host {PLUGIN_API_VERSION!r}"
                )
            if not self._strict_api and not md.is_api_compatible():
                logger.warning(
                    "plugin %r api_version %r incompatible with host %r (non-strict)",
                    md.name, md.api_version, PLUGIN_API_VERSION,
                )
            self._plugins[md.name] = PluginRecord(spec=spec)
            logger.debug("registered plugin %r v%s", md.name, md.version)
        return md

    def unregister(self, name: str) -> bool:
        """Remove a plugin from the registry.

        Refuses to unload a plugin that is STARTED or LOADED — caller must
        stop()/unload() first.  Returns True if removed, False if not found
        or not in a removable state.
        """
        with self._lock:
            rec = self._plugins.get(name)
            if rec is None:
                return False
            if rec.state in (PluginState.LOADED, PluginState.STARTED):
                raise PluginError(
                    f"plugin {name!r} is {rec.state.value}; stop() and unload() first"
                )
            del self._plugins[name]
            return True

    # ── lifecycle ───────────────────────────────────────────────────

    def load(self, name: str, config: dict[str, Any] | None = None) -> PluginContext:
        """Load a registered plugin: resolve deps, build context, call on_load.

        Dependencies are loaded recursively (depth-first) so that a plugin's
        deps are always LOADED before the plugin itself.
        """
        with self._lock:
            rec = self._require(name)
            if rec.state == PluginState.LOADED or rec.state == PluginState.STARTED:
                if rec.context is None:
                    raise PluginError(f"plugin {name} context not built")
                return rec.context  # idempotent
            self._load_dependencies(rec)
            ctx = self._build_context(rec, config or {})
            try:
                rec.spec.on_load(ctx)
            except Exception as exc:
                rec.state = PluginState.ERRORED
                rec.error = str(exc)
                logger.exception("on_load failed for plugin %r", name)
                raise PluginError(f"on_load failed for {name!r}: {exc}") from exc
            rec.context = ctx
            rec.state = PluginState.LOADED
            self._load_counter += 1
            rec.load_order = self._load_counter
            logger.info("loaded plugin %r", name)
            return ctx

    def _load_dependencies(self, rec: PluginRecord) -> None:
        md = rec.spec.metadata()
        for dep in md.dependencies:
            dep_rec = self._plugins.get(dep)
            if dep_rec is None:
                raise PluginError(
                    f"plugin {rec.name!r} depends on {dep!r} which is not registered"
                )
            if dep_rec.state not in (PluginState.LOADED, PluginState.STARTED):
                self.load(dep)

    def _build_context(self, rec: PluginRecord, config: dict[str, Any]) -> PluginContext:
        md = rec.spec.metadata()
        defaults = rec.spec.get_config_defaults()
        merged = {**defaults, **config}
        plugin_data_dir = ""
        if self._data_dir is not None:
            plugin_data_dir = str(self._data_dir / md.name)
            Path(plugin_data_dir).mkdir(parents=True, exist_ok=True)
        return PluginContext(
            logger=logging.getLogger(f"maop.plugin.{md.name}"),
            config=merged,
            data_dir=plugin_data_dir,
            host_info=dict(self._host_info),
        )

    def start(self, name: str) -> None:
        """Start a LOADED plugin (calls on_start).  Idempotent if STARTED."""
        with self._lock:
            rec = self._require(name)
            if rec.state == PluginState.STARTED:
                return
            if rec.state != PluginState.LOADED:
                raise PluginError(
                    f"plugin {name!r} must be LOADED to start (is {rec.state.value})"
                )
            try:
                rec.spec.on_start()
            except Exception as exc:
                rec.state = PluginState.ERRORED
                rec.error = str(exc)
                logger.exception("on_start failed for plugin %r", name)
                raise PluginError(f"on_start failed for {name!r}: {exc}") from exc
            rec.state = PluginState.STARTED
            logger.info("started plugin %r", name)

    def stop(self, name: str) -> None:
        """Stop a STARTED plugin (calls on_stop).  Idempotent if STOPPED/LOADED."""
        with self._lock:
            rec = self._require(name)
            if rec.state in (PluginState.LOADED, PluginState.STOPPED, PluginState.UNLOADED):
                return
            if rec.state != PluginState.STARTED:
                raise PluginError(
                    f"plugin {name!r} must be STARTED to stop (is {rec.state.value})"
                )
            try:
                rec.spec.on_stop()
            except Exception as exc:
                rec.state = PluginState.ERRORED
                rec.error = str(exc)
                logger.exception("on_stop failed for plugin %r", name)
                raise PluginError(f"on_stop failed for {name!r}: {exc}") from exc
            rec.state = PluginState.STOPPED
            logger.info("stopped plugin %r", name)

    def unload(self, name: str) -> None:
        """Unload a plugin (stop if running, then on_unload).  Idempotent."""
        with self._lock:
            rec = self._require(name)
            if rec.state in (PluginState.REGISTERED, PluginState.UNLOADED):
                return
            if rec.state == PluginState.STARTED:
                self.stop(name)
            try:
                rec.spec.on_unload()
            except Exception as exc:
                rec.state = PluginState.ERRORED
                rec.error = str(exc)
                logger.exception("on_unload failed for plugin %r", name)
                raise PluginError(f"on_unload failed for {name!r}: {exc}") from exc
            rec.state = PluginState.UNLOADED
            rec.context = None
            logger.info("unloaded plugin %r", name)

    def reload(self, name: str, config: dict[str, Any] | None = None) -> PluginContext:
        """Reload a plugin: unload() then load() with fresh config."""
        with self._lock:
            self.unload(name)
            return self.load(name, config)

    # ── bulk lifecycle ──────────────────────────────────────────────

    def load_all(self) -> list[str]:
        """Load all registered plugins in dependency + priority order.

        Returns the list of plugin names in the order they were loaded.
        """
        with self._lock:
            order = self._resolution_order()
            loaded: list[str] = []
            for name in order:
                rec = self._plugins[name]
                if rec.state not in (PluginState.LOADED, PluginState.STARTED):
                    self.load(name)
                    loaded.append(name)
            return loaded

    def start_all(self) -> list[str]:
        """Start all LOADED plugins in load order.  Returns started names."""
        with self._lock:
            started: list[str] = []
            for name in self._resolution_order():
                rec = self._plugins[name]
                if rec.state == PluginState.LOADED:
                    self.start(name)
                    started.append(name)
            return started

    def stop_all(self) -> list[str]:
        """Stop all STARTED plugins in reverse load order.  Returns stopped names."""
        with self._lock:
            stopped: list[str] = []
            for name in reversed(self._resolution_order()):
                rec = self._plugins[name]
                if rec.state == PluginState.STARTED:
                    self.stop(name)
                    stopped.append(name)
            return stopped

    def unload_all(self) -> list[str]:
        """Unload all loaded plugins in reverse load order.  Returns unloaded names."""
        with self._lock:
            unloaded: list[str] = []
            for name in reversed(self._resolution_order()):
                rec = self._plugins[name]
                if rec.state in (PluginState.LOADED, PluginState.STARTED, PluginState.STOPPED):
                    self.unload(name)
                    unloaded.append(name)
            return unloaded

    def _resolution_order(self) -> list[str]:
        """Topological sort by dependencies, tie-broken by (priority, registration order).

        Using registration order (dict insertion order) as the secondary key
        makes dispatch deterministic when multiple plugins share the same
        priority — earlier-registered plugins are loaded and dispatched first.
        """
        # Preserve registration order (Python 3.7+ dict is insertion-ordered).
        names = list(self._plugins)
        name_set = set(names)
        reg_index = {n: i for i, n in enumerate(names)}
        indeg = {n: 0 for n in names}
        edges: dict[str, list[str]] = {n: [] for n in names}
        for n in names:
            for dep in self._plugins[n].spec.metadata().dependencies:
                if dep in name_set:
                    edges[dep].append(n)
                    indeg[n] += 1

        def _sort_key(n: str) -> tuple[int, int]:
            return (self._plugins[n].spec.metadata().priority, reg_index[n])

        order: list[str] = []
        ready = sorted((n for n in names if indeg[n] == 0), key=_sort_key)
        while ready:
            n = ready.pop(0)
            order.append(n)
            for m in edges[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    ready.append(m)
            ready.sort(key=_sort_key)
        if len(order) != len(names):
            unresolved = name_set - set(order)
            raise PluginError(f"dependency cycle among plugins: {unresolved}")
        return order

    # ── hook dispatch ───────────────────────────────────────────────

    def dispatch(self, hook: HookPoint | str, payload: dict[str, Any]) -> dict[str, Any]:
        """Invoke all hooks registered for *hook* in load order.

        Each callback receives the current payload and may return a transformed
        payload (or None to leave it unchanged).  Exceptions are routed to the
        owning plugin's :meth:`PluginSpec.on_error` and do not abort dispatch
        of subsequent plugins.
        """
        hook_name = hook.value if isinstance(hook, HookPoint) else hook
        result = dict(payload)
        with self._lock:
            for name in self._resolution_order():
                rec = self._plugins[name]
                if rec.state != PluginState.STARTED:
                    continue
                cb = rec.spec.get_hooks().get(hook_name)
                if cb is None:
                    continue
                try:
                    out = cb(result)
                    if out is not None:
                        result = out
                except Exception as exc:
                    rec.state = PluginState.ERRORED
                    rec.error = str(exc)
                    try:
                        rec.spec.on_error(exc)
                    except Exception:
                        logger.exception("on_error raised in plugin %r", name)
        return result

    def hooks_for(self, hook: HookPoint | str) -> list[tuple[str, HookCallback]]:
        """Return ``(plugin_name, callback)`` pairs registered for *hook*."""
        hook_name = hook.value if isinstance(hook, HookPoint) else hook
        with self._lock:
            pairs: list[tuple[str, HookCallback]] = []
            for name in self._resolution_order():
                rec = self._plugins[name]
                if rec.state != PluginState.STARTED:
                    continue
                cb = rec.spec.get_hooks().get(hook_name)
                if cb is not None:
                    pairs.append((name, cb))
            return pairs

    # ── query ───────────────────────────────────────────────────────

    def get(self, name: str) -> PluginSpec | None:
        """Return the spec for *name*, or None if not registered."""
        with self._lock:
            rec = self._plugins.get(name)
            return rec.spec if rec else None

    def state(self, name: str) -> PluginState | None:
        """Return the current state of *name*, or None if not registered."""
        with self._lock:
            rec = self._plugins.get(name)
            return rec.state if rec else None

    def list_plugins(self) -> list[PluginMetadata]:
        """Return metadata for all registered plugins, sorted by load order."""
        with self._lock:
            recs = sorted(self._plugins.values(), key=lambda r: r.load_order)
            return [r.spec.metadata() for r in recs]

    def __len__(self) -> int:
        with self._lock:
            return len(self._plugins)

    def __contains__(self, name: object) -> bool:
        with self._lock:
            return name in self._plugins

    # ── helpers ─────────────────────────────────────────────────────

    def _require(self, name: str) -> PluginRecord:
        rec = self._plugins.get(name)
        if rec is None:
            raise PluginError(f"plugin {name!r} not registered")
        return rec