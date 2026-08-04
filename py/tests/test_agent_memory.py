"""Tests for maop.core.agent_memory — SQLite-backed agent memory store."""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.agent_memory import (
    MAX_RECORDS_PER_TYPE,
    MEMORY_TYPES,
    AgentMemory,
)


@pytest.fixture
def memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgentMemory:
    """Create an AgentMemory with an isolated temp database."""
    db_path = tmp_path / "agent_memory.db"

    def fake_get_db_path(module_name: str = "", *, legacy_fallback: str = "") -> Path:
        return db_path

    monkeypatch.setattr("maop.core.agent_memory.get_db_path", fake_get_db_path)
    return AgentMemory(root_dir=tmp_path)


class TestMemoryTypes:
    def test_expected_types(self) -> None:
        assert MEMORY_TYPES == {
            "interaction", "preference", "error_pattern",
            "performance", "lesson",
        }

    def test_max_records_config(self) -> None:
        assert MAX_RECORDS_PER_TYPE["interaction"] == 500
        assert MAX_RECORDS_PER_TYPE["preference"] == 100
        assert MAX_RECORDS_PER_TYPE["performance"] == 1000


class TestStore:
    def test_store_returns_id(self, memory: AgentMemory) -> None:
        rid = memory.store("claude", "interaction", {"prompt": "hi"})
        assert isinstance(rid, int)
        assert rid > 0

    def test_store_with_metadata(self, memory: AgentMemory) -> None:
        rid = memory.store(
            "claude", "performance",
            {"latency_ms": 100},
            metadata={"source": "benchmark"},
        )
        records = memory.retrieve("claude", memory_type="performance")
        assert len(records) == 1
        assert records[0]["id"] == rid
        assert records[0]["metadata"] == {"source": "benchmark"}

    def test_store_with_importance(self, memory: AgentMemory) -> None:
        memory.store("claude", "lesson", {"tip": "reuse code"}, importance=0.9)
        records = memory.retrieve("claude", min_importance=0.8)
        assert len(records) == 1
        assert records[0]["importance"] == 0.9

    def test_invalid_memory_type_raises(self, memory: AgentMemory) -> None:
        with pytest.raises(ValueError, match="Invalid memory_type"):
            memory.store("claude", "invalid_type", {"data": 1})

    def test_store_chinese_content(self, memory: AgentMemory) -> None:
        memory.store("claude", "interaction", {"prompt": "你好世界"})
        records = memory.retrieve("claude", memory_type="interaction")
        assert records[0]["content"]["prompt"] == "你好世界"


class TestRetrieve:
    def test_retrieve_empty(self, memory: AgentMemory) -> None:
        records = memory.retrieve("claude")
        assert records == []

    def test_retrieve_by_type(self, memory: AgentMemory) -> None:
        memory.store("claude", "interaction", {"a": 1})
        memory.store("claude", "performance", {"b": 2})
        interactions = memory.retrieve("claude", memory_type="interaction")
        assert len(interactions) == 1
        assert interactions[0]["content"] == {"a": 1}

    def test_retrieve_limit(self, memory: AgentMemory) -> None:
        for i in range(10):
            memory.store("claude", "interaction", {"idx": i})
        records = memory.retrieve("claude", limit=3)
        assert len(records) == 3

    def test_retrieve_min_importance(self, memory: AgentMemory) -> None:
        memory.store("claude", "lesson", {"a": 1}, importance=0.3)
        memory.store("claude", "lesson", {"b": 2}, importance=0.8)
        records = memory.retrieve("claude", memory_type="lesson", min_importance=0.5)
        assert len(records) == 1
        assert records[0]["content"] == {"b": 2}

    def test_retrieve_orders_by_created_desc(self, memory: AgentMemory) -> None:
        memory.store("claude", "interaction", {"seq": "first"})
        memory.store("claude", "interaction", {"seq": "second"})
        records = memory.retrieve("claude", memory_type="interaction")
        assert records[0]["content"]["seq"] == "second"
        assert records[1]["content"]["seq"] == "first"

    def test_retrieve_isolates_agents(self, memory: AgentMemory) -> None:
        memory.store("claude", "interaction", {"a": 1})
        memory.store("codex", "interaction", {"b": 2})
        claude_records = memory.retrieve("claude")
        assert len(claude_records) == 1
        assert claude_records[0]["content"] == {"a": 1}


