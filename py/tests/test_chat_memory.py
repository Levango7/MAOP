"""Tests for v4.2: MemoryManager, ChatEngine, Chat Router."""

from __future__ import annotations


import pytest

from maop.memory.manager import (
    MemoryManager,
    MemoryManagerConfig,
    MemoryContext,
    MemoryLayer,
    ConsolidationTrigger,
)
from maop.core.chat_engine import (
    ChatEngine,
    ChatRequest,
    ChatResponse,
    ChatMessage,
    _sse_event,
)


# ═══════════════════════════════════════════════════════════════════
# MemoryManager Tests
# ═══════════════════════════════════════════════════════════════════

class TestMemoryManagerConfig:
    def test_defaults(self):
        c = MemoryManagerConfig()
        assert c.max_working_tokens == 4000
        assert c.short_term_ttl_days == 30
        assert c.long_term_min_group_size == 3
        assert c.inject_max_results == 5
        assert c.consolidation.auto_trigger is True

    def test_custom(self):
        c = MemoryManagerConfig(max_working_tokens=8000, short_term_ttl_days=7)
        assert c.max_working_tokens == 8000
        assert c.short_term_ttl_days == 7


class TestMemoryContext:
    def test_defaults(self):
        ctx = MemoryContext()
        assert ctx.working_context == []
        assert ctx.short_term_results == []
        assert ctx.long_term_results == []
        assert ctx.injected_summary == ""

    def test_with_data(self):
        ctx = MemoryContext(
            working_context=[{"role": "user", "content": "hi"}],
            short_term_results=[{"id": "1", "task": "test"}],
            injected_summary="[Recent Memory]\n  - test",
        )
        assert len(ctx.working_context) == 1
        assert ctx.injected_summary != ""


class TestMemoryLayer:
    def test_values(self):
        assert MemoryLayer.WORKING == "working"
        assert MemoryLayer.SHORT_TERM == "short_term"
        assert MemoryLayer.LONG_TERM == "long_term"


class TestMemoryManager:
    def test_init(self, tmp_path):
        mgr = MemoryManager(root_dir=str(tmp_path))
        assert mgr.conversation is not None
        assert mgr.memory is not None

    def test_add_exchange(self, tmp_path):
        mgr = MemoryManager(root_dir=str(tmp_path))
        result = mgr.add_exchange(
            session_id="test-session",
            user_msg="Fix the bug",
            assistant_msg="Fixed in auth.py",
            agent="claude",
        )
        assert "working_user_id" in result
        assert "working_asst_id" in result
        assert "short_term_id" in result

    def test_add_exchange_stores_conversation(self, tmp_path):
        mgr = MemoryManager(root_dir=str(tmp_path), config=MemoryManagerConfig(
            consolidation=ConsolidationTrigger(auto_trigger=False),
        ))
        mgr.add_exchange(
            session_id="s1",
            user_msg="Hello",
            assistant_msg="Hi there!",
        )
        history = mgr.conversation.get_history("s1")
        assert len(history) == 2
        assert history[0].role == "user"
        assert history[1].role == "assistant"

    def test_build_context(self, tmp_path):
        mgr = MemoryManager(root_dir=str(tmp_path), config=MemoryManagerConfig(
            consolidation=ConsolidationTrigger(auto_trigger=False),
        ))
        mgr.add_exchange(
            session_id="s1",
            user_msg="Fix auth bug",
            assistant_msg="Fixed",
        )
        ctx = mgr.build_context(session_id="s1", query="auth bug")
        assert len(ctx.working_context) > 0
        assert isinstance(ctx, MemoryContext)

    def test_build_context_empty_session(self, tmp_path):
        mgr = MemoryManager(root_dir=str(tmp_path))
        ctx = mgr.build_context(session_id="nonexistent", query="test")
        assert ctx.working_context == []

    def test_get_messages_for_llm(self, tmp_path):
        mgr = MemoryManager(root_dir=str(tmp_path), config=MemoryManagerConfig(
            consolidation=ConsolidationTrigger(auto_trigger=False),
        ))
        mgr.add_exchange(
            session_id="s1",
            user_msg="Hello",
            assistant_msg="Hi!",
        )
        messages = mgr.get_messages_for_llm(
            session_id="s1",
            system_prompt="You are helpful.",
        )
        assert any(m["role"] == "system" for m in messages)
        assert any(m["role"] == "user" for m in messages)

    def test_search_all_layers(self, tmp_path):
        mgr = MemoryManager(root_dir=str(tmp_path), config=MemoryManagerConfig(
            consolidation=ConsolidationTrigger(auto_trigger=False),
        ))
        mgr.add_exchange(
            session_id="s1",
            user_msg="Fix auth bug in login",
            assistant_msg="Fixed in auth.py",
        )
        results = mgr.search_all_layers(query="auth bug")
        assert "short_term" in results
        assert "long_term" in results

    def test_infer_topic(self):
        assert MemoryManager._infer_topic("Fix the bug") == "debugging"
        assert MemoryManager._infer_topic("Write tests") == "testing"
        assert MemoryManager._infer_topic("Deploy to prod") == "deployment"
        assert MemoryManager._infer_topic("Hello world") == "general"
        assert MemoryManager._infer_topic("Auth token expired") == "authentication"

    def test_build_injection_summary(self):
        short = [{"task": "fix bug", "snippet": "Fixed in main.py"}]
        long = [{"task": "consolidated", "snippet": "Summary of bugs"}]
        summary = MemoryManager._build_injection_summary(short, long)
        assert "Long-term Memory" in summary
        assert "Recent Memory" in summary

    def test_build_injection_summary_empty(self):
        assert MemoryManager._build_injection_summary([], []) == ""

    def test_stats(self, tmp_path):
        mgr = MemoryManager(root_dir=str(tmp_path), config=MemoryManagerConfig(
            consolidation=ConsolidationTrigger(auto_trigger=False),
        ))
        stats = mgr.stats()
        assert "short_term_entries" in stats
        assert "by_agent" in stats

    def test_prune_expired(self, tmp_path):
        mgr = MemoryManager(root_dir=str(tmp_path))
        count = mgr.prune_expired()
        assert isinstance(count, int)


