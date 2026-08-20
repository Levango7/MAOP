"""MAOP Engine — Utility functions (safe eval, template resolution, topo sort).

Extracted from ``engine.py`` (Phase 3-1 module split) to isolate pure
helper functions that have no dependency on the ``Engine`` class.

Contents:
    - safe_eval / _safe_eval_node / _reject_unsafe_value — AST-based safe
      expression evaluator (no builtins, no code execution).
    - _resolve_template — ``{{ key }}`` placeholder substitution.
    - _topological_sort — Kahn's algorithm with layering for DAG execution.
    - _find_step — step lookup by ID with fallback.
    - json_dumps_safe — non-serializable-tolerant JSON dumps.

Dependency: imports ``WorkflowStep`` from ``engine_types`` at runtime
because ``_find_step`` constructs a fallback ``WorkflowStep``. This is a
single-directional dependency (engine_utils → engine_types); no cycle.
"""

from __future__ import annotations

import ast
import json
from collections import defaultdict
from collections.abc import Callable
from typing import Any, cast

from maop.engine_types import WorkflowStep

# ── Safe expression evaluator (replaces eval) ─────────────────────
_SAFE_BINOPS: dict[type[ast.AST], Callable[[Any, Any], Any]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}
_SAFE_CMPOPS: dict[type[ast.AST], Callable[[Any, Any], Any]] = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}
_SAFE_BOOLOPS = {
    ast.And: lambda vals: all(vals),
    ast.Or: lambda vals: any(vals),
}
_SAFE_UNARYOPS = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
    ast.Not: lambda a: not a,
}


def _reject_unsafe_value(value: Any, accessor: str) -> Any:
    """C7 fix: only plain data may flow out of attribute/subscript access.

    Callables, classes and modules are the raw material of sandbox-escape
    gadget chains (e.g. reaching __subclasses__ or os via a module attr).
    safe_eval has no ast.Call handler, so rejecting them loses nothing.
    """
    import types as _types
    if callable(value) or isinstance(value, (type, _types.ModuleType)):
        # NOTE: ValueError (not TypeError) is deliberate — it is safe_eval's
        # API for "rejected value"; callers (condition gates) catch Exception.
        raise ValueError(  # noqa: TRY004
            f"Attribute access blocked: callable/type/module via {accessor!r} "
            "is not allowed in safe_eval"
        )
    return value


