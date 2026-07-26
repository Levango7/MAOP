"""Tests for Iterations B+C+D: Knowledge, Vector Search, Config Mutator, Evolution Strategies."""

from __future__ import annotations

import json

from maop.core.config_mutator import ConfigMutator, MutationResult
from maop.core.evolution_strategies import (
    AggressiveStrategy,
    BalancedStrategy,
    ConservativeStrategy,
    CostAwareStrategy,
    StrategyConfig,
    StrategyEngine,
)
from maop.core.knowledge_extractor import (
    Entity,
    Fact,
    KnowledgeExtractor,
    Relation,
)
from maop.core.knowledge_graph import KnowledgeGraph
from maop.memory.vector_search import VectorResult, VectorSearch

# ═══════════════════════════════════════════════════════════════════
# B1: Knowledge Extractor
# ═══════════════════════════════════════════════════════════════════

class TestEntity:
    def test_defaults(self):
        e = Entity()
        assert e.name == ""
        assert e.entity_type == "concept"
        assert e.confidence == 1.0

    def test_custom(self):
        e = Entity(name="AuthService", entity_type="class", confidence=0.9)
        assert e.name == "AuthService"


class TestRelation:
    def test_defaults(self):
        r = Relation()
        assert r.relation_type == "related_to"

    def test_custom(self):
        r = Relation(source="Auth", target="JWT", relation_type="uses")
        assert r.source == "Auth"


class TestFact:
    def test_defaults(self):
        f = Fact()
        assert f.subject == ""
        assert f.predicate == ""
        assert f.confidence == 1.0


class TestKnowledgeExtractor:
    def test_extract_entities(self, tmp_path):
        ext = KnowledgeExtractor(root_dir=tmp_path)
        result = ext.extract_from_text("The AuthService uses JWT tokens for authentication")
        assert any(e.name == "AuthService" for e in result.entities)

    def test_extract_relations(self, tmp_path):
        ext = KnowledgeExtractor(root_dir=tmp_path)
        result = ext.extract_from_text("AuthService uses JWTHandler")
        assert any(r.source == "AuthService" and r.relation_type == "uses" for r in result.relations)

    def test_extract_facts(self, tmp_path):
        ext = KnowledgeExtractor(root_dir=tmp_path)
        result = ext.extract_from_text("AuthService is a security module")
        assert any(f.subject == "AuthService" and f.predicate == "is_a" for f in result.facts)

    def test_extract_config(self, tmp_path):
        ext = KnowledgeExtractor(root_dir=tmp_path)
        result = ext.extract_from_text("timeout=30 and max_retries=3")
        config_facts = [f for f in result.facts if f.predicate == "configured_as"]
        assert len(config_facts) >= 2

    def test_store_and_query_facts(self, tmp_path):
        ext = KnowledgeExtractor(root_dir=tmp_path)
        result = ext.extract_from_text("AuthService is a security module with timeout=30", topic="auth")
        ext.store_extraction(result)

        facts = ext.query_facts(subject="AuthService")
        assert len(facts) > 0

    def test_extract_from_exchange(self, tmp_path):
        ext = KnowledgeExtractor(root_dir=tmp_path)
        result = ext.extract_from_exchange(
            "How does auth work?",
            "The AuthService uses JWT tokens with 24h expiry",
            topic="auth",
        )
        assert len(result.facts) > 0 or len(result.entities) > 0

    def test_stats(self, tmp_path):
        ext = KnowledgeExtractor(root_dir=tmp_path)
        stats = ext.stats()
        assert "facts" in stats
        assert "entities" in stats
        assert "relations" in stats

    def test_get_entity(self, tmp_path):
        ext = KnowledgeExtractor(root_dir=tmp_path)
        ext.store_entities([Entity(name="TestService", entity_type="class")])
        entity = ext.get_entity("TestService")
        assert entity is not None
        assert entity.entity_type == "class"

    def test_query_relations(self, tmp_path):
        ext = KnowledgeExtractor(root_dir=tmp_path)
        ext.store_relations([Relation(source="A", target="B", relation_type="uses")])
        rels = ext.query_relations(source="A")
        assert len(rels) > 0


# ═══════════════════════════════════════════════════════════════════
# B2: Knowledge Graph
# ═══════════════════════════════════════════════════════════════════

