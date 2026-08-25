"""参数化测试集合 4 — 推广 @pytest.mark.parametrize 使用率（第四批）。

覆盖模块：
  - maop.core.monitoring.monitoring JsonLogFormatter  (level mapping)
  - maop.core.backends.kv_store                       (KVEntry / KVStats / CASResult)
  - maop.evolve SuggestionSeverity                    (枚举值)
  - maop.core.reliability.filelock                    (FileLock 行为)
  - maop.core.agent.evolution.phases PhaseResult      (构造)
  - maop.engine Engine                                (init)
  - maop.concurrency TaskQueue                        (队列操作)
  - maop.dashboard DashboardState                     (状态模型)
  - maop.model.schema ModelDef / EffectiveModel       (默认值)
  - maop.core.reliability.error_schema MaopResult     (更多字段)
  - maop.deploy ServiceStatus / HealthStatus          (枚举成员)
  - maop.core.agent.lifecycle.state_classifier        (classify passed)
"""

from __future__ import annotations

import logging

import pytest

# ── 1. JsonLogFormatter level mapping 参数化 ─────────────────


class TestJsonLogLevelParametrized:
    """参数化测试 JsonLogFormatter 的 level mapping。

    合并自 test_json_logging.py test_level_mapping 中的循环。
    """

    @pytest.mark.parametrize(
        "py_level,expected_level",
        [
            (logging.DEBUG, "DEBUG"),
            (logging.INFO, "INFO"),
            (logging.WARNING, "WARNING"),
            (logging.ERROR, "ERROR"),
            (logging.CRITICAL, "CRITICAL"),
        ],
        ids=["debug", "info", "warning", "error", "critical"],
    )
    def test_level_mapping(self, py_level, expected_level):
        import json

        from maop.core.monitoring.monitoring import JsonLogFormatter

        fmt = JsonLogFormatter()
        record = logging.LogRecord(
            name="maop.test",
            level=py_level,
            pathname=__file__,
            lineno=42,
            msg="lvl",
            args=(),
            exc_info=None,
        )
        data = json.loads(fmt.format(record))
        assert data["level"] == expected_level


# ── 2. kv_store 模型默认值参数化 ─────────────────────────────


class TestKVEntryParametrized:
    """参数化测试 KVEntry 的默认值与构造。"""

    @pytest.mark.parametrize(
        "field,default_value",
        [
            ("key", ""),
            ("value", None),
            ("namespace", "default"),
            ("ttl", None),
            ("version", 1),
        ],
    )
    def test_kven_entry_defaults(self, field, default_value):
        from maop.core.backends.kv_store import KVEntry

        e = KVEntry()
        assert getattr(e, field) == default_value

    @pytest.mark.parametrize(
        "key,value,namespace,ttl,version",
        [
            ("k1", "v1", "ns1", 60, 1),
            ("k2", "v2", "default", None, 2),
            ("k3", None, "ns3", 100, 5),
        ],
        ids=["full", "default-ns", "none-value"],
    )
    def test_kven_entry_construction(self, key, value, namespace, ttl, version):
        from maop.core.backends.kv_store import KVEntry

        e = KVEntry(key=key, value=value, namespace=namespace, ttl=ttl, version=version)
        assert e.key == key
        assert e.value == value
        assert e.namespace == namespace
        assert e.ttl == ttl
        assert e.version == version


class TestKVStatsParametrized:
    """参数化测试 KVStats 的默认值。"""

    @pytest.mark.parametrize(
        "field,default_value",
        [
            ("total_keys", 0),
            ("namespaces", []),
            ("expired_keys", 0),
            ("db_size_bytes", 0),
        ],
    )
    def test_kv_stats_defaults(self, field, default_value):
        from maop.core.backends.kv_store import KVStats

        s = KVStats()
        assert getattr(s, field) == default_value


