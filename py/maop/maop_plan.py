"""MAOP Plan — Route task to agent based on config-driven rules.

Task planning and routing rule application.: reads agents.yaml, applies routing rules,
selects agent + routing_key + gates + budget.

ADR-012: Config routing (match regex + keywords) is the single source of truth.
v5.0.0: Legacy hardcoded _ROUTING_RULES keyword fallback removed.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from pydantic import BaseModel, Field

from maop.config.loader import MaopConfig

logger = logging.getLogger(__name__)

# ── SLA constants (Phase γ-1) ────────────────────────────────
_VALID_SLA_TIERS = frozenset({"best_effort", "standard", "critical"})
_VALID_PRIORITY_RANGE = (1, 5)  # 1 = highest, 5 = lowest

# M1 修复：移除硬编码 agent = "claude"，改为从环境变量读取默认 agent。
# MAOP_DEFAULT_AGENT 优先级最高，未设置时回退到 "codex"（已在 agents.yaml 中定义）。
DEFAULT_AGENT = os.getenv("MAOP_DEFAULT_AGENT", "codex")
# 默认 routing_key（config 路由未匹配时使用）
DEFAULT_ROUTING_KEY = "chat"


class Plan(BaseModel):
    """Result of the Plan phase.

    Phase γ-1 extension: SLA-aware fields (deadline_ms, priority, sla_tier)
    enable scheduling policies like "critical-first" and "deadline-acceleration".
    All new fields have defaults so existing callers remain backward compatible.
    """
    phase: str = "plan"
    task: str = ""
    # M1 修复：默认 agent 改为从配置读取，不再硬编码 "claude"
    selected_agent: str = DEFAULT_AGENT
    routing_key: str = DEFAULT_ROUTING_KEY
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


# ── Legacy keyword routing removed in v5.0.0 ──────────────────
# Config-based routing is now the single source of truth. When config
# routing misses, we fall back to a conservative default (DEFAULT_ROUTING_KEY /
# DEFAULT_AGENT) instead of the removed _route_by_keyword() keyword matcher.
# M1 修复：默认 agent 不再硬编码 "claude"，改为从环境变量读取（DEFAULT_AGENT）。



def _route_by_config(task: str, config: MaopConfig | None, *, adaptive: bool = True, trace_id: str = "", scorer: Any = None) -> tuple[str, str] | None:
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

    if scorer is None:
        from maop.core.routing.route_scorer import RouteScorer
        scorer = RouteScorer(config=config)
    match = scorer.match(task, adaptive=adaptive, trace_id=trace_id)
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
    trace_id: str = "",
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
    # H8 修复：记录 plan 阶段耗时与委派计数
    import time as _time

    _plan_start = _time.monotonic()

    # Priority 1: explicit routing_key override
    if routing_key:
        rk = routing_key
        # M1 修复：移除硬编码 agent = "claude"，改为从配置读取默认 agent
        agent = DEFAULT_AGENT
        if config:
            for route_rk, route in config.routing.items():
                if route_rk == rk:
                    agent = route.primary
                    break
    else:
        # Priority 2: config-based routing (match regex + keywords)
        from maop.core.routing.route_scorer import get_route_scorer
        _scorer = get_route_scorer(config=config)
        config_result = _route_by_config(task, config, trace_id=trace_id, scorer=_scorer)
        if config_result:
            rk, agent = config_result
        else:
            # v5.0.0: legacy keyword routing removed; use conservative default.
            # M1 修复：默认 agent 改为从环境变量读取（DEFAULT_AGENT），不再硬编码 "claude"
            rk, agent = DEFAULT_ROUTING_KEY, DEFAULT_AGENT
            logger.warning("Config routing did not match task, using default: rk=%s agent=%s — check agents.yaml routing table", rk, agent)

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

    # H8 修复：记录 plan 耗时与委派计数（指标调用）
    try:
        from maop.core.monitoring.monitoring import (
            MAOP_DELEGATION_DURATION,
            MAOP_DELEGATIONS_TOTAL,
        )

        MAOP_DELEGATIONS_TOTAL.inc()
        MAOP_DELEGATION_DURATION.observe(_time.monotonic() - _plan_start)
    except Exception:
        # 指标记录失败不应影响业务逻辑；记录 debug 日志便于排查
        logger.debug("record plan delegation metrics failed", exc_info=True)

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
        logger.warning("Condition evaluation failed in maop_plan.py: %r → treating as False (step skipped)", condition, exc_info=True)
        return False


def _topological_sort(steps: list[Any]) -> list[list[int]]:
    """Topological sort with parallel level grouping.

    Returns a list of levels, where each level is a list of step indices
    that can run in parallel. Steps with no depends_on are at level 0.

    Raises
    ------
    ValueError
        当检测到 DAG 循环时，报错并包含循环路径，不再静默绕过（M5 修复）。
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
            # M5 修复：移除循环节点强行排入逻辑（原代码会绕过循环检查，
            # 将循环节点静默排入执行序列，导致含循环的 DAG 无限执行或
            # 产生非预期行为）。改为调用 engine_utils 的循环检测并报错退出。
            remaining = [i for i in range(len(steps)) if str(i) not in completed]
            if not remaining:
                break
            # 构造循环路径：剩余节点即为循环节点，按依赖关系拼接路径
            cycle_nodes = [str(i) for i in remaining]
            # 尝试构造更精确的循环路径（沿依赖链遍历）
            cycle_path = _detect_cycle_path(remaining, dep_map)
            if cycle_path:
                cycle_display = " -> ".join(cycle_path)
            else:
                # 回退：剩余节点首尾相连
                cycle_display = " -> ".join(cycle_nodes + [cycle_nodes[0]])
            raise ValueError(
                f"DAG 存在循环: {cycle_display}。请检查任务依赖配置。"
            )
        for i in level:
            completed.add(str(i))
        levels.append(level)

    return levels


def _detect_cycle_path(
    remaining: list[int],
    dep_map: dict[int, set[str]],
) -> list[str] | None:
    """从剩余（循环）节点中检测一条具体的循环路径。

    使用 DFS 沿依赖关系遍历，找到第一个形成环的路径。
    返回节点索引字符串列表（如 ["1", "2", "3", "1"]）或 None。
    """
    if not remaining:
        return None
    remaining_set = {str(i) for i in remaining}
    # 邻接表：节点 -> 它依赖的节点（仅在剩余节点中）
    adj: dict[str, set[str]] = {}
    for i in remaining:
        adj[str(i)] = dep_map[i] & remaining_set

    # DFS 检测环
    visited: set[str] = set()
    stack: set[str] = set()
    path: list[str] = []

    def dfs(node: str) -> list[str] | None:
        if node in stack:
            # 找到环：返回从环起点到当前节点的路径 + 当前节点
            cycle_start = path.index(node)
            return path[cycle_start:] + [node]
        if node in visited:
            return None
        visited.add(node)
        stack.add(node)
        path.append(node)
        for neighbor in adj.get(node, set()):
            result = dfs(neighbor)
            if result is not None:
                return result
        path.pop()
        stack.discard(node)
        return None

    for start in remaining:
        result = dfs(str(start))
        if result is not None:
            return result
    return None


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

