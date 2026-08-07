"""Tests for MAOP.core.load_balancer — Dynamic weighted routing."""



from maop.core.routing.load_balancer import (
    AgentMetrics,
    LBAlgorithm,
    LoadBalancer,
    get_load_balancer,
)

# ── AgentMetrics ───────────────────────────────────────────────

class TestAgentMetrics:
    def test_default_values(self):
        m = AgentMetrics()
        assert m.weight == 10
        assert m.active_tasks == 0
        assert m.avg_latency_ms == 0.0
        assert m.error_rate == 0.0
        assert m.success_rate == 1.0

    def test_computed_properties(self):
        m = AgentMetrics(
            total_requests=100, total_successes=90,
            total_failures=10, total_latency_ms=50000.0,
        )
        assert m.avg_latency_ms == 500.0
        assert m.error_rate == 0.1
        assert m.success_rate == 0.9


# ── Registration ───────────────────────────────────────────────

class TestRegistration:
    def test_register(self):
        lb = LoadBalancer()
        lb.register("claude", weight=10)
        lb.register("codex", weight=5)
        assert "claude" in lb.all_agents()
        assert "codex" in lb.all_agents()

    def test_unregister(self):
        lb = LoadBalancer()
        lb.register("claude", weight=10)
        lb.unregister("claude")
        assert "claude" not in lb.all_agents()

    def test_register_update_weight(self):
        lb = LoadBalancer()
        lb.register("claude", weight=10)
        lb.register("claude", weight=20)
        m = lb.get_metrics("claude")
        assert m.weight == 20


# ── Weighted Round Robin ───────────────────────────────────────

class TestWeightedRoundRobin:
    def test_basic_selection(self):
        lb = LoadBalancer(algorithm=LBAlgorithm.WEIGHTED_ROUND_ROBIN)
        lb.register("a", weight=5)
        lb.register("b", weight=1)
        # Agent 'a' should be selected much more often
        counts = {"a": 0, "b": 0}
        for _ in range(6):
            selected = lb.select()
            if selected:
                counts[selected] += 1
        assert counts["a"] == 5
        assert counts["b"] == 1

    def test_equal_weights(self):
        lb = LoadBalancer(algorithm=LBAlgorithm.WEIGHTED_ROUND_ROBIN)
        lb.register("a", weight=1)
        lb.register("b", weight=1)
        counts = {"a": 0, "b": 0}
        for _ in range(10):
            selected = lb.select()
            if selected:
                counts[selected] += 1
        assert counts["a"] == 5
        assert counts["b"] == 5

    def test_single_agent(self):
        lb = LoadBalancer(algorithm=LBAlgorithm.WEIGHTED_ROUND_ROBIN)
        lb.register("only", weight=10)
        assert lb.select() == "only"

    def test_no_agents(self):
        lb = LoadBalancer(algorithm=LBAlgorithm.WEIGHTED_ROUND_ROBIN)
        assert lb.select() is None

    def test_exclude(self):
        lb = LoadBalancer(algorithm=LBAlgorithm.WEIGHTED_ROUND_ROBIN)
        lb.register("a", weight=10)
        lb.register("b", weight=1)
        assert lb.select(exclude={"a"}) == "b"


# ── Least Loaded ───────────────────────────────────────────────

class TestLeastLoaded:
    def test_picks_idle(self):
        lb = LoadBalancer(algorithm=LBAlgorithm.LEAST_LOADED)
        lb.register("a", weight=10)
        lb.register("b", weight=10)
        lb.record_start("a", "t1")
        assert lb.select() == "b"

    def test_picks_least_active(self):
        lb = LoadBalancer(algorithm=LBAlgorithm.LEAST_LOADED)
        lb.register("a", weight=10)
        lb.register("b", weight=10)
        lb.register("c", weight=10)
        lb.record_start("a", "t1")
        lb.record_start("a", "t2")
        lb.record_start("b", "t3")
        assert lb.select() == "c"


# ── Adaptive ───────────────────────────────────────────────────

class TestAdaptive:
    def test_basic_selection(self):
        lb = LoadBalancer(algorithm=LBAlgorithm.ADAPTIVE)
        lb.register("a", weight=10)
        lb.register("b", weight=5)
        # Higher weight should be preferred
        selected = lb.select()
        assert selected in ("a", "b")

    def test_penalizes_high_error_rate(self):
        lb = LoadBalancer(algorithm=LBAlgorithm.ADAPTIVE)
        lb.register("good", weight=10)
        lb.register("bad", weight=10)
        # Simulate errors on 'bad'
        for i in range(10):
            lb.record_finish("bad", f"t{i}", duration_ms=100, success=False)
        # 'good' should be selected
        selected = lb.select()
        assert selected == "good"

    def test_penalizes_high_latency(self):
        lb = LoadBalancer(algorithm=LBAlgorithm.ADAPTIVE)
        lb.register("fast", weight=10)
        lb.register("slow", weight=10)
        lb.record_finish("fast", "t1", duration_ms=100, success=True)
        lb.record_finish("slow", "t2", duration_ms=10000, success=True)
        selected = lb.select()
        assert selected == "fast"

    def test_penalizes_active_tasks(self):
        lb = LoadBalancer(algorithm=LBAlgorithm.ADAPTIVE)
        lb.register("busy", weight=10)
        lb.register("idle", weight=10)
        lb.record_start("busy", "t1")
        selected = lb.select()
        assert selected == "idle"


# ── Load tracking ──────────────────────────────────────────────

class TestLoadTracking:
    def test_start_finish(self):
        lb = LoadBalancer()
        lb.register("a", weight=10)
        lb.record_start("a", "t1")
        m = lb.get_metrics("a")
        assert m.active_tasks == 1
        lb.record_finish("a", "t1", duration_ms=500, success=True)
        m = lb.get_metrics("a")
        assert m.active_tasks == 0
        assert m.total_requests == 1
        assert m.total_successes == 1
        assert m.avg_latency_ms == 500.0

    def test_multiple_tasks(self):
        lb = LoadBalancer()
        lb.register("a", weight=10)
        lb.record_start("a", "t1")
        lb.record_start("a", "t2")
        lb.record_finish("a", "t1", duration_ms=100, success=True)
        lb.record_finish("a", "t2", duration_ms=200, success=False)
        m = lb.get_metrics("a")
        assert m.active_tasks == 0
        assert m.total_requests == 2
        assert m.total_successes == 1
        assert m.total_failures == 1


# ── Stats ──────────────────────────────────────────────────────

class TestStats:
    def test_stats_structure(self):
        lb = LoadBalancer()
        lb.register("a", weight=10)
        lb.register("b", weight=5)
        stats = lb.stats()
        assert stats.agents == 2
        assert stats.algorithm in ("adaptive", "weighted_round_robin", "least_loaded")
        assert "a" in stats.agent_details
        assert "b" in stats.agent_details

    def test_repr(self):
        lb = LoadBalancer()
        lb.register("a", weight=10)
        r = repr(lb)
        assert "LoadBalancer" in r
        assert "agents=1" in r


# ── Global singleton ───────────────────────────────────────────

class TestGlobalLB:
    def test_get_load_balancer(self):
        lb = get_load_balancer()
        assert isinstance(lb, LoadBalancer)
