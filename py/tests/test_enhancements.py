"""Tests for P1-P2 architecture enhancements.

Covers:
  - P1-2: Circuit breaker time-series events
  - P1-3: Token-level streaming SSE
  - P1-4: Dynamic task decomposition
  - P2-1: Vector search (HashEmbedding + cosine similarity)
  - P2-2: JSON1 queries + FTS5 full-text search
"""

from __future__ import annotations

import json
import math
import tempfile
import shutil
import time
from pathlib import Path

import pytest


# ── P1-2: Circuit breaker events ─────────────────────────────

class TestBreakerEvents:
    """Test time-series event recording in circuit breaker."""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        from maop.core.circuit_breaker import CircuitBreaker
        self.cb = CircuitBreaker(path=Path(self.tmp) / "cb.db")

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_events_on_state_transition(self):
        """Events recorded when breaker transitions CLOSED → OPEN."""
        # Trigger failure past threshold
        self.cb.record_failure("agent-a")
        self.cb.record_failure("agent-a")
        self.cb.record_failure("agent-a")  # 3 failures → OPEN

        events = self.cb.get_events(agent="agent-a")
        assert len(events) >= 1
        e = events[0]
        assert e["old_state"] == "closed"
        assert e["new_state"] == "open"
        assert e["agent"] == "agent-a"

    def test_events_on_recovery(self):
        """Events recorded when breaker transitions OPEN → CLOSED."""
        self.cb.record_failure("agent-b")
        self.cb.record_failure("agent-b")
        self.cb.record_failure("agent-b")  # → OPEN
        self.cb.record_success("agent-b")  # → CLOSED

        events = self.cb.get_events(agent="agent-b")
        # Should have 2 transitions: closed→open, open→closed
        states = [(e["old_state"], e["new_state"]) for e in events]
        assert ("closed", "open") in states
        assert ("open", "closed") in states

    def test_no_event_when_no_transition(self):
        """No event when state doesn't change."""
        self.cb.record_success("agent-c")  # CLOSED → CLOSED (no transition)
        events = self.cb.get_events(agent="agent-c")
        assert len(events) == 0

    def test_event_count(self):
        """Event count aggregation."""
        self.cb.record_failure("x")
        self.cb.record_failure("x")
        self.cb.record_failure("x")  # → OPEN
        cnt = self.cb.get_event_count(agent="x")
        assert cnt >= 1

    def test_events_time_filter(self):
        """Filter events by time range."""
        before = time.time()
        self.cb.record_failure("y")
        self.cb.record_failure("y")
        self.cb.record_failure("y")
        after = time.time()

        events = self.cb.get_events(agent="y", since=before - 1, until=after + 1)
        assert len(events) >= 1

        # Outside range should return 0
        events_empty = self.cb.get_events(agent="y", since=after + 100)
        assert len(events_empty) == 0


# ── P1-3: Token-level streaming ──────────────────────────────

class TestTokenStreamer:
    """Test TokenStreamer for token-by-token SSE streaming."""

    def test_push_and_count(self):
        from maop.concurrency import TokenStreamer
        ts = TokenStreamer()
        ts.push_token("Hello")
        ts.push_token(" world")
        ts.push_token("!")
        assert ts.token_count == 3
        assert ts.total_chars == 12
        assert not ts.is_ended

    def test_end_signal(self):
        from maop.concurrency import TokenStreamer
        ts = TokenStreamer()
        ts.push_token("test")
        ts.end()
        assert ts.is_ended

    def test_tokens_per_second(self):
        from maop.concurrency import TokenStreamer
        ts = TokenStreamer()
        ts.push_token("a")
        ts.push_token("b")
        tps = ts.tokens_per_second
        assert tps > 0  # Should be positive

    def test_push_tokens_batch(self):
        from maop.concurrency import TokenStreamer
        ts = TokenStreamer()
        ts.push_tokens(["Hello", " ", "world"])
        assert ts.token_count == 3

    @pytest.mark.asyncio
    async def test_token_stream_sse(self):
        from maop.concurrency import TokenStreamer
        ts = TokenStreamer()
        ts.push_token("Hello")
        ts.push_token(" world")
        ts.end()

        chunks = []
        async for chunk in ts.token_stream():
            chunks.append(chunk)

        assert len(chunks) == 3  # 2 tokens + end
        assert "event: token" in chunks[0]
        assert "Hello" in chunks[0]
        assert "event: token_end" in chunks[2]

    @pytest.mark.asyncio
    async def test_text_stream_raw(self):
        from maop.concurrency import TokenStreamer
        ts = TokenStreamer()
        ts.push_token("A")
        ts.push_token("B")
        ts.end()

        tokens = []
        async for token in ts.text_stream():
            tokens.append(token)

        assert tokens == ["A", "B"]


