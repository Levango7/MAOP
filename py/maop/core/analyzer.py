"""MAOP Requirement Analyzer - Semantic decomposition, dependency DAG, and complexity assessment.

Three-layer architecture:
  1. Rule layer: keyword/pattern matching for quick classification
  2. Config layer: agents.yaml routing table for domain-specific routing
  3. Semantic layer: structural analysis for dependency extraction and complexity scoring

G2/G5 (2026-07-22, Phase G): Layer 3 (``_semantic_analyze``) and the public
``analyze`` entrypoint are now ``async`` and follow the ADR-013 dual-path
policy. When ``enable_llm=True`` is passed and an ``llm_factory`` with a
configured provider is supplied, the LLM is asked to identify inter-sub-task
dependency edges, per-sub-task risk levels, and a holistic complexity score
based on the *actual* task semantics rather than regex keyword tables. The
original rule-based logic (regex dependency hints + 4-factor scoring
formula) is retained verbatim as ``_rule_based_semantic_analyze`` and runs
automatically as fallback when:
  - ``enable_llm`` is False (default — preserves prior behavior bit-for-bit)
  - LLM provider is not configured (no API key)
  - LLM call raises or returns invalid JSON

See ``docs/adr/013-agent-llm-direct-cli-fallback.md`` for the rationale.

Produces an AnalysisResult with:
  - Sub-tasks decomposed from the original requirement
  - Dependency DAG (topological order)
  - Complexity score (0-100)
  - Suggested execution strategy (sequential / parallel / hybrid)
"""

from __future__ import annotations

import json
import re
import hashlib
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── Models ──────────────────────────────────────────────────────


class Complexity(str, Enum):
    TRIVIAL = "trivial"       # 0-20: single step, no dependencies
    SIMPLE = "simple"         # 21-40: few steps, linear deps
    MODERATE = "moderate"     # 41-60: multiple steps, some parallelism
    COMPLEX = "complex"       # 61-80: many steps, deep DAG
    CRITICAL = "critical"     # 81-100: high risk, many deps, needs review


class ExecutionStrategy(str, Enum):
    SEQUENTIAL = "sequential"   # Must run in order
    PARALLEL = "parallel"       # All steps independent
    HYBRID = "hybrid"           # Mix of sequential and parallel groups


class SubTask(BaseModel):
    """A decomposed sub-task within a requirement."""
    id: str = ""
    description: str = ""
    category: str = "general"       # code / test / docs / deploy / config / security / data
    priority: int = 1               # 1=high, 2=medium, 3=low
    dependencies: list[str] = Field(default_factory=list)  # IDs of sub-tasks this depends on
    estimated_effort: float = 1.0   # Relative effort units (0.5-10)
    risk_level: str = "low"         # low / medium / high
    assigned_agent: str = ""


class DependencyDAG(BaseModel):
    """Directed acyclic graph of sub-task dependencies."""
    nodes: list[str] = Field(default_factory=list)
    edges: list[tuple[str, str]] = Field(default_factory=list)  # (from, to) = dependency

    def topological_order(self) -> list[str]:
        """Kahn's algorithm for topological sort."""
        in_degree: dict[str, int] = {n: 0 for n in self.nodes}
        adj: dict[str, list[str]] = {n: [] for n in self.nodes}
        for src, dst in self.edges:
            adj[src].append(dst)
            in_degree[dst] = in_degree.get(dst, 0) + 1

        queue = [n for n in self.nodes if in_degree.get(n, 0) == 0]
        order: list[str] = []

        while queue:
            node = queue.pop(0)
            order.append(node)
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # Cycle detection: if not all nodes in order, there's a cycle
        if len(order) != len(self.nodes):
            remaining = [n for n in self.nodes if n not in order]
            # P1-e fix: throw ValueError with cycle chain (per project hard constraint)
            raise ValueError(
                f"Dependency cycle detected: {' -> '.join(remaining)} -> {remaining[0] if remaining else ''}"
            )

        return order

    def parallel_groups(self) -> list[list[str]]:
        """Group nodes that can execute in parallel (by dependency level)."""
        order = self.topological_order()
        in_degree: dict[str, int] = {n: 0 for n in self.nodes}
        for _, dst in self.edges:
            in_degree[dst] = in_degree.get(dst, 0) + 1

        groups: list[list[str]] = []
        assigned: set[str] = set()

        while len(assigned) < len(order):
            ready = [
                n for n in order
                if n not in assigned
                and all(dep in assigned for dep in self._get_deps(n))
            ]
            if not ready:
                # Force-assign remaining (cycle case)
                ready = [n for n in order if n not in assigned][:1]
            groups.append(ready)
            assigned.update(ready)

        return groups

    def _get_deps(self, node: str) -> list[str]:
        """Get direct dependencies of a node."""
        return [src for src, dst in self.edges if dst == node]