class TestKnowledgeGraph:
    def test_get_neighbors(self, tmp_path):
        ext = KnowledgeExtractor(root_dir=tmp_path)
        ext.store_entities([
            Entity(name="AuthService", entity_type="class"),
            Entity(name="JWTHandler", entity_type="class"),
        ])
        ext.store_relations([
            Relation(source="AuthService", target="JWTHandler", relation_type="uses"),
        ])

        kg = KnowledgeGraph(root_dir=tmp_path)
        subgraph = kg.get_neighbors("AuthService", max_depth=1)
        assert subgraph.center == "AuthService"
        assert len(subgraph.edges) > 0

    def test_build_context(self, tmp_path):
        ext = KnowledgeExtractor(root_dir=tmp_path)
        ext.store_facts([Fact(
            id="f1", subject="AuthService", predicate="is_a",
            object_value="security module", topic="auth",
        )])

        kg = KnowledgeGraph(root_dir=tmp_path)
        ctx = kg.build_context("AuthService")
        assert "AuthService" in ctx

    def test_export_visualization(self, tmp_path):
        ext = KnowledgeExtractor(root_dir=tmp_path)
        ext.store_entities([Entity(name="Test", entity_type="class")])
        ext.store_relations([Relation(source="A", target="B", relation_type="uses")])

        kg = KnowledgeGraph(root_dir=tmp_path)
        data = kg.export_for_visualization()
        assert "nodes" in data
        assert "edges" in data

    def test_find_path(self, tmp_path):
        ext = KnowledgeExtractor(root_dir=tmp_path)
        ext.store_relations([
            Relation(source="A", target="B", relation_type="uses"),
            Relation(source="B", target="C", relation_type="uses"),
        ])

        kg = KnowledgeGraph(root_dir=tmp_path)
        path = kg.find_path("A", "C")
        assert len(path) == 2

    def test_get_subgraph_by_topic(self, tmp_path):
        ext = KnowledgeExtractor(root_dir=tmp_path)
        ext.store_facts([Fact(
            id="f1", subject="AuthService", predicate="is_a",
            object_value="module", topic="auth",
        )])

        kg = KnowledgeGraph(root_dir=tmp_path)
        sg = kg.get_subgraph_by_topic("auth")
        assert sg.center == "auth"


# ═══════════════════════════════════════════════════════════════════
# B3: Vector Search
# ═══════════════════════════════════════════════════════════════════

class TestVectorSearch:
    def test_init(self, tmp_path):
        vs = VectorSearch(root_dir=tmp_path)
        assert vs._dim == 384

    def test_hash_embed_deterministic(self, tmp_path):
        vs = VectorSearch(root_dir=tmp_path)
        v1 = vs._hash_embed("hello world")
        v2 = vs._hash_embed("hello world")
        import numpy as np
        assert np.allclose(v1, v2)

    def test_hash_embed_normalized(self, tmp_path):
        vs = VectorSearch(root_dir=tmp_path)
        v = vs._hash_embed("test text")
        import numpy as np
        norm = np.linalg.norm(v)
        assert abs(norm - 1.0) < 0.01 or norm == 0.0

    def test_index_and_search(self, tmp_path):
        vs = VectorSearch(root_dir=tmp_path)
        vs.index_entry("e1", "authentication with JWT tokens")
        vs.index_entry("e2", "database connection pooling")
        vs.index_entry("e3", "JWT token validation and refresh")

        results = vs.search("JWT authentication", top=3)
        assert len(results) > 0
        assert results[0].id != ""

    def test_stats(self, tmp_path):
        vs = VectorSearch(root_dir=tmp_path)
        stats = vs.stats()
        assert "total_vectors" in stats
        assert "is_semantic" in stats

    def test_index_entry_dedup(self, tmp_path):
        vs = VectorSearch(root_dir=tmp_path)
        vs.index_entry("e1", "hello world")
        indexed = vs.index_entry("e1", "hello world")
        assert indexed is False

    def test_vector_result_model(self):
        vr = VectorResult(id="e1", score=0.95, text="test")
        assert vr.id == "e1"
        assert vr.score == 0.95


# ═══════════════════════════════════════════════════════════════════
# C1: Config Mutator
# ═══════════════════════════════════════════════════════════════════

class TestConfigMutator:
    def test_apply_nonexistent_suggestion(self, tmp_path):
        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S999")
        assert not result.applied
        assert "not found" in result.error.lower()

    def test_apply_not_auto_applicable(self, tmp_path):
        mutator = ConfigMutator(root_dir=tmp_path)
        suggestions = [{"id": "S001", "type": "routing_mismatch", "auto_applicable": False, "applied": False}]
        mutator._suggestions_file.parent.mkdir(parents=True, exist_ok=True)
        with open(mutator._suggestions_file, "w") as f:
            json.dump(suggestions, f)

        result = mutator.apply_suggestion("S001")
        assert not result.applied

    def test_apply_already_applied(self, tmp_path):
        mutator = ConfigMutator(root_dir=tmp_path)
        suggestions = [{"id": "S001", "type": "routing_mismatch", "auto_applicable": True, "applied": True}]
        mutator._suggestions_file.parent.mkdir(parents=True, exist_ok=True)
        with open(mutator._suggestions_file, "w") as f:
            json.dump(suggestions, f)

        result = mutator.apply_suggestion("S001")
        assert not result.applied
        assert "already" in result.error.lower()

    def test_mutation_result_model(self):
        r = MutationResult(suggestion_id="S001", applied=True, mutation_type="routing_mismatch")
        assert r.applied is True


