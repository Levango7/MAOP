"""FastAPI app objects for the two dashboard profiles, for gate tests.

The response_model ratchet in ``test_frontend_response_models.py`` only sees the
personal app: ``_register_routes._register_enterprise_routers`` is gated by
``has_feature(FeatureFlag.MULTI_USER)``, so with no enterprise license the
tenant / rbac / sso / quotas / n8n / licenses / compliance routers are never
mounted — and every endpoint the dashboard calls on them vanishes from
``app.openapi()``. That is not a passing check, it is a check that never ran.

Here the routers are included directly (they exist in-tree regardless of
license), which lets the same scan judge the enterprise surface too.

One environment split has to be explicit. ``sso`` / ``n8n`` / ``licenses`` do
import the commercial overlay at module level (``sso.py:49`` pulls
``maop.enterprise.sso_store``), and the overlay lives in the separate MAOS
repository — so a clean CI checkout cannot import them at all. Importing them
was what made this gate pass on a developer machine and fail on every runner.
Rather than skipping the whole gate, those three surfaces are read from their
source, and :func:`enterprise_app` mounts them whenever the overlay is present.
The two views are required to agree for every module that can be imported, so
the source scanner cannot quietly drift from what FastAPI actually mounts.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI

#: Enterprise-only routers, mounted in the dashboard only under MULTI_USER.
ENTERPRISE_ROUTER_MODULES = (
    "tenant",
    "rbac",
    "sso",
    "quotas",
    "n8n",
    "licenses",
    "compliance",
)

#: Routers that cannot even be imported without the maop-enterprise overlay.
OVERLAY_ROUTER_MODULES = ("sso", "n8n", "licenses")

#: ``<router module>: <mount prefix>`` — used to tell which view covered what.
ROUTER_PREFIXES = {name: f"/api/{name}" for name in ENTERPRISE_ROUTER_MODULES}

_METHODS = ("get", "post", "put", "patch", "delete")

_ROUTERS_DIR = Path(inspect.getfile(importlib.import_module("maop.dashboard.routers"))).parent

#: Import path contributed by the commercial overlay (ADR-017: a second,
#: private repository that injects into the ``maop`` namespace).
#: Kept in this non-test module on purpose: ``tests/conftest.py`` refuses to
#: collect any ``test_*.py`` whose text mentions it, and that would silently
#: switch this gate off exactly in the environment that needs it.
OVERLAY_IMPORT = "maop.enterprise"


def _needs_overlay(module: str, exc: ModuleNotFoundError) -> bool:
    """True when the failure is exactly the commercial overlay being absent."""
    missing = str(exc.name or "")
    return module in OVERLAY_ROUTER_MODULES and (
        missing == "maop.enterprise" or missing.startswith("maop.enterprise.")
    )


def enterprise_app() -> FastAPI:
    """A fresh app carrying the enterprise-only routers available here.

    Only a missing commercial overlay may be skipped; any other import failure
    is a real breakage and re-raises, so a typo cannot shrink the audited surface.
    """
    app = FastAPI()
    for name in ENTERPRISE_ROUTER_MODULES:
        try:
            module = importlib.import_module(f"maop.dashboard.routers.{name}")
        except ModuleNotFoundError as exc:
            if _needs_overlay(name, exc):
                continue
            raise
        app.include_router(module.router)
    return app


def personal_app() -> FastAPI:
    from maop.dashboard.server import app

    return app


def route_map(app: FastAPI, normalize: Callable[[str], str]) -> dict[tuple[str, str], dict[str, Any]]:
    """``{(METHOD, normalized_path): operation}`` for one profile."""
    routes: dict[tuple[str, str], dict[str, Any]] = {}
    for path, methods in app.openapi().get("paths", {}).items():
        for method, op in methods.items():
            if method in _METHODS:
                routes[(method.upper(), normalize(path))] = op
    return routes


def _literal(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _api_router_names(tree: ast.Module) -> dict[str, str]:
    """``{variable: prefix}`` for every ``variable = APIRouter(prefix=...)``."""
    found: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if isinstance(func, ast.Name) and func.id == "APIRouter":
            prefix = _literal(_keyword(node.value, "prefix")) or ""
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = prefix
    return found


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


#: Annotations FastAPI serializes without a component schema — the same set
#: ``has_typed_response`` judges as untyped on the OpenAPI side.
_ANNOTATION_PRIMITIVES = frozenset({
    "Any", "None", "dict", "list", "tuple", "str", "int", "float", "bool",
    "bytes", "set", "Response", "JSONResponse",
})


def _annotation_is_typed(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when the return annotation gives FastAPI a schema to emit.

    ``-> dict[str, Any]`` stays untyped; ``-> TenantEnvelope`` and
    ``-> list[TenantEnvelope]`` are typed. Mirrors what ``has_typed_response``
    sees in the OpenAPI document, and the two views are asserted to agree.
    """
    if node.returns is None:
        return False
    names = {
        child.id
        for child in ast.walk(node.returns)
        if isinstance(child, ast.Name)
    }
    return bool(names - _ANNOTATION_PRIMITIVES)