class AnalysisResult(BaseModel):
    """Complete analysis of a requirement."""
    task: str = ""
    task_hash: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Decomposition
    sub_tasks: list[SubTask] = Field(default_factory=list)
    dag: DependencyDAG = Field(default_factory=DependencyDAG)

    # Assessment
    complexity_score: int = 0          # 0-100
    complexity_level: Complexity = Complexity.TRIVIAL
    strategy: ExecutionStrategy = ExecutionStrategy.SEQUENTIAL

    # Routing hints
    primary_category: str = "general"
    suggested_agents: list[str] = Field(default_factory=list)
    requires_human_review: bool = False

    # Metadata
    analysis_layers: list[str] = Field(default_factory=list)  # Which layers contributed
    decomposition_reason: str = ""


# ── Layer 1: Rule-based decomposition ───────────────────────────

_RULE_PATTERNS: list[tuple[str, str, str, float]] = [
    # (regex, category, description_template, effort)
    (r"(?:refactor|rewrite|restructure)", "code", "Refactor code structure", 3.0),
    (r"(?:add\s+test|write\s+test|unit\s+test|integration\s+test)", "test", "Write tests", 2.0),
    (r"(?:fix\s+bug|debug|repair|patch)", "code", "Fix bug/defect", 2.5),
    (r"(?:write\s+doc|document|readme|guide)", "docs", "Write documentation", 1.5),
    (r"(?:deploy|release|publish|ship)", "deploy", "Deploy/release", 3.0),
    (r"(?:security|audit|vuln|cve)", "security", "Security audit/fix", 4.0),
    (r"(?:performance|optim|speed|benchmark)", "code", "Performance optimization", 3.5),
    (r"(?:database|sql|migration|schema)", "data", "Database work", 2.5),
    (r"(?:config|setting|env\s+var)", "config", "Configuration change", 1.0),
    (r"(?:design|architect|plan)", "code", "Design/architecture", 3.0),
]

_CONJUNCTION_PATTERN = re.compile(
    r"(?:\s+and\s+|\s*,\s+|\s*;\s+|\s+then\s+|\s+after\s+|\s+before\s+)",
    re.IGNORECASE
)

_STEP_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:\d+[\.\)]\s*|[-*]\s*|\>\s*)(.+?)(?=\n|$)",
    re.MULTILINE
)


def _rule_decompose(task: str) -> list[SubTask]:
    """Layer 1: Rule-based keyword decomposition."""
    sub_tasks: list[SubTask] = []

    # Check for multi-step patterns (numbered/bulleted lists)
    steps = _STEP_PATTERN.findall(task)
    if steps:
        for i, step_text in enumerate(steps):
            cat = _classify_category(step_text)
            sub_tasks.append(SubTask(
                id=f"st-{i:03d}",
                description=step_text.strip(),
                category=cat,
                priority=1 if i == 0 else 2,
                estimated_effort=_estimate_effort(step_text),
            ))
        return sub_tasks

    # Check for conjunctions splitting
    parts = _CONJUNCTION_PATTERN.split(task)
    if len(parts) > 1:
        for i, part in enumerate(parts):
            part = part.strip()
            if len(part) < 5:
                continue
            cat = _classify_category(part)
            sub_tasks.append(SubTask(
                id=f"st-{i:03d}",
                description=part,
                category=cat,
                priority=1 if i == 0 else 2,
                estimated_effort=_estimate_effort(part),
            ))
        if sub_tasks:
            return sub_tasks

    # Single task - classify it
    cat = _classify_category(task)
    sub_tasks.append(SubTask(
        id="st-000",
        description=task,
        category=cat,
        priority=1,
        estimated_effort=_estimate_effort(task),
    ))
    return sub_tasks


