"""MAOP Visual Builder Compiler — F3-02 bidirectional Visual ↔ YAML conversion.

This module provides the backend compiler for the Workflow Visual Builder:
  - Compile: Visual DAG JSON → MAOP YAML workflow definition
  - Decompile: MAOP YAML workflow → Visual DAG JSON (for re-editing)
  - Validate: DAG well-formedness (no cycles, type match, required fields)

Integration:
  - ``compile()`` output is fed to the Orchestrator / execution service
  - ``decompile()`` reads existing YAML for re-editing in the visual builder
  - ``validate()`` is the backend hook for frontend real-time validation

Usage::

    from maop.core.visual_builder import compile_visual_dag, decompile_yaml, validate_dag

    yaml_str = compile_visual_dag(visual_dag)
    visual_dict = decompile_yaml(yaml_str)
    errors = validate_dag(visual_dag)
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any, cast

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

__all__ = [
    "VisualNode",
    "VisualEdge",
    "VisualDAG",
    "ValidationError",
    "compile_visual_dag",
    "decompile_yaml",
    "validate_dag",
]


# ── Data Models ────────────────────────────────────────────────────

NODE_TYPE_AGENT = "agent"
NODE_TYPE_TOOL = "tool"
NODE_TYPE_GATEWAY = "gateway"
NODE_TYPE_CONDITION = "condition"
VALID_NODE_TYPES = frozenset({
    NODE_TYPE_AGENT, NODE_TYPE_TOOL, NODE_TYPE_GATEWAY, NODE_TYPE_CONDITION,
})


class VisualNode(BaseModel):
    """A node in the visual DAG canvas."""
    id: str
    node_type: str                   # agent | tool | gateway | condition
    label: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    position_x: float = 0.0
    position_y: float = 0.0


class VisualEdge(BaseModel):
    """A directed edge connecting two nodes."""
    source: str                      # source node id
    target: str                      # target node id
    label: str = ""                  # optional edge label (e.g. condition text)
    condition: str = ""              # optional condition expression


class VisualDAG(BaseModel):
    """Complete visual DAG definition (the builder's in-memory model)."""
    name: str = "untitled"
    nodes: list[VisualNode] = Field(default_factory=list)
    edges: list[VisualEdge] = Field(default_factory=list)
    version: int = 1


class ValidationError(BaseModel):
    """A single DAG validation error."""
    node_id: str = ""                # related node (or "" for global errors)
    code: str = ""                   # machine-readable error code
    message: str = ""                # human-readable message


# ── Compile: VisualDAG → YAML ─────────────────────────────────────

def compile_visual_dag(dag: VisualDAG) -> str:
    """Compile a VisualDAG into a MAOP YAML workflow string.

    The output YAML is Git-friendly (deterministic key order) and
    can be submitted to the Orchestrator via the existing execution
    service.

    Parameters
    ----------
    dag : VisualDAG
        The visual DAG from the frontend builder.

    Returns
    -------
    str
        YAML string suitable for direct dispatch.
    """
    import yaml

    nodes_list: list[dict[str, Any]] = []
    edges_list: list[dict[str, Any]] = []

    for node in dag.nodes:
        entry: dict[str, Any] = {
            "id": node.id,
            "type": node.node_type,
            "label": node.label or node.id,
        }
        # Merge node config (agent name, tool params, etc.)
        if node.config:
            entry["config"] = node.config
        if node.node_type == NODE_TYPE_CONDITION:
            entry["condition"] = node.config.get("expression", "true")
        nodes_list.append(entry)

    for edge in dag.edges:
        entry: dict[str, Any] = {
            "source": edge.source,
            "target": edge.target,
        }
        if edge.label:
            entry["label"] = edge.label
        if edge.condition:
            entry["condition"] = edge.condition
        edges_list.append(entry)

    workflow: dict[str, Any] = {
        "name": dag.name,
        "version": dag.version,
        "nodes": nodes_list,
        "edges": edges_list,
    }

    return yaml.dump(workflow, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ── Decompile: YAML → VisualDAG ──────────────────────────────────

def decompile_yaml(yaml_str: str) -> VisualDAG:
    """Decompile a MAOP YAML workflow string into a VisualDAG.

    Used to load an existing YAML workflow into the visual builder
    for re-editing.  The round-trip ``decompile(compile(dag))`` is
    lossless for all structural data (positions are set to 0).

    Parameters
    ----------
    yaml_str : str
        The YAML workflow definition string.

    Returns
    -------
    VisualDAG
        The reconstructed visual DAG.
    """
    import yaml as _yaml

    try:
        data = _yaml.safe_load(yaml_str) or {}
    except Exception as exc:
        logger.warning("[visual-builder] YAML parse failed: %s", exc)
        return VisualDAG(name="parse-error")

    nodes: list[VisualNode] = []
    edges: list[VisualEdge] = []

    for n in data.get("nodes", []):
        node = VisualNode(
            id=str(n.get("id", "")),
            node_type=str(n.get("type", NODE_TYPE_AGENT)),
            label=str(n.get("label", "")),
            config=dict(n.get("config", {})),
        )
        nodes.append(node)

    for e in data.get("edges", []):
        edge = VisualEdge(
            source=str(e.get("source", "")),
            target=str(e.get("target", "")),
            label=str(e.get("label", "")),
            condition=str(e.get("condition", "")),
        )
        edges.append(edge)

    return VisualDAG(
        name=str(data.get("name", "untitled")),
        nodes=nodes,
        edges=edges,
        version=int(data.get("version", 1)),
    )


# ── Validate: DAG correctness checks ──────────────────────────────

def validate_dag(dag: VisualDAG) -> list[ValidationError]:
    """Validate a VisualDAG for structural correctness.

    Checks performed:
      1. Node id uniqueness
      2. Node type validity
      3. Edge references (source/target must reference existing nodes)
      4. Presence of at least one node
      5. DAG-ness (no directed cycles)
      6. At least one source node (no incoming edges) for dispatch

    Parameters
    ----------
    dag : VisualDAG
        The visual DAG to validate.

    Returns
    -------
    list[ValidationError]
        List of errors; empty list means valid.
    """
    errors: list[ValidationError] = []
    node_ids = {n.id for n in dag.nodes}

    # 1. Node id uniqueness
    seen_ids: set[str] = set()
    for node in dag.nodes:
        if node.id in seen_ids:
            errors.append(ValidationError(
                node_id=node.id, code="DUPLICATE_NODE_ID",
                message=f"Duplicate node id '{node.id}'",
            ))
        seen_ids.add(node.id)

    # 2. Node type validity
    for node in dag.nodes:
        if node.node_type not in VALID_NODE_TYPES:
            errors.append(ValidationError(
                node_id=node.id, code="INVALID_NODE_TYPE",
                message=f"Invalid node type '{node.node_type}' on node '{node.id}'",
            ))

    # 3. Edge references
    for edge in dag.edges:
        if edge.source not in node_ids:
            errors.append(ValidationError(
                code="MISSING_EDGE_SOURCE",
                message=f"Edge references unknown source '{edge.source}'",
            ))
        if edge.target not in node_ids:
            errors.append(ValidationError(
                code="MISSING_EDGE_TARGET",
                message=f"Edge references unknown target '{edge.target}'",
            ))

    # 4. At least one node
    if not dag.nodes:
        errors.append(ValidationError(
            code="EMPTY_DAG", message="DAG must contain at least one node",
        ))

    # 5. Cycle detection (BFS-based topological sort)
    if dag.nodes and not _has_cycle(dag):
        pass  # No cycle found — good
    elif dag.nodes:
        errors.append(ValidationError(
            code="CYCLIC_DAG", message="DAG contains a directed cycle",
        ))

    # 6. At least one source node
    target_ids = {e.target for e in dag.edges}
    source_nodes = [n for n in dag.nodes if n.id not in target_ids]
    if dag.nodes and not source_nodes:
        errors.append(ValidationError(
            code="NO_SOURCE_NODE",
            message="DAG has no source node (every node has an incoming edge — likely a cycle)",
        ))

    return errors


def _has_cycle(dag: VisualDAG) -> bool:
    """Detect directed cycles using Kahn's algorithm (BFS topological sort)."""
    node_ids = {n.id for n in dag.nodes}
    adjacency: dict[str, list[str]] = {n.id: [] for n in dag.nodes}
    in_degree: dict[str, int] = {n.id: 0 for n in dag.nodes}

    for edge in dag.edges:
        if edge.source in node_ids and edge.target in node_ids:
            adjacency[edge.source].append(edge.target)
            in_degree[edge.target] += 1

    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    visited = 0

    while queue:
        nid = queue.popleft()
        visited += 1
        for neighbour in adjacency[nid]:
            in_degree[neighbour] -= 1
            if in_degree[neighbour] == 0:
                queue.append(neighbour)

    return visited != len(node_ids)


# ── Dashboard helpers ─────────────────────────────────────────────

def optimization_hints(
    *,
    monthly_spent_usd: float = 0.0,
    monthly_budget_usd: float = 0.0,
    by_agent: dict[str, dict[str, Any]] | None = None,
    by_model: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate cost optimization hints for the dashboard API.

    This function aggregates CostTracker data and CostTier configuration
    to produce actionable optimization suggestions.

    Parameters
    ----------
    monthly_spent_usd : float
        Total spent in the current month.
    monthly_budget_usd : float
        Monthly budget cap.
    by_agent : dict | None
        Per-agent cost breakdown from ``CostTracker.summary()``.
    by_model : dict | None
        Per-model cost breakdown from ``CostTracker.summary()``.

    Returns
    -------
    dict
        Structured hints ready for JSON serialization.
    """
    by_agent = by_agent or {}
    by_model = by_model or {}

    suggestions: list[str] = []
    potential_savings = 0.0

    # 1. Top spender agent
    top_agent = ""
    top_agent_cost = 0.0
    for agent, data in by_agent.items():
        cost = data.get("cost", 0.0)
        if cost > top_agent_cost:
            top_agent_cost = cost
            top_agent = agent

    if top_agent and top_agent_cost > 0:
        # Estimate savings: if top agent's model is in CODE_GENERATION tier,
        # suggest downgrading non-critical tasks
        from maop.core.cost_router import _DEFAULT_TIERS, TaskClass
        code_tier = _DEFAULT_TIERS.get(TaskClass.CODE_GENERATION)
        if code_tier and code_tier.fallback_models:
            saving_pct = 0.6  # rough estimate
            potential_savings = round(top_agent_cost * saving_pct, 2)
            suggestions.append(
                f"降级 {top_agent} 的 LLM 到 {code_tier.fallback_models[0]}，"
                f"预计节省 ${potential_savings:.2f}/月"
            )

    # 2. Budget pressure
    budget_remaining_pct = 100.0
    if monthly_budget_usd > 0:
        budget_remaining_pct = round(
            max(0, (monthly_budget_usd - monthly_spent_usd) / monthly_budget_usd * 100), 1
        )
        if budget_remaining_pct < 20:
            suggestions.append(
                f"月度预算仅剩 {budget_remaining_pct:.1f}%，"
                f"建议切换到 AGGRESSIVE 路由策略或启用语义缓存"
            )

    # 3. Embedding model check
    embed_cost = 0.0
    for model, data in by_model.items():
        if "embed" in model.lower():
            embed_cost += data.get("cost", 0.0)
    if embed_cost > 0:
        suggestions.append(
            f"Embedding 任务月耗资 ${embed_cost:.2f}，"
            f"确认使用专用 embedding 模型而非通用 LLM 以节省成本"
        )

    return {
        "monthly_spent_usd": round(monthly_spent_usd, 4),
        "monthly_budget_usd": round(monthly_budget_usd, 4),
        "budget_remaining_pct": budget_remaining_pct,
        "potential_savings_usd": potential_savings,
        "top_waste_agent": top_agent,
        "top_waste_agent_cost_usd": round(top_agent_cost, 4),
        "suggestions": suggestions,
        "cache_hit_rate": None,  # TODO: wire to SemanticCacheStats
    }
