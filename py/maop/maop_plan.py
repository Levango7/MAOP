"""MAOP Plan — Route task to agent based on config-driven rules.

Task planning and routing rule application.: reads agents.yaml, applies routing rules,
selects agent + routing_key + gates + budget.

ADR-012: Config routing (match regex + keywords) takes precedence over
hardcoded _ROUTING_RULES. Legacy rules kept as fallback only.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from pydantic import BaseModel, Field

from maop.config.loader import MaopConfig, RouteEntry

logger = logging.getLogger(__name__)

# ── SLA constants (Phase γ-1) ────────────────────────────────
_VALID_SLA_TIERS = frozenset({"best_effort", "standard", "critical"})
_VALID_PRIORITY_RANGE = (1, 5)  # 1 = highest, 5 = lowest


class Plan(BaseModel):
    """Result of the Plan phase.

    Phase γ-1 extension: SLA-aware fields (deadline_ms, priority, sla_tier)
    enable scheduling policies like "critical-first" and "deadline-acceleration".
    All new fields have defaults so existing callers remain backward compatible.
    """
    phase: str = "plan"
    task: str = ""
    selected_agent: str = "claude"
    routing_key: str = "chat"
    gates: list[str] = Field(default_factory=lambda: ["exit_code", "output"])
    budget: dict[str, Any] = Field(default_factory=lambda: {"timeout_s": 120, "max_retries": 1})
    # ── SLA fields (Phase γ-1) ───────────────────────────────────
    deadline_ms: int | None = None
    """Absolute deadline timestamp in milliseconds since epoch.
    None means no explicit deadline (best-effort scheduling)."""
    priority: int = 3
    """Scheduling priority: 1 (highest) to 5 (lowest). Default 3 (normal)."""
    sla_tier: str = "standard"
    """SLA tier: best_effort | standard | critical."""

    def is_deadline_urgent(self, threshold_ms: int = 30000) -> bool:
        """Return True if deadline is set and the remaining time is below threshold.

        Parameters
        ----------
        threshold_ms : int
            Urgency threshold in milliseconds (default 30s).

        Returns
        -------
        bool
            False when ``deadline_ms`` is None (no deadline set).
            True when the time remaining to deadline is less than ``threshold_ms``.
        """
        if self.deadline_ms is None:
            return False
        now_ms = int(time.time() * 1000)
        return (self.deadline_ms - now_ms) < threshold_ms

    def effective_priority_score(self) -> float:
        """Combined priority + deadline-urgency score in [0.0, 1.0].

        Higher score = more urgent, should be scheduled first. Used for
        sorting pending tasks when scheduling.

        Score composition:
          - priority contributes 60%: P1 -> 1.0, P5 -> 0.2 (linear).
          - deadline urgency contributes 40%: 1.0 if past deadline,
            decaying buckets otherwise; 0.5 neutral when no deadline set.

        Returns
        -------
        float
            Score in [0.0, 1.0].
        """
        clamped_prio = max(
            _VALID_PRIORITY_RANGE[0],
            min(_VALID_PRIORITY_RANGE[1], self.priority),
        )
        priority_score = (6 - clamped_prio) / 5.0

        if self.deadline_ms is None:
            deadline_score = 0.5
        else:
            now_ms = int(time.time() * 1000)
            remaining_ms = self.deadline_ms - now_ms
            if remaining_ms <= 0:
                deadline_score = 1.0  # past deadline
            elif remaining_ms < 30_000:
                deadline_score = 0.9
            elif remaining_ms < 60_000:
                deadline_score = 0.8
            elif remaining_ms < 300_000:
                deadline_score = 0.7
            else:
                deadline_score = 0.6

        return 0.6 * priority_score + 0.4 * deadline_score


# ── Legacy keyword routing rules (DEPRECATED) ────────────────
# Kept as fallback when config is None or config routing misses.
# Will be removed in a future cleanup after config routing is fully validated.
_ROUTING_RULES: list[tuple[str, str, str]] = [
    # (regex_pattern, routing_key, default_agent)
    (r"(?:refactor|rewrite|restructure|clean\s+up)", "code", "codex"),
    (r"(?:test|spec|verify|assert|unit\s+test|integration)", "test", "codex"),
    (r"(?:debug|fix|bug|error|exception|traceback|repair)", "debug", "codex"),
    (r"(?:deploy|release|publish|ship|rollout)", "deploy", "codex"),
    (r"(?:document|docs?|readme|guide|explain|comment)", "docs", "claude"),
    (r"(?:design|architect|plan|strategy|blueprint)", "design", "claude"),
    (r"(?:security|audit|vuln|cve|hardening)", "security", "codex"),
    (r"(?:performance|optim|speed|benchmark|latency)", "perf", "codex"),
    (r"(?:data|database|sql|query|migration|schema)", "data", "codex"),
    (r"(?:config|setting|env|variable|preference)", "config", "codex"),
]


def _route_by_keyword(task: str) -> tuple[str, str]:
    """DEPRECATED: Fallback keyword matching when config routing is unavailable.

    Will be removed once config routing is fully validated in production.
    Note: returns legacy routing key space (code/test/debug/...), NOT config space.
    """
    task_lower = task.lower()
    for pattern, routing_key, agent in _ROUTING_RULES:
        if re.search(pattern, task_lower):
            return routing_key, agent
    return "chat", "claude"


def _adaptive_agent_select(route: RouteEntry, rk: str) -> str:
    """Select the best agent from route candidates using performance data.

    Considers primary, fallback, and tertiary agents. If the primary agent
    has significantly lower success rate than a fallback, switches to the
    better-performing agent.

    Returns the selected agent name.
    """
    candidates = [a for a in [route.primary, route.fallback, route.tertiary] if a]
    if len(candidates) <= 1:
        return route.primary

    try:
        import os

        from maop.core.agent_performance import AgentPerformanceTracker
        root = os.environ.get("MAOP_ROOT_DIR", ".")
        tracker = AgentPerformanceTracker(root_dir=root)
        best = tracker.best_agent(agents=candidates, routing_key=rk, default=route.primary)
        if best != route.primary:
            logger.info("Adaptive routing: rk=%s primary=%s → %s (performance-based)", rk, route.primary, best)
        return best
    except Exception as exc:
        logger.debug("Adaptive routing fallback: %s", exc)
        return route.primary


def _route_by_config(task: str, config: MaopConfig | None, *, adaptive: bool = True) -> tuple[str, str] | None:
    """Route using config routing table with multi-factor scoring.

    Uses RouteScorer to evaluate ALL routes and pick the best match by score,
    instead of first-match-wins. Handles ambiguous tasks that match multiple
    routing keys (e.g. "写个测试" matches both codegen and verify).

    Scoring: regex(50%) + keyword_count(30%) + capability_bonus(15%) + specificity(5%)
    Confidence: high(>=0.60) / medium(>=0.35) / low(<0.35)

    Also applies agent cooldown — agents that recently failed are skipped in
    favor of fallback candidates.

    Returns (routing_key, selected_agent) or None.
    """
    if config is None:
        return None

    from maop.core.route_scorer import RouteScorer
    scorer = RouteScorer(config=config)
    match = scorer.match(task, adaptive=adaptive)
    if match:
        logger.debug(
            "Route config: rk=%s agent=%s score=%.2f confidence=%s matched_by=%s",
            match.routing_key, match.agent, match.score,
            match.confidence, match.matched_by,
        )
        return match.routing_key, match.agent

    return None


def _get_agent_config(agent_name: str, config: MaopConfig | None) -> dict[str, Any]:
    """Get agent-specific config (timeout, retries, etc.)."""
    if config is None:
        return {}
    # Exact match first
    a = config.agents.get(agent_name)
    if a is not None:
        return {
            "timeout_s": a.timeout_s,
            "max_retries": 1,
        }
    # Fallback: return defaults if agent not found
    return {"timeout_s": 120, "max_retries": 1}


# ── Public API ────────────────────────────────────────────────

def maop_plan(
    task: str,
    *,
    workdir: str = "",
    routing_key: str = "",
    config: MaopConfig | None = None,
) -> Plan:
    """Execute Plan phase: route task to best agent.

    Parameters
    ----------
    task : str
        Task description.
    workdir : str
        Working directory.
    routing_key : str
        Override routing key (skip keyword matching).
    config : MaopConfig | None
        Loaded MAOP configuration.

    Returns
    -------
    Plan
        Routing result with selected_agent, routing_key, gates, budget.
    """
    # Priority 1: explicit routing_key override
    if routing_key:
        rk = routing_key
        agent = "claude"
        if config:
            for route_rk, route in config.routing.items():
                if route_rk == rk:
                    agent = route.primary
                    break
    else:
        # Priority 2: config-based routing (match regex + keywords)
        config_result = _route_by_config(task, config)
        if config_result:
            rk, agent = config_result
        else:
            # Priority 3: legacy keyword routing (DEPRECATED — fallback only)
            rk, agent = _route_by_keyword(task)
            logger.warning("Route fallback to legacy keyword matching: rk=%s agent=%s — config routing did not match, check agents.yaml routing table", rk, agent)

    # Get agent budget
    agent_cfg = _get_agent_config(agent, config)
    budget = {
        "timeout_s": agent_cfg.get("timeout_s", 120),
        "max_retries": agent_cfg.get("max_retries", 1),
    }

    # Determine gates based on routing key (ADR-012: unified key space)
    gates = ["exit_code", "output"]
    # Security-sensitive routes require content-safety gate
    if rk in ("security", "quickfix", "review"):
        gates.append("content-safety")
    # Deployment/infrastructure routes require dry-run gate
    if rk in ("deploy", "pipeline", "fileops"):
        gates.append("dry-run")

    return Plan(
        task=task,
        selected_agent=agent,
        routing_key=rk,
        gates=gates,
        budget=budget,
    )


# ── Workflow DSL Engine ────────────────────────────────────────

class PlanStepResult(BaseModel):
    step_index: int
    agent: str
    status: str = "pending"
    output: str = ""
    skipped: bool = False


class WorkflowResult(BaseModel):
    workflow_name: str
    steps_total: int = 0
    steps_completed: int = 0
    steps_skipped: int = 0
    step_results: list[PlanStepResult] = Field(default_factory=list)
    variables: dict[str, Any] = Field(default_factory=dict)


def _interpolate_vars(text: str, variables: dict[str, Any]) -> str:
    """Replace ${var} placeholders in text with variable values."""
    import re as _re
    def _replace(m):
        key = m.group(1)
        return str(variables.get(key, m.group(0)))
    return _re.sub(r"\$\{([^}]+)\}", _replace, text)


def _evaluate_condition(condition: str, variables: dict[str, Any]) -> bool:
    """Evaluate a simple condition expression.

    Supports: 'steps.N.status == "success"', 'var_name == "value"',
    bare variable truthiness.
    """
    if not condition:
        return True
    condition = _interpolate_vars(condition, variables)
    try:
        # P1-8 fix: use AST-based safe_eval instead of eval() with weak __builtins__ sandbox.
        # The previous eval(condition, {"__builtins__": {}}, variables) can be escaped via
        # ().__class__.__bases__[0].__subclasses__() — safe_eval uses an AST whitelist.
        from maop.engine import safe_eval
        return bool(safe_eval(condition, variables))
    except Exception:
        return False


def _topological_sort(steps: list[Any]) -> list[list[int]]:
    """Topological sort with parallel level grouping.

    Returns a list of levels, where each level is a list of step indices
    that can run in parallel. Steps with no depends_on are at level 0.
    """
    step_ids = {str(i) for i in range(len(steps))}
    dep_map: dict[int, set[str]] = {}
    for i, step in enumerate(steps):
        deps = set(step.depends_on) & step_ids
        dep_map[i] = deps

    levels: list[list[int]] = []
    completed: set[str] = set()

    while len(completed) < len(steps):
        level = []
        for i in range(len(steps)):
            if str(i) in completed:
                continue
            if dep_map[i].issubset(completed):
                level.append(i)
        if not level:
            remaining = [i for i in range(len(steps)) if str(i) not in completed]
            for i in remaining:
                level.append(i)
            if not level:
                break
        for i in level:
            completed.add(str(i))
        levels.append(level)

    return levels


def execute_workflow(
    workflow_name: str,
    config: MaopConfig | None = None,
    initial_vars: dict[str, Any] | None = None,
) -> WorkflowResult:
    """Execute a named workflow from config with DAG scheduling.

    Steps with depends_on are scheduled via topological sort.
    Steps at the same level can run in parallel (marked with parallel=True).

    Parameters
    ----------
    workflow_name : str
        Name of the workflow in config.workflows.
    config : MaopConfig | None
        Loaded MAOP configuration.
    initial_vars : dict, optional
        Initial variable bindings for ${var} interpolation.

    Returns
    -------
    WorkflowResult
    """
    result = WorkflowResult(workflow_name=workflow_name, variables=dict(initial_vars or {}))

    if config is None or workflow_name not in config.workflows:
        logger.warning("Workflow '%s' not found in config", workflow_name)
        return result

    wf = config.workflows[workflow_name]
    steps = wf.steps
    result.steps_total = len(steps)

    if not steps:
        return result

    step_outputs: dict[str, Any] = {}

    has_deps = any(step.depends_on for step in steps)
    if has_deps:
        levels = _topological_sort(steps)
        for level_indices in levels:
            parallel_steps = []
            for i in level_indices:
                step = steps[i]
                if step.parallel and len(level_indices) > 1:
                    parallel_steps.append(i)
            for i in level_indices:
                _execute_step(i, steps[i], config, result, step_outputs)
    else:
        for i, step in enumerate(steps):
            _execute_step(i, step, config, result, step_outputs)

    return result


def _execute_step(
    index: int,
    step: Any,
    config: MaopConfig | None,
    result: WorkflowResult,
    step_outputs: dict[str, Any],
) -> None:
    """Execute a single workflow step and update result."""
    sr = PlanStepResult(step_index=index, agent=step.agent)

    resolved_task = _interpolate_vars(step.task, result.variables)

    cond_vars = {**result.variables, "steps": step_outputs}
    if not step.always_run and not _evaluate_condition(step.condition, cond_vars):
        sr.skipped = True
        sr.status = "skipped"
        result.steps_skipped += 1
        result.step_results.append(sr)
        return

    plan = maop_plan(resolved_task, config=config)
    sr.status = "planned"
    sr.output = f"agent={plan.selected_agent} rk={plan.routing_key}"
    step_outputs[str(index)] = {"status": "success", "output": sr.output, "agent": step.agent}
    result.variables[f"steps.{index}.output"] = sr.output
    result.steps_completed += 1
    result.step_results.append(sr)