def _classify_category(text: str) -> str:
    """Classify text into a category using rule patterns."""
    text_lower = text.lower()
    for pattern, category, _, _ in _RULE_PATTERNS:
        if re.search(pattern, text_lower):
            return category
    return "general"


def _estimate_effort(text: str) -> float:
    """Estimate relative effort for a task description."""
    text_lower = text.lower()
    for pattern, _, _, effort in _RULE_PATTERNS:
        if re.search(pattern, text_lower):
            return effort
    # Heuristic: longer descriptions tend to be more complex
    word_count = len(text.split())
    if word_count > 20:
        return 3.0
    if word_count > 10:
        return 2.0
    return 1.0


# ── Layer 2: Config-based routing ───────────────────────────────

def _config_enrich(
    sub_tasks: list[SubTask],
    config: Any | None,
) -> list[SubTask]:
    """Layer 2: Enrich sub-tasks with config-based agent assignments."""
    if config is None:
        return sub_tasks

    # Try to assign agents from config routing
    routing = getattr(config, "routing", {})
    for st in sub_tasks:
        matched = False
        # Exact category match first (highest priority)
        if st.category and st.category in routing:
            st.assigned_agent = getattr(routing[st.category], "primary", "")
            matched = True
        if not matched:
            # Word-boundary match on description (not substring)
            desc_lower = st.description.lower()
            for rk, route in routing.items():
                # Use word boundary to avoid false substring matches
                # e.g. "code" should match "write code" but not "codec" or "barcode"
                import re
                if re.search(r"\b" + re.escape(rk) + r"\b", desc_lower):
                    st.assigned_agent = getattr(route, "primary", "")
                    matched = True
                    break

    return sub_tasks


# ── Layer 3: Semantic analysis ──────────────────────────────────

_DEPENDENCY_KEYWORDS = re.compile(
    r"(?:depends?\s+on|requires?|needs?|after|before|using|based\s+on)\s+(.+?)(?:\.|,|;|$)",
    re.IGNORECASE
)

_RISK_KEYWORDS = re.compile(
    r"(?:production|prod|critical|irreversible|destructive|drop|delete|remove|truncate|shutdown)",
    re.IGNORECASE
)


def _rule_based_semantic_analyze(task: str, sub_tasks: list[SubTask]) -> tuple[list[SubTask], DependencyDAG, int]:
    """Layer 3 (rule-based fallback): regex-driven dependency extraction + 4-factor scoring.

    G2 (2026-07-22, Phase G): extracted verbatim from the prior
    ``_semantic_analyze`` body. This is the automatic fallback path
    used when LLM extraction is disabled, not configured, or fails.
    Behavior is identical to the pre-Phase-G implementation.
    See ADR-013.
    """
    # Extract dependency hints from the task description
    dep_matches = _DEPENDENCY_KEYWORDS.findall(task)

    # Build dependency edges
    edges: list[tuple[str, str]] = []
    if len(sub_tasks) > 1:
        # Default: sequential dependencies unless parallelism is indicated
        for i in range(len(sub_tasks) - 1):
            edges.append((sub_tasks[i].id, sub_tasks[i + 1].id))

    # Check for explicit dependency overrides
    for st in sub_tasks:
        for dep_hint in dep_matches:
            dep_hint_lower = dep_hint.strip().lower()
            for other in sub_tasks:
                if other.id != st.id and dep_hint_lower in other.description.lower():
                    if (other.id, st.id) not in edges:
                        edges.append((other.id, st.id))
                        st.dependencies.append(other.id)

    dag = DependencyDAG(
        nodes=[st.id for st in sub_tasks],
        edges=edges,
    )

    # Complexity scoring
    score = 0

    # Factor 1: Number of sub-tasks (0-25)
    score += min(len(sub_tasks) * 5, 25)

    # Factor 2: DAG depth (0-25)
    groups = dag.parallel_groups()
    dag_depth = len(groups)
    score += min(dag_depth * 5, 25)

    # Factor 3: Total effort (0-25)
    total_effort = sum(st.estimated_effort for st in sub_tasks)
    score += min(int(total_effort * 3), 25)

    # Factor 4: Risk level (0-25)
    risk_count = sum(1 for st in sub_tasks if st.risk_level == "high")
    risk_count += sum(
        1 for st in sub_tasks
        if _RISK_KEYWORDS.search(st.description)
    )
    if risk_count > 0:
        for st in sub_tasks:
            if _RISK_KEYWORDS.search(st.description):
                st.risk_level = "high"
        score += min(risk_count * 10, 25)

    score = min(score, 100)

    return sub_tasks, dag, score


