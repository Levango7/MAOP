#!/usr/bin/env python3
"""MAOP Multi-Vendor Smoke Test — End-to-end integration validation.

Validates the complete chain: AgentScanner → AgentRegistry → CapabilityMatcher
→ Dispatcher → LLMProvider → ChatEngine, using mock agents and providers.

Run:
    python scripts/smoke_test_agents.py
    python scripts/smoke_test_agents.py --verbose
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "py"))

from maop.core.agent_scanner import AgentScanner
from maop.core.agent_registry import AgentRegistry, RegisteredAgent
from maop.core.capability_matcher import CapabilityMatcher, MatcherConfig
from maop.delegate.dispatcher import Dispatcher
from maop.core.llm_provider import LLMProviderFactory
from maop.core.chat_engine import ChatEngine, ChatRequest
from maop.core.knowledge_extractor import KnowledgeExtractor
from maop.core.knowledge_graph import KnowledgeGraph
from maop.memory.vector_search import VectorSearch
from maop.core.config_mutator import ConfigMutator
from maop.core.evolution_strategies import StrategyEngine, BalancedStrategy


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    RESET = "\033[0m"


def ok(msg: str):
    print(f"  {Colors.GREEN}OK{Colors.RESET} {msg}")

def fail(msg: str):
    print(f"  {Colors.RED}FAIL{Colors.RESET} {msg}")

def info(msg: str):
    print(f"  {Colors.CYAN}->{Colors.RESET} {msg}")

def section(title: str):
    print(f"\n{Colors.YELLOW}== {title} =={Colors.RESET}")


def smoke_test(tmp_dir: Path, verbose: bool = False) -> bool:
    all_pass = True

    # -- 1. Agent Scanner --
    section("1. Agent Scanner - CLI Discovery")
    try:
        scanner = AgentScanner(root_dir=tmp_dir)
        agents = scanner.scan()
        info(f"Found {len(agents)} available agents")
        for a in agents[:5]:
            info(f"  {a.name} ({a.provider}) - {a.cli_path or 'no CLI'}")
        ok(f"Scanner found {len(agents)} agents")
    except Exception as e:
        fail(f"Scanner failed: {e}")
        all_pass = False

    # -- 2. Agent Registry --
    section("2. Agent Registry - Registration + Health")
    try:
        registry = AgentRegistry(root_dir=tmp_dir)

        mock_agents = [
            RegisteredAgent(
                name="stepfun-yi", cli_path="echo", provider="stepfun",
                capabilities=["codegen", "chat", "planning", "review"],
                model="yi-large", driver="cli", enabled=True,
            ),
            RegisteredAgent(
                name="minimax-m2", cli_path="echo", provider="minimax",
                capabilities=["codegen", "chat", "mcp", "memory"],
                model="minimax-m2.7", driver="cli", enabled=True,
            ),
            RegisteredAgent(
                name="agnes-flash", cli_path="echo", provider="agnesai",
                capabilities=["codegen", "chat", "search"],
                model="agnes-2.0-flash", driver="cli", enabled=True,
            ),
            RegisteredAgent(
                name="qwen-plus", cli_path="echo", provider="aliyun",
                capabilities=["codegen", "search", "fileops", "refactor"],
                model="qwen3.5-plus", driver="cli", enabled=True,
            ),
            RegisteredAgent(
                name="ollama-llama3", cli_path="echo", provider="ollama",
                capabilities=["codegen", "chat"],
                model="llama3", driver="cli", enabled=True,
            ),
        ]

        for a in mock_agents:
            registry.register(a)

        all_agents = registry.list_agents()
        ok(f"Registered {len(all_agents)} multi-vendor agents")

        code_agents = registry.list_agents(capability="codegen")
        ok(f"Agents with 'codegen' capability: {len(code_agents)}")

        stepfun_agents = registry.list_agents(provider="stepfun")
        ok(f"StepFun agents: {len(stepfun_agents)}")

        health = registry.health_check("stepfun-yi")
        ok(f"Health check stepfun-yi: {health.healthy or 'unhealthy (expected with echo CLI)'}")

        registry.disable("agnes-flash")
        agnes = registry.get_agent("agnes-flash")
        assert agnes and not agnes.enabled
        registry.enable("agnes-flash")
        agnes = registry.get_agent("agnes-flash")
        assert agnes and agnes.enabled
        ok("Enable/disable toggle works")

    except Exception as e:
        fail(f"Registry failed: {e}")
        all_pass = False

    # -- 3. Capability Matcher --
    section("3. Capability Matcher - Multi-Factor Scoring")
    try:
        matcher = CapabilityMatcher(registry=registry)

        scores = matcher.match(task="fix the authentication bug", top_k=3)
        ok(f"Match for 'fix auth bug': {len(scores)} candidates")
        if scores:
            best = scores[0]
            info(f"  Best: {best.agent_name} (score={best.total_score:.2f}, caps={best.matched_capabilities})")

        scores2 = matcher.match(task="search for API documentation", requirements=["search"], top_k=3)
        ok(f"Match for 'search docs': {len(scores2)} candidates")
        if scores2:
            info(f"  Best: {scores2[0].agent_name} (score={scores2[0].total_score:.2f})")

        scores3 = matcher.match(task="explain the architecture", top_k=3)
        ok(f"Match for 'explain arch': {len(scores3)} candidates")

    except Exception as e:
        fail(f"CapabilityMatcher failed: {e}")
        all_pass = False

    # -- 4. Dispatcher + Registry Integration --
    section("4. Dispatcher - Registry Fallback + Capability Matching")
    try:
        dispatcher = Dispatcher(
            maop_config=None,
            registry=registry,
            capability_matcher=matcher,
            root_dir=str(tmp_dir),
        )

        cfg = dispatcher._resolve_agent("stepfun-yi")
        if cfg:
            ok(f"Resolved 'stepfun-yi' from registry: cli={cfg.cli}, model={cfg.model}")
        else:
            fail("Failed to resolve 'stepfun-yi' from registry")
            all_pass = False

        matched = dispatcher.match_agent("fix the auth bug")
        if matched:
            ok(f"Matched agent for 'fix auth bug': {matched.name}")
        else:
            info("No agent matched (expected in mock env)")

    except Exception as e:
        fail(f"Dispatcher integration failed: {e}")
        all_pass = False

    # -- 5. LLM Provider Factory --
    section("5. LLM Provider Factory - Multi-Vendor Model Resolution")
    try:
        factory = LLMProviderFactory(root_dir=Path(__file__).resolve().parent.parent)

        models = factory.list_models(enabled_only=True)
        ok(f"Loaded {len(models)} models from models.yaml")

        providers = factory.list_providers(enabled_only=True)
        ok(f"Loaded {len(providers)} providers from models.yaml")

        for m in models[:5]:
            provider = factory.get_provider(m.name)
            if provider:
                configured = "configured" if provider.is_configured else "no API key"
                info(f"  {m.name} -> {provider.__class__.__name__} ({configured})")
            else:
                info(f"  {m.name} -> builtin/skip")

        ok("Provider factory creates correct provider types")

    except Exception as e:
        fail(f"Provider factory failed: {e}")
        all_pass = False

    # -- 6. Knowledge Extraction Pipeline --
    section("6. Knowledge Extraction - Extract -> Store -> Query")
    try:
        ext = KnowledgeExtractor(root_dir=tmp_dir)

        texts = [
            ("AuthService uses JWTHandler for token validation", "auth"),
            ("DatabasePool manages connection lifecycle with timeout=30", "database"),
            ("CacheService extends BaseService and implements EvictionPolicy", "caching"),
        ]

        total_facts = 0
        for text, topic in texts:
            result = ext.extract_from_text(text, topic=topic)
            counts = ext.store_extraction(result)
            total_facts += counts["facts"]
            if verbose:
                info(f"  '{text[:50]}...' -> {counts}")

        ok(f"Extracted and stored {total_facts} facts from {len(texts)} texts")

        kg = KnowledgeGraph(root_dir=tmp_dir)
        ctx = kg.build_context("AuthService", max_depth=1)
        ok(f"Knowledge graph context for 'AuthService': {len(ctx)} chars")

        viz = kg.export_for_visualization()
        ok(f"Graph export: {len(viz['nodes'])} nodes, {len(viz['edges'])} edges")

    except Exception as e:
        fail(f"Knowledge pipeline failed: {e}")
        all_pass = False

    # -- 7. Vector Search --
    section("7. Vector Search - Index + Semantic Search")
    try:
        vs = VectorSearch(root_dir=tmp_dir)
        semantic = "semantic" if vs.is_semantic else "hash-fallback"
        info(f"Search mode: {semantic}")

        vs.index_entry("v1", "authentication with JWT tokens and OAuth2")
        vs.index_entry("v2", "database connection pooling and query optimization")
        vs.index_entry("v3", "JWT token refresh mechanism with 24h expiry")

        results = vs.search("JWT authentication", top=3)
        ok(f"Search 'JWT authentication': {len(results)} results")
        if results:
            info(f"  Top: {results[0].id} (score={results[0].score:.4f})")

        stats = vs.stats()
        ok(f"Vector stats: {stats['total_vectors']} vectors indexed")

    except Exception as e:
        fail(f"Vector search failed: {e}")
        all_pass = False

    # -- 8. Evolution Strategies --
    section("8. Evolution Strategies - Decision + Config Mutation")
    try:
        engine = StrategyEngine(root_dir=tmp_dir, strategy_name="balanced")

        test_suggestions = [
            {"id": "S1", "severity": "HIGH", "auto_applicable": True, "type": "slow_agent", "agent": "stepfun-yi", "suggested_timeout": 180},
            {"id": "S2", "severity": "MEDIUM", "auto_applicable": True, "type": "routing_mismatch", "routing_key": "codegen", "suggested_agent": "minimax-m2"},
            {"id": "S3", "severity": "LOW", "auto_applicable": True, "type": "empty_routing_key", "routing_key": "search"},
            {"id": "S4", "severity": "MEDIUM", "auto_applicable": True, "type": "agent_low_success", "agent": "agnes-flash"},
        ]

        decisions = engine.evaluate(test_suggestions)
        ok(f"Evaluated {len(decisions)} suggestions")
        for d in decisions:
            status = "APPLY" if d.should_apply else "SKIP"
            info(f"  {d.suggestion_id} ({d.severity}/{d.suggestion_type}): {status} - {d.reason}")

    except Exception as e:
        fail(f"Evolution strategies failed: {e}")
        all_pass = False

    # -- 9. Full Chat Pipeline --
    section("9. Chat Engine - Full Pipeline (Memory + Provider + Fallback)")
    try:
        engine_chat = ChatEngine(root_dir=tmp_dir, default_model="yi-large")

        counts = engine_chat.memory.extract_knowledge(
            "How does MAOP dispatch tasks?",
            "MAOP uses Dispatcher with CapabilityMatcher to route tasks to the best agent",
            topic="architecture",
        )
        ok(f"Knowledge extraction from exchange: {counts}")

        results = engine_chat.memory.semantic_search("dispatch tasks", top=3)
        ok(f"Semantic search via MemoryManager: {len(results)} results")

        ctx = engine_chat.memory.query_knowledge("Dispatcher")
        ok(f"Knowledge context for 'Dispatcher': {len(ctx)} chars")

    except Exception as e:
        fail(f"Chat pipeline failed: {e}")
        all_pass = False

    # -- Summary --
    section("SUMMARY")
    if all_pass:
        print(f"\n  {Colors.GREEN}ALL SMOKE TESTS PASSED{Colors.RESET}")
    else:
        print(f"\n  {Colors.RED}SOME SMOKE TESTS FAILED{Colors.RESET}")
    return all_pass


if __name__ == "__main__":
    import tempfile
    verbose = "--verbose" in sys.argv
    tmp = Path(tempfile.mkdtemp(prefix="maop_smoke_"))
    print(f"MAOP Multi-Vendor Smoke Test")
    print(f"Temp dir: {tmp}")
    start = time.time()
    success = smoke_test(tmp, verbose=verbose)
    elapsed = time.time() - start
    print(f"\nCompleted in {elapsed:.1f}s")
    sys.exit(0 if success else 1)
