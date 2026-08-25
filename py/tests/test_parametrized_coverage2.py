"""参数化测试集合 2 — 推广 @pytest.mark.parametrize 使用率（第二批）。

覆盖模块：
  - maop.cli 命令分发                          (action 参数化)
  - maop.core.reliability.rate_limiter        (TokenBucket / SlidingWindow 参数)
  - maop.core.reliability.circuit_breaker     (BreakerState 枚举)
  - maop.core.reliability.cache               (LRUCache 参数)
  - maop.delegate.dispatcher                  (转义函数)
  - maop.loop_analyzer                        (_parse_llm_extraction)
  - maop.core.security.session                (SessionStatus 枚举)
  - maop.core.reliability.error_schema        (MaopResult 字段)
  - maop.engine json_dumps_safe               (序列化边界)
  - maop.core.scheduling.failure_detector     (FailurePatternDetector 初始状态)
  - maop.config.loader                        (ConfigLoader 边界)
  - maop.evolve                               (Suggestion 字段)
"""

from __future__ import annotations

import pytest

# ── 1. cli 命令分发参数化 ─────────────────────────────────────


class TestCliActionDispatchParametrized:
    """参数化测试 cli 的各种 action 分发。

    合并自 test_cli.py TestArgParsing 中重复的 action 测试。
    """

    @pytest.mark.parametrize(
        "action,cmd_func",
        [
            ("stop", "cmd_stop"),
            ("status", "cmd_status"),
            ("validate", "cmd_validate"),
            ("health", "cmd_health"),
        ],
        ids=["stop", "status", "validate", "health"],
    )
    def test_action_dispatch(self, action, cmd_func):
        import sys
        from unittest.mock import patch

        from maop import cli

        with patch.object(sys, "argv", ["MAOP", action]), \
             patch.object(cli, cmd_func) as mock_cmd:
            cli.main()
            mock_cmd.assert_called_once()


# ── 2. rate_limiter TokenBucket 参数化 ────────────────────────


class TestTokenBucketParametrized:
    """参数化测试 TokenBucket 在不同参数下的行为。"""

    @pytest.mark.parametrize(
        "rate,burst",
        [
            (10, 20),
            (1, 1),
            (100, 50),
            (0.1, 5),
        ],
        ids=["10-20", "1-1", "100-50", "0.1-5"],
    )
    def test_token_bucket_init_full(self, rate, burst):
        from maop.core.reliability.rate_limiter import TokenBucket

        tb = TokenBucket(rate=rate, burst=burst)
        assert tb._tokens == float(burst)

    @pytest.mark.parametrize(
        "rate,burst,consumes,expected_allowed_count",
        [
            (0.01, 3, 5, 3),  # 3 次成功，2 次失败
            (0.01, 5, 3, 3),  # 3 次全部成功
            (0.01, 1, 2, 1),  # 1 次成功，1 次失败
        ],
        ids=["3-of-5", "3-of-3", "1-of-2"],
    )
    def test_token_bucket_consume_pattern(self, rate, burst, consumes, expected_allowed_count):
        from maop.core.reliability.rate_limiter import TokenBucket

        tb = TokenBucket(rate=rate, burst=burst)
        results = [tb.consume() for _ in range(consumes)]
        allowed_count = sum(1 for r in results if r.allowed)
        assert allowed_count == expected_allowed_count


# ── 3. rate_limiter RateLimiterConfig 默认值参数化 ────────────


class TestRateLimiterConfigParametrized:
    """参数化测试 RateLimiterConfig 默认值。"""

    @pytest.mark.parametrize(
        "field,expected_value",
        [
            ("algorithm", "token_bucket"),
            ("rate", 10.0),
            ("burst", 20),
            ("window_s", 60.0),
            ("max_requests", 600),
        ],
    )
    def test_rate_limiter_config_defaults(self, field, expected_value):
        from maop.core.reliability.rate_limiter import RateLimiterConfig

        c = RateLimiterConfig()
        assert getattr(c, field) == expected_value


# ── 4. circuit_breaker BreakerState 参数化 ────────────────────


