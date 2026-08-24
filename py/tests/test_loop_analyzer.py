"""Coverage tests for maop.loop_analyzer — LLM extraction path + prompt/parser."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from maop.loop_analyzer import (
    RequirementAnalysis,
    _build_llm_extraction_prompt,
    _parse_llm_extraction,
    simple_analyze,
)

# ── _build_llm_extraction_prompt ──────────────────────────────

class TestBuildLlmExtractionPrompt:
    def test_prompt_structure(self):
        messages = _build_llm_extraction_prompt("implement a REST API")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "implement a REST API" in messages[1]["content"]
        assert "JSON" in messages[0]["content"]


# ── _parse_llm_extraction ─────────────────────────────────────

class TestParseLlmExtraction:
    def test_empty_content_returns_none(self):
        assert _parse_llm_extraction("", "task") is None
        assert _parse_llm_extraction("   ", "task") is None

    def test_invalid_json_returns_none(self):
        assert _parse_llm_extraction("not json", "task") is None

    def test_non_dict_json_returns_none(self):
        assert _parse_llm_extraction("[1,2,3]", "task") is None

    def test_markdown_fenced_json(self):
        content = '```json\n{"action_verbs": ["implement"], "tech_stack": ["fastapi"], "complexity": "simple"}\n```'
        result = _parse_llm_extraction(content, "task")
        assert result is not None
        assert result.action_verbs == ["implement"]
        assert result.tech_stack == ["fastapi"]
        assert result.complexity == "simple"

    def test_valid_json_full(self):
        content = json.dumps({
            "action_verbs": ["implement", "test"],
            "tech_stack": ["vue3", "postgres"],
            "complexity": "complex",
            "objectives": ["build feature X"],
            "boundaries": ["scope limited to Y"],
            "acceptance_criteria": ["tests pass"],
            "assumptions": ["db exists"],
            "risks": ["migration needed"],
            "clarified_task": "clarified version",
        })
        result = _parse_llm_extraction(content, "task")
        assert result is not None
        assert result.action_verbs == ["implement", "test"]
        assert result.tech_stack == ["vue3", "postgres"]
        assert result.complexity == "complex"
        assert result.objectives == ["build feature X"]
        assert result.clarified_task == "clarified version"

    def test_invalid_complexity_defaults_to_unknown(self):
        content = json.dumps({"complexity": "invalid_value"})
        result = _parse_llm_extraction(content, "task")
        assert result is not None
        assert result.complexity == "unknown"

    def test_non_list_fields_coerced(self):
        content = json.dumps({"action_verbs": "not a list"})
        result = _parse_llm_extraction(content, "task")
        assert result is not None
        # Should use default empty list
        assert result.action_verbs == []

    def test_list_with_non_string_items_filtered(self):
        content = json.dumps({"action_verbs": ["valid", 123, None, "also_valid"]})
        result = _parse_llm_extraction(content, "task")
        assert result is not None
        assert "valid" in result.action_verbs
        assert "also_valid" in result.action_verbs

    def test_clarified_task_empty_string_ignored(self):
        content = json.dumps({"clarified_task": ""})
        result = _parse_llm_extraction(content, "task")
        assert result is not None
        # Empty string is ignored, falls back to model default (empty string)
        assert result.clarified_task == ""


# ── simple_analyze ────────────────────────────────────────────

class TestSimpleAnalyze:
    @pytest.mark.asyncio
    async def test_rule_based_fallback(self):
        result = await simple_analyze("implement a REST API with FastAPI")
        assert isinstance(result, RequirementAnalysis)
        assert result.task == "implement a REST API with FastAPI"
        assert len(result.action_verbs) > 0

    @pytest.mark.asyncio
    async def test_llm_disabled_uses_rule_based(self):
        result = await simple_analyze("task", enable_llm=False)
        assert isinstance(result, RequirementAnalysis)

    @pytest.mark.asyncio
    async def test_llm_enabled_no_factory_uses_rule_based(self):
        result = await simple_analyze("task", enable_llm=True, llm_factory=None)
        assert isinstance(result, RequirementAnalysis)

    @pytest.mark.asyncio
    async def test_llm_provider_not_configured(self):
        llm_factory = MagicMock()
        provider = MagicMock()
        provider.is_configured = False
        llm_factory.get_provider = MagicMock(return_value=provider)
        result = await simple_analyze("task", enable_llm=True, llm_factory=llm_factory, model_name="gpt")
        assert isinstance(result, RequirementAnalysis)

    @pytest.mark.asyncio
    async def test_llm_provider_none(self):
        llm_factory = MagicMock()
        llm_factory.get_provider = MagicMock(return_value=None)
        result = await simple_analyze("task", enable_llm=True, llm_factory=llm_factory, model_name="gpt")
        assert isinstance(result, RequirementAnalysis)

    @pytest.mark.asyncio
    async def test_llm_success(self):
        llm_factory = MagicMock()
        provider = MagicMock()
        provider.is_configured = True
        llm_factory.get_provider = MagicMock(return_value=provider)
        fb_result = MagicMock()
        fb_result.response.content = json.dumps({
            "action_verbs": ["implement"],
            "tech_stack": ["fastapi"],
            "complexity": "moderate",
        })
        llm_factory.chat_with_fallback = AsyncMock(return_value=fb_result)
        result = await simple_analyze("task", enable_llm=True, llm_factory=llm_factory, model_name="gpt")
        assert result.action_verbs == ["implement"]
        assert result.tech_stack == ["fastapi"]
        assert result.complexity == "moderate"

    @pytest.mark.asyncio
    async def test_llm_invalid_json_falls_back(self):
        llm_factory = MagicMock()
        provider = MagicMock()
        provider.is_configured = True
        llm_factory.get_provider = MagicMock(return_value=provider)
        fb_result = MagicMock()
        fb_result.response.content = "not json"
        llm_factory.chat_with_fallback = AsyncMock(return_value=fb_result)
        result = await simple_analyze("task", enable_llm=True, llm_factory=llm_factory, model_name="gpt")
        assert isinstance(result, RequirementAnalysis)

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back(self):
        llm_factory = MagicMock()
        provider = MagicMock()
        provider.is_configured = True
        llm_factory.get_provider = MagicMock(return_value=provider)
        llm_factory.chat_with_fallback = AsyncMock(side_effect=RuntimeError("API down"))
        result = await simple_analyze("task", enable_llm=True, llm_factory=llm_factory, model_name="gpt")
        assert isinstance(result, RequirementAnalysis)


# ── Rule-based analysis with sections ─────────────────────────

class TestRuleBasedWithSections:
    @pytest.mark.asyncio
    async def test_task_with_objectives(self):
        task = """实现用户认证
目标: 提供安全的登录
边界: 仅限 API 端点
验收GNE: """
        result = await simple_analyze(task)
        assert isinstance(result, RequirementAnalysis)

    @pytest.mark.asyncio
    async def test_task_with_acceptance_criteria(self):
        task = "implement feature\n验收: all tests pass\n假设: db is available"
        result = await simple_analyze(task)
        assert isinstance(result, RequirementAnalysis)

    @pytest.mark.asyncio
    async def test_task_with_risks(self):
        task = "migrate database\n风险: data loss during migration"
        result = await simple_analyze(task)
        assert isinstance(result, RequirementAnalysis)

    @pytest.mark.asyncio
    async def test_chinese_task(self):
        result = await simple_analyze("实现一个REST API接口")
        assert isinstance(result, RequirementAnalysis)
        assert len(result.action_verbs) > 0

    @pytest.mark.asyncio
    async def test_empty_task(self):
        result = await simple_analyze("")
        assert isinstance(result, RequirementAnalysis)