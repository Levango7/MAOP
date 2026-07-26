"""MAOP Unified Engine — Execute workflow steps in topological order.

DAG workflow engine with topological execution.: consumes WorkflowStep arrays, supports
plan/agent/dag/verify/condition/terminal step types.
"""

from __future__ import annotations

import ast
import asyncio
import json
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

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
        op_fn = _SAFE_BINOPS.get(type(node.op))  # type: ignore[assignment]
        if op_fn is None:
            raise ValueError(f"Unsupported binary op: {type(node.op).__name__}")
        return op_fn(_safe_eval_node(node.left, context), _safe_eval_node(node.right, context))  # type: ignore[call-arg]
    if isinstance(node, ast.Compare):
        left = _safe_eval_node(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            op_fn = _SAFE_CMPOPS[type(op)]  # type: ignore[assignment]
            right = _safe_eval_node(comparator, context)
            if not op_fn(left, right):  # type: ignore[call-arg]
                return False
            left = right
        return True
    if isinstance(node, ast.Subscript):
        obj = _safe_eval_node(node.value, context)
        key = _safe_eval_node(node.slice, context)
        return obj[key]
    if isinstance(node, ast.Attribute):
        obj = _safe_eval_node(node.value, context)
        # Only allow access to whitelisted safe attributes
        attr = node.attr
        if attr.startswith('_'):
            raise ValueError(f"Attribute access to private members is not allowed: {attr}")
        # Block dangerous method calls on strings/objects
        _BLOCKED_ATTRS = frozenset({
            'format', 'format_map', '__class__', '__subclasses__', '__bases__',
            '__mro__', '__init__', '__new__', '__delattr__', '__setattr__',
            '__import__', '__builtins__', '__globals__', '__code__', '__func__',
        })
        if attr in _BLOCKED_ATTRS:
            raise ValueError(f"Attribute access blocked: {attr}")
        return getattr(obj, attr)
    if isinstance(node, ast.List):
        return [_safe_eval_node(e, context) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_safe_eval_node(e, context) for e in node.elts)
    raise ValueError(f"Unsupported AST node: {type(node).__name__}")


def safe_eval(expr: str, context: dict) -> Any:
    """Safe expression evaluator using AST — no code execution, no builtins access."""
    tree = ast.parse(expr, mode="eval")
    return _safe_eval_node(tree, context)

from maop.core.event_bus import EventBus, get_event_bus

# ── Step types ────────────────────────────────────────────────

class StepType(str, Enum):
    PLAN = "plan"
    AGENT = "agent"
    DAG = "dag"
    VERIFY = "verify"
    CONDITION = "condition"
    TERMINAL = "terminal"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── Models ────────────────────────────────────────────────────

class WorkflowStep(BaseModel):
    """A single step in a workflow DAG."""
    id: str
    type: StepType = StepType.AGENT
    agent: str = ""
    task: str = ""
    depends_on: list[str] = Field(default_factory=list)
    retry: int = 0
    timeout: int = 120
    description: str = ""
    on_failure: str = ""       # "fallback" | "skip" | "abort"
    fallback_to: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class StepResult(BaseModel):
    """Result of a single step execution."""
    id: str
    status: StepStatus = StepStatus.PENDING
    output: str = ""
    error: str = ""
    exit_code: int = -1
    duration_ms: int = 0
    agent: str = ""


class EngineResult(BaseModel):
    """Result of the entire engine run."""
    trace_id: str = ""
    steps: list[StepResult] = Field(default_factory=list)
    success: bool = False
    total_duration_ms: int = 0
    context: dict[str, Any] = Field(default_factory=dict)


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


# ── Engine ────────────────────────────────────────────────────

class Engine:
    """Unified workflow engine that executes steps in topological order.

    Usage::

        engine = Engine()
        steps = [
            WorkflowStep(id="s1", type=StepType.AGENT, agent="claude", task="Write code"),
            WorkflowStep(id="s2", type=StepType.VERIFY, depends_on=["s1"]),
            WorkflowStep(id="s3", type=StepType.TERMINAL, depends_on=["s2"]),
        ]
        result = await engine.run(steps, context={"task": "refactor"})
    """

    def __init__(
        self,
        event_bus: EventBus | None = None,
        step_executor: Any = None,
    ) -> None:
        self._bus = event_bus or get_event_bus()
        self._step_executor = step_executor  # Optional custom executor

    async def run(
        self,
        steps: list[WorkflowStep],
        context: dict[str, Any] | None = None,
        workdir: str = "",
        trace_id: str = "",
    ) -> EngineResult:
        """Execute all steps in topological order.

        Parameters
        ----------
        steps : list[WorkflowStep]
            Workflow steps to execute.
        context : dict | None
            Initial context with variables for template resolution.
        workdir : str
            Working directory.
        trace_id : str
            Trace ID for observability.
        """
        start = time.monotonic()
        if not trace_id:
            trace_id = uuid.uuid4().hex
        if context is None:
            context = {}

        ctx = dict(context)
        results: dict[str, StepResult] = {}
        layers = _topological_sort(steps)

        for _layer_idx, layer in enumerate(layers):
            # Check if any previous step requested abort
            aborted = any(
                results.get(s.id, StepResult(id=s.id)).status == StepStatus.FAILED
                and _find_step(steps, s.id).on_failure == "abort"
                for s in steps if s.id in results
            )
            if aborted:
                for step in layer:
                    results[step.id] = StepResult(
                        id=step.id, status=StepStatus.SKIPPED,
                        error="Aborted due to upstream failure",
                    )
                continue

            # Execute layer steps in parallel
            tasks = []
            for step in layer:
                tasks.append(self._execute_step(
                    step, ctx, results, workdir, trace_id,
                ))
            layer_results = await asyncio.gather(*tasks, return_exceptions=True)

            for step, lr in zip(layer, layer_results):
                if isinstance(lr, Exception):
                    sr = StepResult(
                        id=step.id, status=StepStatus.FAILED,
                        error=str(lr), agent=step.agent,
                    )
                else:
                    sr = lr  # type: ignore[assignment]
                results[step.id] = sr

                # Update context with step output
                ctx[step.id] = sr.output or sr.error

        total_ms = int((time.monotonic() - start) * 1000)
        all_results = [results[s.id] for s in steps if s.id in results]
        success = all(
            r.status in (StepStatus.SUCCESS, StepStatus.SKIPPED)
            for r in all_results
        )

        return EngineResult(
            trace_id=trace_id,
            steps=all_results,
            success=success,
            total_duration_ms=total_ms,
            context=ctx,
        )

    async def _execute_step(
        self,
        step: WorkflowStep,
        context: dict[str, Any],
        results: dict[str, StepResult],
        workdir: str,
        trace_id: str,
    ) -> StepResult:
        """Execute a single workflow step."""
        start = time.monotonic()

        # Check dependencies
        for dep_id in step.depends_on:
            dep_result = results.get(dep_id)
            if dep_result and dep_result.status == StepStatus.FAILED:
                if step.on_failure == "skip" or step.type == StepType.TERMINAL:
                    return StepResult(
                        id=step.id, status=StepStatus.SKIPPED,
                        error=f"Dependency {dep_id} failed",
                        agent=step.agent,
                    )

        # Resolve templates in task
        resolved_task = _resolve_template(step.task, context)

        try:
            if step.type == StepType.TERMINAL:
                # Terminal step: aggregate context
                output = json_dumps_safe({k: v for k, v in context.items()
                                         if not k.startswith("_")})
                return StepResult(
                    id=step.id, status=StepStatus.SUCCESS,
                    output=output, agent=step.agent,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

            elif step.type == StepType.VERIFY:
                # Verify step: check upstream results
                upstream_ok = all(
                    results[d].status == StepStatus.SUCCESS
                    for d in step.depends_on
                    if d in results
                )
                if upstream_ok:
                    return StepResult(
                        id=step.id, status=StepStatus.SUCCESS,
                        output="All upstream steps passed",
                        agent=step.agent or "verify",
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
                else:
                    return StepResult(
                        id=step.id, status=StepStatus.FAILED,
                        error="Upstream verification failed",
                        agent=step.agent or "verify",
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )

            elif step.type == StepType.CONDITION:
                # Condition step: evaluate params
                condition_expr = step.params.get("expr", "true")
                # Simple boolean evaluation
                try:
                    passed = safe_eval(condition_expr, context)
                except Exception:
                    passed = False
                status = StepStatus.SUCCESS if passed else StepStatus.SKIPPED
                return StepResult(
                    id=step.id, status=status,
                    output=str(passed), agent=step.agent,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

            elif step.type == StepType.PLAN:
                # Plan step: dynamic task decomposition (P1-4)
                substeps = self._decompose_task(resolved_task, step)
                if substeps:
                    # Execute sub-steps recursively
                    sub_results = []
                    for sub in substeps:
                        sr = await self._execute_step(
                            sub, context, results, workdir, trace_id,
                        )
                        sub_results.append(sr)
                        context[sub.id] = sr.output or sr.error
                    # Aggregate sub-step outputs
                    success = all(r.status == StepStatus.SUCCESS for r in sub_results)
                    output = "\n".join(r.output for r in sub_results if r.output)
                    return StepResult(
                        id=step.id,
                        status=StepStatus.SUCCESS if success else StepStatus.FAILED,
                        output=output,
                        error="; ".join(r.error for r in sub_results if r.error) if not success else "",
                        agent=step.agent,
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
                # No decomposition: fall through to agent execution
                if self._step_executor is not None:
                    result = await self._step_executor(
                        step=step, context=context, workdir=workdir,
                        trace_id=trace_id,
                    )
                    return StepResult(
                        id=step.id, status=StepStatus.SUCCESS,
                        output=result.output if hasattr(result, 'output') else str(result),
                        exit_code=result.exit_code if hasattr(result, 'exit_code') else 0,
                        agent=step.agent,
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
                else:
                    return StepResult(
                        id=step.id, status=StepStatus.SUCCESS,
                        output=f"[{step.type.value}] {resolved_task[:100]}",
                        agent=step.agent,
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )

            elif step.type in (StepType.AGENT, StepType.DAG):
                # Agent/DAG step: use custom executor or mock
                if self._step_executor is not None:
                    result = await self._step_executor(
                        step=step, context=context, workdir=workdir,
                        trace_id=trace_id,
                    )
                    return StepResult(
                        id=step.id, status=StepStatus.SUCCESS,
                        output=result.output if hasattr(result, 'output') else str(result),
                        exit_code=result.exit_code if hasattr(result, 'exit_code') else 0,
                        agent=step.agent,
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
                else:
                    # No executor: mark as success (placeholder)
                    return StepResult(
                        id=step.id, status=StepStatus.SUCCESS,
                        output=f"[{step.type.value}] {resolved_task[:100]}",
                        agent=step.agent,
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )

            else:
                return StepResult(
                    id=step.id, status=StepStatus.FAILED,
                    error=f"Unknown step type: {step.type}",
                    agent=step.agent,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

        except Exception as exc:
            return StepResult(
                id=step.id, status=StepStatus.FAILED,
                error=str(exc), agent=step.agent,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    # ── Dynamic task decomposition (P1-4) ──────────────────

    def _decompose_task(
        self,
        task: str,
        step: WorkflowStep,
    ) -> list[WorkflowStep]:
        """Decompose a complex task into sub-steps.

        Uses heuristics to detect compound tasks and split them:
        - Semicolons: "do A; do B" → 2 steps
        - Numbered lists: "1. A 2. B" → 2 steps
        - "and" conjunctions: "implement X and test Y" → 2 steps
        - Bullet lists: "- A\\n- B" → 2 steps

        Returns empty list if task is atomic (no decomposition needed).
        """
        substeps: list[WorkflowStep] = []

        # Strategy 1: Semicolon-separated tasks
        if ";" in task:
            parts = [p.strip() for p in task.split(";") if p.strip()]
            if len(parts) > 1:
                for i, part in enumerate(parts):
                    substeps.append(WorkflowStep(
                        id=f"{step.id}_sub{i}",
                        type=StepType.AGENT,
                        agent=step.agent,
                        task=part,
                        depends_on=[f"{step.id}_sub{i-1}"] if i > 0 else [],
                    ))
                return substeps

        # Strategy 2: Numbered list "1. A 2. B"
        import re as _re
        numbered = _re.findall(r'\d+\.\s+(.+?)(?=\d+\.|$)', task, _re.DOTALL)
        if len(numbered) > 1:
            for i, part in enumerate(numbered):
                substeps.append(WorkflowStep(
                    id=f"{step.id}_sub{i}",
                    type=StepType.AGENT,
                    agent=step.agent,
                    task=part.strip(),
                    depends_on=[],
                ))
            return substeps

        # Strategy 3: Bullet list "- A\n- B"
        bullets = _re.findall(r'^[-*]\s+(.+)$', task, _re.MULTILINE)
        if len(bullets) > 1:
            for i, part in enumerate(bullets):
                substeps.append(WorkflowStep(
                    id=f"{step.id}_sub{i}",
                    type=StepType.AGENT,
                    agent=step.agent,
                    task=part.strip(),
                    depends_on=[],
                ))
            return substeps

        # Strategy 4: "and" conjunction (conservative: only split on clear "and")
        and_parts = _re.split(r'\s+and\s+', task, maxsplit=2)
        if len(and_parts) > 1 and all(len(p) > 10 for p in and_parts):
            for i, part in enumerate(and_parts):
                substeps.append(WorkflowStep(
                    id=f"{step.id}_sub{i}",
                    type=StepType.AGENT,
                    agent=step.agent,
                    task=part.strip(),
                    depends_on=[],
                ))
            return substeps

        # Task is atomic — no decomposition
        return []


def _find_step(steps: list[WorkflowStep], step_id: str) -> WorkflowStep:
    """Find a step by ID."""
    for s in steps:
        if s.id == step_id:
            return s
    return WorkflowStep(id=step_id)  # fallback


def json_dumps_safe(obj: Any) -> str:
    """JSON dumps that handles non-serializable values."""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)