# ── P1-4: Dynamic task decomposition ─────────────────────────

class TestTaskDecomposition:
    """Test engine._decompose_task heuristics."""

    def setup_method(self):
        from maop.engine import Engine, WorkflowStep, StepType
        self.engine = Engine()
        self.StepType = StepType
        self.WorkflowStep = WorkflowStep

    def test_semicolon_decomposition(self):
        step = self.WorkflowStep(
            id="s1", type=self.StepType.PLAN, task="do A; do B; do C",
        )
        subs = self.engine._decompose_task("do A; do B; do C", step)
        assert len(subs) == 3
        assert subs[0].task == "do A"
        assert subs[1].task == "do B"
        assert subs[2].task == "do C"
        # Sequential: each depends on previous
        assert subs[1].depends_on == ["s1_sub0"]
        assert subs[2].depends_on == ["s1_sub1"]

    def test_numbered_list_decomposition(self):
        step = self.WorkflowStep(
            id="s2", type=self.StepType.PLAN,
            task="1. First step 2. Second step 3. Third step",
        )
        subs = self.engine._decompose_task(step.task, step)
        assert len(subs) == 3

    def test_bullet_list_decomposition(self):
        step = self.WorkflowStep(
            id="s3", type=self.StepType.PLAN,
            task="- Fix login bug\n- Add timeout\n- Write tests",
        )
        subs = self.engine._decompose_task(step.task, step)
        assert len(subs) == 3

    def test_and_conjunction(self):
        step = self.WorkflowStep(
            id="s4", type=self.StepType.PLAN,
            task="implement the authentication module and write comprehensive test suite",
        )
        subs = self.engine._decompose_task(step.task, step)
        assert len(subs) == 2

    def test_atomic_task_no_decomposition(self):
        step = self.WorkflowStep(
            id="s5", type=self.StepType.PLAN, task="simple task",
        )
        subs = self.engine._decompose_task("simple task", step)
        assert len(subs) == 0


# ── P2-1: Vector search ─────────────────────────────────────

class TestVectorSearch:
    """Test pure Python vector store with HashEmbedding."""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cosine_similarity(self):
        from maop.core.vector import cosine_similarity
        # Identical vectors → 1.0
        assert abs(cosine_similarity([1, 0, 0], [1, 0, 0]) - 1.0) < 1e-6
        # Orthogonal → 0.0
        assert abs(cosine_similarity([1, 0, 0], [0, 1, 0])) < 1e-6
        # Opposite → -1.0
        assert abs(cosine_similarity([1, 0], [-1, 0]) - (-1.0)) < 1e-6
        # Empty → 0.0
        assert cosine_similarity([], []) == 0.0

    def test_hash_embedding(self):
        from maop.core.vector import HashEmbedding
        emb = HashEmbedding(dim=64)
        v = emb.embed("hello world")
        assert len(v) == 64
        # Should be unit vector
        norm = math.sqrt(sum(x * x for x in v))
        assert abs(norm - 1.0) < 1e-6

    def test_hash_embedding_consistency(self):
        from maop.core.vector import HashEmbedding
        emb = HashEmbedding(dim=32)
        v1 = emb.embed("test")
        v2 = emb.embed("test")
        assert v1 == v2  # Deterministic

    def test_vector_store_index_and_search(self):
        from maop.core.vector import VectorStore
        vs = VectorStore(db_path=Path(self.tmp) / "vec.db")

        vs.index("d1", "Fix login timeout bug", metadata={"agent": "claude"})
        vs.index("d2", "Deploy new config system", metadata={"agent": "kimi"})
        vs.index("d3", "Fix authentication timeout", metadata={"agent": "gpt"})

        assert vs.count() == 3

        # Search for similar content
        results = vs.search("authentication timeout", top=5)
        assert len(results) > 0
        # HashEmbedding is non-semantic; just verify results returned
        assert results[0].id in ("d1", "d2", "d3")

    def test_vector_store_search_by_vector(self):
        from maop.core.vector import VectorStore, HashEmbedding
        emb = HashEmbedding(dim=64)
        vs = VectorStore(db_path=Path(self.tmp) / "vec2.db", embedding=emb)

        vs.index("x1", "hello world")
        vs.index("x2", "goodbye world")

        query_vec = emb.embed("hello world")
        results = vs.search_vector(query_vec, top=2)
        assert len(results) >= 1
        assert results[0].id == "x1"
        assert results[0].score > 0.9  # Same text → very high similarity

    def test_vector_store_delete(self):
        from maop.core.vector import VectorStore
        vs = VectorStore(db_path=Path(self.tmp) / "vec3.db")
        vs.index("del1", "to be deleted")
        assert vs.count() == 1
        vs.delete("del1")
        assert vs.count() == 0

    def test_vector_store_batch_index(self):
        from maop.core.vector import VectorStore
        vs = VectorStore(db_path=Path(self.tmp) / "vec4.db")
        entries = [
            {"id": f"b{i}", "text": f"Document number {i}"}
            for i in range(10)
        ]
        count = vs.index_batch(entries)
        assert count == 10
        assert vs.count() == 10

    def test_vector_store_threshold(self):
        from maop.core.vector import VectorStore
        vs = VectorStore(db_path=Path(self.tmp) / "vec5.db")
        vs.index("t1", "alpha beta gamma")
        vs.index("t2", "delta epsilon zeta")

        # High threshold should filter out dissimilar results
        results = vs.search("alpha beta gamma", threshold=0.99)
        # Only exact match should pass
        assert all(r.score >= 0.99 for r in results)

    def test_vector_store_clear(self):
        from maop.core.vector import VectorStore
        vs = VectorStore(db_path=Path(self.tmp) / "vec6.db")
        vs.index("c1", "content 1")
        vs.index("c2", "content 2")
        deleted = vs.clear()
        assert deleted == 2
        assert vs.count() == 0


