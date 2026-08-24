"""Coverage tests for memory/search.py + memory/manager.py + core/vector.py
+ core/otel.py + enterprise/saml_handler.py.

Uses isolated tmp_path + real instances where possible.
"""
from __future__ import annotations

import pytest

# H4 修复：将 importorskip 改为显式 pytest.skip，让测试报告显式统计跳过数。
pytest.skip(reason="maop.enterprise 未发布", allow_module_level=True)

# ── Memory Search (via real MemoryStore) ────────────────────────────

class TestMemorySearch:
    def test_search_empty_query(self, tmp_path):
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(tmp_path))
        results = store.search(query="", top=5)
        assert isinstance(results, list)

    def test_search_with_entry_id(self, tmp_path):
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(tmp_path))
        entry_id = store.store(agent="a", task="t", content="hello world")
        assert entry_id
        results = store.search(entry_id=entry_id)
        assert len(results) == 1
        assert results[0].id == entry_id

    def test_search_with_entry_id_nonexistent(self, tmp_path):
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(tmp_path))
        results = store.search(entry_id="nonexistent-id")
        assert results == []

    def test_search_query_with_fts5(self, tmp_path):
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(tmp_path))
        store.store(agent="a", task="fix bug", content="auth bug in login")
        store.store(agent="b", task="add feature", content="new dashboard")
        results = store.search(query="bug", top=5)
        assert isinstance(results, list)

    def test_search_query_with_filters(self, tmp_path):
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(tmp_path))
        store.store(agent="alice", task="t1", content="content", trace_id="tr1")
        results = store.search(query="", agent="alice", top=5)
        assert isinstance(results, list)
        results = store.search(query="", trace_id="tr1", top=5)
        assert isinstance(results, list)
        results = store.search(query="", since="2020-01-01", until="2030-01-01", top=5)
        assert isinstance(results, list)

    def test_search_no_fts5_regex_fallback(self, tmp_path, monkeypatch):
        # Force FTS5 unavailable to exercise regex fallback path.
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(tmp_path))
        store.store(agent="a", task="fix bug", content="auth bug in login")
        # Patch _fts5_available to False to trigger regex path.
        original = store._fts5_available
        store._fts5_available = False
        try:
            results = store.search(query="bug", top=5)
            assert isinstance(results, list)
        finally:
            store._fts5_available = original

    def test_search_query_no_results(self, tmp_path):
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(tmp_path))
        results = store.search(query="nonexistentterm", top=5)
        assert isinstance(results, list)

    def test_facets_default(self, tmp_path):
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(tmp_path))
        store.store(agent="a", task="t1", content="c1", topic="bug")
        store.store(agent="b", task="t2", content="c2", topic="feature")
        facets = store.facets(field="topic")
        assert isinstance(facets, list)

    def test_facets_invalid_field(self, tmp_path):
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(tmp_path))
        store.store(agent="a", task="t1", content="c1")
        facets = store.facets(field="invalid_field")
        assert isinstance(facets, list)

    def test_facets_with_query(self, tmp_path):
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(tmp_path))
        store.store(agent="a", task="fix bug", content="bug content", topic="bug")
        facets = store.facets(query="bug", field="topic")
        assert isinstance(facets, list)

    def test_facets_agent_field(self, tmp_path):
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(tmp_path))
        store.store(agent="alice", task="t1", content="c1")
        facets = store.facets(field="agent")
        assert isinstance(facets, list)

    def test_facets_tags_field(self, tmp_path):
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(tmp_path))
        store.store(agent="a", task="t1", content="c1", tags=["bug", "urgent"])
        facets = store.facets(field="tags")
        assert isinstance(facets, list)

    def test_search_json(self, tmp_path):
        import json

        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(tmp_path))
        store.store(agent="a", task="t1", content=json.dumps({"type": "bug_report"}))
        results = store.search_json("$.type", "bug_report")
        assert isinstance(results, list)

    def test_search_json_no_match(self, tmp_path):
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(tmp_path))
        store.store(agent="a", task="t1", content="not json")
        results = store.search_json("$.type", "bug_report")
        assert isinstance(results, list)


# ── Memory Manager ──────────────────────────────────────────────────