# ═══════════════════════════════════════════════════════════════════
# ChatEngine Tests
# ═══════════════════════════════════════════════════════════════════

class TestChatMessage:
    def test_defaults(self):
        m = ChatMessage()
        assert m.role == "user"
        assert m.content == ""


class TestChatRequest:
    def test_defaults(self):
        r = ChatRequest(message="hello")
        assert r.session_id == ""
        assert r.stream is True
        assert r.max_tokens == 4096


class TestChatResponse:
    def test_defaults(self):
        r = ChatResponse()
        assert r.content == ""
        assert r.tokens_used == 0


class TestSSEEvent:
    def test_format(self):
        result = _sse_event("token", {"content": "hello"})
        assert "event: token" in result
        assert '"content": "hello"' in result
        assert result.endswith("\n\n")

    def test_session_event(self):
        result = _sse_event("session", {"session_id": "abc"})
        assert "event: session" in result


class TestChatEngine:
    def test_init(self, tmp_path):
        engine = ChatEngine(root_dir=str(tmp_path))
        assert engine.memory is not None

    def test_default_system_prompt(self, tmp_path):
        engine = ChatEngine(root_dir=str(tmp_path))
        assert "MAOP" in engine._default_system_prompt

    @pytest.mark.asyncio
    async def test_chat_fallback(self, tmp_path):
        engine = ChatEngine(root_dir=str(tmp_path), config=MemoryManagerConfig(
            consolidation=ConsolidationTrigger(auto_trigger=False),
        ))
        request = ChatRequest(
            session_id="test-chat",
            message="Hello MAOP",
            agent="nonexistent-agent",
            stream=False,
        )
        response = await engine.chat(request)
        assert response.session_id == "test-chat"
        assert response.content != ""

    @pytest.mark.asyncio
    async def test_chat_creates_session(self, tmp_path):
        engine = ChatEngine(root_dir=str(tmp_path), config=MemoryManagerConfig(
            consolidation=ConsolidationTrigger(auto_trigger=False),
        ))
        request = ChatRequest(message="Hello", stream=False)
        response = await engine.chat(request)
        assert response.session_id.startswith("chat-")

    @pytest.mark.asyncio
    async def test_chat_stores_conversation(self, tmp_path):
        engine = ChatEngine(root_dir=str(tmp_path), config=MemoryManagerConfig(
            consolidation=ConsolidationTrigger(auto_trigger=False),
        ))
        request = ChatRequest(
            session_id="s1",
            message="What is MAOP?",
            stream=False,
        )
        await engine.chat(request)
        history = engine.memory.conversation.get_history("s1")
        assert len(history) >= 2
        assert history[0].role == "user"
        assert history[0].content == "What is MAOP?"

    @pytest.mark.asyncio
    async def test_chat_stream(self, tmp_path):
        engine = ChatEngine(root_dir=str(tmp_path), config=MemoryManagerConfig(
            consolidation=ConsolidationTrigger(auto_trigger=False),
        ))
        request = ChatRequest(
            session_id="s1",
            message="Hello",
            stream=True,
        )
        events = []
        async for chunk in engine.chat_stream(request):
            events.append(chunk)

        assert len(events) >= 2
        assert any("event: session" in e for e in events)
        assert any("event: done" in e for e in events)


# ═══════════════════════════════════════════════════════════════════
# Integration: MemoryManager + ConversationManager
# ═══════════════════════════════════════════════════════════════════

class TestMemoryConversationIntegration:
    def test_multi_turn_conversation(self, tmp_path):
        mgr = MemoryManager(root_dir=str(tmp_path), config=MemoryManagerConfig(
            consolidation=ConsolidationTrigger(auto_trigger=False),
        ))
        mgr.add_exchange("s1", "Hello", "Hi there!", agent="claude")
        mgr.add_exchange("s1", "Fix bug", "Fixed!", agent="claude")
        mgr.add_exchange("s1", "Write tests", "Tests written", agent="claude")

        history = mgr.conversation.get_history("s1")
        assert len(history) == 6  # 3 exchanges * 2 messages each

    def test_context_window_respects_budget(self, tmp_path):
        mgr = MemoryManager(root_dir=str(tmp_path), config=MemoryManagerConfig(
            max_working_tokens=100,
            consolidation=ConsolidationTrigger(auto_trigger=False),
        ))
        for i in range(10):
            mgr.add_exchange("s1", f"Message {i} " + "x" * 50, f"Response {i}")

        ctx = mgr.build_context(session_id="s1")
        assert ctx.total_tokens_estimate <= 200  # some tolerance

    def test_memory_search_finds_exchange(self, tmp_path):
        mgr = MemoryManager(root_dir=str(tmp_path), config=MemoryManagerConfig(
            consolidation=ConsolidationTrigger(auto_trigger=False),
        ))
        mgr.add_exchange("s1", "Fix authentication bug", "Fixed in auth.py", agent="claude")

        results = mgr.search_all_layers(query="authentication", top=5)
        short_term = results.get("short_term", [])
        assert len(short_term) > 0