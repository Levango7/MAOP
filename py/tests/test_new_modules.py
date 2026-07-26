"""Tests for new P0-P2 modules: analyzer, deploy, kv_store, runtime, cache_guard, timeseries, rate_limiter, auth, monitoring.

G2 (2026-07-22, Phase G): ``maop.core.analyzer.analyze`` is now ``async``
(ADR-013 dual-path). All tests in ``TestAnalyzer`` that call ``analyze(...)``
are declared ``async def`` and use ``await``. pytest-asyncio with
``asyncio_mode = "auto"`` (pyproject.toml) detects and runs them without
explicit ``@pytest.mark.asyncio`` decorators. Tests for other modules
(kv_store, runtime, cache_guard, timeseries, rate_limiter, auth, monitoring,
deploy) remain plain ``def``.
"""

import tempfile
import time
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# Analyzer tests (async — analyze() is now async per ADR-013)
# ═══════════════════════════════════════════════════════════════

class TestAnalyzer:
    async def test_single_task(self):
        from maop.core.analyzer import analyze
        result = await analyze("fix the authentication bug")
        assert len(result.sub_tasks) >= 1
        assert result.complexity_score >= 0
        assert result.primary_category in ("code", "general", "debug")

    async def test_multi_step_decomposition(self):
        from maop.core.analyzer import analyze
        result = await analyze("1. Write unit tests\n2. Fix the bug\n3. Update documentation")
        assert len(result.sub_tasks) == 3
        assert result.dag.nodes == ["st-000", "st-001", "st-002"]

    async def test_conjunction_splitting(self):
        from maop.core.analyzer import analyze
        result = await analyze("refactor the module and add tests")
        assert len(result.sub_tasks) >= 2

    async def test_dag_topological_order(self):
        from maop.core.analyzer import analyze
        result = await analyze("1. Design API\n2. Implement backend\n3. Write tests")
        order = result.dag.topological_order()
        assert len(order) == 3
        # st-000 should come before st-001 and st-002
        assert order.index("st-000") < order.index("st-001")

    def test_dag_parallel_groups(self):
        from maop.core.analyzer import DependencyDAG
        dag = DependencyDAG(nodes=["a", "b", "c"], edges=[("a", "b")])
        groups = dag.parallel_groups()
        assert len(groups) >= 2  # a first, then b+c or b then c

    async def test_complexity_levels(self):
        from maop.core.analyzer import Complexity, analyze
        # Trivial
        r1 = await analyze("say hello")
        assert r1.complexity_level in (Complexity.TRIVIAL, Complexity.SIMPLE)
        # Complex
        r2 = await analyze("1. Design microservice architecture\n2. Implement API gateway\n3. Add authentication\n4. Deploy to production\n5. Monitor metrics")
        assert r2.complexity_score > r1.complexity_score

    async def test_risk_detection(self):
        from maop.core.analyzer import analyze
        result = await analyze("drop the production database and remove all backups")
        assert result.requires_human_review

    async def test_task_hash(self):
        from maop.core.analyzer import analyze
        r1 = await analyze("fix bug")
        r2 = await analyze("fix bug")
        assert r1.task_hash == r2.task_hash
        r3 = await analyze("different task")
        assert r1.task_hash != r3.task_hash

    async def test_analysis_layers(self):
        from maop.core.analyzer import analyze
        result = await analyze("write tests")
        assert "rule" in result.analysis_layers
        assert "semantic" in result.analysis_layers


# ═══════════════════════════════════════════════════════════════
# KV Store tests
# ═══════════════════════════════════════════════════════════════