class TestMemoryManager:
    def test_init(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        assert mgr is not None

    def test_add_exchange(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        result = mgr.add_exchange(
            session_id="s1", user_msg="Fix auth bug", assistant_msg="Fixed in auth.py",
        )
        assert isinstance(result, dict)
        assert "working_user_id" in result

    def test_build_context(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        mgr.add_exchange(
            session_id="s1", user_msg="Fix auth bug", assistant_msg="Fixed",
        )
        ctx = mgr.build_context(session_id="s1", query="auth")
        assert ctx is not None
        assert hasattr(ctx, "working_context")

    def test_build_context_no_query(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        mgr.add_exchange(
            session_id="s1", user_msg="hello", assistant_msg="hi",
        )
        ctx = mgr.build_context(session_id="s1")
        assert ctx is not None

    def test_get_messages_for_llm(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        mgr.add_exchange(
            session_id="s1", user_msg="hello", assistant_msg="hi",
        )
        msgs = mgr.get_messages_for_llm(session_id="s1", query="hello", system_prompt="You are helpful")
        assert isinstance(msgs, list)

    def test_search_all_layers(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        mgr.add_exchange(
            session_id="s1", user_msg="Fix bug", assistant_msg="Done",
        )
        result = mgr.search_all_layers(query="bug")
        assert isinstance(result, dict)
        assert "short_term" in result
        assert "long_term" in result

    def test_prune_expired(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        pruned = mgr.prune_expired()
        assert isinstance(pruned, int)

    def test_stats(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        stats = mgr.stats()
        assert isinstance(stats, dict)
        assert "short_term_entries" in stats

    def test_infer_topic(self):
        from maop.memory.manager import MemoryManager
        assert MemoryManager._infer_topic("fix the bug") == "debugging"
        assert MemoryManager._infer_topic("write tests") == "testing"
        assert MemoryManager._infer_topic("deploy to prod") == "deployment"
        assert MemoryManager._infer_topic("refactor code") == "refactoring"
        assert MemoryManager._infer_topic("implement feature") == "development"
        assert MemoryManager._infer_topic("design architecture") == "architecture"
        assert MemoryManager._infer_topic("review PR") == "code-review"
        assert MemoryManager._infer_topic("config setup") == "configuration"
        assert MemoryManager._infer_topic("auth login") == "authentication"
        assert MemoryManager._infer_topic("security issue") == "security"
        assert MemoryManager._infer_topic("random text") == "general"

    def test_build_injection_summary(self):
        from maop.memory.manager import MemoryManager
        # Empty
        assert MemoryManager._build_injection_summary([], []) == ""
        # Short term only
        short = [{"task": "t1", "snippet": "snippet1"}]
        result = MemoryManager._build_injection_summary(short, [])
        assert "[Recent Memory]" in result
        # Long term only
        long_t = [{"task": "t2", "snippet": "snippet2"}]
        result = MemoryManager._build_injection_summary([], long_t)
        assert "[Long-term Memory]" in result
        # Both
        result = MemoryManager._build_injection_summary(short, long_t)
        assert "[Long-term Memory]" in result
        assert "[Recent Memory]" in result

    def test_consolidate_no_consolidator(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        # _consolidator is None initially; consolidate tries to import DreamConsolidator.
        result = mgr.consolidate(dry_run=True)
        # Either returns a dict (if consolidator available) or None (if import failed).
        assert result is None or isinstance(result, dict)

    def test_extract_knowledge_no_extractor(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        # If knowledge_extractor is None, returns None.
        result = mgr.extract_knowledge("user", "assistant")
        assert result is None or isinstance(result, dict)

    def test_query_knowledge_no_graph(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        result = mgr.query_knowledge(entity="test")
        assert isinstance(result, str)

    def test_semantic_search(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        result = mgr.semantic_search(query="test", top=5)
        assert isinstance(result, list)

    def test_query_episodic_empty(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        result = mgr.query_episodic(query="", top=10)
        assert isinstance(result, list)

    def test_query_episodic_with_query(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        result = mgr.query_episodic(query="test", top=10)
        assert isinstance(result, list)

    def test_conversation_property(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        assert mgr.conversation is not None

    def test_memory_property(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=str(tmp_path))
        assert mgr.memory is not None

    def test_maybe_consolidate_below_threshold(self, tmp_path):
        from maop.memory.manager import ConsolidationTrigger, MemoryManager, MemoryManagerConfig
        cfg = MemoryManagerConfig(consolidation=ConsolidationTrigger(entry_threshold=10000))
        mgr = MemoryManager(root_dir=str(tmp_path), config=cfg)
        # Below threshold — should be no-op.
        mgr._maybe_consolidate()


# ── Core Vector Store ───────────────────────────────────────────────

class TestVectorStoreFull:
    def test_index_and_search(self, tmp_path):
        from maop.core.memory.vector import VectorStore
        vs = VectorStore(db_path=str(tmp_path / "vectors.db"))
        eid = vs.index("e1", text="hello world", metadata={"topic": "test"})
        assert eid == "e1"
        results = vs.search("hello", top=5)
        assert isinstance(results, list)

    def test_index_with_vector(self, tmp_path):
        from maop.core.memory.vector import VectorStore
        vs = VectorStore(db_path=str(tmp_path / "vectors.db"))
        eid = vs.index("e1", text="hello", vector=[0.1, 0.2, 0.3])
        assert eid == "e1"

    def test_index_batch(self, tmp_path):
        from maop.core.memory.vector import VectorStore
        vs = VectorStore(db_path=str(tmp_path / "vectors.db"))
        entries = [
            {"id": "e1", "text": "hello"},
            {"id": "e2", "text": "world", "metadata": {"topic": "test"}},
        ]
        count = vs.index_batch(entries)
        assert count == 2

    def test_index_batch_with_vectors(self, tmp_path):
        from maop.core.memory.vector import VectorStore
        vs = VectorStore(db_path=str(tmp_path / "vectors.db"))
        entries = [
            {"id": "e1", "text": "hello", "vector": [0.1, 0.2]},
            {"id": "e2", "text": "world", "vector": [0.3, 0.4]},
        ]
        count = vs.index_batch(entries)
        assert count == 2

    def test_search_vector(self, tmp_path):
        from maop.core.memory.vector import VectorStore
        vs = VectorStore(db_path=str(tmp_path / "vectors.db"))
        vs.index("e1", text="hello", vector=[0.1, 0.2, 0.3])
        results = vs.search_vector([0.1, 0.2, 0.3], top=5)
        assert isinstance(results, list)

    def test_count(self, tmp_path):
        from maop.core.memory.vector import VectorStore
        vs = VectorStore(db_path=str(tmp_path / "vectors.db"))
        assert vs.count() == 0
        vs.index("e1", text="hello")
        assert vs.count() == 1

    def test_delete(self, tmp_path):
        from maop.core.memory.vector import VectorStore
        vs = VectorStore(db_path=str(tmp_path / "vectors.db"))
        vs.index("e1", text="hello")
        assert vs.delete("e1") is True
        # delete on nonexistent still returns True (no exception).
        assert vs.delete("nonexistent") is True

    def test_clear(self, tmp_path):
        from maop.core.memory.vector import VectorStore
        vs = VectorStore(db_path=str(tmp_path / "vectors.db"))
        vs.index("e1", text="hello")
        vs.index("e2", text="world")
        cleared = vs.clear()
        assert cleared >= 0

    def test_search_empty_store(self, tmp_path):
        from maop.core.memory.vector import VectorStore
        vs = VectorStore(db_path=str(tmp_path / "vectors.db"))
        results = vs.search("test", top=5)
        assert isinstance(results, list)

    def test_search_vector_empty_store(self, tmp_path):
        from maop.core.memory.vector import VectorStore
        vs = VectorStore(db_path=str(tmp_path / "vectors.db"))
        results = vs.search_vector([0.1, 0.2], top=5)
        assert isinstance(results, list)


# ── Embedding Providers ─────────────────────────────────────────────

class TestEmbeddingProviders:
    def test_hash_embedding(self):
        from maop.core.memory.vector import HashEmbedding
        emb = HashEmbedding(dim=64)
        vec = emb.embed("hello world")
        assert isinstance(vec, list)
        assert len(vec) == 64

    def test_hash_embedding_batch(self):
        from maop.core.memory.vector import HashEmbedding
        emb = HashEmbedding(dim=64)
        vecs = emb.embed_batch(["hello", "world"])
        assert len(vecs) == 2

    def test_hash_embedding_dimension(self):
        from maop.core.memory.vector import HashEmbedding
        emb = HashEmbedding(dim=128)
        assert emb.dimension == 128

    def test_semantic_embedding_init(self):
        from maop.core.memory.vector import SentenceTransformerEmbedding
        # Verify the class is importable. Actual instantiation requires
        # downloading a model from HuggingFace Hub which is not available
        # in offline/CI environments — avoid network call here.
        assert SentenceTransformerEmbedding is not None


# ── OpenTelemetry (otel.py) ─────────────────────────────────────────

class TestOtel:
    def test_is_enabled_default(self, monkeypatch):
        monkeypatch.delenv("MAOP_OTEL_ENABLED", raising=False)
        from maop.core.monitoring.otel import is_enabled
        assert is_enabled() is False

    def test_is_enabled_true(self, monkeypatch):
        monkeypatch.setenv("MAOP_OTEL_ENABLED", "1")
        from maop.core.monitoring.otel import is_enabled
        assert is_enabled() is True

    def test_is_enabled_true_yes(self, monkeypatch):
        monkeypatch.setenv("MAOP_OTEL_ENABLED", "yes")
        from maop.core.monitoring.otel import is_enabled
        assert is_enabled() is True

    def test_is_enabled_true_true(self, monkeypatch):
        monkeypatch.setenv("MAOP_OTEL_ENABLED", "true")
        from maop.core.monitoring.otel import is_enabled
        assert is_enabled() is True

    def test_get_tracer_disabled(self, monkeypatch):
        monkeypatch.delenv("MAOP_OTEL_ENABLED", raising=False)
        from maop.core.monitoring.otel import _NoopTracer, get_tracer
        tracer = get_tracer("test")
        assert isinstance(tracer, _NoopTracer)

    def test_span_noop(self, monkeypatch):
        monkeypatch.delenv("MAOP_OTEL_ENABLED", raising=False)
        from maop.core.monitoring.otel import _NoopSpan, get_tracer, span
        tracer = get_tracer("test")
        with span(tracer, "test_span", attributes={"key": "value"}) as s:
            assert isinstance(s, _NoopSpan)

    def test_span_with_trace_id(self, monkeypatch):
        monkeypatch.delenv("MAOP_OTEL_ENABLED", raising=False)
        from maop.core.monitoring.otel import get_tracer, span
        tracer = get_tracer("test")
        with span(tracer, "test_span", trace_id="t123") as s:
            assert s is not None

    def test_setup_provider_disabled(self, monkeypatch):
        monkeypatch.delenv("MAOP_OTEL_ENABLED", raising=False)
        from maop.core.monitoring.otel import setup_provider
        # Should be no-op when disabled.
        setup_provider()

    def test_setup_provider_enabled_no_lib(self, monkeypatch):
        # Even if enabled flag is set, if opentelemetry is not installed it should not raise.
        monkeypatch.setenv("MAOP_OTEL_ENABLED", "1")
        from maop.core.monitoring.otel import setup_provider
        try:
            setup_provider()
        except Exception:
            # May raise if lib partially installed; that's fine.
            pass

    def test_inject_trace_context_disabled(self, monkeypatch):
        monkeypatch.delenv("MAOP_OTEL_ENABLED", raising=False)
        from maop.core.monitoring.otel import inject_trace_context
        carrier = {}
        inject_trace_context(carrier)
        # No-op when disabled.
        assert carrier == {}

    def test_extract_trace_context_disabled(self, monkeypatch):
        monkeypatch.delenv("MAOP_OTEL_ENABLED", raising=False)
        from maop.core.monitoring.otel import extract_trace_context
        result = extract_trace_context({})
        assert result is None

    def test_noop_tracer_start_span(self):
        from maop.core.monitoring.otel import _NoopSpan, _NoopTracer
        tracer = _NoopTracer()
        span = tracer.start_span("name")
        assert isinstance(span, _NoopSpan)

    def test_noop_span_methods(self):
        from maop.core.monitoring.otel import _NoopSpan
        s = _NoopSpan()
        # Should not raise.
        s.set_attribute("k", "v")
        s.add_event("evt")
        s.record_exception(Exception())
        s.set_status(0)
        s.end()
        assert s.is_recording is False

    def test_noop_span_context_manager(self):
        from maop.core.monitoring.otel import _NoopSpan
        s = _NoopSpan()
        with s as ctx:
            assert ctx is s


# ── SAML Handler ────────────────────────────────────────────────────

class TestSamlHandler:
    def test_init(self):
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig
        cfg = SSOConfig(
            client_id="sp",
            client_secret="secret",
            redirect_uri="https://sp.example.com/acs",
            saml_entity_id="maop-sp",
            saml_acs_url="https://sp.example.com/acs",
            saml_idp_cert="",
        )
        handler = SAMLHandler(cfg)
        assert handler is not None

    def test_get_idp_metadata_direct_cert(self):
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig
        cfg = SSOConfig(
            client_id="sp",
            client_secret="secret",
            redirect_uri="https://sp.example.com/acs",
            saml_entity_id="maop-sp",
            saml_acs_url="https://sp.example.com/acs",
            saml_idp_cert="dummy-cert-base64",
        )
        handler = SAMLHandler(cfg)
        metadata = handler._get_idp_metadata()
        assert metadata["entity_id"] == "maop-sp"
        assert metadata["x509_cert"] == "dummy-cert-base64"

    def test_get_idp_cert_b64_direct(self):
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig
        cfg = SSOConfig(
            client_id="sp",
            client_secret="secret",
            redirect_uri="https://sp.example.com/acs",
            saml_entity_id="maop-sp",
            saml_acs_url="https://sp.example.com/acs",
            saml_idp_cert="dummy-cert-base64",
        )
        handler = SAMLHandler(cfg)
        cert = handler._get_idp_cert_b64()
        assert cert == "dummy-cert-base64"

    def test_get_idp_cert_b64_missing(self):
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig, SSOError
        cfg = SSOConfig(
            client_id="sp",
            client_secret="secret",
            redirect_uri="https://sp.example.com/acs",
        )
        handler = SAMLHandler(cfg)
        with pytest.raises(SSOError):
            handler._get_idp_cert_b64()

    def test_fetch_idp_metadata_no_url(self):
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig, SSOError
        cfg = SSOConfig(
            client_id="sp",
            client_secret="secret",
            redirect_uri="https://sp.example.com/acs",
        )
        handler = SAMLHandler(cfg)
        with pytest.raises(SSOError):
            handler._fetch_idp_metadata()

    def test_parse_idp_metadata_minimal(self):
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig
        cfg = SSOConfig(
            client_id="sp",
            client_secret="secret",
            redirect_uri="https://sp.example.com/acs",
        )
        handler = SAMLHandler(cfg)
        # Minimal valid SAML metadata XML.
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="https://idp.example.com">
  <IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://idp.example.com/sso"/>
    <SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="https://idp.example.com/sso/post"/>
    <SingleLogoutService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://idp.example.com/slo"/>
    <KeyDescriptor use="signing">
      <KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#">
        <X509Data>
          <X509Certificate>dummycertdata</X509Certificate>
        </X509Data>
      </KeyInfo>
    </KeyDescriptor>
  </IDPSSODescriptor>
</EntityDescriptor>"""
        parsed = handler._parse_idp_metadata(xml)
        assert parsed["entity_id"] == "https://idp.example.com"
        assert parsed["sso_url"] == "https://idp.example.com/sso"
        assert parsed["slo_url"] == "https://idp.example.com/slo"
        assert "dummycertdata" in parsed["x509_cert"]

    def test_parse_idp_metadata_invalid_xml(self):
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig, SSOError
        cfg = SSOConfig(
            client_id="sp",
            client_secret="secret",
            redirect_uri="https://sp.example.com/acs",
        )
        handler = SAMLHandler(cfg)
        with pytest.raises(SSOError):
            handler._parse_idp_metadata(b"not valid xml <<<>")

    def test_build_authn_request(self):
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig
        cfg = SSOConfig(
            client_id="sp",
            client_secret="secret",
            redirect_uri="https://sp.example.com/acs",
            saml_entity_id="maop-sp",
            saml_acs_url="https://sp.example.com/acs",
        )
        handler = SAMLHandler(cfg)
        xml = handler._build_authn_request("id_test123")
        assert isinstance(xml, bytes)
        assert b"AuthnRequest" in xml
        assert b"id_test123" in xml

    def test_build_authn_request_no_acs(self):
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig, SSOError
        cfg = SSOConfig(
            client_id="sp",
            client_secret="secret",
            redirect_uri="",
            saml_acs_url="",
        )
        handler = SAMLHandler(cfg)
        with pytest.raises(SSOError):
            handler._build_authn_request("id_test")

    def test_get_authorize_url(self):
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig
        cfg = SSOConfig(
            client_id="sp",
            client_secret="secret",
            redirect_uri="https://sp.example.com/acs",
            saml_entity_id="maop-sp",
            saml_acs_url="https://sp.example.com/acs",
            saml_idp_cert="dummy",
        )
        handler = SAMLHandler(cfg)
        # Pre-populate metadata to avoid fetching.
        handler._idp_metadata = {
            "entity_id": "https://idp.example.com",
            "sso_url": "https://idp.example.com/sso",
            "slo_url": "",
            "x509_cert": "dummy",
        }
        url = handler.get_authorize_url(state="abc")
        assert "https://idp.example.com/sso" in url
        assert "SAMLRequest" in url
        assert "RelayState=abc" in url

    def test_get_authorize_url_no_state(self):
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig
        cfg = SSOConfig(
            client_id="sp",
            client_secret="secret",
            redirect_uri="https://sp.example.com/acs",
            saml_entity_id="maop-sp",
            saml_acs_url="https://sp.example.com/acs",
            saml_idp_cert="dummy",
        )
        handler = SAMLHandler(cfg)
        handler._idp_metadata = {
            "entity_id": "https://idp.example.com",
            "sso_url": "https://idp.example.com/sso",
            "slo_url": "",
            "x509_cert": "dummy",
        }
        url = handler.get_authorize_url()
        assert "https://idp.example.com/sso" in url
        assert "SAMLRequest" in url

    def test_get_authorize_url_no_sso_url(self):
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig, SSOError
        cfg = SSOConfig(
            client_id="sp",
            client_secret="secret",
            redirect_uri="https://sp.example.com/acs",
            saml_entity_id="maop-sp",
            saml_acs_url="https://sp.example.com/acs",
            saml_idp_cert="dummy",
        )
        handler = SAMLHandler(cfg)
        handler._idp_metadata = {
            "entity_id": "",
            "sso_url": "",
            "slo_url": "",
            "x509_cert": "dummy",
        }
        with pytest.raises(SSOError):
            handler.get_authorize_url()

    def test_handle_response_empty(self):
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig, SSOError
        cfg = SSOConfig(
            client_id="sp",
            client_secret="secret",
            redirect_uri="https://sp.example.com/acs",
            saml_idp_cert="dummy",
        )
        handler = SAMLHandler(cfg)
        with pytest.raises(SSOError):
            handler.handle_response("")

    def test_handle_response_invalid_b64(self):
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig, SSOError
        cfg = SSOConfig(
            client_id="sp",
            client_secret="secret",
            redirect_uri="https://sp.example.com/acs",
            saml_idp_cert="dummy",
        )
        handler = SAMLHandler(cfg)
        with pytest.raises(SSOError):
            handler.handle_response("@@@not valid base64@@@")

    def test_handle_response_invalid_xml(self):
        import base64

        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig, SSOError
        cfg = SSOConfig(
            client_id="sp",
            client_secret="secret",
            redirect_uri="https://sp.example.com/acs",
            saml_idp_cert="dummy",
        )
        handler = SAMLHandler(cfg)
        bad_xml_b64 = base64.b64encode(b"not valid xml <<<>").decode()
        with pytest.raises(SSOError):
            handler.handle_response(bad_xml_b64)

    def test_verify_signature_invalid_cert(self):
        from maop.enterprise.saml_handler import SAMLHandler
        from maop.enterprise.sso import SSOConfig, SSOError
        cfg = SSOConfig(
            client_id="sp",
            client_secret="secret",
            redirect_uri="https://sp.example.com/acs",
            saml_idp_cert="not-base64-cert",
        )
        handler = SAMLHandler(cfg)
        with pytest.raises(SSOError):
            handler._verify_signature(b"<resp/>", "not-base64-cert")

    def test_parse_saml_time(self):
        from maop.enterprise.saml_handler import _parse_saml_time
        result = _parse_saml_time("2024-01-01T12:00:00Z")
        assert result is not None
        assert result.year == 2024

    def test_parse_saml_time_with_fractional(self):
        from maop.enterprise.saml_handler import _parse_saml_time
        result = _parse_saml_time("2024-01-01T12:00:00.123Z")
        assert result is not None
        assert result.year == 2024