# ── G2: LLM-first semantic extraction ──────────────────────────

# JSON schema keys the LLM must return. Maps to the analyzer's outputs.
_LLM_DECOMP_KEYS: tuple[str, ...] = (
    "dependencies",
    "risk_levels",
    "complexity_score",
    "reasoning",
)

_VALID_RISK_LEVELS: frozenset[str] = frozenset({"low", "medium", "high"})


def _build_llm_decomp_prompt(task: str, sub_tasks: list[SubTask]) -> list[dict[str, str]]:
    """Build chat messages for LLM-based dependency + risk + complexity extraction.

    The prompt enumerates each sub-task (id + description) and asks the LLM
    to return strict JSON identifying inter-sub-task dependencies, per-sub-task
    risk levels, and a holistic complexity score. Temperature should be 0.0
    for deterministic extraction.
    """
    sub_task_lines = "\n".join(
        f"  - {st.id}: {st.description}" for st in sub_tasks
    )
    valid_ids = ", ".join(st.id for st in sub_tasks)
    system_prompt = (
        "You are a requirements analysis assistant. Given a task description "
        "and a list of already-decomposed sub-tasks (each with an id and "
        "description), identify the inter-sub-task dependency relationships, "
        "per-sub-task risk levels, and a holistic complexity score for the "
        "whole task. Return strict JSON — no markdown fences.\n\n"
        "Required JSON fields (all keys must be present):\n"
        "  - dependencies: list of {\"from\": <id>, \"to\": <id>} pairs.\n"
        "    Semantics: \"from\" must complete before \"to\" can start.\n"
        "    Only use ids from the provided list. Empty list if no deps.\n"
        "  - risk_levels: object mapping sub-task id → one of "
        "\"low\" | \"medium\" | \"high\". Must include every provided id.\n"
        "  - complexity_score: integer 0-100 reflecting overall task "
        "complexity (sub-task count, dep depth, total effort, risk).\n"
        "  - reasoning: short string explaining the analysis.\n\n"
        "Return ONLY the JSON object, no commentary."
    )
    user_prompt = (
        f"Task:\n{task}\n\n"
        f"Sub-tasks (id: description):\n{sub_task_lines}\n\n"
        f"Valid sub-task ids: {valid_ids}\n\n"
        "Return the structured analysis as JSON."
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _parse_llm_decomp(
    content: str,
    sub_tasks: list[SubTask],
) -> tuple[list[SubTask], DependencyDAG, int] | None:
    """Parse the LLM JSON response into (sub_tasks, dag, score).

    Returns None if the response cannot be parsed or fails validation.
    The caller falls back to ``_rule_based_semantic_analyze`` in that case.
    """
    if not content or not content.strip():
        return None
    text = content.strip()
    # Strip accidental markdown code fences if present.
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        data: Any = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.debug("[analyzer] LLM decomp JSON parse failed: %s", exc)
        return None
    if not isinstance(data, dict):
        return None

    valid_ids = {st.id for st in sub_tasks}

    # ── Dependencies → DAG edges ────────────────────────────
    edges: list[tuple[str, str]] = []
    deps_raw = data.get("dependencies", [])
    if isinstance(deps_raw, list):
        # Start from sequential default (preserves prior behavior baseline)
        if len(sub_tasks) > 1:
            for i in range(len(sub_tasks) - 1):
                edges.append((sub_tasks[i].id, sub_tasks[i + 1].id))
        # Add LLM-identified edges (dedup, id-validated)
        for entry in deps_raw:
            if not isinstance(entry, dict):
                continue
            src = str(entry.get("from", "")).strip()
            dst = str(entry.get("to", "")).strip()
            if src in valid_ids and dst in valid_ids and src != dst:
                edge = (src, dst)
                if edge not in edges:
                    edges.append(edge)

    # ── Risk levels → mutate sub_tasks ──────────────────────
    risk_raw = data.get("risk_levels", {})
    if isinstance(risk_raw, dict):
        for st in sub_tasks:
            val = risk_raw.get(st.id)
            if isinstance(val, str):
                v = val.strip().lower()
                if v in _VALID_RISK_LEVELS:
                    st.risk_level = v

    # ── Update dependencies field on each SubTask ────────────
    # Re-derive per-node dependency list from the edge list
    deps_by_node: dict[str, list[str]] = {st.id: [] for st in sub_tasks}
    for src, dst in edges:
        if src in deps_by_node and dst in deps_by_node and src != dst:
            if src not in deps_by_node[dst]:
                deps_by_node[dst].append(src)
    for st in sub_tasks:
        st.dependencies = deps_by_node.get(st.id, [])

    dag = DependencyDAG(
        nodes=[st.id for st in sub_tasks],
        edges=edges,
    )

    # ── Complexity score ────────────────────────────────────
    # Prefer LLM-provided score when valid; otherwise compute via rule formula
    # (factor-based) so the caller still gets a sane value.
    score_raw = data.get("complexity_score")
    if isinstance(score_raw, (int, float)) and 0 <= score_raw <= 100:
        score = int(score_raw)
    else:
        # Fall back to rule-based scoring on the LLM-built DAG.
        s = 0
        s += min(len(sub_tasks) * 5, 25)
        groups = dag.parallel_groups()
        s += min(len(groups) * 5, 25)
        total_effort = sum(st.estimated_effort for st in sub_tasks)
        s += min(int(total_effort * 3), 25)
        risk_count = sum(1 for st in sub_tasks if st.risk_level == "high")
        if risk_count > 0:
            s += min(risk_count * 10, 25)
        score = min(s, 100)

    return sub_tasks, dag, score


async def _semantic_analyze(
    task: str,
    sub_tasks: list[SubTask],
    *,
    llm_factory: Any = None,
    model_name: str = "",
    enable_llm: bool = False,
) -> tuple[list[SubTask], DependencyDAG, int]:
    """Layer 3: Semantic dependency extraction + complexity scoring.

    G2 (2026-07-22, Phase G): dual-path implementation per ADR-013.

    - **Primary path (LLM)**: when ``enable_llm=True`` and ``llm_factory``
      is provided with a configured provider, call
      ``provider.chat(messages=_build_llm_decomp_prompt(task, sub_tasks),
      model=model_name, temperature=0.0, max_tokens=1024)`` and parse the
      JSON response into (sub_tasks, dag, score). Real semantic
      understanding — dependency edges, per-sub-task risk levels, and
      holistic complexity come from the LLM, not from regex keyword tables.
    - **Fallback path (rule)**: when LLM is disabled, not configured, or
      raises/returns invalid JSON, delegates to ``_rule_based_semantic_analyze``
      (the original regex + 4-factor scoring logic).
    """
    if not enable_llm or llm_factory is None:
        return _rule_based_semantic_analyze(task, sub_tasks)

    # Primary path: LLM semantic extraction.
    try:
        provider = llm_factory.get_provider(model_name) if model_name else None
        if provider is None or not provider.is_configured:
            return _rule_based_semantic_analyze(task, sub_tasks)
        model_cfg = llm_factory.get_model_config(model_name) if model_name else None
        model_id = model_cfg.model_id if model_cfg else model_name
        messages = _build_llm_decomp_prompt(task, sub_tasks)
        response = await provider.chat(
            messages=messages, model=model_id,
            temperature=0.0, max_tokens=1024,
        )
        result = _parse_llm_decomp(response.content, sub_tasks)
        if result is not None:
            return result
    except Exception as exc:
        logger.warning("[analyzer] LLM decomp call failed, falling back: %s", exc)
    return _rule_based_semantic_analyze(task, sub_tasks)


# ── Strategy selection ──────────────────────────────────────────

def _select_strategy(dag: DependencyDAG, score: int) -> ExecutionStrategy:
    """Select execution strategy based on DAG structure and complexity."""
    groups = dag.parallel_groups()
    if len(groups) <= 1:
        return ExecutionStrategy.PARALLEL
    return ExecutionStrategy.HYBRID


def _complexity_level(score: int) -> Complexity:
    """Map score to complexity level."""
    if score <= 20:
        return Complexity.TRIVIAL
    if score <= 40:
        return Complexity.SIMPLE
    if score <= 60:
        return Complexity.MODERATE
    if score <= 80:
        return Complexity.COMPLEX
    return Complexity.CRITICAL


# ── Public API ──────────────────────────────────────────────────

async def analyze(
    task: str,
    *,
    config: Any | None = None,
    max_subtasks: int = 20,
    llm_factory: Any = None,
    model_name: str = "",
    enable_llm: bool = False,
) -> AnalysisResult:
    """Analyze a requirement: decompose, build DAG, assess complexity.

    G2 (2026-07-22, Phase G): now ``async`` and forwards LLM-related
    kwargs to ``_semantic_analyze`` for ADR-013 dual-path semantic
    extraction. When ``enable_llm=False`` (default), the function is
    behaviorally identical to the pre-Phase-G synchronous rule-based
    implementation — callers must still ``await`` it, but no LLM
    network call happens.

    Parameters
    ----------
    task : str
        The requirement/task description to analyze.
    config : MaopConfig | None
        Optional MAOP configuration for agent routing.
    max_subtasks : int
        Maximum number of sub-tasks to decompose (safety limit).
    llm_factory : LLMProviderFactory | None
        Optional LLM provider factory for the semantic-extraction
        primary path. Ignored when ``enable_llm=False``.
    model_name : str
        Model key in ``models.yaml`` to use for LLM extraction.
        Empty string means "use the default configured model".
    enable_llm : bool
        Toggle the LLM-first semantic extraction path. When False
        (default), rule-based extraction runs synchronously inside
        the async function (no await actually happens on the LLM).

    Returns
    -------
    AnalysisResult
        Complete analysis with sub-tasks, DAG, complexity, and strategy.
    """
    task_hash = hashlib.sha256(task.encode()).hexdigest()[:12]
    layers: list[str] = []

    # Layer 1: Rule-based decomposition
    sub_tasks = _rule_decompose(task)
    layers.append("rule")

    # Layer 2: Config enrichment
    if config is not None:
        sub_tasks = _config_enrich(sub_tasks, config)
        layers.append("config")

    # Safety: limit sub-tasks
    if len(sub_tasks) > max_subtasks:
        sub_tasks = sub_tasks[:max_subtasks]
        logger.info("Truncated sub-tasks to %d (max_subtasks)", max_subtasks)

    # Layer 3: Semantic analysis (async dual-path per ADR-013)
    sub_tasks, dag, score = await _semantic_analyze(
        task, sub_tasks,
        llm_factory=llm_factory,
        model_name=model_name,
        enable_llm=enable_llm,
    )
    layers.append("semantic")

    # Determine strategy and complexity
    strategy = _select_strategy(dag, score)
    level = _complexity_level(score)

    # Determine primary category
    categories = [st.category for st in sub_tasks]
    primary_category = max(set(categories), key=categories.count) if categories else "general"

    # Suggest agents
    suggested_agents: list[str] = []
    for st in sub_tasks:
        if st.assigned_agent and st.assigned_agent not in suggested_agents:
            suggested_agents.append(st.assigned_agent)

    # Human review needed for critical complexity or high-risk tasks
    requires_review = level in (Complexity.COMPLEX, Complexity.CRITICAL) or \
                      any(st.risk_level == "high" for st in sub_tasks)

    # Build decomposition reason
    reason_parts: list[str] = []
    if len(sub_tasks) == 1:
        reason_parts.append("single task")
    else:
        reason_parts.append(f"{len(sub_tasks)} sub-tasks")
    if dag.edges:
        reason_parts.append(f"{len(dag.edges)} dependencies")
    reason_parts.append(f"score={score}")
    reason = "; ".join(reason_parts)

    return AnalysisResult(
        task=task,
        task_hash=task_hash,
        sub_tasks=sub_tasks,
        dag=dag,
        complexity_score=score,
        complexity_level=level,
        strategy=strategy,
        primary_category=primary_category,
        suggested_agents=suggested_agents,
        requires_human_review=requires_review,
        analysis_layers=layers,
        decomposition_reason=reason,
    )