# ── P2-2: JSON1 queries ─────────────────────────────────────

class TestJson1Queries:
    """Test SQLite JSON1 extension queries on MaopDatabase."""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        from maop.core.data import MaopDatabase
        self.db = MaopDatabase(db_path=Path(self.tmp) / "test.db")
        self.db.init()

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_json_extract_query(self):
        """Query JSON column with json_extract."""
        # Insert a delegation with JSON in stdout
        self.db.insert_delegation(
            agent="claude", task="test",
            stdout=json.dumps({"agent": "claude", "status": "ok"}),
        )
        self.db.insert_delegation(
            agent="kimi", task="test",
            stdout=json.dumps({"agent": "kimi", "status": "error"}),
        )

        # Query for agent = "claude" in JSON
        rows = self.db.json_query("delegations", "stdout", "$.agent", "claude")
        assert len(rows) >= 1
        assert rows[0]["json_value"] == "claude"

    def test_json_each_expand(self):
        """Expand JSON array with json_each."""
        # Insert metrics with tags array
        self.db.record_metric(
            agent="test", metric_name="latency", metric_value=100.0,
            tags=json.dumps(["prod", "api"]),
        )

        rows = self.db.json_each("metrics", "tags", "$")
        assert len(rows) >= 1
        # Each tag should be a separate row
        values = [r.get("atom") or r.get("value") for r in rows]
        assert any(v == "prod" or "prod" in str(v) for v in values if v)


# ── P2-2: FTS5 search ───────────────────────────────────────

class TestFts5Search:
    """Test FTS5 full-text search on MaopDatabase."""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        from maop.core.data import MaopDatabase
        self.db = MaopDatabase(db_path=Path(self.tmp) / "fts.db")
        self.db.init()

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fts_init(self):
        """Create FTS5 virtual table."""
        ok = self.db.fts_init("delegations", ["task", "stdout"])
        assert ok

    def test_fts_search_basic(self):
        """Basic FTS5 search."""
        self.db.fts_init("delegations", ["task", "stdout"])

        # Insert test data
        self.db.insert_delegation(agent="a", task="fix login timeout bug", stdout="error in auth module")
        self.db.insert_delegation(agent="b", task="deploy config system", stdout="success")

        # Rebuild FTS index
        self.db.fts_rebuild("delegations")

        # Search
        results = self.db.fts_search("delegations", "login timeout", highlight=False)
        assert len(results) >= 1
        assert results[0]["task"] == "fix login timeout bug"

    def test_fts_search_with_highlight(self):
        """FTS5 search with snippet highlighting."""
        self.db.fts_init("delegations", ["task", "stdout"])

        self.db.insert_delegation(agent="a", task="fix login timeout bug", stdout="error in auth")
        self.db.fts_rebuild("delegations")

        results = self.db.fts_search("delegations", "login", highlight=True)
        assert len(results) >= 1
        # Should have snippet field
        assert "snippet" in results[0]