class TestForget:
    def test_forget_by_id(self, memory: AgentMemory) -> None:
        rid = memory.store("claude", "interaction", {"a": 1})
        deleted = memory.forget("claude", memory_id=rid)
        assert deleted == 1
        assert memory.retrieve("claude") == []

    def test_forget_all(self, memory: AgentMemory) -> None:
        memory.store("claude", "interaction", {"a": 1})
        memory.store("claude", "performance", {"b": 2})
        deleted = memory.forget("claude")
        assert deleted == 2
        assert memory.retrieve("claude") == []

    def test_forget_nonexistent_id(self, memory: AgentMemory) -> None:
        deleted = memory.forget("claude", memory_id=99999)
        assert deleted == 0

    def test_forget_type(self, memory: AgentMemory) -> None:
        memory.store("claude", "interaction", {"a": 1})
        memory.store("claude", "performance", {"b": 2})
        deleted = memory.forget_type("claude", "interaction")
        assert deleted == 1
        assert len(memory.retrieve("claude", memory_type="interaction")) == 0
        assert len(memory.retrieve("claude", memory_type="performance")) == 1


class TestSummarize:
    def test_summarize_empty(self, memory: AgentMemory) -> None:
        summary = memory.summarize("claude")
        assert summary["total_memories"] == 0
        assert summary["agent_name"] == "claude"
        assert summary["evolution_count"] == 0

    def test_summarize_with_data(self, memory: AgentMemory) -> None:
        memory.store("claude", "interaction", {"a": 1}, importance=0.5)
        memory.store("claude", "interaction", {"b": 2}, importance=1.0)
        memory.store("claude", "error_pattern", {"err": "timeout"})
        summary = memory.summarize("claude")
        assert summary["total_memories"] == 3
        assert summary["by_type"]["interaction"] == 2
        assert summary["by_type"]["error_pattern"] == 1
        assert summary["last_activity"] != ""
        assert 0.5 < summary["avg_importance"] <= 1.0

    def test_summarize_error_patterns(self, memory: AgentMemory) -> None:
        for _ in range(3):
            memory.store("claude", "error_pattern", {"err": "timeout"})
        memory.store("claude", "error_pattern", {"err": "oom"})
        summary = memory.summarize("claude")
        patterns = summary["top_error_patterns"]
        assert len(patterns) > 0
        assert patterns[0]["frequency"] == 3


class TestEvolutionHistory:
    def test_record_and_retrieve(self, memory: AgentMemory) -> None:
        memory.record_evolution(
            "claude", "config_change",
            "Adjusted timeout from 30s to 60s",
            {"timeout_s": 60},
        )
        history = memory.get_evolution_history("claude")
        assert len(history) == 1
        assert history[0]["evolution_type"] == "config_change"
        assert history[0]["changes"] == {"timeout_s": 60}
        assert history[0]["success"] is True

    def test_record_failure(self, memory: AgentMemory) -> None:
        memory.record_evolution(
            "claude", "failed_attempt",
            "Tried invalid config",
            {"bad": True},
            success=False,
        )
        history = memory.get_evolution_history("claude")
        assert history[0]["success"] is False

    def test_evolution_count_in_summary(self, memory: AgentMemory) -> None:
        memory.record_evolution("claude", "t1", "d1", {})
        memory.record_evolution("claude", "t2", "d2", {})
        summary = memory.summarize("claude")
        assert summary["evolution_count"] == 2

    def test_history_limit(self, memory: AgentMemory) -> None:
        for i in range(30):
            memory.record_evolution("claude", "t", f"d{i}", {})
        history = memory.get_evolution_history("claude", limit=5)
        assert len(history) == 5


class TestAutoCleanup:
    def test_cleanup_on_exceed(self, memory: AgentMemory) -> None:
        max_count = MAX_RECORDS_PER_TYPE["preference"]
        for i in range(max_count + 50):
            memory.store("claude", "preference", {"idx": i})
        records = memory.retrieve("claude", memory_type="preference", limit=10000)
        assert len(records) == max_count