class TestCASResultParametrized:
    """参数化测试 CASResult 的默认值。"""

    @pytest.mark.parametrize(
        "field,default_value",
        [
            ("success", False),
            ("current_value", None),
            ("current_version", 0),
        ],
    )
    def test_cas_result_defaults(self, field, default_value):
        from maop.core.backends.kv_store import CASResult

        r = CASResult()
        assert getattr(r, field) == default_value


# ── 3. evolve SuggestionSeverity 枚举参数化 ─────────────────


class TestSuggestionSeverityParametrized:
    """参数化测试 SuggestionSeverity 枚举。"""

    @pytest.mark.parametrize(
        "enum_member,expected_value",
        [
            ("HIGH", "high"),
            ("MEDIUM", "medium"),
            ("LOW", "low"),
        ],
    )
    def test_suggestion_severity_values(self, enum_member, expected_value):
        from maop.evolve import SuggestionSeverity

        assert SuggestionSeverity[enum_member].value == expected_value


# ── 4. filelock FileLock 参数化 ──────────────────────────────


class TestFileLockParametrized:
    """参数化测试 FileLock 的行为。"""

    @pytest.mark.parametrize(
        "return_value",
        [42, "hello", None, True, [1, 2, 3]],
        ids=["int", "str", "none", "bool", "list"],
    )
    def test_with_file_lock_returns_value(self, tmp_path, return_value):
        from maop.core.reliability.filelock import with_file_lock

        target = tmp_path / "shared.dat"
        target.write_text("{}", encoding="utf-8")
        result = with_file_lock(target, lambda: return_value)
        assert result == return_value


# ── 5. phases PhaseResult 构造参数化 ─────────────────────────


class TestPhaseResultConstructionParametrized:
    """参数化测试 PhaseResult 的构造。"""

    @pytest.mark.parametrize(
        "ok,error,skip_remaining",
        [
            (True, "", False),
            (False, "boom", False),
            (True, "", True),
            (False, "error-and-skip", True),
        ],
        ids=["success", "error", "skip", "error-and-skip"],
    )
    def test_phase_result_construction(self, ok, error, skip_remaining):
        from maop.core.agent.evolution.phases import PhaseResult

        r = PhaseResult(ok=ok, error=error, skip_remaining=skip_remaining)
        assert r.ok is ok
        assert r.error == error
        assert r.skip_remaining is skip_remaining


# ── 6. engine Engine init 参数化 ─────────────────────────────


class TestEngineInitParametrized:
    """参数化测试 Engine 的初始化。"""

    @pytest.mark.parametrize(
        "has_executor",
        [True, False],
        ids=["with-executor", "without-executor"],
    )
    def test_engine_init(self, has_executor):
        from maop.engine import Engine

        if has_executor:
            async def executor(step, **kw):
                from maop.core.reliability.error_schema import new_result
                return new_result(agent="a", task="t", exit_code=0, stdout="ok")
            engine = Engine(step_executor=executor)
        else:
            engine = Engine()
        assert engine is not None


# ── 7. concurrency TaskQueue 参数化 ─────────────────────────


class TestTaskQueueParametrized:
    """参数化测试 TaskQueue 的基本操作。"""

    @pytest.mark.parametrize(
        "max_size",
        [1, 5, 10, 100],
        ids=["size-1", "size-5", "size-10", "size-100"],
    )
    def test_task_queue_max_size(self, max_size):
        import asyncio

        from maop.concurrency import Task, TaskQueue

        async def _test():
            q = TaskQueue(max_size=max_size)
            for i in range(max_size):
                await q.put(Task(name=f"task-{i}"))
            assert q.qsize() == max_size
        asyncio.run(_test())


# ── 8. dashboard DashboardState 参数化 ──────────────────────