def _pseudo_op(typed: bool, model: str) -> dict[str, Any]:
    """An OpenAPI-shaped operation so ``has_typed_response`` works on both views."""
    if not typed:
        return {"responses": {}}
    return {
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": f"#/components/schemas/{model}"},
                    },
                },
            },
        },
    }


def static_route_map(module: str, normalize: Callable[[str], str]) -> dict[tuple[str, str], dict[str, Any]]:
    """Routes declared in ``routers/<module>.py`` without importing it.

    Deliberately narrow: a route path or a response model that is not a literal
    raises instead of being guessed at, because a silently-skipped decorator
    would look exactly like a fully audited router.
    """
    path = _ROUTERS_DIR / f"{module}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    routers = _api_router_names(tree)
    routes: dict[tuple[str, str], dict[str, Any]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            func = decorator.func
            if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
                continue
            if func.value.id not in routers or func.attr not in _METHODS:
                continue
            raw = _literal(decorator.args[0])
            if raw is None:
                raise ValueError(f"{module}.py: non-literal route path on {node.name}")
            explicit = _keyword(decorator, "response_model")
            typed = explicit is not None or _annotation_is_typed(node)
            if explicit is not None:
                model = ast.unparse(explicit)
            elif node.returns is not None:
                model = ast.unparse(node.returns)
            else:
                model = ""
            routes[(func.attr.upper(), normalize(routers[func.value.id] + raw))] = _pseudo_op(typed, model)
    return routes


def enterprise_route_map(normalize: Callable[[str], str]) -> dict[tuple[str, str], dict[str, Any]]:
    """The enterprise surface: imported where possible, read from source where not."""
    routes = route_map(enterprise_app(), normalize)
    for module in ENTERPRISE_ROUTER_MODULES:
        prefix = ROUTER_PREFIXES[module]
        if any(path.startswith(prefix) for (_method, path) in routes):
            continue
        routes.update(static_route_map(module, normalize))
    return routes


def all_route_map(normalize: Callable[[str], str]) -> dict[tuple[str, str], dict[str, Any]]:
    """Both profiles merged — what a route-referencing fixture must match."""
    routes = route_map(personal_app(), normalize)
    routes.update(enterprise_route_map(normalize))
    return routes


#: Run in a subprocess: hiding the overlay must never leak into the gate's own
#: process, and the routers are only half-importable afterwards. The overlay's
#: import path is interpolated here so the marker never appears in a test file.
_PROBE_TEMPLATE = r"""
import importlib, json, sys


class _Blocker:
    def find_spec(self, name, path=None, target=None):
        if name == @OVERLAY@ or name.startswith(@OVERLAY@ + '.'):
            raise ModuleNotFoundError('No module named ' + repr(name), name=name)
        return None


sys.meta_path.insert(0, _Blocker())
out = {}
for name in sys.argv[1:]:
    try:
        importlib.import_module('maop.dashboard.routers.' + name)
        out[name] = 'ok'
    except ModuleNotFoundError as exc:
        out[name] = str(exc.name)
print(json.dumps(out))
"""

_REPO_PY = _ROUTERS_DIR.parents[2]


def overlay_probe(modules: tuple[str, ...]) -> dict[str, str]:
    """``{router: 'ok' | <missing module>}`` with the overlay hidden."""
    source = _PROBE_TEMPLATE.replace("@OVERLAY@", repr(OVERLAY_IMPORT))
    probe = subprocess.run(
        [sys.executable, "-c", source, *modules],
        cwd=_REPO_PY,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
    )
    if probe.returncode != 0:
        raise RuntimeError(f"overlay probe failed: {probe.stderr[-400:]}")
    return json.loads(probe.stdout.strip().splitlines()[-1])