def _safe_eval_node(node: ast.AST, context: dict) -> Any:
    """Recursively evaluate an AST node safely."""
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body, context)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return context.get(node.id)
    if isinstance(node, ast.BoolOp):
        op_fn = _SAFE_BOOLOPS[type(node.op)]
        return op_fn(_safe_eval_node(v, context) for v in node.values)
    if isinstance(node, ast.UnaryOp):
        op_fn = _SAFE_UNARYOPS[type(node.op)]
        return op_fn(_safe_eval_node(node.operand, context))
    if isinstance(node, ast.BinOp):
        op_fn = _SAFE_BINOPS.get(type(node.op))  # type: ignore
        if op_fn is None:
            raise ValueError(f"Unsupported binary op: {type(node.op).__name__}")
        return op_fn(_safe_eval_node(node.left, context), _safe_eval_node(node.right, context))  # type: ignore
    if isinstance(node, ast.Compare):
        left = _safe_eval_node(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            op_fn = _SAFE_CMPOPS[type(op)]  # type: ignore
            right = _safe_eval_node(comparator, context)
            if not op_fn(left, right):  # type: ignore
                return False
            left = right
        return True
    if isinstance(node, ast.Subscript):
        obj = _safe_eval_node(node.value, context)
        # C7 fix: restrict subscript to plain data containers — arbitrary
        # objects with custom __getitem__ could execute code or expose
        # internals.
        if not isinstance(obj, (dict, list, tuple, str)):
            raise TypeError(
                f"Subscript only allowed on dict/list/tuple/str, got {type(obj).__name__}"
            )
        key = _safe_eval_node(node.slice, context)
        if not isinstance(key, (str, int, bool)):
            raise TypeError(f"Subscript key must be str/int, got {type(key).__name__}")
        return _reject_unsafe_value(cast(Any, obj)[key], f"[{key!r}]")
    if isinstance(node, ast.Attribute):
        obj = _safe_eval_node(node.value, context)
        attr = node.attr
        # C7 fix: default-deny hardening. The old blacklist missed escape
        # vectors; now (1) any underscore-prefixed name is denied, and
        # (2) the resolved value must be plain data — callables, types and
        # modules are rejected outright (there is no ast.Call handler, so
        # losing bound methods costs nothing but closes gadget chains).
        if attr.startswith('_'):
            raise ValueError(f"Attribute access to private members is not allowed: {attr}")
        return _reject_unsafe_value(getattr(obj, attr), attr)
    if isinstance(node, ast.List):
        return [_safe_eval_node(e, context) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_safe_eval_node(e, context) for e in node.elts)
    raise ValueError(f"Unsupported AST node: {type(node).__name__}")


def safe_eval(expr: str, context: dict) -> Any:
    """Safe expression evaluator using AST — no code execution, no builtins access."""
    tree = ast.parse(expr, mode="eval")
    return _safe_eval_node(tree, context)


# ── Template resolution ───────────────────────────────────────

def _resolve_template(template: str, context: dict[str, Any]) -> str:
    """Replace {{ key }} placeholders with context values."""
    if not template:
        return template
    result = template
    for key, val in context.items():
        result = result.replace("{{ " + key + " }}", str(val))
    return result


# ── Topological sort ──────────────────────────────────────────

def _topological_sort(steps: list[WorkflowStep]) -> list[list[WorkflowStep]]:
    """Sort steps into layers for parallel execution.

    Returns a list of layers, where each layer contains steps
    that can be executed in parallel (no inter-dependencies).
    """
    step_map = {s.id: s for s in steps}
    in_degree: dict[str, int] = defaultdict(int)
    dependents: dict[str, list[str]] = defaultdict(list)

    for step in steps:
        if step.id not in in_degree:
            in_degree[step.id] = 0
        for dep in step.depends_on:
            in_degree[step.id] += 1
            dependents[dep].append(step.id)

    # Kahn's algorithm with layering
    layers: list[list[WorkflowStep]] = []
    ready = [sid for sid, deg in in_degree.items() if deg == 0]
    visited: set[str] = set()

    while ready:
        layer = [step_map[sid] for sid in ready if sid in step_map]
        layers.append(layer)
        visited.update(ready)
        next_ready: list[str] = []
        for sid in ready:
            for dep_id in dependents[sid]:
                in_degree[dep_id] -= 1
                if in_degree[dep_id] == 0 and dep_id not in visited:
                    next_ready.append(dep_id)
        ready = next_ready

    # Detect cycles: any remaining (unvisited) nodes are part of a cycle.
    remaining = [step_map[sid] for sid in step_map if sid not in visited]
    if remaining:
        # Hard constraint: DAG execution must check for cyclic dependencies
        # and throw ValueError with cycle chain.
        remaining_ids = [s.id for s in remaining]
        cycle_chain = remaining_ids + [remaining_ids[0]]
        raise ValueError(f"Cycle detected: {' -> '.join(cycle_chain)}")

    return layers


# ── Step lookup ───────────────────────────────────────────────

def _find_step(steps: list[WorkflowStep], step_id: str) -> WorkflowStep:
    """Find a step by ID."""
    for s in steps:
        if s.id == step_id:
            return s
    return WorkflowStep(id=step_id)  # fallback


# ── JSON helpers ──────────────────────────────────────────────

def json_dumps_safe(obj: Any) -> str:
    """JSON dumps that handles non-serializable values."""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)