# ═══════════════════════════════════════════════════════════════════
# C2: Evolution Strategies
# ═══════════════════════════════════════════════════════════════════

class TestConservativeStrategy:
    def test_auto_apply_high(self):
        s = ConservativeStrategy()
        d = s.evaluate({"id": "S1", "severity": "HIGH", "auto_applicable": True}, [])
        assert d.should_apply is True

    def test_no_auto_apply_medium(self):
        s = ConservativeStrategy()
        d = s.evaluate({"id": "S1", "severity": "MEDIUM", "auto_applicable": True}, [])
        assert d.should_apply is False


class TestAggressiveStrategy:
    def test_auto_apply_all(self):
        s = AggressiveStrategy()
        d = s.evaluate({"id": "S1", "severity": "LOW", "auto_applicable": True}, [])
        assert d.should_apply is True

    def test_not_applicable(self):
        s = AggressiveStrategy()
        d = s.evaluate({"id": "S1", "severity": "HIGH", "auto_applicable": False}, [])
        assert d.should_apply is False


class TestBalancedStrategy:
    def test_auto_apply_high(self):
        s = BalancedStrategy()
        d = s.evaluate({"id": "S1", "severity": "HIGH", "auto_applicable": True, "type": "slow_agent"}, [])
        assert d.should_apply is True

    def test_routing_requires_approval(self):
        s = BalancedStrategy()
        d = s.evaluate({"id": "S1", "severity": "MEDIUM", "auto_applicable": True, "type": "routing_mismatch"}, [])
        assert d.should_apply is False

    def test_disable_requires_approval(self):
        s = BalancedStrategy()
        d = s.evaluate({"id": "S1", "severity": "MEDIUM", "auto_applicable": True, "type": "agent_low_success"}, [])
        assert d.should_apply is False


class TestCostAwareStrategy:
    def test_below_threshold(self):
        s = CostAwareStrategy()
        d = s.evaluate({"id": "S1", "severity": "MEDIUM", "auto_applicable": True, "estimated_cost_impact": 0.001}, [])
        assert d.should_apply is True

    def test_above_threshold(self):
        s = CostAwareStrategy()
        d = s.evaluate({"id": "S1", "severity": "MEDIUM", "auto_applicable": True, "estimated_cost_impact": 1.0}, [])
        assert d.should_apply is False


class TestStrategyEngine:
    def test_evaluate(self, tmp_path):
        engine = StrategyEngine(root_dir=tmp_path, strategy_name="conservative")
        decisions = engine.evaluate([
            {"id": "S1", "severity": "HIGH", "auto_applicable": True, "type": "slow_agent"},
            {"id": "S2", "severity": "LOW", "auto_applicable": True, "type": "routing_mismatch"},
        ])
        assert len(decisions) == 2
        assert decisions[0].should_apply is True
        assert decisions[1].should_apply is False

    def test_strategy_config(self):
        cfg = StrategyConfig(auto_apply_medium=True)
        s = ConservativeStrategy(config=cfg)
        assert s.config.auto_apply_medium is True


# ═══════════════════════════════════════════════════════════════════
# B4: DreamConsolidator with Knowledge Extraction
# ═══════════════════════════════════════════════════════════════════

class TestDreamConsolidatorExtract:
    def test_report_has_extract_fields(self):
        from maop.memory.consolidator import ConsolidationReport
        r = ConsolidationReport()
        assert r.facts_extracted == 0
        assert r.entities_extracted == 0
        assert r.relations_extracted == 0

    def test_consolidator_accepts_root_dir(self, tmp_path):
        from maop.memory.consolidator import DreamConsolidator
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=tmp_path)
        c = DreamConsolidator(memory_store=store, root_dir=str(tmp_path))
        assert c._root_dir == str(tmp_path)


# ═══════════════════════════════════════════════════════════════════
# B5: MemoryManager Integration
# ═══════════════════════════════════════════════════════════════════

class TestMemoryManagerKnowledge:
    def test_knowledge_extractor_property(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=tmp_path)
        ext = mgr.knowledge_extractor
        assert ext is not None

    def test_knowledge_graph_property(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=tmp_path)
        kg = mgr.knowledge_graph
        assert kg is not None

    def test_vector_search_property(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=tmp_path)
        vs = mgr.vector_search
        assert vs is not None

    def test_extract_knowledge(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=tmp_path)
        result = mgr.extract_knowledge(
            "How does auth work?",
            "AuthService uses JWT tokens with 24h expiry",
            topic="auth",
        )
        assert result is not None
        assert "facts" in result

    def test_query_knowledge(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=tmp_path)
        mgr.extract_knowledge("What is AuthService?", "AuthService is a security module", topic="auth")
        ctx = mgr.query_knowledge("AuthService")
        assert isinstance(ctx, str)

    def test_semantic_search(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=tmp_path)
        results = mgr.semantic_search("authentication", top=5)
        assert isinstance(results, list)
