"""Every int query parameter on a route must declare a bound.

Why
---
`limit: int = 100` in a handler *is* a query parameter to FastAPI, so a caller can
send `limit=1000000000` and the handler forwards it straight into
`SELECT ... LIMIT ?` / a time window. Bounds are the difference between a
pagination parameter and a self-inflicted DoS knob.

Scope decisions
---------------
* Only parameters **with a default** are checked — those are query parameters.
  A bare `provider_id: int` with no default is a path parameter: its magnitude is
  not caller-selectable in the same way, and the handler looks it up by identity.
* The ceiling values are absurd-value guards, not product limits (e.g.
  `le=87_600` hours is ten years). Refusing a number that large costs nobody a
  real query; refusing nothing costs the process memory.
"""

from __future__ import annotations

import ast
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "maop" / "dashboard"
ROUTE_METHODS = {"get", "post", "put", "delete", "patch"}
_BOUND_KEYS = ("ge=", "le=", "gt=", "lt=")


def _route_handlers(tree: ast.Module) -> list[ast.AST]:
    out: list[ast.AST] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for dec in node.decorator_list:
            func = dec.func if isinstance(dec, ast.Call) else dec
            name = getattr(func, "attr", "")
            if name in ROUTE_METHODS:
                out.append(node)
                break
    return out


def _unbounded_params() -> list[str]:
    offenders: list[str] = []
    for path in sorted(DASHBOARD.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        rel = path.relative_to(DASHBOARD).as_posix()
        for fn in _route_handlers(tree):
            defaults = [None] * (len(fn.args.posonlyargs + fn.args.args)
                                 - len(fn.args.defaults)) + fn.args.defaults
            for arg, default in zip(fn.args.posonlyargs + fn.args.args, defaults):
                if default is None:
                    continue  # path parameter
                annotation = ast.unparse(arg.annotation) if arg.annotation else ""
                if annotation not in {"int", "Optional[int]", "int | None"}:
                    continue
                src = ast.unparse(default)
                if "Query(" in src and any(k in src for k in _BOUND_KEYS):
                    continue
                offenders.append(f"{rel}:{fn.lineno} {fn.name}({arg.arg} = {src})")
            for arg, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
                if default is None:
                    continue
                annotation = ast.unparse(arg.annotation) if arg.annotation else ""
                if annotation not in {"int", "Optional[int]", "int | None"}:
                    continue
                src = ast.unparse(default)
                if "Query(" in src and any(k in src for k in _BOUND_KEYS):
                    continue
                offenders.append(f"{rel}:{fn.lineno} {fn.name}({arg.arg} = {src})")
    return offenders


def test_int_query_params_are_bounded() -> None:
    offenders = _unbounded_params()
    assert not offenders, (
        "int query parameters without ge/le bounds "
        "(caller can send an unbounded magnitude): "
        + "; ".join(offenders)
    )


def test_gate_is_not_blind() -> None:
    """The scan must actually see bounded params — an empty dashboard or a
    broken walk would pass the test above vacuously."""
    bounded = 0
    for path in sorted(DASHBOARD.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for fn in _route_handlers(tree):
            for default in fn.args.defaults:
                src = ast.unparse(default)
                if "Query(" in src and any(k in src for k in _BOUND_KEYS):
                    bounded += 1
    assert bounded >= 20, f"only {bounded} bounded int query params found — scanner is blind"