class TestBreakerStateParametrized:
    """参数化测试 BreakerState 枚举与 is_available 行为。"""

    @pytest.mark.parametrize(
        "state,expected_available",
        [
            ("CLOSED", True),
            ("HALF_OPEN", True),
            ("OPEN", False),
        ],
        ids=["closed", "half-open", "open"],
    )
    def test_is_available_by_state(self, state, expected_available):
        import contextlib
        import shutil
        import tempfile
        from pathlib import Path

        from maop.core.reliability.circuit_breaker import BreakerState, CircuitBreaker

        tmp = tempfile.mkdtemp(prefix="MAOP_cb_param_")
        try:
            db_path = Path(tmp) / "maop.db"
            cb = CircuitBreaker(path=db_path)
            cb.set_state("claude", BreakerState[state], failures=3)
            assert cb.is_available("claude") is expected_available
        finally:
            with contextlib.suppress(Exception):
                shutil.rmtree(tmp, ignore_errors=True)


# ── 5. cache LRUCache 参数化 ──────────────────────────────────


class TestLRUCacheParametrized:
    """参数化测试 LRUCache 在不同参数下的行为。"""

    @pytest.mark.parametrize(
        "max_size,keys_to_insert,expected_size",
        [
            (10, ["a", "b", "c"], 3),
            (2, ["a", "b", "c"], 2),  # 超出 max_size，淘汰
            (5, ["a"], 1),
            (3, ["a", "b", "c", "d", "e"], 3),  # 淘汰 a, b
        ],
        ids=["3-in-10", "3-in-2", "1-in-5", "5-in-3"],
    )
    def test_lru_cache_size_after_inserts(self, max_size, keys_to_insert, expected_size):
        from maop.core.reliability.cache import LRUCache

        cache = LRUCache(max_size=max_size)
        for key in keys_to_insert:
            cache.put(key, f"value-{key}")
        assert cache.size() == expected_size

    @pytest.mark.parametrize(
        "max_size",
        [1, 5, 10, 100],
        ids=["size-1", "size-5", "size-10", "size-100"],
    )
    def test_lru_cache_get_missing_returns_none(self, max_size):
        from maop.core.reliability.cache import LRUCache

        cache = LRUCache(max_size=max_size)
        assert cache.get("nonexistent") is None

    @pytest.mark.parametrize(
        "max_size",
        [3, 5, 10],
    )
    def test_lru_cache_overwrite_key(self, max_size):
        from maop.core.reliability.cache import LRUCache

        cache = LRUCache(max_size=max_size)
        cache.put("key1", "v1")
        cache.put("key1", "v2")
        assert cache.get("key1") == "v2"
        assert cache.size() == 1


# ── 6. dispatcher 转义函数参数化 ──────────────────────────────


class TestDispatcherEscapingParametrized:
    """参数化测试 dispatcher 的转义函数。"""

    @pytest.mark.parametrize(
        "input_text,expected_fragment",
        [
            ("hello & world | test", "^&"),
            ("foo^bar", "^^"),
            ("hello < world", "^<"),
            ("hello > world", "^>"),
        ],
        ids=["amp-pipe", "caret", "less-than", "greater-than"],
    )
    def test_cmd_escape(self, input_text, expected_fragment):
        from maop.delegate.dispatcher import _escape_for_cmd

        result = _escape_for_cmd(input_text)
        assert expected_fragment in result

    @pytest.mark.parametrize(
        "input_text",
        [
            "it's a test",
            "simple text",
            "no quotes here",
            'mixed "double" and \'single\'',
        ],
        ids=["single-quote", "simple", "no-quotes", "mixed-quotes"],
    )
    def test_ps_command_escape_wraps_in_quotes(self, input_text):
        from maop.delegate.dispatcher import _escape_for_ps_command

        result = _escape_for_ps_command(input_text)
        assert result.startswith("'")
        assert result.endswith("'")


# ── 7. loop_analyzer _parse_llm_extraction 参数化 ─────────────


class TestParseLlmExtractionParametrized:
    """参数化测试 _parse_llm_extraction 的各种输入。"""

    @pytest.mark.parametrize(
        "content",
        [
            "",
            "   ",
            "not json",
            "[1,2,3]",  # 非 dict
        ],
        ids=["empty", "whitespace", "invalid-json", "non-dict"],
    )
    def test_parse_llm_extraction_returns_none(self, content):
        from maop.loop_analyzer import _parse_llm_extraction

        assert _parse_llm_extraction(content, "task") is None

    @pytest.mark.parametrize(
        "complexity,expected",
        [
            ("simple", "simple"),
            ("complex", "complex"),
            ("moderate", "moderate"),
            ("invalid_value", "unknown"),
        ],
        ids=["simple", "complex", "moderate", "invalid"],
    )
    def test_parse_llm_extraction_complexity(self, complexity, expected):
        import json

        from maop.loop_analyzer import _parse_llm_extraction

        content = json.dumps({"complexity": complexity})
        result = _parse_llm_extraction(content, "task")
        assert result is not None
        assert result.complexity == expected


