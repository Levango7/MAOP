"""Tests for UnifiedMemoryProtocol & MemoryFacade.

验证：
1. ``MemoryFacade`` 能按 mode 路由到对应实现
2. ``ThreeLayerMemory`` 与 ``MemoryManager`` 均实现 ``UnifiedMemoryProtocol``
3. 统一术语 API (working / short_term / long_term) 在两种 mode 下都能工作
4. 两种 mode 共享同一个 maop.db，可互相读取对方写入的数据
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from maop.core.memory.three_layer_memory import ThreeLayerMemory
from maop.memory.facade import MemoryFacade
from maop.memory.manager import MemoryManager
from maop.memory.unified import (
    LAYER_LONG_TERM,
    LAYER_SHORT_TERM,
    LAYER_WORKING,
    VALID_LAYERS,
    UnifiedMemoryProtocol,
)

# ── Fixtures ────────────────────────────────────────────────────

def _mock_heavy_deps(mgr: MemoryManager) -> None:
    """Mock 掉 MemoryManager 的重量级依赖以避免网络/模型下载。

    涉及：VectorSearch (HuggingFace 模型)、KnowledgeExtractor (spacy/LLM)、
    KnowledgeGraph。这些在 CI/无网络环境会卡住或失败。
    """
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
    tmpdir = tempfile.mkdtemp(prefix="maop_unified_test_")
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def agent_facade(tmp_root):
    return MemoryFacade(root_dir=tmp_root, mode="agent")


@pytest.fixture
def chat_facade(tmp_root):
    """构造 chat facade 并 mock 掉重量级依赖以避免 HuggingFace 模型下载。

    ``MemoryManager.vector_search`` property 在首次访问时会初始化
    ``VectorSearch``，后者尝试从 HuggingFace 下载 sentence-transformer
    模型，在无网络/CI 环境会卡住。这里预设 ``_vector_search`` 为 mock，
    使 property 直接返回 mock 而不触发下载。
    """
    facade = MemoryFacade(root_dir=tmp_root, mode="chat")
    _mock_heavy_deps(facade.impl)
    return facade


# ── Protocol 常量 ───────────────────────────────────────────────

class TestProtocolConstants:
    def test_layer_constants(self):
        assert LAYER_WORKING == "working"
        assert LAYER_SHORT_TERM == "short_term"
        assert LAYER_LONG_TERM == "long_term"

    def test_valid_layers(self):
        assert VALID_LAYERS == frozenset({"working", "short_term", "long_term"})


# ── Protocol 实现 ───────────────────────────────────────────────

class TestProtocolCompliance:
    """验证两套实现均具备 UnifiedMemoryProtocol 要求的方法。"""

    REQUIRED_METHODS = (
        "working_put", "working_get", "working_clear",
        "short_term_store", "short_term_search", "short_term_get", "short_term_stats",
        "long_term_index", "long_term_search",
        "consolidate", "build_context", "stats",
    )

    def test_three_layer_memory_has_methods(self, tmp_root):
        mem = ThreeLayerMemory(root_dir=tmp_root)
        for method in self.REQUIRED_METHODS:
            assert hasattr(mem, method), f"ThreeLayerMemory missing {method}"
            assert callable(getattr(mem, method)), f"{method} not callable"

    def test_memory_manager_has_methods(self, tmp_root):
        mgr = MemoryManager(root_dir=tmp_root)
        _mock_heavy_deps(mgr)
        for method in self.REQUIRED_METHODS:
            assert hasattr(mgr, method), f"MemoryManager missing {method}"
            assert callable(getattr(mgr, method)), f"{method} not callable"

    def test_runtime_checkable_protocol(self, tmp_root):
        """Protocol 是 runtime_checkable，isinstance 检查应通过。"""
        mem = ThreeLayerMemory(root_dir=tmp_root)
        mgr = MemoryManager(root_dir=tmp_root)
        _mock_heavy_deps(mgr)
        # runtime_checkable 只检查方法存在性，不检查签名
        assert isinstance(mem, UnifiedMemoryProtocol)
        assert isinstance(mgr, UnifiedMemoryProtocol)


# ── Facade 路由 ─────────────────────────────────────────────────

class TestFacadeRouting:
    def test_invalid_mode_raises(self, tmp_root):
        with pytest.raises(ValueError, match="Invalid mode"):
            MemoryFacade(root_dir=tmp_root, mode="invalid")  # type: ignore[arg-type]

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

    def test_repr(self, agent_facade, chat_facade):
        assert "mode='agent'" in repr(agent_facade)
        assert "ThreeLayerMemory" in repr(agent_facade)
        assert "mode='chat'" in repr(chat_facade)
        assert "MemoryManager" in repr(chat_facade)

    def test_impl_property_exposes_underlying(self, agent_facade, chat_facade):
        assert agent_facade.impl is agent_facade._impl
        assert chat_facade.impl is chat_facade._impl


# ── Working Memory (L1) ─────────────────────────────────────────

class TestWorkingMemory:
    def test_put_get_agent(self, agent_facade):
        agent_facade.working_put("k1", {"v": 1})
        assert agent_facade.working_get("k1") == {"v": 1}

    def test_put_get_chat(self, chat_facade):
        chat_facade.working_put("k1", "value")
        assert chat_facade.working_get("k1") == "value"

    def test_get_missing(self, agent_facade):
        assert agent_facade.working_get("nonexistent") is None

    def test_clear_agent(self, agent_facade):
        agent_facade.working_put("a", 1)
        agent_facade.working_put("b", 2)
        agent_facade.working_clear()
        assert agent_facade.working_get("a") is None
        assert agent_facade.working_get("b") is None

    def test_clear_chat(self, chat_facade):
        chat_facade.working_put("a", 1)
        chat_facade.working_clear()
        assert chat_facade.working_get("a") is None


# ── Short-term Memory (L2) ──────────────────────────────────────

class TestShortTermMemory:
    def test_store_returns_id_agent(self, agent_facade):
        entry_id = agent_facade.short_term_store(
            "Fix login timeout by setting socket option",
            task="Fix login timeout",
            agent="claude",
        )
        assert isinstance(entry_id, str)
        assert len(entry_id) > 0

    def test_store_returns_id_chat(self, chat_facade):
        entry_id = chat_facade.short_term_store(
            "User asked about auth bug",
            task="auth bug",
            agent="user",
        )
        assert isinstance(entry_id, str)
        assert len(entry_id) > 0

    def test_search_agent(self, agent_facade):
        agent_facade.short_term_store(
            "Fix login timeout",
            task="Fix login timeout",
            agent="claude",
        )
        agent_facade.short_term_store(
            "Deploy to production",
            task="Deploy",
            agent="kimi",
        )
        results = agent_facade.short_term_search(query="login", top=10)
        assert isinstance(results, list)
        assert len(results) >= 1
        assert all(isinstance(r, dict) for r in results)
        # 找到 login 相关条目
        tasks = [r.get("task", "") for r in results]
        assert any("login" in t.lower() for t in tasks)

    def test_search_chat(self, chat_facade):
        chat_facade.short_term_store(
            "Auth bug discussion",
            task="auth bug",
            agent="user",
        )
        results = chat_facade.short_term_search(query="auth", top=10)
        assert isinstance(results, list)
        assert all(isinstance(r, dict) for r in results)

    def test_search_empty_query_agent(self, agent_facade):
        agent_facade.short_term_store("content", task="t1")
        results = agent_facade.short_term_search(query="", top=10)
        assert isinstance(results, list)

    def test_stats_agent(self, agent_facade):
        agent_facade.short_term_store("c1", task="t1", agent="claude")
        stats = agent_facade.short_term_stats()
        assert isinstance(stats, dict)
        assert "total" in stats

    def test_stats_chat(self, chat_facade):
        chat_facade.short_term_store("c1", task="t1", agent="user")
        stats = chat_facade.short_term_stats()
        assert isinstance(stats, dict)
        assert "total_entries" in stats

    def test_get_by_id_agent(self, agent_facade):
        entry_id = agent_facade.short_term_store(
            "test content",
            task="test task",
            agent="claude",
        )
        result = agent_facade.short_term_get(entry_id)
        assert result is not None
        assert isinstance(result, dict)
        assert result["task"] == "test task"

    def test_get_missing_returns_none(self, agent_facade):
        assert agent_facade.short_term_get("nonexistent-id") is None


# ── Long-term Memory (L3) ───────────────────────────────────────

class TestLongTermMemory:
    def test_search_empty_agent(self, agent_facade):
        """无索引时 search 应返回空列表而非抛异常。"""
        results = agent_facade.long_term_search("test query", top=5)
        assert isinstance(results, list)

    def test_search_empty_chat(self, chat_facade):
        results = chat_facade.long_term_search("test query", top=5)
        assert isinstance(results, list)


# ── 跨层操作 ────────────────────────────────────────────────────

class TestCrossLayer:
    def test_build_context_agent(self, agent_facade):
        agent_facade.short_term_store("auth bug fix", task="auth", agent="claude")
        ctx = agent_facade.build_context(query="auth")
        # agent mode 返回 dict
        assert isinstance(ctx, dict)
        assert "short_term" in ctx
        assert "long_term" in ctx

    def test_build_context_chat(self, chat_facade):
        ctx = chat_facade.build_context(session_id="s1", query="")
        # chat mode 返回原生 MemoryContext（向后兼容）
        assert hasattr(ctx, "working_context")
        assert hasattr(ctx, "short_term_results")
        assert hasattr(ctx, "long_term_results")

    def test_stats_agent(self, agent_facade):
        agent_facade.short_term_store("c1", task="t1")
        s = agent_facade.stats()
        assert isinstance(s, dict)
        assert "working_size" in s
        assert "short_term" in s

    def test_stats_chat(self, chat_facade):
        s = chat_facade.stats()
        assert isinstance(s, dict)
        assert "short_term_entries" in s

    def test_consolidate_agent(self, agent_facade):
        """consolidate 应可无参调用并返回 dict 或 None。"""
        result = agent_facade.consolidate()
        assert result is None or isinstance(result, dict)

    def test_consolidate_chat(self, chat_facade):
        result = chat_facade.consolidate()
        assert result is None or isinstance(result, dict)


# ── 跨实现通信 ──────────────────────────────────────────────────

class TestCrossImplementation:
    """验证两种 mode 共享同一个 maop.db，可互相读取对方写入的数据。"""

    def test_agent_can_query_chat_data(self, tmp_root):
        """chat 写入的 memory_entries 可被 agent 通过 query_memory_entries 读取。"""
        chat = MemoryFacade(root_dir=tmp_root, mode="chat")
        _mock_heavy_deps(chat.impl)
        agent = MemoryFacade(root_dir=tmp_root, mode="agent")

        chat.short_term_store("chat-side data", task="chat_task", agent="user")
        # agent 通过 query_memory_entries 读取 chat 写入的数据
        rows = agent.query_memory_entries(query="chat_task", top=10)
        assert isinstance(rows, list)
        assert len(rows) >= 1

    def test_chat_can_query_agent_data(self, tmp_root):
        """agent 写入的 episodic_memory 可被 chat 通过 query_episodic 读取。"""
        agent = MemoryFacade(root_dir=tmp_root, mode="agent")
        chat = MemoryFacade(root_dir=tmp_root, mode="chat")
        _mock_heavy_deps(chat.impl)

        agent.short_term_store("agent-side data", task="agent_task", agent="claude")
        # chat 通过 query_episodic 读取 agent 写入的数据
        rows = chat.query_episodic(query="agent_task", top=10)
        assert isinstance(rows, list)
        assert len(rows) >= 1


# ── 向后兼容 ────────────────────────────────────────────────────

class TestBackwardCompatibility:
    """验证原 ThreeLayerMemory / MemoryManager 类名与构造签名保持不变。"""

    def test_three_layer_memory_construction(self, tmp_root):
        mem = ThreeLayerMemory(root_dir=tmp_root, working_max=50, working_ttl=60)
        assert mem.working_get("x") is None  # 原有 API 仍可用

    def test_memory_manager_construction(self, tmp_root):
        mgr = MemoryManager(root_dir=tmp_root)
        _mock_heavy_deps(mgr)
        # 原有 API 仍可用
        assert mgr.conversation is not None
        assert mgr.memory is not None

    def test_three_layer_memory_episodic_api_unchanged(self, tmp_root):
        """原 episodic_store / episodic_search API 保持不变。"""
        mem = ThreeLayerMemory(root_dir=tmp_root)
        entry_id = mem.episodic_store(task="t1", agent="claude", outcome="success", score=0.9)
        results = mem.episodic_search(query="t1")
        assert len(results) >= 1
        assert results[0].entry.id == entry_id

    def test_memory_manager_add_exchange_unchanged(self, tmp_root):
        """原 add_exchange API 保持不变。"""
        mgr = MemoryManager(root_dir=tmp_root)
        _mock_heavy_deps(mgr)
        result = mgr.add_exchange(
            session_id="s1",
            user_msg="hello",
            assistant_msg="hi there",
        )
        assert "working_user_id" in result
        assert "short_term_id" in result


# ── Chat 场景透传（T3-A） ─────────────────────────────────────

class TestChatPassthrough:
    """T3-A: MemoryFacade 的 chat 专属透传 API。

    - ``chat_get_messages_for_llm`` 与直连 MemoryManager 结果一致
    - ``chat_add_exchange`` 写入三层记忆
    - ``conversation`` 只读属性透传 ConversationManager
    - mode="agent" 时告警并抛 NotImplementedError
    """

    def test_chat_get_messages_for_llm_delegates(self, chat_facade):
        """透传结果与底层 impl 完全一致（同一实例）。"""
        chat_facade.chat_add_exchange(
            session_id="s1", user_msg="hello", assistant_msg="hi there",
        )
        expected = chat_facade.impl.get_messages_for_llm(
            session_id="s1", query="hello", system_prompt="sys",
        )
        actual = chat_facade.chat_get_messages_for_llm(
            session_id="s1", query="hello", system_prompt="sys",
        )
        assert actual == expected
        # 至少包含 system prompt + 历史消息
        assert len(actual) >= 2
        assert actual[0]["role"] == "system"

    def test_chat_get_messages_for_llm_matches_direct(self, tmp_root):
        """验收：facade 透传与直连 MemoryManager 结果一致。"""
        facade = MemoryFacade(root_dir=tmp_root, mode="chat")
        _mock_heavy_deps(facade.impl)
        facade.chat_add_exchange(
            session_id="s1", user_msg="hello", assistant_msg="hi there",
        )

        direct = MemoryManager(root_dir=tmp_root)
        _mock_heavy_deps(direct)
        direct.add_exchange(
            session_id="s1", user_msg="hello", assistant_msg="hi there",
        )

        assert facade.chat_get_messages_for_llm(
            session_id="s1", query="hello", system_prompt="sys",
        ) == direct.get_messages_for_llm(
            session_id="s1", query="hello", system_prompt="sys",
        )

    def test_chat_add_exchange_stores(self, chat_facade):
        """chat_add_exchange 写入 working/short_term，conversation 可读。"""
        result = chat_facade.chat_add_exchange(
            session_id="s1", user_msg="hello", assistant_msg="hi there", agent="user",
        )
        assert "working_user_id" in result
        assert "working_asst_id" in result
        assert "short_term_id" in result

        msgs = chat_facade.conversation.get_context_window("s1").messages
        contents = [m.content for m in msgs]
        assert "hello" in contents
        assert "hi there" in contents

    def test_conversation_property_returns_manager(self, chat_facade):
        """conversation 只读属性透传 MemoryManager.conversation。"""
        conv = chat_facade.conversation
        assert conv is chat_facade.impl.conversation
        assert hasattr(conv, "add_message")
        assert hasattr(conv, "get_context_window")

    def test_agent_mode_chat_get_messages_raises(self, agent_facade, caplog):
        """mode=agent 时 chat API 告警并抛 NotImplementedError。"""
        with caplog.at_level(logging.WARNING, logger="maop.memory.facade"):  # noqa: SIM117
            with pytest.raises(NotImplementedError, match="chat-only"):
                agent_facade.chat_get_messages_for_llm(session_id="s1")
        assert any("chat-only" in r.message for r in caplog.records)

    def test_agent_mode_chat_add_exchange_raises(self, agent_facade, caplog):
        with caplog.at_level(logging.WARNING, logger="maop.memory.facade"):  # noqa: SIM117
            with pytest.raises(NotImplementedError, match="chat-only"):
                agent_facade.chat_add_exchange(
                    session_id="s1", user_msg="u", assistant_msg="a",
                )
        assert any("chat-only" in r.message for r in caplog.records)

    def test_agent_mode_conversation_raises(self, agent_facade, caplog):
        with caplog.at_level(logging.WARNING, logger="maop.memory.facade"):  # noqa: SIM117
            with pytest.raises(NotImplementedError, match="chat-only"):
                _ = agent_facade.conversation
        assert any("chat-only" in r.message for r in caplog.records)