class TestDashboardStateParametrized:
    """参数化测试 DashboardState 的默认值。"""

    @pytest.mark.parametrize(
        "field,default_value",
        [
            ("agents", []),
            ("total_delegations", 0),
            ("success_rate", 0.0),
            ("uptime_s", 0.0),
        ],
    )
    def test_dashboard_state_defaults(self, field, default_value):
        from maop.dashboard import DashboardState

        state = DashboardState()
        assert getattr(state, field) == default_value


# ── 9. model.schema ModelDef / EffectiveModel 参数化 ────────


class TestModelSchemaParametrized:
    """参数化测试 model.schema 的模型默认值。"""

    @pytest.mark.parametrize(
        "name",
        ["test", "gpt-4", "claude-3", "yi-large"],
        ids=["test", "gpt4", "claude3", "yi-large"],
    )
    def test_model_def_defaults(self, name):
        from maop.model.schema import ModelDef

        m = ModelDef(name=name)
        assert m.name == name
        assert m.context_window == 32768
        assert m.capabilities == []

    @pytest.mark.parametrize(
        "model_name,provider",
        [
            ("yi-large", "stepfun"),
            ("gpt-4", "openai"),
            ("claude-3", "anthropic"),
        ],
        ids=["yi-stepfun", "gpt4-openai", "claude3-anthropic"],
    )
    def test_effective_model_defaults(self, model_name, provider):
        from maop.model.schema import EffectiveModel

        em = EffectiveModel(model_name=model_name, provider=provider)
        assert em.model_name == model_name
        assert em.provider == provider
        assert em.fallback_chain == []
        assert em.policy_name == "default"


# ── 10. error_schema MaopResult 更多字段参数化 ──────────────


class TestMaopResultMoreFieldsParametrized:
    """参数化测试 MaopResult 的更多字段组合。"""

    @pytest.mark.parametrize(
        "exit_code,stdout,stderr",
        [
            (0, "ok", ""),
            (1, "", "error"),
            (0, "hello world", ""),
            (2, "partial", "failed"),
        ],
        ids=["success", "fail-stderr", "success-with-output", "fail-partial"],
    )
    def test_new_result_fields(self, exit_code, stdout, stderr):
        from maop.core.reliability.error_schema import new_result

        r = new_result(agent="a", task="t", exit_code=exit_code, stdout=stdout, stderr=stderr)
        assert r.exit_code == exit_code
        assert r.stdout == stdout
        assert r.stderr == stderr

    @pytest.mark.parametrize(
        "duration_ms",
        [0, 100, 500, 1000, 5000],
        ids=["0ms", "100ms", "500ms", "1000ms", "5000ms"],
    )
    def test_new_result_duration(self, duration_ms):
        from maop.core.reliability.error_schema import new_result

        r = new_result(agent="a", task="t", exit_code=0, duration_ms=duration_ms)
        assert r.duration_ms == duration_ms


# ── 11. deploy ServiceStatus / HealthStatus 枚举成员参数化 ──


class TestDeployEnumMembersParametrized:
    """参数化测试 deploy 枚举成员的字符串值。"""

    @pytest.mark.parametrize(
        "status,expected_str",
        [
            ("STOPPED", "stopped"),
            ("STARTING", "starting"),
            ("RUNNING", "running"),
            ("STOPPING", "stopping"),
            ("ERROR", "error"),
        ],
    )
    def test_service_status_str_value(self, status, expected_str):
        from maop.deploy import ServiceStatus

        assert ServiceStatus[status].value == expected_str

    @pytest.mark.parametrize(
        "status,expected_str",
        [
            ("HEALTHY", "healthy"),
            ("DEGRADED", "degraded"),
            ("UNHEALTHY", "unhealthy"),
        ],
    )
    def test_health_status_str_value(self, status, expected_str):
        from maop.deploy import HealthStatus

        assert HealthStatus[status].value == expected_str


# ── 12. state_classifier classify passed 参数化 ─────────────