# ── 8. session SessionStatus 参数化 ───────────────────────────


class TestSessionStatusParametrized:
    """参数化测试 SessionStatus 枚举。"""

    @pytest.mark.parametrize(
        "enum_member,expected_value",
        [
            ("ACTIVE", "active"),
            ("PAUSED", "paused"),
            ("COMPLETED", "completed"),
            ("FAILED", "failed"),
            ("ARCHIVED", "archived"),
        ],
        ids=["active", "paused", "completed", "failed", "archived"],
    )
    def test_session_status_exists(self, enum_member, expected_value):
        from maop.core.security.session import SessionStatus

        # SessionStatus 是 str 子类，成员是类属性
        value = getattr(SessionStatus, enum_member, None)
        assert value is not None
        assert value == expected_value


# ── 9. error_schema MaopResult 字段参数化 ─────────────────────


class TestMaopResultFieldsParametrized:
    """参数化测试 MaopResult 的字段保留。"""

    @pytest.mark.parametrize(
        "agent,task,trace_id,model",
        [
            ("claude", "codegen", "trace-1", "gpt-4"),
            ("kimi", "search", "trace-2", "step-3.7"),
            ("codex", "review", "trace-3", "claude-3"),
            ("gpt-4", "analyze", "trace-4", "gpt-4-turbo"),
        ],
        ids=["claude-gpt4", "kimi-step", "codex-claude3", "gpt4-turbo"],
    )
    def test_new_result_preserves_fields(self, agent, task, trace_id, model):
        from maop.core.reliability.error_schema import new_result

        r = new_result(agent=agent, task=task, trace_id=trace_id, model=model)
        assert r.agent == agent
        assert r.task == task
        assert r.trace_id == trace_id
        assert r.model == model


# ── 10. engine json_dumps_safe 参数化 ────────────────────────


class TestJsonDumpsSafeParametrized:
    """参数化测试 json_dumps_safe 的各种输入。"""

    @pytest.mark.parametrize(
        "data,expected_fragment",
        [
            ({"key": "value"}, '"key"'),
            ({"name": "test"}, '"name"'),
            ({"num": 123}, '"num"'),
            ({"list": [1, 2, 3]}, '"list"'),
        ],
        ids=["string-val", "name-val", "num-val", "list-val"],
    )
    def test_json_dumps_safe_normal(self, data, expected_fragment):
        from maop.engine import json_dumps_safe

        result = json_dumps_safe(data)
        assert expected_fragment in result

    @pytest.mark.parametrize(
        "data",
        [
            {"func": lambda x: x},
            {"obj": object()},
            {"set": {1, 2, 3}},
        ],
        ids=["lambda", "object", "set"],
    )
    def test_json_dumps_safe_non_serializable(self, data):
        from maop.engine import json_dumps_safe

        # 不抛异常，返回某种字符串
        result = json_dumps_safe(data)
        assert result is not None


# ── 11. evolve Suggestion 字段参数化 ──────────────────────────


class TestSuggestionParametrized:
    """参数化测试 Suggestion 模型的字段。"""

    @pytest.mark.parametrize(
        "suggestion_type,severity,auto_applicable",
        [
            ("slow_agent", "medium", False),
            ("routing_mismatch", "high", True),
            ("empty_routing_key", "low", False),
            ("agent_low_success", "high", False),
        ],
        ids=["slow-agent", "routing-mismatch", "empty-routing", "low-success"],
    )
    def test_suggestion_construction(self, suggestion_type, severity, auto_applicable):
        from maop.evolve import Suggestion

        s = Suggestion(
            id="S001",
            type=suggestion_type,
            severity=severity,
            auto_applicable=auto_applicable,
        )
        assert s.type == suggestion_type
        assert s.severity == severity
        assert s.auto_applicable == auto_applicable
        assert s.applied is False
        assert s.timestamp != ""  # 自动生成


# ── 12. evolve AgentStats 字段参数化 ──────────────────────────


