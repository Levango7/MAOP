"""Tests for maop.core.scheduling.task_splitter + dashboard/routers/dag.py。

覆盖:
  - TaskSplitter.split 基本流程（单任务、多任务带依赖）
  - _parse_response 容错（markdown code fence、纯 JSON、非法 JSON）
  - _validate_dag（无环通过、有环失败、引用缺失失败）
  - _topological_sort 正确性
  - fallback 路径（LLM 不可用）
  - API 端点 POST /api/dag/auto-split（httpx.AsyncClient + ASGITransport）

mock LLM 调用，不实际调用 LLM API。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from maop.core.scheduling.task_splitter import TaskSplitError, TaskSplitter


# ── 辅助：构造 mock LLM provider ────────────────────────────────

def _make_mock_provider(response_content: str) -> MagicMock:
    """构造一个 mock BaseLLMProvider，其 chat() 返回指定 content。"""
    provider = MagicMock()
    provider.is_configured = True
    response = MagicMock()
    response.content = response_content
    provider.chat = AsyncMock(return_value=response)
    return provider


# ── TaskSplitter.split ──────────────────────────────────────────

class TestSplit:
    """split() 核心方法测试。"""

    @pytest.mark.asyncio
    async def test_split_single_task(self) -> None:
        """简单描述应返回至少 1 个子任务。"""
        llm_response = '{"subtasks": [{"id": "t1", "name": "do task", "description": "do task", "depends_on": []}], "edges": []}'
        provider = _make_mock_provider(llm_response)
        splitter = TaskSplitter(llm_provider=provider)
        result = await splitter.split("do task")
        assert "subtasks" in result
        assert "edges" in result
        assert "dag" in result
        assert len(result["subtasks"]) >= 1
        assert result["subtasks"][0]["id"] == "t1"

    @pytest.mark.asyncio
    async def test_split_with_dependencies(self) -> None:
        """复杂描述应生成有依赖的 DAG。"""
        llm_response = """{
            "subtasks": [
                {"id": "t1", "name": "research", "description": "调研竞品", "depends_on": []},
                {"id": "t2", "name": "analyze", "description": "分析数据", "depends_on": ["t1"]},
                {"id": "t3", "name": "report", "description": "生成报告", "depends_on": ["t2"]}
            ],
            "edges": [["t1", "t2"], ["t2", "t3"]]
        }"""
        provider = _make_mock_provider(llm_response)
        splitter = TaskSplitter(llm_provider=provider)
        result = await splitter.split("调研竞品并生成对比报告")
        assert len(result["subtasks"]) == 3
        assert ["t1", "t2"] in result["edges"]
        assert ["t2", "t3"] in result["edges"]
        # dag 字段结构
        assert "nodes" in result["dag"]
        assert "edges" in result["dag"]
        assert len(result["dag"]["nodes"]) == 3

    @pytest.mark.asyncio
    async def test_split_empty_description_raises(self) -> None:
        """空描述应抛 TaskSplitError。"""
        provider = _make_mock_provider("{}")
        splitter = TaskSplitter(llm_provider=provider)
        with pytest.raises(TaskSplitError):
            await splitter.split("")

    @pytest.mark.asyncio
    async def test_split_max_subtasks_limit(self) -> None:
        """超过 max_subtasks 的子任务应被截断。"""
        # 构造 5 个串行子任务
        subtasks = []
        for i in range(1, 6):
            deps = [f"t{i - 1}"] if i > 1 else []
            subtasks.append({"id": f"t{i}", "name": f"task{i}", "description": f"task{i}", "depends_on": deps})
        edges = [[f"t{i}", f"t{i + 1}"] for i in range(1, 5)]
        import json
        llm_response = json.dumps({"subtasks": subtasks, "edges": edges})
        provider = _make_mock_provider(llm_response)
        splitter = TaskSplitter(llm_provider=provider)
        result = await splitter.split("complex task", max_subtasks=3)
        assert len(result["subtasks"]) <= 3

    @pytest.mark.asyncio
    async def test_split_with_context(self) -> None:
        """带 context 参数应正常工作。"""
        llm_response = '{"subtasks": [{"id": "t1", "name": "task", "description": "task", "depends_on": []}], "edges": []}'
        provider = _make_mock_provider(llm_response)
        splitter = TaskSplitter(llm_provider=provider)
        result = await splitter.split("do something", context="extra context info")
        assert len(result["subtasks"]) == 1
        # 验证 LLM 被调用
        provider.chat.assert_called_once()


# ── _parse_response ─────────────────────────────────────────────

class TestParseResponse:
    """_parse_response 容错测试。"""

    def test_parse_response_pure_json(self) -> None:
        """纯 JSON 应正确解析。"""
        splitter = TaskSplitter()
        raw = '{"subtasks": [{"id": "t1"}], "edges": []}'
        result = splitter._parse_response(raw)
        assert result["subtasks"][0]["id"] == "t1"

    def test_parse_response_with_code_fence(self) -> None:
        """markdown json code fence 包裹的 JSON 应正确解析。"""
        splitter = TaskSplitter()
        raw = '```json\n{"subtasks": [{"id": "t1"}], "edges": []}\n```'
        result = splitter._parse_response(raw)
        assert result["subtasks"][0]["id"] == "t1"

    def test_parse_response_with_bare_code_fence(self) -> None:
        """无语言标识的 code fence 包裹的 JSON 应正确解析。"""
        splitter = TaskSplitter()
        raw = '```\n{"subtasks": [{"id": "t1"}], "edges": []}\n```'
        result = splitter._parse_response(raw)
        assert result["subtasks"][0]["id"] == "t1"

    def test_parse_response_with_surrounding_text(self) -> None:
        """JSON 前后带解释文字应能提取出 JSON。"""
        splitter = TaskSplitter()
        raw = 'Here is the result:\n{"subtasks": [{"id": "t1"}], "edges": []}\nDone.'
        result = splitter._parse_response(raw)
        assert result["subtasks"][0]["id"] == "t1"

    def test_parse_response_invalid_json(self) -> None:
        """无效 JSON 应抛 TaskSplitError。"""
        splitter = TaskSplitter()
        with pytest.raises(TaskSplitError):
            splitter._parse_response("not a json at all")

    def test_parse_response_empty_string(self) -> None:
        """空字符串应抛 TaskSplitError。"""
        splitter = TaskSplitter()
        with pytest.raises(TaskSplitError):
            splitter._parse_response("")

    def test_parse_response_non_object_top_level(self) -> None:
        """顶层非对象（如数组）应抛 TaskSplitError。"""
        splitter = TaskSplitter()
        with pytest.raises(TaskSplitError):
            splitter._parse_response("[1, 2, 3]")


# ── _validate_dag ───────────────────────────────────────────────

class TestValidateDag:
    """_validate_dag 测试。"""

    def test_validate_dag_no_cycle(self) -> None:
        """无环 DAG 应验证通过。"""
        splitter = TaskSplitter()
        subtasks = [
            {"id": "t1", "name": "a", "depends_on": []},
            {"id": "t2", "name": "b", "depends_on": ["t1"]},
            {"id": "t3", "name": "c", "depends_on": ["t2"]},
        ]
        edges = [["t1", "t2"], ["t2", "t3"]]
        assert splitter._validate_dag(subtasks, edges) is True

    def test_validate_dag_with_cycle(self) -> None:
        """有环 DAG 应验证失败。"""
        splitter = TaskSplitter()
        subtasks = [
            {"id": "t1", "name": "a", "depends_on": ["t2"]},
            {"id": "t2", "name": "b", "depends_on": ["t1"]},
        ]
        edges = [["t1", "t2"], ["t2", "t1"]]
        assert splitter._validate_dag(subtasks, edges) is False

    def test_validate_dag_invalid_reference(self) -> None:
        """引用不存在的节点应验证失败。"""
        splitter = TaskSplitter()
        subtasks = [
            {"id": "t1", "name": "a", "depends_on": ["t99"]},
        ]
        edges: list[list[str]] = []
        assert splitter._validate_dag(subtasks, edges) is False

    def test_validate_dag_empty_subtasks(self) -> None:
        """空 subtasks 应验证失败。"""
        splitter = TaskSplitter()
        assert splitter._validate_dag([], []) is False

    def test_validate_dag_edge_invalid_ref(self) -> None:
        """edge 引用不存在节点应验证失败。"""
        splitter = TaskSplitter()
        subtasks = [{"id": "t1", "name": "a", "depends_on": []}]
        edges = [["t1", "t99"]]
        assert splitter._validate_dag(subtasks, edges) is False

    def test_validate_dag_missing_id(self) -> None:
        """subtask 缺少 id 应验证失败。"""
        splitter = TaskSplitter()
        subtasks = [{"name": "a", "depends_on": []}]  # type: ignore[dict-item]
        assert splitter._validate_dag(subtasks, []) is False

    def test_validate_dag_single_node(self) -> None:
        """单节点无依赖应验证通过。"""
        splitter = TaskSplitter()
        subtasks = [{"id": "t1", "name": "a", "depends_on": []}]
        assert splitter._validate_dag(subtasks, []) is True


# ── _topological_sort ───────────────────────────────────────────

class TestTopologicalSort:
    """_topological_sort 测试。"""

    def test_topological_sort_linear(self) -> None:
        """线性链 t1 → t2 → t3 拓扑顺序正确。"""
        splitter = TaskSplitter()
        subtasks = [
            {"id": "t1", "depends_on": []},
            {"id": "t2", "depends_on": ["t1"]},
            {"id": "t3", "depends_on": ["t2"]},
        ]
        edges: list[list[str]] = []
        order = splitter._topological_sort(subtasks, edges)
        assert order.index("t1") < order.index("t2") < order.index("t3")

    def test_topological_sort_parallel(self) -> None:
        """并行节点 t1, t2 都指向 t3。"""
        splitter = TaskSplitter()
        subtasks = [
            {"id": "t1", "depends_on": []},
            {"id": "t2", "depends_on": []},
            {"id": "t3", "depends_on": ["t1", "t2"]},
        ]
        edges: list[list[str]] = []
        order = splitter._topological_sort(subtasks, edges)
        assert order.index("t1") < order.index("t3")
        assert order.index("t2") < order.index("t3")

    def test_topological_sort_with_cycle_raises(self) -> None:
        """有环应抛 TaskSplitError。"""
        splitter = TaskSplitter()
        subtasks = [
            {"id": "t1", "depends_on": ["t2"]},
            {"id": "t2", "depends_on": ["t1"]},
        ]
        edges: list[list[str]] = []
        with pytest.raises(TaskSplitError):
            splitter._topological_sort(subtasks, edges)

    def test_topological_sort_single_node(self) -> None:
        """单节点拓扑排序。"""
        splitter = TaskSplitter()
        subtasks = [{"id": "t1", "depends_on": []}]
        order = splitter._topological_sort(subtasks, [])
        assert order == ["t1"]


# ── fallback 路径 ───────────────────────────────────────────────

class TestFallback:
    """LLM 不可用时的 fallback 测试。"""

    @pytest.mark.asyncio
    async def test_fallback_no_llm(self) -> None:
        """LLM 不可用（provider=None）应返回单节点 DAG。"""
        splitter = TaskSplitter(llm_provider=None)
        # 强制 _resolve_provider 返回 None
        splitter._resolve_provider = lambda: None  # type: ignore[method-assign]
        result = await splitter.split("do something")
        assert len(result["subtasks"]) == 1
        assert result["subtasks"][0]["id"] == "t1"
        assert result["edges"] == []
        assert result["dag"]["nodes"] == result["subtasks"]

    @pytest.mark.asyncio
    async def test_fallback_on_llm_exception(self) -> None:
        """LLM 调用抛异常应走 fallback。"""
        provider = MagicMock()
        provider.is_configured = True
        provider.chat = AsyncMock(side_effect=RuntimeError("LLM down"))
        splitter = TaskSplitter(llm_provider=provider)
        result = await splitter.split("do something")
        assert len(result["subtasks"]) == 1
        assert result["subtasks"][0]["id"] == "t1"

    @pytest.mark.asyncio
    async def test_fallback_on_empty_response(self) -> None:
        """LLM 返回空 content 应走 fallback。"""
        provider = _make_mock_provider("")
        splitter = TaskSplitter(llm_provider=provider)
        result = await splitter.split("do something")
        assert len(result["subtasks"]) == 1

    @pytest.mark.asyncio
    async def test_fallback_on_invalid_json(self) -> None:
        """LLM 返回非法 JSON 应走 fallback。"""
        provider = _make_mock_provider("not a json")
        splitter = TaskSplitter(llm_provider=provider)
        result = await splitter.split("do something")
        assert len(result["subtasks"]) == 1

    @pytest.mark.asyncio
    async def test_fallback_on_dag_validation_fail(self) -> None:
        """LLM 返回有环 DAG 应走 fallback。"""
        llm_response = """{
            "subtasks": [
                {"id": "t1", "name": "a", "depends_on": ["t2"]},
                {"id": "t2", "name": "b", "depends_on": ["t1"]}
            ],
            "edges": [["t1", "t2"], ["t2", "t1"]]
        }"""
        provider = _make_mock_provider(llm_response)
        splitter = TaskSplitter(llm_provider=provider)
        result = await splitter.split("do something")
        # fallback 后是单节点
        assert len(result["subtasks"]) == 1
        assert result["subtasks"][0]["id"] == "t1"


# ── _build_prompt ───────────────────────────────────────────────

class TestBuildPrompt:
    """_build_prompt 测试。"""

    def test_build_prompt_basic(self) -> None:
        """基本 prompt 构造。"""
        splitter = TaskSplitter()
        prompt = splitter._build_prompt("do task", "", 5)
        assert "do task" in prompt
        assert "5" in prompt

    def test_build_prompt_with_context(self) -> None:
        """带 context 的 prompt 构造。"""
        splitter = TaskSplitter()
        prompt = splitter._build_prompt("do task", "extra info", 10)
        assert "do task" in prompt
        assert "extra info" in prompt


# ── API 端点 POST /api/dag/auto-split ──────────────────────────

class TestApiAutoSplitEndpoint:
    """POST /api/dag/auto-split 端点测试（httpx.AsyncClient + ASGITransport）。"""

    @pytest.fixture
    async def client(self) -> Any:
        """Async client bound to a minimal FastAPI app with dag router mounted.

        注入 admin 角色以通过 require_admin 校验。
        """
        from fastapi import FastAPI

        app = FastAPI()

        @app.middleware("http")
        async def _inject_admin(request, call_next):
            request.state.auth_roles = ["admin"]
            return await call_next(request)

        from maop.dashboard.routers import dag as dag_router
        app.include_router(dag_router.router)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    @pytest.mark.asyncio
    async def test_api_auto_split_success(self, client: Any) -> None:
        """成功拆分应返回 200 + success=True。"""
        llm_response = '{"subtasks": [{"id": "t1", "name": "task", "description": "task", "depends_on": []}], "edges": []}'
        with pytest.MonkeyPatch.context() as mp:
            # mock TaskSplitter._resolve_provider 返回 mock provider
            from maop.core.scheduling.task_splitter import TaskSplitter as _TS
            provider = _make_mock_provider(llm_response)
            mp.setattr(_TS, "_resolve_provider", lambda self: provider)
            resp = await client.post("/api/dag/auto-split", json={"description": "do task"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "data" in data
        assert len(data["data"]["subtasks"]) == 1

    @pytest.mark.asyncio
    async def test_api_auto_split_empty_description(self, client: Any) -> None:
        """只含空格的描述应返回 400（pydantic 通过但 TaskSplitter 拒绝）。"""
        resp = await client.post("/api/dag/auto-split", json={"description": "   "})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_api_auto_split_missing_description(self, client: Any) -> None:
        """缺少 description 字段应返回 422（pydantic 校验）。"""
        resp = await client.post("/api/dag/auto-split", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_api_auto_split_fallback(self, client: Any) -> None:
        """LLM 不可用走 fallback 应仍返回 200。"""
        with pytest.MonkeyPatch.context() as mp:
            from maop.core.scheduling.task_splitter import TaskSplitter as _TS
            mp.setattr(_TS, "_resolve_provider", lambda self: None)
            resp = await client.post("/api/dag/auto-split", json={"description": "do task"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]["subtasks"]) == 1

    @pytest.mark.asyncio
    async def test_api_dag_health(self, client: Any) -> None:
        """健康检查端点。"""
        resp = await client.get("/api/dag/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "auto-split" in data["features"]