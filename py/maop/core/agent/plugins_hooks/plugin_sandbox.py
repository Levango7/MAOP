"""MAOP Plugin — Sandbox: restricted execution environment for untrusted plugin code.

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

This module is the sandbox layer of the plugin subsystem and has no
dependency on the manager or hook-declaration layers, avoiding circular
imports.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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


def _mp_init_target(fn: Any, cfg: dict[str, Any], q: Any) -> None:
    """模块级 multiprocessing target（PluginSandbox 的 init 超时沙箱）。

    必须是模块级函数：macOS/Windows 的 multiprocessing 用 spawn 启动子进程，
    target 需可 pickle；局部闭包（此前 _run_init_multiprocess 内的 _target）
    在 spawn 下抛 "Can't pickle local object"（macOS CI 全挂）。
    """
    try:
        fn(cfg)
        q.put(None)
    except Exception as exc:
        q.put(exc)


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
        # 跨平台软链逃逸检测：Windows（py<3.13）的 realpath 不跟随悬空符号
        # 链接（目标不存在时直接返回链接自身路径），会漏检「软链指向
        # plugins/ 之外」的越界（test_symlink_escape_blocked 在 Windows 矩阵
        # 挂掉 + 真实安全缺口）。故手动沿 readlink 链解析目标并逐一校验
        # 是否仍在 plugins_dir 内，再叠加 realpath 终检。
        cur = Path(path)
        seen: set[str] = set()
        while cur.is_symlink():
            target = os.readlink(cur)
            t = Path(target)
            if not t.is_absolute():
                t = cur.parent / target
            cur = Path(os.path.abspath(t))
            key = str(cur)
            if key in seen:
                break
            seen.add(key)
            if not str(cur).startswith(str(self._plugins_dir)):
                raise SandboxViolation(
                    f"Path traversal blocked: {path} escapes plugins directory via symlink"
                )
        resolved = Path(os.path.realpath(cur))
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

        # 关键：spawn（macOS/某些环境默认）需要 pickle target 与 args。
        # 插件 init_fn 是从 importlib 动态加载的模块函数（__module__ 不可重新
        # import），spawn 下永远不可 pickle —— "Can't pickle local object"
        # 或 "module not found"（macOS CI 插件测试全挂的根因）。测试传入
        # lambda/局部函数同样中招。
        # 修复：非 win32 用 fork context —— fork 复制内存，target/args 不
        # 序列化，动态模块函数/lambda/局部函数全部可用。macOS Python 3.12+
        # 对 fork 有 DeprecationWarning（项目未开 -W error，无害）；win32 走
        # _run_init_threaded 分支不经过这里。
        ctx = (
            mp.get_context("fork")
            if "fork" in mp.get_all_start_methods()
            else mp.get_context()
        )
        proc = ctx.Process(  # type: ignore[attr-defined]
            target=_mp_init_target,
            args=(init_fn, config, result_queue),
            daemon=True,
        )
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