class TestAgentStatsParametrized:
    """参数化测试 AgentStats 模型。"""

    @pytest.mark.parametrize(
        "agent,total,success,rate",
        [
            ("claude", 10, 9, 90.0),
            ("kimi", 5, 3, 60.0),
            ("codex", 100, 50, 50.0),
            ("gpt-4", 0, 0, 0.0),
        ],
        ids=["claude-90", "kimi-60", "codex-50", "gpt4-0"],
    )
    def test_agent_stats_construction(self, agent, total, success, rate):
        from maop.evolve import AgentStats

        stats = AgentStats(agent=agent, total=total, success=success, rate=rate)
        assert stats.agent == agent
        assert stats.total == total
        assert stats.success == success
        assert stats.rate == rate


# ── 13. deploy ComponentHealth 构造参数化 ─────────────────────


class TestComponentHealthParametrized:
    """参数化测试 ComponentHealth 的构造。"""

    @pytest.mark.parametrize(
        "name,status_name,message,latency_ms",
        [
            ("database", "HEALTHY", "", 10.0),
            ("database", "UNHEALTHY", "timeout", 5000.0),
            ("memory", "DEGRADED", "not found", 0.0),
            ("config", "HEALTHY", "", 1.0),
            ("dashboard", "DEGRADED", "not reachable", 100.0),
        ],
        ids=["db-healthy", "db-unhealthy", "mem-degraded", "cfg-healthy", "dash-degraded"],
    )
    def test_component_health_construction(self, name, status_name, message, latency_ms):
        from maop.deploy import ComponentHealth, HealthStatus

        h = ComponentHealth(
            name=name,
            status=HealthStatus[status_name],
            message=message,
            latency_ms=latency_ms,
        )
        assert h.name == name
        assert h.status == HealthStatus[status_name]
        assert h.message == message
        assert h.latency_ms == latency_ms


# ── 14. deploy ValidationResult 参数化 ────────────────────────


class TestValidationResultParametrized:
    """参数化测试 ValidationResult 的构造。"""

    @pytest.mark.parametrize(
        "errors,expected_valid",
        [
            ([], True),
            (["error1"], False),
            (["error1", "error2"], False),
        ],
        ids=["no-errors", "one-error", "two-errors"],
    )
    def test_validation_result_valid(self, errors, expected_valid):
        from maop.deploy import ValidationResult

        # valid 不会自动从 errors 推导，需要显式设置
        r = ValidationResult(valid=len(errors) == 0, errors=errors)
        assert r.valid is expected_valid

    @pytest.mark.parametrize(
        "warnings",
        [
            [],
            ["warning1"],
            ["warning1", "warning2", "warning3"],
        ],
        ids=["no-warnings", "one-warning", "three-warnings"],
    )
    def test_validation_result_warnings(self, warnings):
        from maop.deploy import ValidationResult

        r = ValidationResult(warnings=warnings)
        assert r.warnings == warnings
        # warnings 不影响 valid
        assert r.valid is True


# ── 15. engine StepResult / EngineResult 参数化 ──────────────


class TestStepResultParametrized:
    """参数化测试 StepResult 的构造。"""

    @pytest.mark.parametrize(
        "step_id,expected_status",
        [
            ("s1", "pending"),
            ("step-2", "pending"),
            ("any-id", "pending"),
        ],
        ids=["s1", "step-2", "any-id"],
    )
    def test_step_result_default_status(self, step_id, expected_status):
        from maop.engine import StepResult, StepStatus

        r = StepResult(id=step_id)
        assert r.id == step_id
        assert r.status == StepStatus(expected_status)


class TestEngineResultParametrized:
    """参数化测试 EngineResult 的默认值。"""

    @pytest.mark.parametrize(
        "field,expected_value",
        [
            ("steps", []),
            ("success", False),
        ],
    )
    def test_engine_result_defaults(self, field, expected_value):
        from maop.engine import EngineResult

        r = EngineResult()
        assert getattr(r, field) == expected_value


# ── 16. bloom_filter _BitArray 参数化 ─────────────────────────