class TestKVStore:
    def test_set_and_get(self):
        from maop.core.kv_store import KVStore
        with tempfile.TemporaryDirectory() as tmp:
            store = KVStore(Path(tmp) / "kv.db")
            store.set("key1", "value1")
            assert store.get("key1") == "value1"
            store.close()

    def test_get_default(self):
        from maop.core.kv_store import KVStore
        with tempfile.TemporaryDirectory() as tmp:
            store = KVStore(Path(tmp) / "kv.db")
            assert store.get("missing", default="fallback") == "fallback"
            store.close()

    def test_ttl_expiration(self):
        from maop.core.kv_store import KVStore
        with tempfile.TemporaryDirectory() as tmp:
            store = KVStore(Path(tmp) / "kv.db")
            store.set("temp_key", "temp_value", ttl=0.1)
            assert store.get("temp_key") == "temp_value"
            time.sleep(0.15)
            assert store.get("temp_key") is None
            store.close()

    def test_namespaces(self):
        from maop.core.kv_store import KVStore
        with tempfile.TemporaryDirectory() as tmp:
            store = KVStore(Path(tmp) / "kv.db")
            store.set("key", "v1", namespace="ns1")
            store.set("key", "v2", namespace="ns2")
            assert store.get("key", namespace="ns1") == "v1"
            assert store.get("key", namespace="ns2") == "v2"
            store.close()

    def test_delete(self):
        from maop.core.kv_store import KVStore
        with tempfile.TemporaryDirectory() as tmp:
            store = KVStore(Path(tmp) / "kv.db")
            store.set("key", "value")
            assert store.delete("key") is True
            assert store.get("key") is None
            store.close()

    def test_cas(self):
        from maop.core.kv_store import KVStore
        with tempfile.TemporaryDirectory() as tmp:
            store = KVStore(Path(tmp) / "kv.db")
            entry = store.set("key", "v1")
            result = store.cas("key", entry.version, "v2")
            assert result.success
            assert store.get("key") == "v2"
            store.close()

    def test_cas_conflict(self):
        from maop.core.kv_store import KVStore
        with tempfile.TemporaryDirectory() as tmp:
            store = KVStore(Path(tmp) / "kv.db")
            store.set("key", "v1")
            result = store.cas("key", 999, "v2")  # Wrong version
            assert not result.success
            store.close()

    def test_bulk_operations(self):
        from maop.core.kv_store import KVStore
        with tempfile.TemporaryDirectory() as tmp:
            store = KVStore(Path(tmp) / "kv.db")
            store.set_many({"a": 1, "b": 2, "c": 3})
            result = store.get_many(["a", "b", "c"])
            assert result == {"a": 1, "b": 2, "c": 3}
            store.close()

    def test_stats(self):
        from maop.core.kv_store import KVStore
        with tempfile.TemporaryDirectory() as tmp:
            store = KVStore(Path(tmp) / "kv.db")
            store.set("k1", "v1")
            store.set("k2", "v2", namespace="other")
            stats = store.stats()
            assert stats.total_keys == 2
            assert "default" in stats.namespaces
            assert "other" in stats.namespaces
            store.close()


# ═══════════════════════════════════════════════════════════════
# Runtime tests
# ═══════════════════════════════════════════════════════════════

class TestRuntime:
    def test_local_execute(self):
        from maop.core.runtime import LocalRuntime, RuntimeConfig, RuntimeType
        rt = LocalRuntime(RuntimeConfig(type=RuntimeType.LOCAL))
        result = rt.execute("echo hello")
        assert result.exit_code == 0
        assert "hello" in result.stdout

    def test_local_info(self):
        from maop.core.runtime import LocalRuntime, RuntimeConfig, RuntimeType
        rt = LocalRuntime(RuntimeConfig())
        info = rt.info()
        assert info.available
        assert info.type == RuntimeType.LOCAL

    def test_isolated_execute(self):
        from maop.core.runtime import IsolatedRuntime, RuntimeConfig, RuntimeType
        with tempfile.TemporaryDirectory() as tmp:
            rt = IsolatedRuntime(RuntimeConfig(
                type=RuntimeType.ISOLATED,
                sandbox_dir=tmp,
            ))
            result = rt.execute("echo isolated")
            assert result.exit_code == 0
            assert "isolated" in result.stdout

    def test_create_runtime_local(self):
        from maop.core.runtime import LocalRuntime, RuntimeConfig, RuntimeType, create_runtime
        rt = create_runtime(RuntimeConfig(type=RuntimeType.LOCAL))
        assert isinstance(rt, LocalRuntime)

    def test_timeout(self):
        from maop.core.runtime import LocalRuntime, RuntimeConfig
        rt = LocalRuntime(RuntimeConfig(timeout_s=0.5))
        result = rt.execute("ping -n 5 127.0.0.1")
        assert result.timed_out


# ═══════════════════════════════════════════════════════════════
# Cache Guard tests
# ═══════════════════════════════════════════════════════════════

