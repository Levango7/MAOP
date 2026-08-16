"""MAOP Plugin System — Discovery, loading, lifecycle, and Hook extension points.

Provides:
  - PluginManifest: Pydantic model for plugin metadata (MAOP-plugin.yaml)
  - PluginState: lifecycle states (discovered/loaded/started/stopped/errored)
  - PluginSandbox: restricted execution environment for untrusted plugin code
  - PluginManager: discover/load/start/stop/reload plugins
  - Extension points via HookManager integration

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

Plugin directory layout::

    plugins/
      my_plugin/
        MAOP-plugin.yaml   # manifest
        main.py           # entry point (must export MAOP_plugin_init)
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class PluginState(str, Enum):
    DISCOVERED = "discovered"
    LOADED = "loaded"
    STARTED = "started"
    STOPPED = "stopped"
    ERRORED = "errored"


class PluginManifest(BaseModel):
    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    entry_point: str = "main.py"
    init_function: str = "MAOP_plugin_init"
    hooks: list[dict[str, Any]] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    checksum: str = ""
    allowed_imports: list[str] = Field(default_factory=list)
    timeout_seconds: float = 30.0


# ── Sandbox: builtins whitelist + dunder AST scan ──────────────────────
#
# Defense in depth against sandbox escape:
#   1. Static AST scan rejects forbidden dunder attribute access
#      (__class__, __subclasses__, __globals__, __code__, ...) before the
#      plugin source ever executes — blocks the classic
#      ``().__class__.__bases__[0].__subclasses__()`` escape chain.
#   2. Runtime __builtins__ *whitelist*: only explicitly-listed safe names
#      are exposed; dangerous builtins (globals, locals, getattr, type,
#      vars, dir, eval, exec, compile, open, __import__, ...) are either
#      omitted or replaced with stubs that raise SandboxViolation.
#   3. Import guard: __import__ is wrapped to only allow whitelisted
#      top-level modules.
#   4. Wall-clock timeout on init functions (threaded on Windows,
#      multiprocessing on POSIX).

# Builtins exposed to plugin code (pure whitelist — anything not listed
# here is unavailable).  Chosen to be sufficient for ordinary data
# processing without enabling sandbox escape.
_SAFE_BUILTIN_NAMES = frozenset({
    # ── types ──
    "str", "int", "float", "bool", "list", "dict", "tuple", "set",
    "frozenset", "bytes", "complex", "range", "slice",
    # ── functional ──
    "print", "len", "enumerate", "zip", "map", "filter", "sorted",
    "reversed", "isinstance", "abs", "min", "max", "sum", "any", "all",
    "round", "pow", "divmod", "hash", "repr", "format",
    "chr", "ord", "hex", "oct", "bin", "ascii", "callable",
    "iter", "next",
    # ── exceptions (plugins may raise standard exceptions) ──
    "BaseException", "Exception", "ArithmeticError", "AssertionError",
    "AttributeError", "BlockingIOError", "BrokenPipeError",
    "BytesWarning", "ChildProcessError", "ConnectionAbortedError",
    "ConnectionError", "ConnectionRefusedError", "ConnectionResetError",
    "DeprecationWarning", "EOFError", "EnvironmentError",
    "FileExistsError", "FileNotFoundError", "FloatingPointError",
    "FutureWarning", "GeneratorExit", "IOError", "ImportError",
    "IndentationError", "IndexError", "InterruptedError",
    "IsADirectoryError", "KeyError", "KeyboardInterrupt",
    "LookupError", "MemoryError", "ModuleNotFoundError",
    "NameError", "NotADirectoryError", "NotImplementedError",
    "OSError", "OverflowError", "PendingDeprecationWarning",
    "PermissionError", "ProcessLookupError", "RecursionError",
    "ReferenceError", "ResourceWarning", "RuntimeError", "RuntimeWarning",
    "StopAsyncIteration", "StopIteration", "SyntaxError", "SyntaxWarning",
    "SystemError", "SystemExit", "TabError", "TimeoutError",
    "TypeError", "UnboundLocalError", "UnicodeDecodeError",
    "UnicodeEncodeError", "UnicodeError", "UnicodeTranslateError",
    "UnicodeWarning", "UserWarning", "ValueError", "Warning",
    "ZeroDivisionError",
})

# Safe builtin constants (singletons exposed as names).
_SAFE_BUILTIN_CONSTS: dict[str, object] = {
    "True": True,
    "False": False,
    "None": None,
    "NotImplemented": NotImplemented,
    "Ellipsis": ...,
}

# Builtins replaced with stubs that raise SandboxViolation (kept in the
# namespace so plugins get a clear error instead of a bare NameError).
# Note: ``__import__`` is handled separately via a guarded wrapper.
_DANGEROUS_BUILTINS = frozenset({
    # code execution / IO
    "exec", "eval", "compile", "open", "breakpoint", "input",
    "exit", "quit", "help", "copyright", "credits", "license",
    # attribute reflection → enables sandbox escape
    "getattr", "setattr", "delattr", "hasattr",
    # namespace introspection → leaks real builtins
    "globals", "locals", "vars", "dir",
    # type / inheritance → enables __subclasses__ walk
    "type", "object", "super",
    # descriptors / metaclass helpers
    "staticmethod", "classmethod", "property",
    # mutable binary / memory view
    "memoryview", "bytearray",
})

# Dunder attribute names rejected by the static AST scan.  Accessing any
# of these as an attribute (``obj.__class__``) is blocked at parse time,
# preventing the well-known ``().__class__.__bases__[0].__subclasses__()``
# escape chain and its variants.
_DUNDER_DENYLIST = frozenset({
    # inheritance / type graph
    "__class__", "__bases__", "__base__", "__mro__", "__subclasses__",
    # namespace leakage
    "__globals__", "__dict__", "__builtins__", "__closure__", "__code__",
    # module loader state
    "__loader__", "__spec__", "__import__", "__path__", "__file__",
    # pickle / copy (can trigger arbitrary code)
    "__reduce__", "__reduce_ex__", "__getstate__", "__setstate__",
    "__getnewargs__", "__getinitargs__", "__deepcopy__", "__copy__",
    # attribute descriptors
    "__getattribute__", "__getattr__", "__setattr__", "__delattr__",
    # bound-method internals
    "__self__", "__func__", "__wrapped__",
    # metaclass hooks
    "__instancecheck__", "__subclasscheck__", "__subclasshook__",
    "__init_subclass__", "__class_getitem__",
    # descriptor owner / formatting / memory
    "__objclass__", "__format__", "__buffer__", "__sizeof__",
    # reflective naming
    "__module__", "__qualname__", "__defaults__", "__kwdefaults__",
    # exception chain (can leak traceback frames)
    "__traceback__", "__context__", "__cause__",
})

_DEFAULT_ALLOWED_IMPORTS = frozenset({
    "json", "math", "re", "datetime", "collections",
    "itertools", "functools", "operator", "copy",
    "string", "textwrap", "uuid", "hashlib",
    "base64", "decimal", "fractions", "statistics",
    "dataclasses", "typing", "enum", "time",
})


class SandboxViolation(Exception):
    """Raised when plugin code attempts a blocked operation."""


class PluginSandbox:
    """Restricted execution environment for plugin code.

    - Strips dangerous builtins (exec, eval, open, __import__, etc.)
    - Provides a custom ``__import__`` that only allows whitelisted modules
    - Validates that loaded files reside under the plugins directory
    - Optionally verifies SHA-256 checksums
    - Enforces a wall-clock timeout on init function calls
    """

    def __init__(
        self,
        plugins_dir: Path,
        allowed_imports: frozenset[str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._plugins_dir = plugins_dir.resolve()
        self._allowed_imports = allowed_imports or _DEFAULT_ALLOWED_IMPORTS
        self._timeout = timeout_seconds

    def validate_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if not str(resolved).startswith(str(self._plugins_dir)):
            raise SandboxViolation(
                f"Path traversal blocked: {path} is outside plugins directory"
            )
        return resolved

    def verify_checksum(self, path: Path, expected: str) -> None:
        if not expected:
            return
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if sha != expected:
            raise SandboxViolation(
                f"Checksum mismatch for {path.name}: expected {expected[:16]}..., got {sha[:16]}..."
            )

    def _make_safe_builtins(self, allowed_imports: frozenset[str]) -> dict[str, Any]:
        import builtins as _builtins

        # Pure whitelist: only explicitly-listed safe names are exposed.
        # Anything not in _SAFE_BUILTIN_NAMES / _SAFE_BUILTIN_CONSTS is
        # simply absent from the plugin namespace.
        safe: dict[str, Any] = {}
        for name in _SAFE_BUILTIN_NAMES:
            val = getattr(_builtins, name, None)
            if val is not None:
                safe[name] = val
        safe.update(_SAFE_BUILTIN_CONSTS)

        # Controlled __import__: only whitelisted top-level modules.
        def _guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
            top_level = name.split(".")[0]
            if top_level not in allowed_imports:
                raise SandboxViolation(
                    f"Import blocked: '{name}' is not in the allowed list"
                )
            return __import__(name, *args, **kwargs)

        safe["__import__"] = _guarded_import

        # Stubs for dangerous builtins — raise SandboxViolation with a
        # clear, name-specific message so plugin authors get actionable
        # diagnostics instead of a bare NameError.
        def _make_blocker(name: str) -> Any:
            def _raise(*args: Any, **kwargs: Any) -> Any:
                raise SandboxViolation(
                    f"{name}() is not allowed in plugin sandbox"
                )
            return _raise

        for danger in _DANGEROUS_BUILTINS:
            safe[danger] = _make_blocker(danger)

        return safe

    def _scan_source(self, path: Path) -> None:
        """Static AST scan: reject forbidden dunder attribute access.

        Blocks known sandbox-escape primitives such as ``__class__``,
        ``__subclasses__``, ``__globals__``, ``__code__`` before the
        plugin code ever executes.  This is a defense-in-depth layer on
        top of the runtime ``__builtins__`` whitelist — it catches the
        classic ``().__class__.__bases__[0].__subclasses__()`` chain
        which does not rely on any builtin function.
        """
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SandboxViolation(
                f"Cannot read plugin source {path}: {exc}"
            ) from exc
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            raise SandboxViolation(
                f"Plugin source has invalid syntax: {exc}"
            ) from exc

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in _DUNDER_DENYLIST:
                raise SandboxViolation(
                    f"Forbidden attribute access '.{node.attr}' at "
                    f"{path}:{node.lineno} — sandbox escape primitive blocked"
                )

    def create_restricted_module(self, module_name: str, path: Path) -> Any:
        path = self.validate_path(path)
        # Defense-in-depth: statically reject forbidden dunder attribute
        # access (e.g. ``().__class__.__bases__[0].__subclasses__()``)
        # before the plugin source is compiled and executed.
        self._scan_source(path)
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec from {path}")
        module = importlib.util.module_from_spec(spec)
        module.__builtins__ = self._make_safe_builtins(self._allowed_imports)  # type: ignore
        return module, spec

    def exec_module(self, module: Any, spec: Any) -> None:
        spec.loader.exec_module(module)

    def run_init_with_timeout(
        self, init_fn: Any, config: dict[str, Any], timeout: float | None = None
    ) -> None:
        deadline = timeout or self._timeout
        if deadline <= 0:
            init_fn(config)
            return

        import sys
        if sys.platform == "win32":
            self._run_init_threaded(init_fn, config, deadline)
        else:
            self._run_init_multiprocess(init_fn, config, deadline)

    def _run_init_threaded(self, init_fn: Any, config: dict[str, Any], deadline: float) -> None:
        import threading
        result: dict[str, Any] = {"exc": None}

        def _target() -> None:
            try:
                init_fn(config)
            except Exception as exc:
                result["exc"] = exc

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout=deadline)
        if t.is_alive():
            raise SandboxViolation(f"Plugin init exceeded {deadline}s timeout")
        if result["exc"] is not None:
            raise result["exc"]

    def _run_init_multiprocess(self, init_fn: Any, config: dict[str, Any], deadline: float) -> None:
        import multiprocessing as mp
        result_queue: mp.Queue = mp.Queue()

        def _target(fn, cfg, q):
            try:
                fn(cfg)
                q.put(None)
            except Exception as exc:
                q.put(exc)

        proc = mp.Process(target=_target, args=(init_fn, config, result_queue), daemon=True)
        proc.start()
        proc.join(timeout=deadline)
        if proc.is_alive():
            proc.kill()
            proc.join()
            raise SandboxViolation(
                f"Plugin init exceeded {deadline}s timeout"
            )
        if not result_queue.empty():
            exc = result_queue.get_nowait()
            if exc is not None:
                raise exc


class PluginInfo(BaseModel):
    id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    state: PluginState = PluginState.DISCOVERED
    path: str = ""
    error: str = ""
    loaded_at: str = ""
    started_at: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


class PluginManager:
    """Plugin lifecycle manager with discovery, loading, and Hook integration.

    Features:
      - Auto-discover plugins from a plugins/ directory
      - Load plugins via importlib (sandboxed entry point)
      - Start/stop lifecycle with init function
      - Hook registration bridge (plugin hooks → HookManager)
      - SQLite persistence for plugin state
      - Config per-plugin with schema validation
    """

    def __init__(self, root_dir: str | Path = "data", hook_manager: Any = None, sandbox_enabled: bool = True) -> None:
        self._root = Path(root_dir)
        self._plugins_dir = self._root / "plugins"
        self._db_path = get_db_path("plugin")
        self._hook_manager = hook_manager
        self._loaded: dict[str, Any] = {}
        self._manifests: dict[str, PluginManifest] = {}
        self._sandbox_enabled = sandbox_enabled
        self._sandbox = PluginSandbox(self._plugins_dir)
        self._init_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS plugins (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT DEFAULT '0.1.0',
                    description TEXT DEFAULT '',
                    author TEXT DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'discovered',
                    path TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    loaded_at TEXT DEFAULT '',
                    started_at TEXT DEFAULT '',
                    config TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_plugins_state
                ON plugins(state)
            """)

    # ── Discovery ───────────────────────────────────────────────

    def discover(self) -> list[PluginInfo]:
        """Scan plugins/ directory for valid plugin manifests."""
        self._plugins_dir.mkdir(parents=True, exist_ok=True)
        found = []
        for plugin_path in sorted(self._plugins_dir.iterdir()):
            if not plugin_path.is_dir():
                continue
            manifest_path = plugin_path / "MAOP-plugin.yaml"
            if not manifest_path.exists():
                continue
            try:
                manifest = self._load_manifest(manifest_path)
                info = PluginInfo(
                    id=self._plugin_id(manifest.name, plugin_path),
                    name=manifest.name,
                    version=manifest.version,
                    description=manifest.description,
                    author=manifest.author,
                    state=PluginState.DISCOVERED,
                    path=str(plugin_path),
                )
                self._manifests[info.id] = manifest
                self._upsert_db(info)
                found.append(info)
            except Exception as exc:
                logger.warning("[plugin] Failed to parse manifest %s: %s", manifest_path, exc)
                pid = plugin_path.name
                info = PluginInfo(id=pid, name=pid, state=PluginState.ERRORED, path=str(plugin_path), error=str(exc))
                self._upsert_db(info)
                found.append(info)
        return found

    def _load_manifest(self, path: Path) -> PluginManifest:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return PluginManifest(**data)

    @staticmethod
    def _plugin_id(name: str, path: Path) -> str:
        slug = name.lower().replace(" ", "-").replace("_", "-")
        return f"plug-{slug}-{uuid.uuid4().hex[:6]}"

    # ── Loading ─────────────────────────────────────────────────

    def load(self, plugin_id: str) -> PluginInfo:
        """Load a discovered plugin by importing its entry point."""
        info = self.get_plugin(plugin_id)
        if info is None:
            raise ValueError(f"Plugin '{plugin_id}' not found")
        if info.state == PluginState.LOADED:
            return info
        manifest = self._manifests.get(plugin_id)
        if manifest is None:
            raise ValueError(f"Manifest for '{plugin_id}' not found")

        entry_path = Path(info.path) / manifest.entry_point
        if not entry_path.exists():
            info.state = PluginState.ERRORED
            info.error = f"Entry point not found: {manifest.entry_point}"
            self._upsert_db(info)
            return info

        try:
            module = self._import_module(plugin_id, entry_path, manifest)
            init_fn = getattr(module, manifest.init_function, None)
            if init_fn is None:
                info.state = PluginState.ERRORED
                info.error = f"Init function '{manifest.init_function}' not found in {manifest.entry_point}"
                self._upsert_db(info)
                return info

            self._loaded[plugin_id] = {"module": module, "init_fn": init_fn, "shutdown_fn": getattr(module, "MAOP_plugin_shutdown", None)}
            info.state = PluginState.LOADED
            info.loaded_at = datetime.now(timezone.utc).isoformat()
            info.error = ""
            self._upsert_db(info)

            if manifest.hooks and self._hook_manager:
                self._register_plugin_hooks(plugin_id, manifest)

            logger.info("[plugin] Loaded '%s' v%s", manifest.name, manifest.version)
            return info
        except Exception as exc:
            info.state = PluginState.ERRORED
            info.error = str(exc)
            self._upsert_db(info)
            logger.error("[plugin] Failed to load '%s': %s", plugin_id, exc)
            return info

    def _import_module(self, plugin_id: str, path: Path, manifest: PluginManifest | None = None) -> Any:
        module_name = f"MAOP_plugin_{plugin_id.replace('-', '_')}"

        if self._sandbox_enabled:
            allowed = _DEFAULT_ALLOWED_IMPORTS
            if manifest and manifest.allowed_imports:
                allowed = _DEFAULT_ALLOWED_IMPORTS | frozenset(manifest.allowed_imports)
            sandbox = PluginSandbox(
                self._plugins_dir,
                allowed_imports=allowed,
                timeout_seconds=manifest.timeout_seconds if manifest else 30.0,
            )
            if manifest and manifest.checksum:
                sandbox.verify_checksum(path, manifest.checksum)
            elif manifest and not manifest.checksum:
                # Default: strict (fail-closed). Set MAOP_PLUGIN_STRICT_CHECKSUM=0
                # in dev to allow unverified plugins with a warning.
                strict = os.environ.get("MAOP_PLUGIN_STRICT_CHECKSUM", "1") != "0"
                if strict:
                    raise SandboxViolation(
                        f"Plugin '{manifest.name or path.name}' rejected: "
                        "checksum is mandatory in manifest (SHA-256 of entry_point file). "
                        "Set MAOP_PLUGIN_STRICT_CHECKSUM=0 to allow in dev."
                    )
                logger.warning(
                    "Plugin '%s' loaded without checksum verification (dev mode). "
                    "Set MAOP_PLUGIN_STRICT_CHECKSUM=1 (or unset) for production.",
                    manifest.name or path.name,
                )
            module, spec = sandbox.create_restricted_module(module_name, path)
            sandbox.exec_module(module, spec)
            return module

        spec = importlib.util.spec_from_file_location(module_name, str(path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _register_plugin_hooks(self, plugin_id: str, manifest: PluginManifest) -> None:
        if self._hook_manager is None:
            return
        for hook_cfg in manifest.hooks:
            event = hook_cfg.get("event", "")
            callback_name = hook_cfg.get("callback", "")
            if not event:
                continue
            loaded = self._loaded.get(plugin_id)
            if loaded is None:
                continue
            cb = getattr(loaded["module"], callback_name, None)
            if cb is None:
                logger.warning("[plugin] Hook callback '%s' not found in plugin '%s'", callback_name, plugin_id)
                continue
            self._hook_manager.register(
                event=event,
                callback=cb,
                priority=hook_cfg.get("priority", 0),
                description=f"Plugin: {manifest.name}",
                source="plugin",
            )

    # ── Lifecycle ───────────────────────────────────────────────

    def start(self, plugin_id: str, config: dict[str, Any] | None = None) -> PluginInfo:
        """Start a loaded plugin by calling its init function."""
        info = self.get_plugin(plugin_id)
        if info is None:
            raise ValueError(f"Plugin '{plugin_id}' not found")
        if info.state == PluginState.STARTED:
            return info
        if info.state != PluginState.LOADED:
            raise ValueError(f"Plugin '{plugin_id}' must be loaded first (current: {info.state})")

        loaded = self._loaded.get(plugin_id)
        if loaded is None:
            raise ValueError(f"Plugin '{plugin_id}' not in loaded registry")

        try:
            plugin_config = config or info.config
            if self._sandbox_enabled:
                manifest = self._manifests.get(plugin_id)
                timeout = manifest.timeout_seconds if manifest else 30.0
                self._sandbox.run_init_with_timeout(loaded["init_fn"], plugin_config, timeout=timeout)
            else:
                loaded["init_fn"](plugin_config)
            info.state = PluginState.STARTED
            info.started_at = datetime.now(timezone.utc).isoformat()
            info.config = plugin_config
            info.error = ""
            self._upsert_db(info)
            logger.info("[plugin] Started '%s'", plugin_id)
            return info
        except Exception as exc:
            info.state = PluginState.ERRORED
            info.error = str(exc)
            self._upsert_db(info)
            logger.error("[plugin] Failed to start '%s': %s", plugin_id, exc)
            return info

    def stop(self, plugin_id: str) -> PluginInfo:
        """Stop a running plugin by calling its shutdown function."""
        info = self.get_plugin(plugin_id)
        if info is None:
            raise ValueError(f"Plugin '{plugin_id}' not found")
        if info.state != PluginState.STARTED:
            return info

        loaded = self._loaded.get(plugin_id)
        if loaded and loaded["shutdown_fn"]:
            try:
                loaded["shutdown_fn"]()
            except Exception as exc:
                logger.warning("[plugin] Shutdown error for '%s': %s", plugin_id, exc)

        info.state = PluginState.STOPPED
        info.started_at = ""
        self._upsert_db(info)
        logger.info("[plugin] Stopped '%s'", plugin_id)
        return info

    def reload(self, plugin_id: str) -> PluginInfo:
        """Stop, re-load, and re-start a plugin."""
        info = self.get_plugin(plugin_id)
        if info is None:
            raise ValueError(f"Plugin '{plugin_id}' not found")
        if info.state == PluginState.STARTED:
            self.stop(plugin_id)
        if plugin_id in self._loaded:
            del self._loaded[plugin_id]
        info.state = PluginState.DISCOVERED
        self._upsert_db(info)
        self.load(plugin_id)
        return self.start(plugin_id, config=info.config)

    def load_all(self) -> list[PluginInfo]:
        """Discover and load all plugins."""
        discovered = self.discover()
        results = []
        for info in discovered:
            if info.state == PluginState.ERRORED:
                results.append(info)
                continue
            results.append(self.load(info.id))
        return results

    def start_all(self) -> list[PluginInfo]:
        """Start all loaded plugins."""
        return [self.start(info.id) for info in self.list_plugins(state=PluginState.LOADED)]

    def stop_all(self) -> list[PluginInfo]:
        """Stop all running plugins."""
        return [self.stop(info.id) for info in self.list_plugins(state=PluginState.STARTED)]

    # ── Query ───────────────────────────────────────────────────

    def list_plugins(self, state: PluginState | None = None) -> list[PluginInfo]:
        with sqlite_connect(self._db_path) as conn:
            if state:
                rows = conn.execute("SELECT * FROM plugins WHERE state=? ORDER BY name", (state.value,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM plugins ORDER BY name").fetchall()
        return [self._row_to_info(r) for r in rows]

    def get_plugin(self, plugin_id: str) -> PluginInfo | None:
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute("SELECT * FROM plugins WHERE id=?", (plugin_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_info(row)

    # ── Config ──────────────────────────────────────────────────

    def update_config(self, plugin_id: str, config: dict[str, Any]) -> PluginInfo:
        info = self.get_plugin(plugin_id)
        if info is None:
            raise ValueError(f"Plugin '{plugin_id}' not found")
        info.config = config
        self._upsert_db(info)
        return info

    # ── Internal ────────────────────────────────────────────────

    def _upsert_db(self, info: PluginInfo) -> None:
        import json
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO plugins
                   (id, name, version, description, author, state, path, error, loaded_at, started_at, config)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (info.id, info.name, info.version, info.description, info.author,
                 info.state.value, info.path, info.error, info.loaded_at, info.started_at,
                 json.dumps(info.config)),
            )

    @staticmethod
    def _row_to_info(row: sqlite3.Row) -> PluginInfo:
        import json
        config: dict[str, Any] = {}
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            config = json.loads(row["config"]) if row["config"] else {}
        return PluginInfo(
            id=row["id"], name=row["name"], version=row["version"],
            description=row["description"], author=row["author"],
            state=PluginState(row["state"]), path=row["path"],
            error=row["error"], loaded_at=row["loaded_at"],
            started_at=row["started_at"], config=config,
        )
