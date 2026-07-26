"""Tests for MAOP.core.analyzer — Semantic decomposition, dependency DAG, complexity.

G2 (2026-07-22, Phase G): ``analyze`` and ``_semantic_analyze`` are now
``async`` (ADR-013 dual-path). All tests that call either function are
declared ``async def`` — pytest-asyncio with ``asyncio_mode = "auto"``
(pyproject.toml) detects and runs them without explicit ``@pytest.mark.asyncio``
decorators. Tests that touch only sync helpers (``_rule_decompose``,
``_classify_category``, ``_estimate_effort``, ``_config_enrich``,
``_select_strategy``, ``_complexity_level``, model constructors, DAG
algorithms) remain plain ``def``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from maop.core.analyzer import (
    AnalysisResult,
    Complexity,
    DependencyDAG,
    ExecutionStrategy,
    SubTask,
    _classify_category,
    _complexity_level,
    _config_enrich,
    _estimate_effort,
    _rule_decompose,
    _select_strategy,
    _semantic_analyze,
    analyze,
)

# ── Model tests ───────────────────────────────────────────────────

class TestComplexityEnum:
    def test_values(self):
        assert Complexity.TRIVIAL == "trivial"
        assert Complexity.CRITICAL == "critical"

    def test_ordering_by_score(self):
        assert _complexity_level(0) == Complexity.TRIVIAL
        assert _complexity_level(20) == Complexity.TRIVIAL
        assert _complexity_level(21) == Complexity.SIMPLE
        assert _complexity_level(40) == Complexity.SIMPLE
        assert _complexity_level(41) == Complexity.MODERATE
        assert _complexity_level(60) == Complexity.MODERATE
        assert _complexity_level(61) == Complexity.COMPLEX
        assert _complexity_level(80) == Complexity.COMPLEX
        assert _complexity_level(81) == Complexity.CRITICAL
        assert _complexity_level(100) == Complexity.CRITICAL


class TestExecutionStrategyEnum:
    def test_values(self):
        assert ExecutionStrategy.SEQUENTIAL == "sequential"
        assert ExecutionStrategy.PARALLEL == "parallel"
        assert ExecutionStrategy.HYBRID == "hybrid"


class TestSubTask:
    def test_defaults(self):
        st = SubTask()
        assert st.category == "general"
        assert st.priority == 1
        assert st.dependencies == []
        assert st.estimated_effort == 1.0
        assert st.risk_level == "low"

    def test_with_values(self):
        st = SubTask(id="s1", description="do thing", category="code", priority=2, dependencies=["s0"])
        assert st.category == "code"
        assert st.dependencies == ["s0"]


class TestAnalysisResult:
    def test_defaults(self):
        r = AnalysisResult()
        assert r.complexity_score == 0
        assert r.complexity_level == Complexity.TRIVIAL
        assert r.strategy == ExecutionStrategy.SEQUENTIAL
        assert r.requires_human_review is False
        assert r.analysis_layers == []


# ── DependencyDAG tests ───────────────────────────────────────────

class TestDependencyDAG:
    def test_empty_dag(self):
        dag = DependencyDAG()
        assert dag.topological_order() == []
        assert dag.parallel_groups() == []

    def test_single_node(self):
        dag = DependencyDAG(nodes=["a"])
        assert dag.topological_order() == ["a"]
        assert dag.parallel_groups() == [["a"]]

    def test_linear_chain(self):
        dag = DependencyDAG(nodes=["a", "b", "c"], edges=[("a", "b"), ("b", "c")])
        order = dag.topological_order()
        assert order == ["a", "b", "c"]

    def test_parallel_nodes(self):
        dag = DependencyDAG(nodes=["a", "b", "c"])
        groups = dag.parallel_groups()
        # All independent → one group
        assert len(groups) == 1
        assert set(groups[0]) == {"a", "b", "c"}

    def test_diamond_dependency(self):
        # a → b, a → c, b → d, c → d
        dag = DependencyDAG(
            nodes=["a", "b", "c", "d"],
            edges=[("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")],
        )
        order = dag.topological_order()
        assert order[0] == "a"
        assert order[-1] == "d"
        groups = dag.parallel_groups()
        # Level 0: a, Level 1: b,c, Level 2: d
        assert groups[0] == ["a"]
        assert set(groups[1]) == {"b", "c"}
        assert groups[2] == ["d"]

    def test_cycle_detection(self):
        # a → b → a (cycle)
        dag = DependencyDAG(nodes=["a", "b"], edges=[("a", "b"), ("b", "a")])
        with pytest.raises(ValueError, match="cycle"):
            dag.topological_order()

    def test_get_deps(self):
        dag = DependencyDAG(nodes=["a", "b", "c"], edges=[("a", "c"), ("b", "c")])
        deps = dag._get_deps("c")
        assert set(deps) == {"a", "b"}


# ── Rule-based decomposition ──────────────────────────────────────

class TestRuleDecompose:
    def test_single_task(self):
        tasks = _rule_decompose("do something")
        assert len(tasks) == 1
        assert tasks[0].id == "st-000"

    def test_numbered_list(self):
        text = "1. First step\n2. Second step\n3. Third step"
        tasks = _rule_decompose(text)
        assert len(tasks) == 3
        assert tasks[0].priority == 1
        assert tasks[1].priority == 2

    def test_bulleted_list(self):
        text = "- First\n- Second\n- Third"
        tasks = _rule_decompose(text)
        assert len(tasks) == 3

    def test_conjunction_split(self):
        tasks = _rule_decompose("fix bug and write tests")
        assert len(tasks) >= 2

    def test_category_classification(self):
        tasks = _rule_decompose("deploy to production")
        assert tasks[0].category == "deploy"

    def test_security_category(self):
        tasks = _rule_decompose("security audit of the system")
        assert tasks[0].category == "security"


class TestClassifyCategory:
    def test_refactor(self):
        assert _classify_category("refactor the module") == "code"

    def test_test(self):
        assert _classify_category("write unit test for foo") == "test"

    def test_docs(self):
        assert _classify_category("write documentation") == "docs"

    def test_deploy(self):
        assert _classify_category("deploy to prod") == "deploy"

    def test_security(self):
        assert _classify_category("fix security vuln") == "security"

    def test_config(self):
        assert _classify_category("change config setting") == "config"

    def test_general_fallback(self):
        assert _classify_category("random text with no keywords") == "general"


class TestEstimateEffort:
    def test_known_pattern(self):
        assert _estimate_effort("refactor the code") == 3.0

    def test_long_text(self):
        long_text = " ".join(["word"] * 25)
        assert _estimate_effort(long_text) == 3.0

    def test_medium_text(self):
        medium_text = " ".join(["word"] * 15)
        assert _estimate_effort(medium_text) == 2.0

    def test_short_text(self):
        assert _estimate_effort("short") == 1.0


# ── Config enrichment ─────────────────────────────────────────────

class TestConfigEnrich:
    def test_no_config_returns_unchanged(self):
        tasks = [SubTask(id="s1", category="code")]
        result = _config_enrich(tasks, None)
        assert result == tasks

    def test_assigns_agent_by_category(self):
        route = MagicMock()
        route.primary = "claude"
        config = MagicMock()
        config.routing = {"code": route}
        tasks = [SubTask(id="s1", description="write code", category="code")]
        result = _config_enrich(tasks, config)
        assert result[0].assigned_agent == "claude"

    def test_word_boundary_match(self):
        route = MagicMock()
        route.primary = "kimi"
        config = MagicMock()
        config.routing = {"code": route}
        tasks = [SubTask(id="s1", description="write code here", category="general")]
        result = _config_enrich(tasks, config)
        assert result[0].assigned_agent == "kimi"

    def test_no_false_substring_match(self):
        route = MagicMock()
        route.primary = "should_not_match"
        config = MagicMock()
        config.routing = {"code": route}
        # "barcode" contains "code" but should not match via word boundary
        tasks = [SubTask(id="s1", description="scan barcode", category="general")]
        result = _config_enrich(tasks, config)
        assert result[0].assigned_agent == ""


# ── Semantic analysis (async — calls _semantic_analyze which is now async) ────

class TestSemanticAnalyze:
    async def test_single_task_no_edges(self):
        tasks = [SubTask(id="s1", description="do thing")]
        result_tasks, dag, score = await _semantic_analyze("do thing", tasks)
        assert dag.edges == []
        assert 0 <= score <= 100

    async def test_multiple_tasks_sequential_deps(self):
        tasks = [
            SubTask(id="s1", description="step one"),
            SubTask(id="s2", description="step two"),
            SubTask(id="s3", description="step three"),
        ]
        result_tasks, dag, score = await _semantic_analyze("do steps", tasks)
        assert len(dag.edges) >= 2  # sequential deps

    async def test_risk_keywords_detected(self):
        tasks = [SubTask(id="s1", description="delete production data")]
        result_tasks, dag, score = await _semantic_analyze("delete production data", tasks)
        assert result_tasks[0].risk_level == "high"

    async def test_score_bounded(self):
        tasks = [SubTask(id=f"s{i}", description="task") for i in range(20)]
        _, _, score = await _semantic_analyze("many tasks", tasks)
        assert score <= 100


# ── Strategy selection ────────────────────────────────────────────

class TestSelectStrategy:
    def test_single_group_parallel(self):
        dag = DependencyDAG(nodes=["a", "b", "c"])
        assert _select_strategy(dag, 10) == ExecutionStrategy.PARALLEL

    def test_linear_chain_hybrid(self):
        dag = DependencyDAG(nodes=["a", "b", "c"], edges=[("a", "b"), ("b", "c")])
        # Groups: [a],[b],[c] → max_group_size=1, len=3, 1≠3 → hybrid
        assert _select_strategy(dag, 30) == ExecutionStrategy.HYBRID

    def test_diamond_hybrid(self):
        dag = DependencyDAG(
            nodes=["a", "b", "c", "d"],
            edges=[("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")],
        )
        # Groups: [a], [b,c], [d] → max_group_size=2, len=4 → hybrid
        assert _select_strategy(dag, 50) == ExecutionStrategy.HYBRID


# ── Public API: analyze() (async — calls _semantic_analyze internally) ────────

class TestAnalyze:
    async def test_returns_analysis_result(self):
        result = await analyze("fix a bug")
        assert isinstance(result, AnalysisResult)

    async def test_task_hash_computed(self):
        result = await analyze("some task")
        assert len(result.task_hash) == 12

    async def test_task_hash_deterministic(self):
        r1 = await analyze("same task")
        r2 = await analyze("same task")
        assert r1.task_hash == r2.task_hash

    async def test_analysis_layers_includes_rule_and_semantic(self):
        result = await analyze("do thing")
        assert "rule" in result.analysis_layers
        assert "semantic" in result.analysis_layers

    async def test_config_layer_added(self):
        config = MagicMock()
        config.routing = {}
        result = await analyze("do thing", config=config)
        assert "config" in result.analysis_layers

    async def test_max_subtasks_limit(self):
        text = "\n".join(f"{i}. step {i}" for i in range(1, 50))
        result = await analyze(text, max_subtasks=5)
        assert len(result.sub_tasks) <= 5

    async def test_complexity_score_in_range(self):
        result = await analyze("some task")
        assert 0 <= result.complexity_score <= 100

    async def test_primary_category(self):
        result = await analyze("fix bug and fix another bug")
        assert result.primary_category in ("code", "general")

    async def test_decomposition_reason(self):
        result = await analyze("do thing")
        assert "score=" in result.decomposition_reason

    async def test_single_task_reason(self):
        result = await analyze("simple")
        assert "single task" in result.decomposition_reason

    async def test_multi_task_reason(self):
        text = "1. first\n2. second"
        result = await analyze(text)
        assert "sub-tasks" in result.decomposition_reason

    async def test_human_review_for_high_risk(self):
        result = await analyze("delete production data immediately")
        assert result.requires_human_review is True

    async def test_empty_task(self):
        result = await analyze("")
        assert isinstance(result, AnalysisResult)
        assert len(result.sub_tasks) >= 1

    async def test_suggested_agents_from_config(self):
        route = MagicMock()
        route.primary = "agent-x"
        config = MagicMock()
        config.routing = {"code": route}
        result = await analyze("refactor code", config=config)
        assert "agent-x" in result.suggested_agents