class TestCacheGuard:
    def test_basic_get(self):
        from maop.core.cache_guard import CacheGuard
        guard = CacheGuard()
        result = guard.get("key1", lambda: "loaded_value")
        assert result == "loaded_value"

    def test_cache_hit(self):
        from maop.core.cache_guard import CacheGuard
        guard = CacheGuard()
        call_count = 0
        def loader():
            nonlocal call_count
            call_count += 1
            return "value"
        guard.get("key1", loader)
        guard.get("key1", loader)
        assert call_count == 1  # Second call should hit cache

    def test_null_caching(self):
        from maop.core.cache_guard import CacheGuard, CacheGuardConfig
        guard = CacheGuard(config=CacheGuardConfig(null_ttl=0.1))
        call_count = 0
        def loader():
            nonlocal call_count
            call_count += 1
        guard.get("null_key", loader)
        guard.get("null_key", loader)
        assert call_count == 1  # Null should be cached

    def test_invalidate(self):
        from maop.core.cache_guard import CacheGuard
        guard = CacheGuard()
        guard.get("key1", lambda: "value")
        assert guard.invalidate("key1") is True
        assert guard.get("key1", lambda: "new_value") == "new_value"

    def test_stats(self):
        from maop.core.cache_guard import CacheGuard
        guard = CacheGuard()
        guard.get("key1", lambda: "v1")
        guard.get("key1", lambda: "v1")  # hit
        stats = guard.stats()
        assert stats.hits >= 1
        assert stats.misses >= 1


# ═══════════════════════════════════════════════════════════════
# TimeSeries tests
# ═══════════════════════════════════════════════════════════════

class TestTimeSeries:
    def test_record_and_query(self):
        from maop.core.timeseries import TimeSeriesQuery, TimeSeriesStore
        with tempfile.TemporaryDirectory() as tmp:
            store = TimeSeriesStore(Path(tmp) / "ts.db")
            now = time.time()
            store.record("cpu_usage", 75.5, timestamp=now - 60)
            store.record("cpu_usage", 80.0, timestamp=now)
            q = TimeSeriesQuery(metric="cpu_usage", start=now - 120, end=now + 1)
            results = store.query(q)
            assert len(results) == 2
            store.close()

    def test_batch_record(self):
        from maop.core.timeseries import DataPoint, TimeSeriesStore
        with tempfile.TemporaryDirectory() as tmp:
            store = TimeSeriesStore(Path(tmp) / "ts.db")
            points = [DataPoint(timestamp=time.time() - i, metric="req_rate", value=float(i)) for i in range(10)]
            count = store.record_batch(points)
            assert count == 10
            store.close()

    def test_stats(self):
        from maop.core.timeseries import TimeSeriesStore
        with tempfile.TemporaryDirectory() as tmp:
            store = TimeSeriesStore(Path(tmp) / "ts.db")
            store.record("test_metric", 1.0)
            stats = store.stats()
            assert stats.total_points >= 1
            assert "test_metric" in stats.metrics
            store.close()


# ═══════════════════════════════════════════════════════════════
# Rate Limiter tests
# ═══════════════════════════════════════════════════════════════

class TestRateLimiter:
    def test_token_bucket_allows(self):
        from maop.core.rate_limiter import TokenBucket
        tb = TokenBucket(rate=10, burst=5)
        result = tb.consume()
        assert result.allowed

    def test_token_bucket_limits(self):
        from maop.core.rate_limiter import TokenBucket
        tb = TokenBucket(rate=1, burst=2)
        tb.consume()
        tb.consume()
        result = tb.consume()
        assert not result.allowed

    def test_sliding_window(self):
        from maop.core.rate_limiter import SlidingWindow
        sw = SlidingWindow(max_requests=3, window_s=60.0)
        assert sw.consume().allowed
        assert sw.consume().allowed
        assert sw.consume().allowed
        assert not sw.consume().allowed

    def test_multi_key_limiter(self):
        from maop.core.rate_limiter import RateLimiter, RateLimiterConfig
        rl = RateLimiter(RateLimiterConfig(algorithm="token_bucket", rate=1, burst=2))
        assert rl.consume("user1").allowed
        assert rl.consume("user2").allowed  # Different key, separate limit
        assert "user1" in rl.active_keys()

    def test_reset(self):
        from maop.core.rate_limiter import RateLimiter, RateLimiterConfig
        rl = RateLimiter(RateLimiterConfig(rate=1, burst=1))
        rl.consume("key1")
        rl.reset("key1")
        assert rl.consume("key1").allowed


# ═══════════════════════════════════════════════════════════════
# Auth tests
# ═══════════════════════════════════════════════════════════════

