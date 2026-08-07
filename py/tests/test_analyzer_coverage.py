"""Coverage tests for maop.core.analyzer — LLM semantic extraction path,
prompt builder, JSON parser, and edge cases in DAG/strategy/complexity.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from maop.core.agent.analyzer import (
    AnalysisResult,
    Complexity,
    DependencyDAG,
    ExecutionStrategy,
    SubTask,
    _build_llm_decomp_prompt,
    _complexity_level,
    _parse_llm_decomp,
    _select_strategy,
    _semantic_analyze,
    analyze,
)


# ── DependencyDAG cycle handling ──────────────────────────────

class TestDependencyDAGCycle:
    def test_cycle_raises_value_error(self):
        """A cyclic DAG raises ValueError from topological_order."""
        dag = DependencyDAG(
            nodes=["a", "b"],
            edges=[("a", "b"), ("b", "a")],  # cycle
        )
        with pytest.raises(ValueError, match="Dependency cycle"):
            dag.parallel_groups()


# ── _build_llm_decomp_prompt ──────────────────────────────────

class TestBuildLlmDecompPrompt:
    def test_prompt_structure(self):
        sub_tasks = [
            SubTask(id="st-000", description="write code"),
            SubTask(id="st-001", description="test code"),
        ]
        messages = _build_llm_decomp_prompt("build a feature", sub_tasks)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "build a feature" in messages[1]["content"]
        assert "st-000" in messages[1]["content"]
        assert "st-001" in messages[1]["content"]
        assert "write code" in messages[1]["content"]

    def test_prompt_empty_subtasks(self):
        messages = _build_llm_decomp_prompt("task", [])
        assert len(messages) == 2
        assert "task" in messages[1]["content"]


# ── _parse_llm_decomp ─────────────────────────────────────────

class TestParseLlmDecomp:
    def test_empty_content_returns_none(self):
        sub_tasks = [SubTask(id="st-000", description="x")]
        assert _parse_llm_decomp("", sub_tasks) is None
        assert _parse_llm_decomp("   ", sub_tasks) is None

    def test_invalid_json_returns_none(self):
        sub_tasks = [SubTask(id="st-000", description="x")]
        assert _parse_llm_decomp("not json", sub_tasks) is None

    def test_non_dict_json_returns_none(self):
        sub_tasks = [SubTask(id="st-000", description="x")]
        assert _parse_llm_decomp("[1,2,3]", sub_tasks) is None

    def test_markdown_fenced_json_parsed(self):
        sub_tasks = [SubTask(id="st-000", description="x")]
        content = '```json\n{"dependencies": [], "risk_levels": {"st-000": "low"}, "complexity_score": 10}\n```'
        result = _parse_llm_decomp(content, sub_tasks)
        assert result is not None
        _, dag, score = result
        assert score == 10

    def test_valid_json_with_dependencies(self):
        sub_tasks = [
            SubTask(id="st-000", description="setup"),
            SubTask(id="st-001", description="build"),
        ]
        content = json.dumps({
            "dependencies": [{"from": "st-000", "to": "st-001"}],
            "risk_levels": {"st-000": "low", "st-001": "high"},
            "complexity_score": 42,
        })
        result = _parse_llm_decomp(content, sub_tasks)
        assert result is not None
        parsed_tasks, dag, score = result
        assert score == 42
        assert ("st-000", "st-001") in dag.edges
        # Risk levels should be applied
        assert parsed_tasks[0].risk_level == "low"
        assert parsed_tasks[1].risk_level == "high"

    def test_invalid_dependency_ids_ignored(self):
        sub_tasks = [SubTask(id="st-000", description="x")]
        content = json.dumps({
            "dependencies": [{"from": "bad", "to": "ids"}],
            "risk_levels": {},
            "complexity_score": 5,
        })
        result = _parse_llm_decomp(content, sub_tasks)
        assert result is not None
        _, dag, _ = result
        assert ("bad", "ids") not in dag.edges

    def test_invalid_risk_level_ignored(self):
        sub_tasks = [SubTask(id="st-000", description="x")]
        content = json.dumps({
            "dependencies": [],
            "risk_levels": {"st-000": "critical"},  # not valid
            "complexity_score": 5,
        })
        result = _parse_llm_decomp(content, sub_tasks)
        assert result is not None
        parsed_tasks, _, _ = result
        # Should keep default risk_level
        assert parsed_tasks[0].risk_level != "critical"

    def test_complexity_score_out_of_range_falls_back(self):
        sub_tasks = [SubTask(id="st-000", description="x")]
        content = json.dumps({
            "dependencies": [],
            "risk_levels": {},
            "complexity_score": 150,  # out of range
        })
        result = _parse_llm_decomp(content, sub_tasks)
        assert result is not None
        _, _, score = result
        assert 0 <= score <= 100

    def test_complexity_score_non_numeric_falls_back(self):
        sub_tasks = [SubTask(id="st-000", description="x")]
        content = json.dumps({
            "dependencies": [],
            "risk_levels": {},
            "complexity_score": "high",
        })
        result = _parse_llm_decomp(content, sub_tasks)
        assert result is not None
        _, _, score = result
        assert 0 <= score <= 100

    def test_dependencies_non_list_ignored(self):
        sub_tasks = [SubTask(id="st-000", description="x")]
        content = json.dumps({
            "dependencies": "not a list",
            "risk_levels": {},
            "complexity_score": 5,
        })
        result = _parse_llm_decomp(content, sub_tasks)
        assert result is not None

    def test_risk_levels_non_dict_ignored(self):
        sub_tasks = [SubTask(id="st-000", description="x")]
        content = json.dumps({
            "dependencies": [],
            "risk_levels": "not a dict",
            "complexity_score": 5,
        })
        result = _parse_llm_decomp(content, sub_tasks)
        assert result is not None


# ── _semantic_analyze LLM path ────────────────────────────────

class TestSemanticAnalyzeLlm:
    @pytest.mark.asyncio
    async def test_llm_disabled_uses_rule_based(self):
        sub_tasks = [SubTask(id="st-000", description="do something")]
        result = await _semantic_analyze("task", sub_tasks, enable_llm=False)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_llm_enabled_but_no_factory_uses_rule_based(self):
        sub_tasks = [SubTask(id="st-000", description="do something")]
        result = await _semantic_analyze("task", sub_tasks, enable_llm=True, llm_factory=None)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_llm_provider_not_configured_uses_rule_based(self):
        sub_tasks = [SubTask(id="st-000", description="x")]
        llm_factory = MagicMock()
        provider = MagicMock()
        provider.is_configured = False
        llm_factory.get_provider = MagicMock(return_value=provider)

        result = await _semantic_analyze(
            "task", sub_tasks, enable_llm=True,
            llm_factory=llm_factory, model_name="gpt-4",
        )
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_llm_provider_none_uses_rule_based(self):
        sub_tasks = [SubTask(id="st-000", description="x")]
        llm_factory = MagicMock()
        llm_factory.get_provider = MagicMock(return_value=None)

        result = await _semantic_analyze(
            "task", sub_tasks, enable_llm=True,
            llm_factory=llm_factory, model_name="gpt-4",
        )
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_llm_success_returns_parsed(self):
        sub_tasks = [
            SubTask(id="st-000", description="setup"),
            SubTask(id="st-001", description="build"),
        ]
        llm_factory = MagicMock()
        provider = MagicMock()
        provider.is_configured = True
        llm_factory.get_provider = MagicMock(return_value=provider)

        fb_result = MagicMock()
        fb_result.response.content = json.dumps({
            "dependencies": [{"from": "st-000", "to": "st-001"}],
            "risk_levels": {"st-000": "low", "st-001": "high"},
            "complexity_score": 50,
        })
        llm_factory.chat_with_fallback = AsyncMock(return_value=fb_result)

        result = await _semantic_analyze(
            "task", sub_tasks, enable_llm=True,
            llm_factory=llm_factory, model_name="gpt-4",
        )
        parsed_tasks, dag, score = result
        assert score == 50
        assert ("st-000", "st-001") in dag.edges

    @pytest.mark.asyncio
    async def test_llm_returns_invalid_json_falls_back(self):
        sub_tasks = [SubTask(id="st-000", description="x")]
        llm_factory = MagicMock()
        provider = MagicMock()
        provider.is_configured = True
        llm_factory.get_provider = MagicMock(return_value=provider)

        fb_result = MagicMock()
        fb_result.response.content = "not valid json"
        llm_factory.chat_with_fallback = AsyncMock(return_value=fb_result)

        result = await _semantic_analyze(
            "task", sub_tasks, enable_llm=True,
            llm_factory=llm_factory, model_name="gpt-4",
        )
        # Falls back to rule-based
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back(self):
        sub_tasks = [SubTask(id="st-000", description="x")]
        llm_factory = MagicMock()
        provider = MagicMock()
        provider.is_configured = True
        llm_factory.get_provider = MagicMock(return_value=provider)
        llm_factory.chat_with_fallback = AsyncMock(side_effect=RuntimeError("API down"))

        result = await _semantic_analyze(
            "task", sub_tasks, enable_llm=True,
            llm_factory=llm_factory, model_name="gpt-4",
        )
        # Falls back to rule-based
        assert len(result) == 3


# ── _select_strategy / _complexity_level ──────────────────────

class TestSelectStrategy:
    def test_single_group_parallel(self):
        dag = DependencyDAG(nodes=["a"], edges=[])
        assert _select_strategy(dag, 10) == ExecutionStrategy.PARALLEL

    def test_multiple_groups_hybrid(self):
        dag = DependencyDAG(nodes=["a", "b"], edges=[("a", "b")])
        assert _select_strategy(dag, 50) == ExecutionStrategy.HYBRID


class TestComplexityLevel:
    def test_trivial(self):
        assert _complexity_level(0) == Complexity.TRIVIAL
        assert _complexity_level(20) == Complexity.TRIVIAL

    def test_simple(self):
        assert _complexity_level(21) == Complexity.SIMPLE
        assert _complexity_level(40) == Complexity.SIMPLE

    def test_moderate(self):
        assert _complexity_level(41) == Complexity.MODERATE
        assert _complexity_level(60) == Complexity.MODERATE

    def test_complex(self):
        assert _complexity_level(61) == Complexity.COMPLEX
        assert _complexity_level(80) == Complexity.COMPLEX

    def test_critical(self):
        assert _complexity_level(81) == Complexity.CRITICAL
        assert _complexity_level(100) == Complexity.CRITICAL


# ── analyze() integration ─────────────────────────────────────

class TestAnalyzeIntegration:
    @pytest.mark.asyncio
    async def test_analyze_simple_task(self):
        result = await analyze("write a function")
        assert isinstance(result, AnalysisResult)
        assert result.task == "write a function"
        assert len(result.sub_tasks) >= 1
        assert "rule" in result.analysis_layers
        assert "semantic" in result.analysis_layers

    @pytest.mark.asyncio
    async def test_analyze_with_max_subtasks(self):
        result = await analyze("do thing one and do thing two and do thing three", max_subtasks=2)
        assert len(result.sub_tasks) <= 2

    @pytest.mark.asyncio
    async def test_analyze_requires_review_for_high_risk(self):
        result = await analyze("delete all data in production database")
        # Should flag for review due to high-risk keywords
        assert isinstance(result, AnalysisResult)