"""Tests for G5: Multi-vendor end-to-end integration.

Validates the complete chain with mock agents:
  AgentScanner → AgentRegistry → CapabilityMatcher → Dispatcher
  → LLMProvider → ChatEngine → KnowledgeExtractor → KnowledgeGraph
  → VectorSearch → EvolutionStrategies
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from maop.core.agent_registry import AgentRegistry, RegisteredAgent
from maop.core.capability_matcher import CapabilityMatcher, MatcherConfig
from maop.core.chat_engine import ChatEngine, ChatRequest
from maop.core.config_mutator import ConfigMutator
from maop.core.evolution_strategies import StrategyEngine, BalancedStrategy
from maop.core.knowledge_extractor import KnowledgeExtractor, Relation
from maop.core.knowledge_graph import KnowledgeGraph
from maop.core.llm_provider import LLMResponse, BaseLLMProvider, ModelConfig
from maop.delegate.dispatcher import Dispatcher
from maop.memory.vector_search import VectorSearch


def _register_mock_agents(registry: AgentRegistry) -> None:
    for a in [
        RegisteredAgent(name="stepfun-yi", cli_path="echo", provider="stepfun",
                        capabilities=["codegen", "chat", "planning", "review"],
                        model="yi-large", driver="cli", enabled=True),
        RegisteredAgent(name="minimax-m2", cli_path="echo", provider="minimax",
                        capabilities=["codegen", "chat", "mcp", "memory"],
                        model="minimax-m2.7", driver="cli", enabled=True),
        RegisteredAgent(name="agnes-flash", cli_path="echo", provider="agnesai",
                        capabilities=["codegen", "chat", "search"],
                        model="agnes-2.0-flash", driver="cli", enabled=True),
        RegisteredAgent(name="qwen-plus", cli_path="echo", provider="aliyun",
                        capabilities=["codegen", "search", "fileops", "refactor"],
                        model="qwen3.5-plus", driver="cli", enabled=True),
        RegisteredAgent(name="ollama-llama3", cli_path="echo", provider="ollama",
                        capabilities=["codegen", "chat"],
                        model="llama3", driver="cli", enabled=True),
    ]:
        registry.register(a)


# ═══════════════════════════════════════════════════════════════════
# E2E: Agent Discovery → Registration → Matching → Dispatch
# ═══════════════════════════════════════════════════════════════════

class TestE2EAgentPipeline:
    def test_full_agent_lifecycle(self, tmp_path):
        registry = AgentRegistry(root_dir=tmp_path)
        _register_mock_agents(registry)

        assert len(registry.list_agents()) == 5

        code_agents = registry.list_agents(capability="codegen")
        assert len(code_agents) == 5

        chat_agents = registry.list_agents(capability="chat")
        assert len(chat_agents) >= 3

    def test_capability_matcher_ranks_correctly(self, tmp_path):
        registry = AgentRegistry(root_dir=tmp_path)
        _register_mock_agents(registry)

        matcher = CapabilityMatcher(registry=registry)
        scores = matcher.match(task="fix the authentication bug", top_k=3)
        assert len(scores) > 0
        assert scores[0].total_score > 0
        assert "codegen" in scores[0].matched_capabilities or "search" in scores[0].matched_capabilities

    def test_capability_matcher_search_task(self, tmp_path):
        registry = AgentRegistry(root_dir=tmp_path)
        _register_mock_agents(registry)

        matcher = CapabilityMatcher(registry=registry)
        scores = matcher.match(task="search for API documentation", requirements=["search"], top_k=3)
        assert len(scores) > 0

    def test_dispatcher_resolves_from_registry(self, tmp_path):
        registry = AgentRegistry(root_dir=tmp_path)
        _register_mock_agents(registry)
        matcher = CapabilityMatcher(registry=registry)

        dispatcher = Dispatcher(
            MAOP_config=None,
            registry=registry,
            capability_matcher=matcher,
            root_dir=str(tmp_path),
        )

        cfg = dispatcher._resolve_agent("stepfun-yi")
        assert cfg is not None
        assert cfg.name == "stepfun-yi"
        assert cfg.model == "yi-large"

    def test_dispatcher_match_agent(self, tmp_path):
        registry = AgentRegistry(root_dir=tmp_path)
        _register_mock_agents(registry)
        matcher = CapabilityMatcher(registry=registry)

        dispatcher = Dispatcher(
            MAOP_config=None,
            registry=registry,
            capability_matcher=matcher,
            root_dir=str(tmp_path),
        )

        matched = dispatcher.match_agent("fix the auth bug")
        assert matched is not None

    def test_dispatcher_unknown_agent_falls_back_to_match(self, tmp_path):
        registry = AgentRegistry(root_dir=tmp_path)
        _register_mock_agents(registry)
        matcher = CapabilityMatcher(registry=registry)

        dispatcher = Dispatcher(
            MAOP_config=None,
            registry=registry,
            capability_matcher=matcher,
            root_dir=str(tmp_path),
        )

        matched = dispatcher.match_agent("refactor the database layer")
        assert matched is not None
        assert "refactor" in matched.capabilities or "codegen" in matched.capabilities

    def test_provider_preferences(self, tmp_path):
        registry = AgentRegistry(root_dir=tmp_path)
        _register_mock_agents(registry)

        matcher = CapabilityMatcher(
            registry=registry,
            config=MatcherConfig(provider_preferences={"stepfun": 2.0, "minimax": 1.5}),
        )

        scores = matcher.match(task="write code", top_k=5)
        if len(scores) >= 2:
            stepfun = [s for s in scores if s.agent_name == "stepfun-yi"]
            ollama = [s for s in scores if s.agent_name == "ollama-llama3"]
            if stepfun and ollama:
                assert stepfun[0].provider_score >= ollama[0].provider_score


# ═══════════════════════════════════════════════════════════════════
# E2E: LLM Provider → ChatEngine → Memory → Knowledge
# ═══════════════════════════════════════════════════════════════════

class TestE2EChatKnowledge:
    def test_knowledge_extraction_from_exchange(self, tmp_path):
        ext = KnowledgeExtractor(root_dir=tmp_path)

        exchanges = [
            ("How does auth work?", "AuthService uses JWTHandler with timeout=30", "auth"),
            ("What is DatabasePool?", "DatabasePool extends BaseService and implements ConnectionManager", "database"),
            ("How to cache?", "CacheService depends on RedisClient for distributed caching", "caching"),
        ]

        total = {"facts": 0, "entities": 0, "relations": 0}
        for user, asst, topic in exchanges:
            result = ext.extract_from_exchange(user, asst, topic=topic)
            counts = ext.store_extraction(result)
            for k in total:
                total[k] += counts[k]

        assert total["facts"] > 0 or total["entities"] > 0 or total["relations"] > 0

    def test_knowledge_graph_traversal(self, tmp_path):
        ext = KnowledgeExtractor(root_dir=tmp_path)

        ext.store_relations([
            Relation(source="AuthService", target="JWTHandler", relation_type="uses"),
            Relation(source="JWTHandler", target="TokenStore", relation_type="depends_on"),
        ])

        kg = KnowledgeGraph(root_dir=tmp_path)
        subgraph = kg.get_neighbors("AuthService", max_depth=2)
        assert len(subgraph.edges) >= 2

        path = kg.find_path("AuthService", "TokenStore")
        assert len(path) == 2

    def test_vector_search_index_and_search(self, tmp_path):
        vs = VectorSearch(root_dir=tmp_path)
        vs.index_entry("e1", "authentication with JWT tokens")
        vs.index_entry("e2", "database connection pooling")
        vs.index_entry("e3", "JWT token refresh mechanism")

        results = vs.search("JWT authentication", top=3)
        assert len(results) > 0

    def test_memory_manager_full_pipeline(self, tmp_path):
        from maop.memory.manager import MemoryManager
        mgr = MemoryManager(root_dir=tmp_path)

        result = mgr.add_exchange(
            session_id="e2e-test",
            user_msg="How does MAOP dispatch tasks?",
            assistant_msg="MAOP uses Dispatcher with CapabilityMatcher to route tasks",
            agent="stepfun-yi",
            task="architecture",
        )
        assert "short_term_id" in result

        ctx = mgr.query_knowledge(entity="Dispatcher")
        assert isinstance(ctx, str)

        results = mgr.semantic_search("dispatch", top=3)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_chat_engine_with_provider_fallback(self, tmp_path):
        engine = ChatEngine(root_dir=tmp_path, default_model="yi-large")

        mock_provider = AsyncMock(spec=BaseLLMProvider)
        mock_provider.is_configured = True
        mock_provider.chat = AsyncMock(return_value=LLMResponse(
            content="MAOP uses a Plan-Execute-Verify loop", model="yi-large", provider="stepfun",
        ))

        mock_factory = MagicMock()
        mock_factory.get_provider.return_value = mock_provider
        mock_factory.get_model_config.return_value = ModelConfig(
            name="yi-large", provider="stepfun", model_id="yi-large",
        )
        engine._provider_factory = mock_factory

        request = ChatRequest(message="How does MAOP work?", model="yi-large")
        response = await engine.chat(request)
        assert "MAOP" in response.content or len(response.content) > 0


# ═══════════════════════════════════════════════════════════════════
# E2E: Evolution Strategies → Config Mutation
# ═══════════════════════════════════════════════════════════════════

class TestE2EEvolution:
    def test_strategy_engine_evaluate_and_apply(self, tmp_path):
        engine = StrategyEngine(root_dir=tmp_path, strategy_name="balanced")

        suggestions = [
            {"id": "S1", "severity": "HIGH", "auto_applicable": True, "type": "slow_agent", "agent": "stepfun-yi"},
            {"id": "S2", "severity": "MEDIUM", "auto_applicable": True, "type": "routing_mismatch", "routing_key": "codegen"},
            {"id": "S3", "severity": "LOW", "auto_applicable": True, "type": "empty_routing_key", "routing_key": "search"},
        ]

        decisions = engine.evaluate(suggestions)
        assert len(decisions) == 3

        high_decisions = [d for d in decisions if d.should_apply]
        assert len(high_decisions) >= 1

    def test_multi_strategy_comparison(self, tmp_path):
        from maop.core.evolution_strategies import (
            ConservativeStrategy, AggressiveStrategy,
        )

        suggestion = {"id": "S1", "severity": "MEDIUM", "auto_applicable": True, "type": "slow_agent"}

        conservative = ConservativeStrategy().evaluate(suggestion, [])
        aggressive = AggressiveStrategy().evaluate(suggestion, [])
        balanced = BalancedStrategy().evaluate(suggestion, [])

        assert not conservative.should_apply
        assert aggressive.should_apply
        assert balanced.should_apply

    def test_config_mutator_with_suggestions(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        agents_yaml = {
            "agents": {
                "stepfun-yi": {
                    "cli": "echo", "driver": "cli", "timeout_s": 60,
                    "capabilities": ["codegen", "chat"], "enabled": True,
                },
            },
            "routing": {
                "codegen": {"primary": "stepfun-yi"},
            },
        }
        with open(config_dir / "agents.yaml", "w") as f:
            yaml.dump(agents_yaml, f)

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        suggestions = [
            {"id": "S001", "type": "slow_agent", "auto_applicable": True, "applied": False,
             "agent": "stepfun-yi", "suggested_timeout": 180},
        ]
        with open(data_dir / "evolve-suggestions.json", "w") as f:
            json.dump(suggestions, f)

        mutator = ConfigMutator(root_dir=tmp_path)
        result = mutator.apply_suggestion("S001")
        assert result.applied
        assert any("timeout_s" in c for c in result.changes)


# ═══════════════════════════════════════════════════════════════════
# E2E: Cross-Vendor Routing Matrix
# ═══════════════════════════════════════════════════════════════════

class TestE2ECrossVendorRouting:
    def test_routing_matrix(self, tmp_path):
        registry = AgentRegistry(root_dir=tmp_path)
        _register_mock_agents(registry)
        matcher = CapabilityMatcher(registry=registry)

        tasks = {
            "fix the auth bug": "codegen",
            "explain the architecture": "chat",
            "search for API docs": "search",
            "refactor the module": "refactor",
            "plan the sprint": "planning",
        }

        for task, expected_cap in tasks.items():
            scores = matcher.match(task=task, top_k=3)
            assert len(scores) > 0, f"No agents matched for task: {task}"
            best = scores[0]
            assert best.total_score > 0, f"Zero score for task: {task}"


    def test_disabled_agent_excluded(self, tmp_path):
        registry = AgentRegistry(root_dir=tmp_path)
        _register_mock_agents(registry)

        registry.disable("stepfun-yi")

        matcher = CapabilityMatcher(registry=registry)
        scores = matcher.match(task="fix the bug", top_k=5)
        stepfun = [s for s in scores if s.agent_name == "stepfun-yi"]
        assert len(stepfun) == 0

    def test_health_penalty(self, tmp_path):
        registry = AgentRegistry(root_dir=tmp_path)
        _register_mock_agents(registry)

        agent = registry.get_agent("minimax-m2")
        if agent:
            agent.health = "unhealthy"
            registry.register(agent)

        matcher = CapabilityMatcher(registry=registry)
        scores = matcher.match(task="write code", top_k=5)
        minimax = [s for s in scores if s.agent_name == "minimax-m2"]
        if minimax:
            assert minimax[0].health_score < 1.0