class TestAuth:
    def test_api_key_create_and_validate(self):
        from maop.core.auth import APIKeyStore
        with tempfile.TemporaryDirectory() as tmp:
            store = APIKeyStore(Path(tmp) / "auth.db")
            raw_key = store.create_key("test-service", roles=["read"])
            result = store.validate_key(raw_key)
            assert result.authenticated
            assert result.identity == "test-service"
            assert "read" in result.roles
            store.close()

    def test_api_key_invalid(self):
        from maop.core.auth import APIKeyStore
        with tempfile.TemporaryDirectory() as tmp:
            store = APIKeyStore(Path(tmp) / "auth.db")
            result = store.validate_key("invalid_key")
            assert not result.authenticated
            store.close()

    def test_api_key_revoke(self):
        from maop.core.auth import APIKeyStore
        with tempfile.TemporaryDirectory() as tmp:
            store = APIKeyStore(Path(tmp) / "auth.db")
            raw_key = store.create_key("revoke-me")
            store.revoke_key("revoke-me")
            result = store.validate_key(raw_key)
            assert not result.authenticated
            store.close()

    def test_jwt_create_and_validate(self):
        from maop.core.auth import JWTConfig, JWTHandler
        handler = JWTHandler(JWTConfig(secret="test-secret"))
        token = handler.create_token("user1", roles=["admin"])
        result = handler.validate_token(token)
        assert result.authenticated
        assert result.identity == "user1"

    def test_jwt_expired(self):
        from maop.core.auth import JWTConfig, JWTHandler
        handler = JWTHandler(JWTConfig(secret="test-secret"))
        token = handler.create_token("user1", ttl_s=-1)  # Already expired
        result = handler.validate_token(token)
        assert not result.authenticated
        assert "expired" in result.error.lower()

    def test_auth_manager(self):
        from maop.core.auth import AuthConfig, AuthManager
        with tempfile.TemporaryDirectory() as tmp:
            from maop.core.auth import APIKeyStore
            key_store = APIKeyStore(Path(tmp) / "auth.db")
            raw_key = key_store.create_key("svc", roles=["read"])
            mgr = AuthManager(AuthConfig(enabled=True), key_store=key_store)
            result = mgr.authenticate(api_key=raw_key)
            assert result.authenticated
            key_store.close()


# ═══════════════════════════════════════════════════════════════
# Monitoring tests
# ═══════════════════════════════════════════════════════════════

class TestMonitoring:
    def test_counter(self):
        from maop.core.monitoring import Counter
        c = Counter("test_counter", "Test")
        c.inc()
        c.inc(5)
        assert c.get() == 6.0

    def test_gauge(self):
        from maop.core.monitoring import Gauge
        g = Gauge("test_gauge", "Test")
        g.set(42)
        assert g.get() == 42
        g.inc(8)
        assert g.get() == 50
        g.dec(10)
        assert g.get() == 40

    def test_histogram(self):
        from maop.core.monitoring import Histogram
        h = Histogram("test_hist", "Test")
        h.observe(0.1)
        h.observe(0.5)
        h.observe(1.5)
        assert h._total == 3
        assert abs(h._sum - 2.1) < 0.01

    def test_prometheus_export(self):
        from maop.core.monitoring import MetricsCollector
        mc = MetricsCollector()
        mc.counter("c1", "Help").inc()
        mc.gauge("g1", "Help").set(42)
        output = mc.to_prometheus()
        assert "c1" in output
        assert "g1" in output

    def test_structured_logger(self):
        from maop.core.monitoring import StructuredLogger
        sl = StructuredLogger("test", trace_id="abc123")
        # Just verify it doesn't crash
        sl.info("test message", extra_key="extra_value")

    def test_global_metrics(self):
        from maop.core.monitoring import MAOP_ACTIVE_AGENTS, MAOP_DELEGATIONS_TOTAL
        MAOP_DELEGATIONS_TOTAL.inc()
        assert MAOP_DELEGATIONS_TOTAL.get() >= 1
        MAOP_ACTIVE_AGENTS.set(5)
        assert MAOP_ACTIVE_AGENTS.get() == 5


# ═══════════════════════════════════════════════════════════════
# Deploy tests
# ═══════════════════════════════════════════════════════════════

class TestDeploy:
    def test_validate_config_missing_dir(self):
        from maop.deploy import validate_config
        with tempfile.TemporaryDirectory() as tmp:
            result = validate_config(tmp)
            # Missing config/ and data/ dirs
            assert len(result.errors) > 0 or len(result.warnings) > 0

    def test_validate_config_with_dirs(self):
        from maop.deploy import validate_config
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config").mkdir()
            (Path(tmp) / "data").mkdir()
            result = validate_config(tmp)
            # Should have fewer errors
            assert isinstance(result.valid, bool)

    def test_health_check_no_db(self):
        from maop.deploy import health_check
        with tempfile.TemporaryDirectory() as tmp:
            results = health_check(tmp)
            assert len(results) > 0
            # Database should be degraded (not found)
            db_health = [r for r in results if r.name == "database"]
            assert len(db_health) > 0