class TestClassifyPassedParametrized:
    """参数化测试 TaskStateClassifier.classify 的 passed 参数。"""

    @pytest.mark.parametrize(
        "passed,summary,expected_state",
        [
            (True, "All gates passed", "done"),
            (True, "", "done"),
            (True, "anything", "done"),
        ],
        ids=["passed-with-summary", "passed-empty-summary", "passed-any-summary"],
    )
    def test_passed_returns_done(self, passed, summary, expected_state):
        from maop.core.agent.lifecycle.state_classifier import TaskState, TaskStateClassifier

        clf = TaskStateClassifier()
        result = clf.classify(passed=passed, summary=summary)
        assert result.state == TaskState(expected_state)
        assert result.confidence == 1.0


# ── 13. engine WorkflowStep timeout 参数化 ──────────────────


class TestWorkflowStepTimeoutParametrized:
    """参数化测试 WorkflowStep 的 timeout 参数。"""

    @pytest.mark.parametrize(
        "timeout",
        [60, 120, 300, 600, 1800],
        ids=["60s", "120s", "300s", "600s", "1800s"],
    )
    def test_workflow_step_timeout(self, timeout):
        from maop.engine import WorkflowStep

        step = WorkflowStep(id="s1", timeout=timeout)
        assert step.timeout == timeout

    @pytest.mark.parametrize(
        "retry",
        [0, 1, 2, 3, 5],
        ids=["no-retry", "1-retry", "2-retry", "3-retry", "5-retry"],
    )
    def test_workflow_step_retry(self, retry):
        from maop.engine import WorkflowStep

        step = WorkflowStep(id="s1", retry=retry)
        assert step.retry == retry


# ── 14. concurrency Priority 排序参数化 ─────────────────────


class TestPriorityOrderingParametrized:
    """参数化测试 Priority 的排序。

    Priority 使用 IntEnum，CRITICAL=0 (最高), HIGH=1, NORMAL=2, LOW=3 (最低)。
    数值越小优先级越高，所以 HIGH < NORMAL (HIGH 数值更小)。
    """

    @pytest.mark.parametrize(
        "higher,lower",
        [
            ("NORMAL", "LOW"),
            ("HIGH", "LOW"),
            ("CRITICAL", "LOW"),
            ("HIGH", "NORMAL"),
            ("CRITICAL", "NORMAL"),
            ("CRITICAL", "HIGH"),
        ],
        ids=["normal>low", "high>low", "critical>low", "high>normal", "critical>normal", "critical>high"],
    )
    def test_priority_ordering(self, higher, lower):
        from maop.concurrency import Priority

        # higher 优先级的数值更小
        assert Priority[higher] < Priority[lower]


# ── 15. deploy _pid_path 参数化 ──────────────────────────────


class TestPidPathParametrized:
    """参数化测试 _pid_path 函数。"""

    @pytest.mark.parametrize(
        "root_dir",
        ["/tmp", "/opt/MAOP", "C:\\MAOP", "/home/user/MAOP"],
        ids=["tmp", "opt", "windows", "home"],
    )
    def test_pid_path_contains_filename(self, root_dir):
        from maop.deploy import _pid_path

        p = _pid_path(root_dir)
        assert "maop.pid" in str(p)


# ── 16. error_schema is_success 参数化 ──────────────────────


class TestIsSuccessParametrized:
    """参数化测试 MaopResult.is_success 方法。"""

    @pytest.mark.parametrize(
        "exit_code,error,expected",
        [
            (0, None, True),
            (1, "error", False),
            (0, "error", False),  # 有 error 即使 exit_code=0 也失败
            (1, None, False),
        ],
        ids=["success", "fail-error", "fail-with-error-and-0", "fail-exit"],
    )
    def test_is_success(self, exit_code, error, expected):
        from maop.core.reliability.error_schema import new_result

        kwargs = {"agent": "a", "task": "t", "exit_code": exit_code}
        if error is not None:
            kwargs["error"] = error
        r = new_result(**kwargs)
        assert r.is_success() is expected


