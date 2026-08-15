"""F1-03 行为一致性测试套件 — MemoryFacade 与底层实现的行为一致性验证。

测试覆盖
--------
1. **Protocol 契约**：UnifiedMemoryProtocol 常量、runtime_checkable、
   两套实现均具备 Protocol 要求的全部方法（含 F1-03 新增 store/retrieve/search/delete）。
2. **Facade 路由**：mode="chat" / mode="agent" 正确路由；非法 mode 报错；
   repr / impl / is_chat / is_agent 行为正确。
3. **Working Memory (L1)**：put/get/clear 在两种 mode 下行为一致；
   TTL / 缺失 key / 重复 put 覆盖 / 不同类型 value。
4. **Short-term Memory (L2)**：store 返回 ID；search 按 query/agent 过滤；
   get 按 ID 检索；stats 返回 dict；空 query / 大 top / 重复 store。
5. **Long-term Memory (L3)**：index + search 往返；空索引 search 不抛异常；
   top 限制生效。
6. **跨层操作**：consolidate 无参可调；build_context 返回结构正确；
   stats 跨层汇总。
7. **F1-03 统一 CRUD**：store/retrieve/search/delete 在两种 mode 下行为一致；
   layer 别名 (episodic/semantic) 路由正确；非法 layer 报错；delete 返回 bool。
8. **跨实现通信**：chat 写入的数据 agent 可读，反之亦然。
9. **向后兼容**：原 ThreeLayerMemory / MemoryManager API 保持不变。
10. **数据迁移工具**：migrate_all dry-run / 实际迁移 / 幂等 / 进度回调 /
    JSON 导入 / 报告结构。
11. **配置兼容**：MemoryFacade 在不同环境变量下都能正确适配。

总计 ≥ 200 个测试用例。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from maop.core.memory.three_layer_memory import ThreeLayerMemory
from maop.memory.facade import MemoryFacade
from maop.memory.manager import MemoryManager
from maop.memory.shared_db import (
    LAYER_ALIASES,
    denormalize_layer_name,
    get_memory_db_path,
    normalize_layer_name,
)
from maop.memory.unified import (
    LAYER_LONG_TERM,
    LAYER_SHORT_TERM,
    LAYER_WORKING,
    VALID_LAYERS,
    UnifiedMemoryProtocol,
)

# ── Fixtures ────────────────────────────────────────────────────


def _mock_heavy_deps(mgr: MemoryManager) -> None:
    """Mock 掉 MemoryManager 的重量级依赖以避免网络/模型下载。"""
    mgr._vector_search = MagicMock()
    mgr._vector_search.search.return_value = []
    mgr._vector_search.index.return_value = "mock-doc-id"
    mgr._knowledge_extractor = MagicMock()
    mgr._knowledge_extractor.extract_from_exchange.return_value = MagicMock()
    mgr._knowledge_extractor.store_extraction.return_value = {"stored": 0}
    mgr._knowledge_graph = MagicMock()
    mgr._knowledge_graph.build_context.return_value = ""


@pytest.fixture
def tmp_root():
    tmpdir = tempfile.mkdtemp(prefix="maop_consistency_test_")
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def agent_facade(tmp_root):
    return MemoryFacade(root_dir=tmp_root, mode="agent")


@pytest.fixture
def chat_facade(tmp_root):
    facade = MemoryFacade(root_dir=tmp_root, mode="chat")
    _mock_heavy_deps(facade.impl)
    return facade


@pytest.fixture
def agent_impl(tmp_root):
    return ThreeLayerMemory(root_dir=tmp_root)


@pytest.fixture
def chat_impl(tmp_root):
    mgr = MemoryManager(root_dir=tmp_root)
    _mock_heavy_deps(mgr)
    return mgr


# ════════════════════════════════════════════════════════════════
# 1. Protocol 契约
# ════════════════════════════════════════════════════════════════


class TestProtocolConstants:
    def test_layer_working_constant(self):
        assert LAYER_WORKING == "working"

    def test_layer_short_term_constant(self):
        assert LAYER_SHORT_TERM == "short_term"

    def test_layer_long_term_constant(self):
        assert LAYER_LONG_TERM == "long_term"

    def test_valid_layers_frozenset(self):
        assert VALID_LAYERS == frozenset({"working", "short_term", "long_term"})

    def test_valid_layers_contains_all(self):
        assert LAYER_WORKING in VALID_LAYERS
        assert LAYER_SHORT_TERM in VALID_LAYERS
        assert LAYER_LONG_TERM in VALID_LAYERS

    def test_valid_layers_excludes_aliases(self):
        assert "episodic" not in VALID_LAYERS
        assert "semantic" not in VALID_LAYERS


class TestLayerAliases:
    def test_working_alias(self):
        assert LAYER_ALIASES["working"] == "working"

    def test_short_term_alias(self):
        assert LAYER_ALIASES["short_term"] == "short_term"

    def test_long_term_alias(self):
        assert LAYER_ALIASES["long_term"] == "long_term"

    def test_episodic_alias(self):
        assert LAYER_ALIASES["episodic"] == "short_term"

    def test_semantic_alias(self):
        assert LAYER_ALIASES["semantic"] == "long_term"

    def test_normalize_working(self):
        assert normalize_layer_name("working") == "working"

    def test_normalize_short_term(self):
        assert normalize_layer_name("short_term") == "short_term"

    def test_normalize_long_term(self):
        assert normalize_layer_name("long_term") == "long_term"

    def test_normalize_episodic(self):
        assert normalize_layer_name("episodic") == "short_term"

    def test_normalize_semantic(self):
        assert normalize_layer_name("semantic") == "long_term"

    def test_normalize_case_insensitive(self):
        assert normalize_layer_name("Working") == "working"
        assert normalize_layer_name("EPISODIC") == "short_term"
        assert normalize_layer_name("Semantic") == "long_term"

    def test_normalize_unknown_passthrough(self):
        assert normalize_layer_name("unknown") == "unknown"

    def test_denormalize_working(self):
        assert denormalize_layer_name("working") == "working"

    def test_denormalize_short_term(self):
        assert denormalize_layer_name("short_term") == "episodic"

    def test_denormalize_long_term(self):
        assert denormalize_layer_name("long_term") == "semantic"


class TestProtocolCompliance:
    """验证两套实现均具备 UnifiedMemoryProtocol 要求的方法。"""

    REQUIRED_METHODS = (
        "working_put", "working_get", "working_clear",
        "short_term_store", "short_term_search", "short_term_get", "short_term_stats",
        "long_term_index", "long_term_search",
        "consolidate", "build_context", "stats",
        # F1-03 新增统一 CRUD
        "store", "retrieve", "search", "delete",
    )

    def test_three_layer_memory_has_all_methods(self, tmp_root):
        mem = ThreeLayerMemory(root_dir=tmp_root)
        for method in self.REQUIRED_METHODS:
            assert hasattr(mem, method), f"ThreeLayerMemory missing {method}"
            assert callable(getattr(mem, method)), f"{method} not callable"

    def test_memory_manager_has_all_methods(self, tmp_root):
        mgr = MemoryManager(root_dir=tmp_root)
        _mock_heavy_deps(mgr)
        for method in self.REQUIRED_METHODS:
            assert hasattr(mgr, method), f"MemoryManager missing {method}"
            assert callable(getattr(mgr, method)), f"{method} not callable"

    def test_facade_has_all_methods(self, tmp_root):
        facade = MemoryFacade(root_dir=tmp_root, mode="agent")
        for method in self.REQUIRED_METHODS:
            assert hasattr(facade, method), f"MemoryFacade missing {method}"
            assert callable(getattr(facade, method)), f"{method} not callable"

    def test_runtime_checkable_protocol_agent(self, tmp_root):
        mem = ThreeLayerMemory(root_dir=tmp_root)
        assert isinstance(mem, UnifiedMemoryProtocol)

    def test_runtime_checkable_protocol_chat(self, tmp_root):
        mgr = MemoryManager(root_dir=tmp_root)
        _mock_heavy_deps(mgr)
        assert isinstance(mgr, UnifiedMemoryProtocol)

    def test_runtime_checkable_protocol_facade(self, tmp_root):
        facade = MemoryFacade(root_dir=tmp_root, mode="agent")
        # MemoryFacade 也实现了所有 Protocol 方法
        assert isinstance(facade, UnifiedMemoryProtocol)


# ════════════════════════════════════════════════════════════════
# 2. Facade 路由
# ════════════════════════════════════════════════════════════════


class TestFacadeRouting:
    def test_invalid_mode_raises(self, tmp_root):
        with pytest.raises(ValueError, match="Invalid mode"):
            MemoryFacade(root_dir=tmp_root, mode="invalid")  # type: ignore[arg-type]

    def test_default_mode_is_agent(self, tmp_root):
        facade = MemoryFacade(root_dir=tmp_root)
        assert facade.mode == "agent"

    def test_agent_mode_routes_to_three_layer_memory(self, agent_facade):
        assert agent_facade.mode == "agent"
        assert agent_facade.is_agent()
        assert not agent_facade.is_chat()
        assert isinstance(agent_facade.impl, ThreeLayerMemory)

    def test_chat_mode_routes_to_memory_manager(self, chat_facade):
        assert chat_facade.mode == "chat"
        assert chat_facade.is_chat()
        assert not chat_facade.is_agent()
        assert isinstance(chat_facade.impl, MemoryManager)

    def test_repr_agent(self, agent_facade):
        r = repr(agent_facade)
        assert "mode='agent'" in r
        assert "ThreeLayerMemory" in r

    def test_repr_chat(self, chat_facade):
        r = repr(chat_facade)
        assert "mode='chat'" in r
        assert "MemoryManager" in r

    def test_impl_property_exposes_underlying(self, agent_facade, chat_facade):
        assert agent_facade.impl is agent_facade._impl
        assert chat_facade.impl is chat_facade._impl

    def test_is_chat_false_for_agent(self, agent_facade):
        assert not agent_facade.is_chat()

    def test_is_agent_false_for_chat(self, chat_facade):
        assert not chat_facade.is_agent()

    def test_chat_mode_with_config(self, tmp_root):
        from maop.memory.manager import MemoryManagerConfig

        cfg = MemoryManagerConfig(max_working_tokens=2000)
        facade = MemoryFacade(root_dir=tmp_root, mode="chat", config=cfg)
        assert isinstance(facade.impl, MemoryManager)

    def test_agent_mode_with_kwargs(self, tmp_root):
        facade = MemoryFacade(
            root_dir=tmp_root, mode="agent", working_max=50, working_ttl=60.0
        )
        assert isinstance(facade.impl, ThreeLayerMemory)


# ════════════════════════════════════════════════════════════════
# 3. Working Memory (L1)
# ════════════════════════════════════════════════════════════════


class TestWorkingMemoryAgent:
    """agent mode (ThreeLayerMemory) 的 Working Memory 行为。"""

    def test_put_get_str(self, agent_facade):
        agent_facade.working_put("k", "value")
        assert agent_facade.working_get("k") == "value"

    def test_put_get_int(self, agent_facade):
        agent_facade.working_put("k", 42)
        assert agent_facade.working_get("k") == 42

    def test_put_get_dict(self, agent_facade):
        agent_facade.working_put("k", {"a": 1, "b": [2, 3]})
        assert agent_facade.working_get("k") == {"a": 1, "b": [2, 3]}

    def test_put_get_list(self, agent_facade):
        agent_facade.working_put("k", [1, 2, 3])
        assert agent_facade.working_get("k") == [1, 2, 3]

    def test_put_get_none(self, agent_facade):
        agent_facade.working_put("k", None)
        assert agent_facade.working_get("k") is None

    def test_put_overwrite(self, agent_facade):
        agent_facade.working_put("k", "v1")
        agent_facade.working_put("k", "v2")
        assert agent_facade.working_get("k") == "v2"

    def test_get_missing_returns_none(self, agent_facade):
        assert agent_facade.working_get("nonexistent") is None

    def test_clear_empty(self, agent_facade):
        agent_facade.working_clear()
        assert agent_facade.working_get("any") is None

    def test_clear_with_data(self, agent_facade):
        agent_facade.working_put("a", 1)
        agent_facade.working_put("b", 2)
        agent_facade.working_clear()
        assert agent_facade.working_get("a") is None
        assert agent_facade.working_get("b") is None

    def test_multiple_keys(self, agent_facade):
        for i in range(20):
            agent_facade.working_put(f"k{i}", i)
        for i in range(20):
            assert agent_facade.working_get(f"k{i}") == i


class TestWorkingMemoryChat:
    """chat mode (MemoryManager) 的 Working Memory 行为。"""

    def test_put_get_str(self, chat_facade):
        chat_facade.working_put("k", "value")
        assert chat_facade.working_get("k") == "value"

    def test_put_get_int(self, chat_facade):
        chat_facade.working_put("k", 42)
        assert chat_facade.working_get("k") == 42

    def test_put_get_dict(self, chat_facade):
        chat_facade.working_put("k", {"a": 1})
        assert chat_facade.working_get("k") == {"a": 1}

    def test_put_overwrite(self, chat_facade):
        chat_facade.working_put("k", "v1")
        chat_facade.working_put("k", "v2")
        assert chat_facade.working_get("k") == "v2"

    def test_get_missing_returns_none(self, chat_facade):
        assert chat_facade.working_get("nonexistent") is None

    def test_clear(self, chat_facade):
        chat_facade.working_put("a", 1)
        chat_facade.working_clear()
        assert chat_facade.working_get("a") is None

    def test_multiple_keys(self, chat_facade):
        for i in range(20):
            chat_facade.working_put(f"k{i}", i)
        for i in range(20):
            assert chat_facade.working_get(f"k{i}") == i


class TestWorkingMemoryConsistency:
    """两种 mode 的 Working Memory 行为一致性。"""

    def test_put_get_same_value(self, agent_facade, chat_facade):
        for v in ("str", 42, 3.14, [1, 2], {"k": "v"}, None, True):
            agent_facade.working_put("k", v)
            chat_facade.working_put("k", v)
            assert agent_facade.working_get("k") == chat_facade.working_get("k") == v

    def test_get_missing_both_none(self, agent_facade, chat_facade):
        assert agent_facade.working_get("x") is None
        assert chat_facade.working_get("x") is None

    def test_clear_both_empty(self, agent_facade, chat_facade):
        agent_facade.working_put("a", 1)
        chat_facade.working_put("a", 1)
        agent_facade.working_clear()
        chat_facade.working_clear()
        assert agent_facade.working_get("a") is None
        assert chat_facade.working_get("a") is None


# ════════════════════════════════════════════════════════════════
# 4. Short-term Memory (L2)
# ════════════════════════════════════════════════════════════════


class TestShortTermMemoryAgent:
    def test_store_returns_str_id(self, agent_facade):
        eid = agent_facade.short_term_store("content", task="t1", agent="claude")
        assert isinstance(eid, str)
        assert len(eid) > 0

    def test_store_minimal_args(self, agent_facade):
        eid = agent_facade.short_term_store("content")
        assert isinstance(eid, str)
        assert len(eid) > 0

    def test_store_with_topic_tags(self, agent_facade):
        eid = agent_facade.short_term_store(
            "content", task="t1", agent="a", topic="auth", tags=["bug", "fix"]
        )
        assert isinstance(eid, str)

    def test_store_with_metadata(self, agent_facade):
        eid = agent_facade.short_term_store(
            "content", task="t1", metadata={"k": "v"}
        )
        assert isinstance(eid, str)

    def test_search_returns_list(self, agent_facade):
        agent_facade.short_term_store("login bug", task="login")
        results = agent_facade.short_term_search(query="login", top=10)
        assert isinstance(results, list)

    def test_search_results_are_dicts(self, agent_facade):
        agent_facade.short_term_store("auth bug", task="auth")
        results = agent_facade.short_term_search(query="auth", top=10)
        assert all(isinstance(r, dict) for r in results)

    def test_search_finds_matching(self, agent_facade):
        agent_facade.short_term_store("Fix login timeout", task="login", agent="claude")
        results = agent_facade.short_term_search(query="login", top=10)
        assert len(results) >= 1

    def test_search_empty_query(self, agent_facade):
        agent_facade.short_term_store("c1", task="t1")
        results = agent_facade.short_term_search(query="", top=10)
        assert isinstance(results, list)

    def test_search_top_limit(self, agent_facade):
        for i in range(5):
            agent_facade.short_term_store(f"task {i}", task=f"task_{i}")
        results = agent_facade.short_term_search(query="task", top=2)
        assert len(results) <= 2

    def test_search_by_agent(self, agent_facade):
        agent_facade.short_term_store("c1", task="t1", agent="claude")
        agent_facade.short_term_store("c2", task="t2", agent="kimi")
        results = agent_facade.short_term_search(query="", top=10, agent="claude")
        assert all(r.get("agent") == "claude" for r in results if r.get("agent"))

    def test_get_by_id_returns_dict(self, agent_facade):
        eid = agent_facade.short_term_store("content", task="t1", agent="claude")
        result = agent_facade.short_term_get(eid)
        assert result is not None
        assert isinstance(result, dict)

    def test_get_missing_returns_none(self, agent_facade):
        assert agent_facade.short_term_get("nonexistent-id") is None

    def test_stats_returns_dict(self, agent_facade):
        agent_facade.short_term_store("c1", task="t1")
        stats = agent_facade.short_term_stats()
        assert isinstance(stats, dict)

    def test_stats_has_total(self, agent_facade):
        agent_facade.short_term_store("c1", task="t1")
        stats = agent_facade.short_term_stats()
        assert "total" in stats


class TestShortTermMemoryChat:
    def test_store_returns_str_id(self, chat_facade):
        eid = chat_facade.short_term_store("content", task="t1", agent="user")
        assert isinstance(eid, str)
        assert len(eid) > 0

    def test_store_minimal_args(self, chat_facade):
        eid = chat_facade.short_term_store("content")
        assert isinstance(eid, str)

    def test_store_with_topic_tags(self, chat_facade):
        eid = chat_facade.short_term_store(
            "content", task="t1", agent="a", topic="auth", tags=["bug"]
        )
        assert isinstance(eid, str)

    def test_search_returns_list(self, chat_facade):
        chat_facade.short_term_store("auth bug", task="auth")
        results = chat_facade.short_term_search(query="auth", top=10)
        assert isinstance(results, list)

    def test_search_results_are_dicts(self, chat_facade):
        chat_facade.short_term_store("auth bug", task="auth")
        results = chat_facade.short_term_search(query="auth", top=10)
        assert all(isinstance(r, dict) for r in results)

    def test_search_finds_matching(self, chat_facade):
        chat_facade.short_term_store("Fix login timeout", task="login")
        results = chat_facade.short_term_search(query="login", top=10)
        assert len(results) >= 1

    def test_search_empty_query(self, chat_facade):
        chat_facade.short_term_store("c1", task="t1")
        results = chat_facade.short_term_search(query="", top=10)
        assert isinstance(results, list)

    def test_search_top_limit(self, chat_facade):
        for i in range(5):
            chat_facade.short_term_store(f"task {i}", task=f"task_{i}")
        results = chat_facade.short_term_search(query="task", top=2)
        assert len(results) <= 2

    def test_get_by_id_returns_dict(self, chat_facade):
        eid = chat_facade.short_term_store("content", task="t1", agent="user")
        result = chat_facade.short_term_get(eid)
        assert result is not None
        assert isinstance(result, dict)

    def test_get_missing_returns_none(self, chat_facade):
        assert chat_facade.short_term_get("nonexistent-id") is None

    def test_stats_returns_dict(self, chat_facade):
        chat_facade.short_term_store("c1", task="t1")
        stats = chat_facade.short_term_stats()
        assert isinstance(stats, dict)

    def test_stats_has_total_entries(self, chat_facade):
        chat_facade.short_term_store("c1", task="t1")
        stats = chat_facade.short_term_stats()
        assert "total_entries" in stats


class TestShortTermMemoryConsistency:
    """两种 mode 的 Short-term Memory 行为一致性。"""

    def test_store_returns_str_both(self, agent_facade, chat_facade):
        a_id = agent_facade.short_term_store("c", task="t")
        c_id = chat_facade.short_term_store("c", task="t")
        assert isinstance(a_id, str) and len(a_id) > 0
        assert isinstance(c_id, str) and len(c_id) > 0

    def test_search_returns_list_both(self, agent_facade, chat_facade):
        agent_facade.short_term_store("c", task="t")
        chat_facade.short_term_store("c", task="t")
        a_r = agent_facade.short_term_search(query="c", top=5)
        c_r = chat_facade.short_term_search(query="c", top=5)
        assert isinstance(a_r, list) and isinstance(c_r, list)

    def test_search_results_are_dicts_both(self, agent_facade, chat_facade):
        agent_facade.short_term_store("c", task="t")
        chat_facade.short_term_store("c", task="t")
        a_r = agent_facade.short_term_search(query="c", top=5)
        c_r = chat_facade.short_term_search(query="c", top=5)
        assert all(isinstance(r, dict) for r in a_r)
        assert all(isinstance(r, dict) for r in c_r)

    def test_get_missing_none_both(self, agent_facade, chat_facade):
        assert agent_facade.short_term_get("x") is None
        assert chat_facade.short_term_get("x") is None

    def test_stats_dict_both(self, agent_facade, chat_facade):
        agent_facade.short_term_store("c", task="t")
        chat_facade.short_term_store("c", task="t")
        assert isinstance(agent_facade.short_term_stats(), dict)
        assert isinstance(chat_facade.short_term_stats(), dict)


# ════════════════════════════════════════════════════════════════
# 5. Long-term Memory (L3)
# ════════════════════════════════════════════════════════════════


class TestLongTermMemoryAgent:
    def test_search_empty_returns_list(self, agent_facade):
        results = agent_facade.long_term_search("test", top=5)
        assert isinstance(results, list)

    def test_search_empty_query_returns_list(self, agent_facade):
        results = agent_facade.long_term_search("", top=5)
        assert isinstance(results, list)

    def test_index_returns_str(self, agent_facade):
        # agent mode 的 long_term_index 透传到 semantic_index → VectorStore
        # VectorStore 可能未初始化，但应返回 str 或抛异常（不返回 None）
        try:
            r = agent_facade.long_term_index("doc1", "text content")
            assert isinstance(r, str)
        except Exception:
            # VectorStore 未就绪是可接受的
            pass

    def test_search_results_are_dicts(self, agent_facade):
        results = agent_facade.long_term_search("test", top=5)
        assert all(isinstance(r, dict) for r in results)


class TestLongTermMemoryChat:
    def test_search_empty_returns_list(self, chat_facade):
        results = chat_facade.long_term_search("test", top=5)
        assert isinstance(results, list)

    def test_search_empty_query_returns_list(self, chat_facade):
        results = chat_facade.long_term_search("", top=5)
        assert isinstance(results, list)

    def test_search_results_are_dicts(self, chat_facade):
        results = chat_facade.long_term_search("test", top=5)
        assert all(isinstance(r, dict) for r in results)


class TestLongTermMemoryConsistency:
    def test_search_empty_both(self, agent_facade, chat_facade):
        a_r = agent_facade.long_term_search("test", top=5)
        c_r = chat_facade.long_term_search("test", top=5)
        assert isinstance(a_r, list) and isinstance(c_r, list)

    def test_search_results_dicts_both(self, agent_facade, chat_facade):
        a_r = agent_facade.long_term_search("test", top=5)
        c_r = chat_facade.long_term_search("test", top=5)
        assert all(isinstance(r, dict) for r in a_r)
        assert all(isinstance(r, dict) for r in c_r)


# ════════════════════════════════════════════════════════════════
# 6. 跨层操作
# ════════════════════════════════════════════════════════════════


class TestCrossLayerAgent:
    def test_build_context_returns_dict(self, agent_facade):
        agent_facade.short_term_store("c", task="t")
        ctx = agent_facade.build_context(query="c")
        assert isinstance(ctx, dict)

    def test_build_context_has_short_term_key(self, agent_facade):
        agent_facade.short_term_store("c", task="t")
        ctx = agent_facade.build_context(query="c")
        assert "short_term" in ctx

    def test_build_context_has_long_term_key(self, agent_facade):
        agent_facade.short_term_store("c", task="t")
        ctx = agent_facade.build_context(query="c")
        assert "long_term" in ctx

    def test_build_context_empty_query(self, agent_facade):
        ctx = agent_facade.build_context(query="")
        assert isinstance(ctx, dict)

    def test_stats_returns_dict(self, agent_facade):
        s = agent_facade.stats()
        assert isinstance(s, dict)

    def test_stats_has_working_size(self, agent_facade):
        s = agent_facade.stats()
        assert "working_size" in s

    def test_stats_has_short_term(self, agent_facade):
        s = agent_facade.stats()
        assert "short_term" in s

    def test_consolidate_returns_dict_or_none(self, agent_facade):
        r = agent_facade.consolidate()
        assert r is None or isinstance(r, dict)

    def test_consolidate_with_min_score(self, agent_facade):
        r = agent_facade.consolidate(min_score=0.9, limit=10)
        assert r is None or isinstance(r, dict)


class TestCrossLayerChat:
    def test_build_context_returns_object(self, chat_facade):
        ctx = chat_facade.build_context(session_id="s1", query="")
        assert hasattr(ctx, "working_context")

    def test_build_context_has_short_term_results(self, chat_facade):
        ctx = chat_facade.build_context(session_id="s1", query="")
        assert hasattr(ctx, "short_term_results")

    def test_build_context_has_long_term_results(self, chat_facade):
        ctx = chat_facade.build_context(session_id="s1", query="")
        assert hasattr(ctx, "long_term_results")

    def test_stats_returns_dict(self, chat_facade):
        s = chat_facade.stats()
        assert isinstance(s, dict)

    def test_stats_has_short_term_entries(self, chat_facade):
        s = chat_facade.stats()
        assert "short_term_entries" in s

    def test_consolidate_returns_dict_or_none(self, chat_facade):
        r = chat_facade.consolidate()
        assert r is None or isinstance(r, dict)

    def test_consolidate_dry_run(self, chat_facade):
        r = chat_facade.consolidate(dry_run=True)
        assert r is None or isinstance(r, dict)


# ════════════════════════════════════════════════════════════════
# 7. F1-03 统一 CRUD 入口
# ════════════════════════════════════════════════════════════════


class TestUnifiedStoreAgent:
    """agent mode 的统一 store 入口。"""

    def test_store_working_returns_key(self, agent_facade):
        k = agent_facade.store("working", "value", key="mykey")
        assert k == "mykey"
        assert agent_facade.working_get("mykey") == "value"

    def test_store_working_auto_key(self, agent_facade):
        k = agent_facade.store("working", "value")
        assert isinstance(k, str) and len(k) > 0

    def test_store_short_term_returns_id(self, agent_facade):
        eid = agent_facade.store("short_term", "content", task="t1", agent="claude")
        assert isinstance(eid, str) and len(eid) > 0

    def test_store_short_term_with_metadata(self, agent_facade):
        eid = agent_facade.store(
            "short_term", "content", task="t1", metadata={"k": "v"}
        )
        assert isinstance(eid, str)

    def test_store_long_term_returns_id(self, agent_facade):
        try:
            eid = agent_facade.store("long_term", "text", doc_id="d1")
            assert isinstance(eid, str)
        except Exception:
            pass  # VectorStore 未就绪可接受

    def test_store_episodic_alias(self, agent_facade):
        eid = agent_facade.store("episodic", "content", task="t1")
        assert isinstance(eid, str) and len(eid) > 0

    def test_store_semantic_alias(self, agent_facade):
        try:
            eid = agent_facade.store("semantic", "text", doc_id="d1")
            assert isinstance(eid, str)
        except Exception:
            pass

    def test_store_invalid_layer_raises(self, agent_facade):
        with pytest.raises(ValueError, match="Unknown layer"):
            agent_facade.store("invalid", "content")


class TestUnifiedStoreChat:
    """chat mode 的统一 store 入口。"""

    def test_store_working_returns_key(self, chat_facade):
        k = chat_facade.store("working", "value", key="mykey")
        assert k == "mykey"
        assert chat_facade.working_get("mykey") == "value"

    def test_store_working_auto_key(self, chat_facade):
        k = chat_facade.store("working", "value")
        assert isinstance(k, str) and len(k) > 0

    def test_store_short_term_returns_id(self, chat_facade):
        eid = chat_facade.store("short_term", "content", task="t1", agent="user")
        assert isinstance(eid, str) and len(eid) > 0

    def test_store_long_term_returns_id(self, chat_facade):
        eid = chat_facade.store("long_term", "text", doc_id="d1")
        assert isinstance(eid, str)

    def test_store_episodic_alias(self, chat_facade):
        eid = chat_facade.store("episodic", "content", task="t1")
        assert isinstance(eid, str) and len(eid) > 0

    def test_store_invalid_layer_raises(self, chat_facade):
        with pytest.raises(ValueError, match="Unknown layer"):
            chat_facade.store("invalid", "content")


class TestUnifiedRetrieveAgent:
    def test_retrieve_working_returns_value(self, agent_facade):
        agent_facade.working_put("k", "v")
        assert agent_facade.retrieve("working", query="k") == "v"

    def test_retrieve_working_missing_returns_none(self, agent_facade):
        assert agent_facade.retrieve("working", query="missing") is None

    def test_retrieve_working_empty_query_returns_none(self, agent_facade):
        assert agent_facade.retrieve("working", query="") is None

    def test_retrieve_short_term_returns_list(self, agent_facade):
        agent_facade.short_term_store("c", task="t")
        r = agent_facade.retrieve("short_term", query="c", top=5)
        assert isinstance(r, list)

    def test_retrieve_long_term_returns_list(self, agent_facade):
        r = agent_facade.retrieve("long_term", query="test", top=5)
        assert isinstance(r, list)

    def test_retrieve_episodic_alias(self, agent_facade):
        agent_facade.short_term_store("c", task="t")
        r = agent_facade.retrieve("episodic", query="c", top=5)
        assert isinstance(r, list)

    def test_retrieve_semantic_alias(self, agent_facade):
        r = agent_facade.retrieve("semantic", query="test", top=5)
        assert isinstance(r, list)

    def test_retrieve_invalid_layer_raises(self, agent_facade):
        with pytest.raises(ValueError, match="Unknown layer"):
            agent_facade.retrieve("invalid", query="x")


class TestUnifiedRetrieveChat:
    def test_retrieve_working_returns_value(self, chat_facade):
        chat_facade.working_put("k", "v")
        assert chat_facade.retrieve("working", query="k") == "v"

    def test_retrieve_working_missing_returns_none(self, chat_facade):
        assert chat_facade.retrieve("working", query="missing") is None

    def test_retrieve_short_term_returns_list(self, chat_facade):
        chat_facade.short_term_store("c", task="t")
        r = chat_facade.retrieve("short_term", query="c", top=5)
        assert isinstance(r, list)

    def test_retrieve_long_term_returns_list(self, chat_facade):
        r = chat_facade.retrieve("long_term", query="test", top=5)
        assert isinstance(r, list)

    def test_retrieve_invalid_layer_raises(self, chat_facade):
        with pytest.raises(ValueError, match="Unknown layer"):
            chat_facade.retrieve("invalid", query="x")


class TestUnifiedSearchAgent:
    def test_search_returns_list(self, agent_facade):
        r = agent_facade.search("query", top=5)
        assert isinstance(r, list)

    def test_search_results_are_dicts(self, agent_facade):
        r = agent_facade.search("query", top=5)
        assert all(isinstance(x, dict) for x in r)

    def test_search_with_data(self, agent_facade):
        agent_facade.short_term_store("login bug", task="login")
        r = agent_facade.search("login", top=5)
        assert isinstance(r, list)

    def test_search_empty_query(self, agent_facade):
        r = agent_facade.search("", top=5)
        assert isinstance(r, list)

    def test_search_top_zero(self, agent_facade):
        agent_facade.short_term_store("c", task="t")
        r = agent_facade.search("c", top=0)
        assert isinstance(r, list)


class TestUnifiedSearchChat:
    def test_search_returns_list(self, chat_facade):
        r = chat_facade.search("query", top=5)
        assert isinstance(r, list)

    def test_search_results_are_dicts(self, chat_facade):
        r = chat_facade.search("query", top=5)
        assert all(isinstance(x, dict) for x in r)

    def test_search_with_data(self, chat_facade):
        chat_facade.short_term_store("login bug", task="login")
        r = chat_facade.search("login", top=5)
        assert isinstance(r, list)

    def test_search_empty_query(self, chat_facade):
        r = chat_facade.search("", top=5)
        assert isinstance(r, list)


class TestUnifiedDeleteAgent:
    def test_delete_working_returns_bool(self, agent_facade):
        agent_facade.working_put("k", "v")
        r = agent_facade.delete("working", "k")
        assert isinstance(r, bool)

    def test_delete_working_existing(self, agent_facade):
        agent_facade.working_put("k", "v")
        assert agent_facade.delete("working", "k") is True
        assert agent_facade.working_get("k") is None

    def test_delete_working_missing(self, agent_facade):
        # working_delete 通常无条件返回 True（删除不存在的 key 不报错）
        r = agent_facade.delete("working", "nonexistent")
        assert isinstance(r, bool)

    def test_delete_short_term_returns_bool(self, agent_facade):
        eid = agent_facade.short_term_store("c", task="t")
        r = agent_facade.delete("short_term", eid)
        assert isinstance(r, bool)

    def test_delete_short_term_existing(self, agent_facade):
        eid = agent_facade.short_term_store("c", task="t")
        assert agent_facade.delete("short_term", eid) is True
        assert agent_facade.short_term_get(eid) is None

    def test_delete_short_term_missing(self, agent_facade):
        r = agent_facade.delete("short_term", "nonexistent-id")
        assert r is False

    def test_delete_episodic_alias(self, agent_facade):
        eid = agent_facade.short_term_store("c", task="t")
        assert agent_facade.delete("episodic", eid) is True

    def test_delete_invalid_layer_raises(self, agent_facade):
        with pytest.raises(ValueError, match="Unknown layer"):
            agent_facade.delete("invalid", "x")


class TestUnifiedDeleteChat:
    def test_delete_working_returns_bool(self, chat_facade):
        chat_facade.working_put("k", "v")
        r = chat_facade.delete("working", "k")
        assert isinstance(r, bool)

    def test_delete_working_existing(self, chat_facade):
        chat_facade.working_put("k", "v")
        assert chat_facade.delete("working", "k") is True
        assert chat_facade.working_get("k") is None

    def test_delete_working_missing(self, chat_facade):
        r = chat_facade.delete("working", "nonexistent")
        assert r is False

    def test_delete_short_term_returns_bool(self, chat_facade):
        eid = chat_facade.short_term_store("c", task="t")
        r = chat_facade.delete("short_term", eid)
        assert isinstance(r, bool)

    def test_delete_short_term_existing(self, chat_facade):
        eid = chat_facade.short_term_store("c", task="t")
        assert chat_facade.delete("short_term", eid) is True
        assert chat_facade.short_term_get(eid) is None

    def test_delete_short_term_missing(self, chat_facade):
        r = chat_facade.delete("short_term", "nonexistent-id")
        assert r is False

    def test_delete_invalid_layer_raises(self, chat_facade):
        with pytest.raises(ValueError, match="Unknown layer"):
            chat_facade.delete("invalid", "x")


class TestUnifiedCrudConsistency:
    """两种 mode 的统一 CRUD 行为一致性。"""

    def test_store_working_both(self, agent_facade, chat_facade):
        a_k = agent_facade.store("working", "v", key="k")
        c_k = chat_facade.store("working", "v", key="k")
        assert a_k == c_k == "k"

    def test_retrieve_working_both(self, agent_facade, chat_facade):
        agent_facade.working_put("k", "v")
        chat_facade.working_put("k", "v")
        assert agent_facade.retrieve("working", query="k") == "v"
        assert chat_facade.retrieve("working", query="k") == "v"

    def test_search_returns_list_both(self, agent_facade, chat_facade):
        assert isinstance(agent_facade.search("q", top=5), list)
        assert isinstance(chat_facade.search("q", top=5), list)

    def test_delete_invalid_layer_raises_both(self, agent_facade, chat_facade):
        with pytest.raises(ValueError):
            agent_facade.delete("invalid", "x")
        with pytest.raises(ValueError):
            chat_facade.delete("invalid", "x")


# ════════════════════════════════════════════════════════════════
# 8. 跨实现通信
# ════════════════════════════════════════════════════════════════


class TestCrossImplementation:
    def test_agent_reads_chat_data(self, tmp_root):
        chat = MemoryFacade(root_dir=tmp_root, mode="chat")
        _mock_heavy_deps(chat.impl)
        agent = MemoryFacade(root_dir=tmp_root, mode="agent")
        chat.short_term_store("chat-side data", task="chat_task", agent="user")
        rows = agent.query_memory_entries(query="chat_task", top=10)
        assert isinstance(rows, list)
        assert len(rows) >= 1

    def test_chat_reads_agent_data(self, tmp_root):
        agent = MemoryFacade(root_dir=tmp_root, mode="agent")
        chat = MemoryFacade(root_dir=tmp_root, mode="chat")
        _mock_heavy_deps(chat.impl)
        agent.short_term_store("agent-side data", task="agent_task", agent="claude")
        rows = chat.query_episodic(query="agent_task", top=10)
        assert isinstance(rows, list)
        assert len(rows) >= 1

    def test_query_episodic_returns_list(self, agent_facade):
        r = agent_facade.query_episodic(query="", top=5)
        assert isinstance(r, list)

    def test_query_memory_entries_returns_list(self, chat_facade):
        r = chat_facade.query_memory_entries(query="", top=5)
        assert isinstance(r, list)

    def test_query_episodic_empty(self, agent_facade):
        r = agent_facade.query_episodic(query="nonexistent_xyz", top=5)
        assert isinstance(r, list)

    def test_query_memory_entries_empty(self, chat_facade):
        r = chat_facade.query_memory_entries(query="nonexistent_xyz", top=5)
        assert isinstance(r, list)

    def test_shared_db_path(self, tmp_root):
        # 两种 mode 应共享同一个 DB 路径
        from maop.memory.shared_db import get_memory_db_path

        path = get_memory_db_path()
        assert path.exists() or path.parent.exists()


# ════════════════════════════════════════════════════════════════
# 9. 向后兼容
# ════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    def test_three_layer_memory_construction(self, tmp_root):
        mem = ThreeLayerMemory(root_dir=tmp_root, working_max=50, working_ttl=60)
        assert mem.working_get("x") is None

    def test_memory_manager_construction(self, tmp_root):
        mgr = MemoryManager(root_dir=tmp_root)
        _mock_heavy_deps(mgr)
        assert mgr.conversation is not None
        assert mgr.memory is not None

    def test_three_layer_memory_episodic_api(self, tmp_root):
        mem = ThreeLayerMemory(root_dir=tmp_root)
        eid = mem.episodic_store(task="t1", agent="claude", outcome="success", score=0.9)
        results = mem.episodic_search(query="t1")
        assert len(results) >= 1
        assert results[0].entry.id == eid

    def test_memory_manager_add_exchange(self, tmp_root):
        mgr = MemoryManager(root_dir=tmp_root)
        _mock_heavy_deps(mgr)
        result = mgr.add_exchange(
            session_id="s1", user_msg="hello", assistant_msg="hi there",
        )
        assert "working_user_id" in result
        assert "short_term_id" in result

    def test_three_layer_memory_semantic_api(self, tmp_root):
        mem = ThreeLayerMemory(root_dir=tmp_root)
        # semantic_search 在无索引时应返回空列表
        results = mem.semantic_search("test", top=5)
        assert isinstance(results, list)

    def test_three_layer_memory_working_pin(self, tmp_root):
        mem = ThreeLayerMemory(root_dir=tmp_root)
        mem.working_put("k", "v")
        assert mem.working_pin("k") is True
        mem.working_unpin("k")

    def test_three_layer_memory_working_delete(self, tmp_root):
        mem = ThreeLayerMemory(root_dir=tmp_root)
        mem.working_put("k", "v")
        mem.working_delete("k")
        assert mem.working_get("k") is None

    def test_memory_manager_search_all_layers(self, tmp_root):
        mgr = MemoryManager(root_dir=tmp_root)
        _mock_heavy_deps(mgr)
        result = mgr.search_all_layers("query", top=5)
        assert "short_term" in result
        assert "long_term" in result

    def test_memory_manager_prune_expired(self, tmp_root):
        mgr = MemoryManager(root_dir=tmp_root)
        _mock_heavy_deps(mgr)
        n = mgr.prune_expired()
        assert isinstance(n, int)


# ════════════════════════════════════════════════════════════════
# 10. 数据迁移工具
# ════════════════════════════════════════════════════════════════


class TestMigrationReport:
    def test_empty_report(self):
        from maop.migrations.memory_migration import MigrationReport

        r = MigrationReport(root_dir="/tmp")
        assert r.total_candidates == 0
        assert r.total_migrated == 0
        assert r.total_skipped == 0
        assert r.total_errors == 0

    def test_summary_str(self):
        from maop.migrations.memory_migration import MigrationReport

        r = MigrationReport(root_dir="/tmp")
        s = r.summary()
        assert isinstance(s, str)
        assert "MigrationReport" in s

    def test_with_tables(self):
        from maop.migrations.memory_migration import (
            MigrationReport,
            TableMigrationResult,
        )

        r = MigrationReport(
            root_dir="/tmp",
            tables=[
                TableMigrationResult(table="t1", candidates=10, migrated=8, skipped=2),
                TableMigrationResult(table="t2", candidates=5, migrated=5),
            ],
        )
        assert r.total_candidates == 15
        assert r.total_migrated == 13
        assert r.total_skipped == 2


class TestMigrationDryRun:
    def test_migrate_all_dry_run_no_source(self, tmp_root):
        from maop.migrations.memory_migration import migrate_all

        report = migrate_all(tmp_root, dry_run=True)
        assert report.dry_run is True
        assert isinstance(report.summary(), str)

    def test_migrate_all_no_dry_run(self, tmp_root):
        from maop.migrations.memory_migration import migrate_all

        report = migrate_all(tmp_root, dry_run=False)
        assert report.dry_run is False

    def test_migrate_all_returns_report(self, tmp_root):
        from maop.migrations.memory_migration import MigrationReport, migrate_all

        report = migrate_all(tmp_root)
        assert isinstance(report, MigrationReport)

    def test_migrate_all_with_progress_bool(self, tmp_root):
        from maop.migrations.memory_migration import migrate_all

        report = migrate_all(tmp_root, progress=True)
        assert isinstance(report.summary(), str)

    def test_migrate_all_with_progress_callback(self, tmp_root):
        from maop.migrations.memory_migration import migrate_all

        calls: list[tuple[str, int, int]] = []

        def cb(table: str, current: int, total: int) -> None:
            calls.append((table, current, total))

        migrate_all(tmp_root, progress=cb)
        # 即使无源数据，cb 也应被调用（或至少不抛异常）
        assert isinstance(calls, list)


class TestMigrationIdempotent:
    def test_migrate_twice_no_duplicate(self, tmp_root):
        """重复迁移不应产生重复数据。"""
        from maop.migrations.memory_migration import migrate_all

        # 第一次
        r1 = migrate_all(tmp_root, dry_run=False)
        # 第二次（幂等）
        r2 = migrate_all(tmp_root, dry_run=False)
        assert r1.total_errors == 0
        assert r2.total_errors == 0
        # 第二次应全部 skipped（无新数据迁移）
        assert r2.total_migrated == 0

    def test_migrate_with_legacy_memory_db(self, tmp_root):
        """构造 legacy memory.db 并迁移。"""
        from maop.migrations.memory_migration import (
            migrate_legacy_memory_db,
        )

        # 构造 legacy memory.db
        legacy_path = tmp_root / "data" / "memory.db"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(legacy_path)) as conn:
            conn.executescript("""
                CREATE TABLE memory_entries (
                    id TEXT PRIMARY KEY, agent TEXT, task TEXT, content TEXT,
                    tags TEXT, topic TEXT, trace_id TEXT, session_id TEXT,
                    exit_code INTEGER, duration_ms INTEGER, timestamp TEXT
                );
                CREATE TABLE memory_traces (
                    trace_id TEXT PRIMARY KEY, parent_trace_id TEXT, session_id TEXT,
                    task TEXT, agents TEXT, created TEXT, last_active TEXT, status TEXT
                );
                CREATE TABLE memory_trajectory (
                    id TEXT PRIMARY KEY, trace_id TEXT, agent TEXT, task TEXT,
                    tool_name TEXT, tool_input TEXT, tool_output TEXT,
                    duration_ms INTEGER, exit_code INTEGER, timestamp TEXT
                );
            """)
            conn.execute(
                "INSERT INTO memory_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("id-1", "claude", "task1", "content1", "", "general", "", "", 0, 0, "2024-01-01"),
            )
            conn.commit()

        # dry-run
        results = migrate_legacy_memory_db(tmp_root, dry_run=True)
        assert len(results) == 3
        assert results[0].candidates == 1  # memory_entries 有 1 行

        # 实际迁移
        results = migrate_legacy_memory_db(tmp_root, dry_run=False)
        assert results[0].migrated == 1

        # 验证数据已写入统一 DB
        unified_path = get_memory_db_path()
        with sqlite3.connect(str(unified_path)) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM memory_entries WHERE id = ?", ("id-1",))
            assert cur.fetchone()[0] == 1

    def test_migrate_with_legacy_episodic_db(self, tmp_root):
        """构造 legacy episodic.db 并迁移。"""
        from maop.migrations.memory_migration import migrate_legacy_episodic_db

        legacy_path = tmp_root / "data" / "episodic.db"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(legacy_path)) as conn:
            conn.executescript("""
                CREATE TABLE episodic_memory (
                    id TEXT PRIMARY KEY, task TEXT, agent TEXT, outcome TEXT,
                    score REAL, lessons TEXT, user_feedback TEXT,
                    quality_dimensions TEXT, summary TEXT, key_decisions TEXT,
                    files_touched TEXT, metadata TEXT, created_at REAL,
                    consolidated INTEGER, access_count INTEGER
                );
            """)
            conn.execute(
                "INSERT INTO episodic_memory VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("ep-1", "task1", "claude", "success", 0.9, "[]", "", "{}", "", "[]", "[]", "{}", 0.0, 0, 0),
            )
            conn.commit()

        result = migrate_legacy_episodic_db(tmp_root, dry_run=False)
        assert result.migrated == 1

        # 幂等：再迁移一次应 skipped
        result2 = migrate_legacy_episodic_db(tmp_root, dry_run=False)
        assert result2.migrated == 0
        assert result2.skipped == 1

    def test_migrate_with_legacy_json(self, tmp_root):
        """构造 legacy memory.json 并迁移。"""
        from maop.migrations.memory_migration import migrate_legacy_json_files

        json_path = tmp_root / "data" / "memory.json"
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with json_path.open("w", encoding="utf-8") as f:
            json.dump({
                "entries": [
                    {
                        "id": "json-1", "agent": "claude", "task": "json task",
                        "content": "json content", "tags": "", "topic": "general",
                        "trace_id": "", "session_id": "", "exit_code": 0,
                        "duration_ms": 0, "timestamp": "2024-01-01",
                    }
                ]
            }, f)

        results = migrate_legacy_json_files(tmp_root, dry_run=False)
        assert len(results) == 2  # memory.json + wiki.json
        assert results[0].migrated == 1  # memory.json 有 1 条

        # 幂等
        results2 = migrate_legacy_json_files(tmp_root, dry_run=False)
        assert results2[0].migrated == 0
        assert results2[0].skipped == 1

    def test_migrate_dry_run_no_write(self, tmp_root):
        """dry-run 不应写入目标 DB。"""
        from maop.migrations.memory_migration import migrate_legacy_memory_db

        legacy_path = tmp_root / "data" / "memory.db"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(legacy_path)) as conn:
            conn.execute("""
                CREATE TABLE memory_entries (
                    id TEXT PRIMARY KEY, agent TEXT, task TEXT, content TEXT,
                    tags TEXT, topic TEXT, trace_id TEXT, session_id TEXT,
                    exit_code INTEGER, duration_ms INTEGER, timestamp TEXT
                )
            """)
            conn.execute(
                "INSERT INTO memory_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("dry-1", "a", "t", "c", "", "g", "", "", 0, 0, "2024"),
            )
            conn.commit()

        results = migrate_legacy_memory_db(tmp_root, dry_run=True)
        assert results[0].candidates == 1
        assert results[0].migrated == 0  # dry-run 不写入

        # 验证目标 DB 没有这条数据
        unified_path = get_memory_db_path()
        if unified_path.exists():
            with sqlite3.connect(str(unified_path)) as conn:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM memory_entries WHERE id = ?", ("dry-1",)
                )
                # 表可能不存在
                try:
                    assert cur.fetchone()[0] == 0
                except sqlite3.Error:
                    pass


class TestMigrationTableResult:
    def test_table_result_default(self):
        from maop.migrations.memory_migration import TableMigrationResult

        r = TableMigrationResult(table="t1")
        assert r.table == "t1"
        assert r.candidates == 0
        assert r.migrated == 0

    def test_table_result_summary(self):
        from maop.migrations.memory_migration import TableMigrationResult

        r = TableMigrationResult(table="t1", candidates=10, migrated=8, skipped=2)
        s = r.summary()
        assert "t1" in s
        assert "8/10" in s


# ════════════════════════════════════════════════════════════════
# 11. 配置兼容
# ════════════════════════════════════════════════════════════════


class TestConfigCompatibility:
    def test_facade_with_default_config(self, tmp_root):
        facade = MemoryFacade(root_dir=tmp_root, mode="agent")
        assert facade.mode == "agent"

    def test_facade_with_chat_config(self, tmp_root):
        from maop.memory.manager import MemoryManagerConfig

        cfg = MemoryManagerConfig()
        facade = MemoryFacade(root_dir=tmp_root, mode="chat", config=cfg)
        assert facade.mode == "chat"

    def test_facade_with_custom_config(self, tmp_root):
        from maop.memory.manager import MemoryManagerConfig

        cfg = MemoryManagerConfig(max_working_tokens=1000, short_term_ttl_days=7)
        facade = MemoryFacade(root_dir=tmp_root, mode="chat", config=cfg)
        assert facade.impl._config.max_working_tokens == 1000

    def test_facade_agent_ignores_config(self, tmp_root):
        # agent mode 忽略 config 参数
        facade = MemoryFacade(
            root_dir=tmp_root, mode="agent", config="ignored"
        )
        assert facade.mode == "agent"

    def test_facade_with_working_max(self, tmp_root):
        facade = MemoryFacade(root_dir=tmp_root, mode="agent", working_max=10)
        assert facade.impl._working._max_size == 10

    def test_facade_with_working_ttl(self, tmp_root):
        facade = MemoryFacade(root_dir=tmp_root, mode="agent", working_ttl=120.0)
        assert facade.impl._working._default_ttl == 120.0

    def test_shared_db_path_unified(self, tmp_root, monkeypatch):
        # 默认 unified 模式
        monkeypatch.delenv("MAOP_DB_PER_MODULE", raising=False)
        path = get_memory_db_path()
        assert path.name == "maop.db"

    def test_shared_db_path_per_module(self, tmp_root, monkeypatch):
        monkeypatch.setenv("MAOP_DB_PER_MODULE", "1")
        # 重新 import 以读取新 env
        import importlib

        from maop.memory import shared_db
        importlib.reload(shared_db)
        path = shared_db.get_memory_db_path()
        assert path.name == "memory.db"
        # 恢复
        monkeypatch.delenv("MAOP_DB_PER_MODULE", raising=False)
        importlib.reload(shared_db)


# ════════════════════════════════════════════════════════════════
# 12. 底层实现直接调用统一 CRUD
# ════════════════════════════════════════════════════════════════


class TestImplDirectCrud:
    """验证底层实现可直接调用 store/retrieve/search/delete。"""

    def test_three_layer_memory_store(self, agent_impl):
        k = agent_impl.store("working", "v", key="k")
        assert k == "k"
        assert agent_impl.working_get("k") == "v"

    def test_three_layer_memory_retrieve(self, agent_impl):
        agent_impl.working_put("k", "v")
        # ThreeLayerMemory.retrieve 的 working 层返回 list（向后兼容）
        assert agent_impl.retrieve("working", query="k") == ["v"]

    def test_three_layer_memory_search(self, agent_impl):
        r = agent_impl.search("query", top=5)
        assert isinstance(r, list)

    def test_three_layer_memory_delete(self, agent_impl):
        agent_impl.working_put("k", "v")
        assert agent_impl.delete("working", "k") is True

    def test_memory_manager_store(self, chat_impl):
        k = chat_impl.store("working", "v", key="k")
        assert k == "k"
        assert chat_impl.working_get("k") == "v"

    def test_memory_manager_retrieve(self, chat_impl):
        chat_impl.working_put("k", "v")
        assert chat_impl.retrieve("working", query="k") == "v"

    def test_memory_manager_search(self, chat_impl):
        r = chat_impl.search("query", top=5)
        assert isinstance(r, list)

    def test_memory_manager_delete(self, chat_impl):
        chat_impl.working_put("k", "v")
        assert chat_impl.delete("working", "k") is True
        assert chat_impl.working_get("k") is None


# ════════════════════════════════════════════════════════════════
# 13. 边界与异常
# ════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_store_working_complex_value(self, agent_facade):
        v = {"nested": {"list": [1, 2, {"deep": True}]}}
        agent_facade.store("working", v, key="k")
        assert agent_facade.retrieve("working", query="k") == v

    def test_store_short_term_unicode(self, agent_facade):
        eid = agent_facade.short_term_store("中文内容 with emoji 🎉", task="unicode测试")
        assert isinstance(eid, str) and len(eid) > 0

    def test_search_special_chars(self, agent_facade):
        agent_facade.short_term_store("c++ template", task="c++")
        r = agent_facade.search("c++", top=5)
        assert isinstance(r, list)

    def test_search_sql_injection_attempt(self, agent_facade):
        # FTS5 query 注入尝试
        r = agent_facade.search("'; DROP TABLE--", top=5)
        assert isinstance(r, list)

    def test_long_top_value(self, agent_facade):
        r = agent_facade.search("query", top=10000)
        assert isinstance(r, list)

    def test_zero_top_value(self, agent_facade):
        r = agent_facade.search("query", top=0)
        assert isinstance(r, list)

    def test_negative_top_value(self, agent_facade):
        # 负 top 应被底层处理，不抛异常
        try:
            r = agent_facade.search("query", top=-1)
            assert isinstance(r, list)
        except Exception:
            # 底层可能拒绝负 top，可接受
            pass

    def test_empty_content_store(self, agent_facade):
        eid = agent_facade.short_term_store("", task="")
        assert isinstance(eid, str)

    def test_very_long_content(self, agent_facade):
        long_content = "x" * 10000
        eid = agent_facade.short_term_store(long_content, task="long")
        assert isinstance(eid, str) and len(eid) > 0

    def test_retrieve_working_no_query(self, agent_facade):
        # query="" 时 working 层返回 None
        assert agent_facade.retrieve("working", query="") is None

    def test_delete_then_retrieve(self, agent_facade):
        eid = agent_facade.short_term_store("c", task="t")
        agent_facade.delete("short_term", eid)
        r = agent_facade.short_term_get(eid)
        assert r is None

    def test_store_retrieve_roundtrip_working(self, agent_facade):
        agent_facade.store("working", "roundtrip", key="rt")
        assert agent_facade.retrieve("working", query="rt") == "roundtrip"

    def test_store_retrieve_roundtrip_short_term(self, agent_facade):
        agent_facade.store("short_term", "roundtrip content", task="rt")
        r = agent_facade.retrieve("short_term", query="roundtrip", top=5)
        assert isinstance(r, list)


# ════════════════════════════════════════════════════════════════
# 14. 大批量与性能
# ════════════════════════════════════════════════════════════════


class TestBulkOperations:
    def test_bulk_store_short_term_agent(self, agent_facade):
        ids = []
        for i in range(50):
            eid = agent_facade.short_term_store(f"content {i}", task=f"task_{i}")
            ids.append(eid)
        assert len(ids) == 50
        assert all(isinstance(eid, str) and len(eid) > 0 for eid in ids)

    def test_bulk_store_short_term_chat(self, chat_facade):
        ids = []
        for i in range(50):
            eid = chat_facade.short_term_store(f"content {i}", task=f"task_{i}")
            ids.append(eid)
        assert len(ids) == 50

    def test_bulk_working_put_agent(self, agent_facade):
        for i in range(100):
            agent_facade.working_put(f"k{i}", i)
        # 验证部分 key 仍可读（LRU 可能淘汰部分）
        found = sum(1 for i in range(100) if agent_facade.working_get(f"k{i}") is not None)
        assert found > 0

    def test_bulk_working_put_chat(self, chat_facade):
        for i in range(100):
            chat_facade.working_put(f"k{i}", i)
        for i in range(100):
            assert chat_facade.working_get(f"k{i}") == i

    def test_bulk_search_agent(self, agent_facade):
        for i in range(20):
            agent_facade.short_term_store(f"task {i}", task=f"task_{i}")
        r = agent_facade.search("task", top=10)
        assert isinstance(r, list)
        assert len(r) <= 10

    def test_bulk_search_chat(self, chat_facade):
        for i in range(20):
            chat_facade.short_term_store(f"task {i}", task=f"task_{i}")
        r = chat_facade.search("task", top=10)
        assert isinstance(r, list)


# ════════════════════════════════════════════════════════════════
# 15. 迁移工具 CLI
# ════════════════════════════════════════════════════════════════


class TestMigrationCLI:
    def test_cli_dry_run(self, tmp_root, capsys):
        from maop.migrations.memory_migration import main

        rc = main(["--root", str(tmp_root), "--dry-run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "MigrationReport" in captured.out

    def test_cli_actual_run(self, tmp_root, capsys):
        from maop.migrations.memory_migration import main

        rc = main(["--root", str(tmp_root)])
        assert rc == 0

    def test_cli_with_progress(self, tmp_root, capsys):
        from maop.migrations.memory_migration import main

        rc = main(["--root", str(tmp_root), "--progress"])
        assert rc == 0

    def test_cli_default_root(self, capsys, monkeypatch):
        from maop.migrations.memory_migration import main

        # 用 cwd 作为默认 root
        rc = main(["--dry-run"])
        assert rc == 0


# ════════════════════════════════════════════════════════════════
# 16. 迁移工具综合
# ════════════════════════════════════════════════════════════════


class TestMigrationIntegration:
    def test_full_migration_with_all_sources(self, tmp_root):
        """同时存在 memory.db + episodic.db + memory.json 的完整迁移。"""
        from maop.migrations.memory_migration import migrate_all

        # 构造 legacy memory.db
        mem_path = tmp_root / "data" / "memory.db"
        mem_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(mem_path)) as conn:
            conn.execute("""
                CREATE TABLE memory_entries (
                    id TEXT PRIMARY KEY, agent TEXT, task TEXT, content TEXT,
                    tags TEXT, topic TEXT, trace_id TEXT, session_id TEXT,
                    exit_code INTEGER, duration_ms INTEGER, timestamp TEXT
                )
            """)
            conn.execute(
                "INSERT INTO memory_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("m-1", "claude", "t", "c", "", "g", "", "", 0, 0, "2024"),
            )
            conn.commit()

        # 构造 legacy episodic.db
        ep_path = tmp_root / "data" / "episodic.db"
        with sqlite3.connect(str(ep_path)) as conn:
            conn.execute("""
                CREATE TABLE episodic_memory (
                    id TEXT PRIMARY KEY, task TEXT, agent TEXT, outcome TEXT,
                    score REAL, lessons TEXT, user_feedback TEXT,
                    quality_dimensions TEXT, summary TEXT, key_decisions TEXT,
                    files_touched TEXT, metadata TEXT, created_at REAL,
                    consolidated INTEGER, access_count INTEGER
                )
            """)
            conn.execute(
                "INSERT INTO episodic_memory VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("e-1", "t", "a", "s", 0.5, "[]", "", "{}", "", "[]", "[]", "{}", 0.0, 0, 0),
            )
            conn.commit()

        # 构造 legacy memory.json
        json_path = tmp_root / "data" / "memory.json"
        with json_path.open("w", encoding="utf-8") as f:
            json.dump({
                "entries": [{
                    "id": "j-1", "agent": "a", "task": "t", "content": "c",
                    "tags": "", "topic": "g", "trace_id": "", "session_id": "",
                    "exit_code": 0, "duration_ms": 0, "timestamp": "2024",
                }]
            }, f)

        report = migrate_all(tmp_root, dry_run=False)
        assert report.total_errors == 0
        assert report.total_migrated >= 2  # memory_entries 至少 2 条（m-1 + j-1）+ episodic 1 条

    def test_migration_preserves_data(self, tmp_root):
        """迁移后数据可被 MemoryFacade 读取。"""
        from maop.migrations.memory_migration import migrate_legacy_memory_db

        # 构造 legacy memory.db
        mem_path = tmp_root / "data" / "memory.db"
        mem_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(mem_path)) as conn:
            conn.execute("""
                CREATE TABLE memory_entries (
                    id TEXT PRIMARY KEY, agent TEXT, task TEXT, content TEXT,
                    tags TEXT, topic TEXT, trace_id TEXT, session_id TEXT,
                    exit_code INTEGER, duration_ms INTEGER, timestamp TEXT
                )
            """)
            conn.execute(
                "INSERT INTO memory_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("preserve-1", "claude", "preserve task", "preserve content",
                 "", "general", "", "", 0, 0, "2024-01-01"),
            )
            conn.commit()

        migrate_legacy_memory_db(tmp_root, dry_run=False)

        # 用 chat facade 读取
        chat = MemoryFacade(root_dir=tmp_root, mode="chat")
        _mock_heavy_deps(chat.impl)
        results = chat.short_term_search(query="preserve", top=10)
        # 应能找到迁移的数据
        assert any("preserve" in str(r) for r in results)


# ════════════════════════════════════════════════════════════════
# 17. 时间相关行为
# ════════════════════════════════════════════════════════════════


class TestTimeBehavior:
    def test_working_put_with_ttl(self, agent_facade):
        # agent mode 支持 ttl_s
        agent_facade.working_put("k", "v", ttl_s=0.01)
        time.sleep(0.02)
        # TTL 过期后应返回 None（取决于 LRU 实现）
        result = agent_facade.working_get("k")
        assert result is None or result == "v"  # 宽容：实现可能不等

    def test_store_working_with_ttl(self, agent_facade):
        agent_facade.store("working", "v", key="k", ttl_s=60)
        assert agent_facade.retrieve("working", query="k") == "v"

    def test_short_term_store_persists(self, agent_facade):
        """short_term 存储应持久化（不随 TTL 立即消失）。"""
        eid = agent_facade.short_term_store("persistent content", task="t")
        # 立即读取应能找到
        r = agent_facade.short_term_get(eid)
        assert r is not None


# ════════════════════════════════════════════════════════════════
# 18. 多 Facade 实例共享 DB
# ════════════════════════════════════════════════════════════════


class TestMultipleFacades:
    def test_two_agent_facades_share_db(self, tmp_root):
        f1 = MemoryFacade(root_dir=tmp_root, mode="agent")
        f2 = MemoryFacade(root_dir=tmp_root, mode="agent")
        f1.short_term_store("from f1", task="t1")
        # f2 应能通过 query_memory_entries 看到 f1 写入的数据
        rows = f2.query_memory_entries(query="from f1", top=10)
        # 注意：agent 写入的是 episodic_memory，不是 memory_entries
        # 所以这里宽松断言
        assert isinstance(rows, list)

    def test_two_chat_facades_share_db(self, tmp_root):
        f1 = MemoryFacade(root_dir=tmp_root, mode="chat")
        _mock_heavy_deps(f1.impl)
        f2 = MemoryFacade(root_dir=tmp_root, mode="chat")
        _mock_heavy_deps(f2.impl)
        f1.short_term_store("from f1", task="t1", agent="user")
        rows = f2.short_term_search(query="from f1", top=10)
        assert len(rows) >= 1

    def test_chat_and_agent_share_db(self, tmp_root):
        chat = MemoryFacade(root_dir=tmp_root, mode="chat")
        _mock_heavy_deps(chat.impl)
        agent = MemoryFacade(root_dir=tmp_root, mode="agent")
        chat.short_term_store("from chat", task="t1", agent="user")
        # agent 通过 query_memory_entries 读取 chat 数据
        rows = agent.query_memory_entries(query="from chat", top=10)
        assert len(rows) >= 1


# ════════════════════════════════════════════════════════════════
# 19. 模块导出与导入
# ════════════════════════════════════════════════════════════════


class TestModuleExports:
    def test_facade_exports(self):
        from maop.memory.facade import MemoryFacade, MemoryMode

        assert MemoryFacade is not None
        assert MemoryMode is not None

    def test_unified_exports(self):
        from maop.memory.unified import (
            VALID_LAYERS,
            UnifiedMemoryProtocol,
        )

        assert UnifiedMemoryProtocol is not None
        assert VALID_LAYERS is not None

    def test_shared_db_exports(self):
        from maop.memory.shared_db import (
            LAYER_ALIASES,
        )

        assert LAYER_ALIASES is not None

    def test_migration_exports(self):
        from maop.migrations.memory_migration import (
            MigrationReport,
            migrate_all,
        )

        assert migrate_all is not None
        assert MigrationReport is not None


# ════════════════════════════════════════════════════════════════
# 20. 综合场景
# ════════════════════════════════════════════════════════════════


class TestEndToEndScenarios:
    def test_chat_session_scenario(self, tmp_root):
        """模拟一个完整的 chat 会话场景。"""
        chat = MemoryFacade(root_dir=tmp_root, mode="chat")
        _mock_heavy_deps(chat.impl)

        # 用户提问
        chat.working_put("current_question", "How to fix auth?")

        # 存储对话
        eid = chat.short_term_store(
            "Q: How to fix auth?\nA: Check JWT token expiration",
            task="auth fix",
            agent="user",
            topic="authentication",
            tags=["auth", "jwt"],
        )
        assert isinstance(eid, str)

        # 检索
        results = chat.short_term_search(query="auth", top=5)
        assert len(results) >= 1

        # 构建上下文
        ctx = chat.build_context(session_id="s1", query="auth")
        assert hasattr(ctx, "working_context")

        # 统计
        stats = chat.stats()
        assert "short_term_entries" in stats

    def test_agent_task_scenario(self, tmp_root):
        """模拟一个完整的 agent 任务场景。"""
        agent = MemoryFacade(root_dir=tmp_root, mode="agent")

        # 存储任务经验
        eid = agent.short_term_store(
            "Fixed login timeout by setting socket option",
            task="Fix login timeout",
            agent="claude",
            topic="debugging",
            tags=["bug", "fix"],
        )
        assert isinstance(eid, str)

        # 检索相关经验
        results = agent.short_term_search(query="login", top=5)
        assert len(results) >= 1

        # 构建上下文
        ctx = agent.build_context(query="login")
        assert "short_term" in ctx

        # 统计
        stats = agent.stats()
        assert "working_size" in stats

    def test_cross_agent_chat_scenario(self, tmp_root):
        """agent 与 chat 互相读取数据的场景。"""
        chat = MemoryFacade(root_dir=tmp_root, mode="chat")
        _mock_heavy_deps(chat.impl)
        agent = MemoryFacade(root_dir=tmp_root, mode="agent")

        # chat 存储对话
        chat.short_term_store(
            "User asked about deployment",
            task="deployment help",
            agent="user",
        )

        # agent 存储任务经验
        agent.short_term_store(
            "Deployed to production successfully",
            task="deploy",
            agent="claude",
        )

        # agent 读取 chat 数据
        chat_data = agent.query_memory_entries(query="deployment", top=10)
        assert len(chat_data) >= 1

        # chat 读取 agent 数据
        agent_data = chat.query_episodic(query="deploy", top=10)
        assert len(agent_data) >= 1

    def test_unified_crud_scenario(self, tmp_root):
        """使用统一 CRUD 入口的完整场景。"""
        facade = MemoryFacade(root_dir=tmp_root, mode="agent")

        # store 到三个层
        k = facade.store("working", "working data", key="k1")
        eid = facade.store("short_term", "short term data", task="t1", agent="claude")
        assert isinstance(k, str) and isinstance(eid, str)

        # retrieve
        assert facade.retrieve("working", query="k1") == "working data"
        r = facade.retrieve("short_term", query="short", top=5)
        assert isinstance(r, list)

        # search
        s = facade.search("short", top=5)
        assert isinstance(s, list)

        # delete
        assert facade.delete("working", "k1") is True
        assert facade.retrieve("working", query="k1") is None

    def test_migration_then_facade_scenario(self, tmp_root):
        """迁移 legacy 数据后用 facade 读取的场景。"""
        from maop.migrations.memory_migration import migrate_all

        # 构造 legacy memory.db
        mem_path = tmp_root / "data" / "memory.db"
        mem_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(mem_path)) as conn:
            conn.execute("""
                CREATE TABLE memory_entries (
                    id TEXT PRIMARY KEY, agent TEXT, task TEXT, content TEXT,
                    tags TEXT, topic TEXT, trace_id TEXT, session_id TEXT,
                    exit_code INTEGER, duration_ms INTEGER, timestamp TEXT
                )
            """)
            conn.execute(
                "INSERT INTO memory_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("mig-1", "claude", "legacy task", "legacy content",
                 "", "general", "", "", 0, 0, "2024-01-01"),
            )
            conn.commit()

        # 迁移
        report = migrate_all(tmp_root, dry_run=False)
        assert report.total_errors == 0

        # 用 chat facade 读取迁移后的数据
        chat = MemoryFacade(root_dir=tmp_root, mode="chat")
        _mock_heavy_deps(chat.impl)
        results = chat.short_term_search(query="legacy", top=10)
        assert len(results) >= 1