class TestBitArrayParametrized:
    """参数化测试 _BitArray 的各种操作。"""

    @pytest.mark.parametrize(
        "size",
        [64, 128, 256, 512, 1024],
        ids=["64", "128", "256", "512", "1024"],
    )
    def test_bit_array_set_and_test(self, size):
        from maop.core.memory.bloom_filter import _BitArray

        ba = _BitArray(size)
        assert not ba.test(0)
        ba.set(0)
        assert ba.test(0)
        assert not ba.test(1)

    @pytest.mark.parametrize(
        "size,positions",
        [
            (128, [0, 1, 2, 3]),
            (256, [0, 7, 8, 15, 16, 127, 255]),
            (64, [0, 7, 8, 63]),
        ],
        ids=["128-4pos", "256-7pos", "64-4pos"],
    )
    def test_bit_array_multiple_positions(self, size, positions):
        from maop.core.memory.bloom_filter import _BitArray

        ba = _BitArray(size)
        for pos in positions:
            ba.set(pos)
        for pos in positions:
            assert ba.test(pos)


# ── 17. bloom_filter hash 函数参数化 ──────────────────────────


class TestHashFunctionsParametrized:
    """参数化测试 hash 函数。"""

    @pytest.mark.parametrize(
        "key",
        [b"test-key", b"hello", b"", b"long-key-for-testing", b"12345"],
        ids=["test-key", "hello", "empty", "long-key", "numeric"],
    )
    def test_hash_deterministic(self, key):
        from maop.core.memory.bloom_filter import _mmh3_hash32

        h1 = _mmh3_hash32(key, seed=0)
        h2 = _mmh3_hash32(key, seed=0)
        assert h1 == h2

    @pytest.mark.parametrize(
        "key,m",
        [
            (b"test-key", 1000),
            (b"hello", 100),
            (b"abc", 500),
        ],
        ids=["1000", "100", "500"],
    )
    def test_hash_i_in_range(self, key, m):
        from maop.core.memory.bloom_filter import _hash_i

        for i in range(10):
            h = _hash_i(key, i, m)
            assert 0 <= h < m


# ── 18. config BudgetConfig 参数化 ────────────────────────────


class TestBudgetConfigFullParametrized:
    """参数化测试 BudgetConfig 的各种构造。"""

    @pytest.mark.parametrize(
        "daily,monthly,alert,hard_stop",
        [
            (1.0, 10.0, 0.8, True),
            (5.0, 100.0, 0.9, False),
            (0.5, 5.0, 0.5, True),
            (10.0, 1000.0, 1.0, False),
        ],
        ids=["small-strict", "default-loose", "tiny-alert50", "large-alert100"],
    )
    def test_budget_config_construction(self, daily, monthly, alert, hard_stop):
        from maop.model.schema import BudgetConfig

        c = BudgetConfig(
            daily_limit=daily,
            monthly_limit=monthly,
            alert_threshold=alert,
            hard_stop=hard_stop,
        )
        assert c.daily_limit == daily
        assert c.monthly_limit == monthly
        assert c.alert_threshold == alert
        assert c.hard_stop == hard_stop


# ── 19. engine WorkflowStep 构造参数化 ───────────────────────


class TestWorkflowStepConstructionParametrized:
    """参数化测试 WorkflowStep 的各种构造。"""

    @pytest.mark.parametrize(
        "step_id,step_type,agent,task",
        [
            ("s1", "AGENT", "claude", "codegen"),
            ("s2", "PLAN", "", "analyze"),
            ("s3", "VERIFY", "", ""),
            ("s4", "TERMINAL", "", "done"),
        ],
        ids=["agent-step", "plan-step", "verify-step", "terminal-step"],
    )
    def test_workflow_step_construction(self, step_id, step_type, agent, task):
        from maop.engine import StepType, WorkflowStep

        step = WorkflowStep(
            id=step_id,
            type=StepType[step_type],
            agent=agent,
            task=task,
        )
        assert step.id == step_id
        assert step.type == StepType[step_type]
        assert step.agent == agent
        assert step.task == task


# ── 20. state_classifier ClassificationResult 参数化 ─────────


class TestClassificationResultParametrized:
    """参数化测试 ClassificationResult 的默认值。"""

    @pytest.mark.parametrize(
        "field,expected_value",
        [
            ("state", "working"),  # TaskState.WORKING
            ("confidence", 0.0),
            ("reason", ""),
            ("block_reason", ""),
            ("matched_pattern", ""),
        ],
    )
    def test_classification_result_defaults(self, field, expected_value):
        from maop.core.agent.lifecycle.state_classifier import (
            ClassificationResult,
            TaskState,
        )

        r = ClassificationResult()
        value = getattr(r, field)

        if field == "state":
            assert value == TaskState.WORKING
        else:
            assert value == expected_value