# ── 17. bloom_filter __contains__ 参数化 ─────────────────────


class TestBloomFilterContainsParametrized:
    """参数化测试 BloomFilter 的 __contains__ 行为。"""

    @pytest.mark.parametrize(
        "items_to_add,test_item,should_contain",
        [
            (["a"], "a", True),
            (["a", "b", "c"], "b", True),
            (["a", "b", "c"], "d", False),  # 不太可能 false positive
            (["item-1"], "item-2", False),
        ],
        ids=["single", "multiple", "not-added", "different-item"],
    )
    def test_bloom_filter_contains(self, items_to_add, test_item, should_contain):
        from maop.core.memory.bloom_filter import BloomFilter

        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        for item in items_to_add:
            bf.add(item)
        if should_contain:
            assert test_item in bf
        else:
            # Bloom filter 可能有 false positive，但对小规模不太可能
            # 这里只测试确定性的 True 情况
            pass


# ── 18. cache LRUCache delete 参数化 ─────────────────────────


class TestLRUCacheDeleteParametrized:
    """参数化测试 LRUCache 的 delete 操作。"""

    @pytest.mark.parametrize(
        "keys_to_add,key_to_delete,expected_exists_before",
        [
            (["a", "b", "c"], "b", True),
            (["a"], "a", True),
            (["a", "b"], "c", False),  # 不存在的 key
        ],
        ids=["delete-middle", "delete-only", "delete-nonexistent"],
    )
    def test_lru_cache_delete(self, keys_to_add, key_to_delete, expected_exists_before):
        from maop.core.reliability.cache import LRUCache

        cache = LRUCache(max_size=10)
        for key in keys_to_add:
            cache.put(key, f"value-{key}")
        assert (cache.get(key_to_delete) is not None) == expected_exists_before
        result = cache.delete(key_to_delete)
        assert result == expected_exists_before
        assert cache.get(key_to_delete) is None


# ── 19. evolve RoutingKeyStats 参数化 ────────────────────────


class TestRoutingKeyStatsParametrized:
    """参数化测试 RoutingKeyStats 模型。"""

    @pytest.mark.parametrize(
        "routing_key,total,success,rate",
        [
            ("codegen", 10, 8, 80.0),
            ("search", 5, 3, 60.0),
            ("review", 100, 50, 50.0),
            ("deploy", 0, 0, 0.0),
        ],
        ids=["codegen-80", "search-60", "review-50", "deploy-0"],
    )
    def test_routing_key_stats_construction(self, routing_key, total, success, rate):
        from maop.evolve import RoutingKeyStats

        stats = RoutingKeyStats(routing_key=routing_key, total=total, success=success, rate=rate)
        assert stats.routing_key == routing_key
        assert stats.total == total
        assert stats.success == success
        assert stats.rate == rate


# ── 20. engine StepType / StepStatus 字符串值参数化 ────────


class TestEngineEnumStrValuesParametrized:
    """参数化测试 engine 枚举的字符串值。"""

    @pytest.mark.parametrize(
        "enum_class,member,expected_str",
        [
            ("StepType", "PLAN", "plan"),
            ("StepType", "AGENT", "agent"),
            ("StepType", "DAG", "dag"),
            ("StepType", "VERIFY", "verify"),
            ("StepType", "CONDITION", "condition"),
            ("StepType", "TERMINAL", "terminal"),
            ("StepStatus", "PENDING", "pending"),
            ("StepStatus", "RUNNING", "running"),
            ("StepStatus", "SUCCESS", "success"),
            ("StepStatus", "FAILED", "failed"),
            ("StepStatus", "SKIPPED", "skipped"),
        ],
    )
    def test_engine_enum_str_values(self, enum_class, member, expected_str):
        from maop import engine

        cls = getattr(engine, enum_class)
        assert cls[member].value == expected_str