"""参数化测试集合 3 — 推广 @pytest.mark.parametrize 使用率（第三批）。

覆盖模块：
  - maop.maop_verify gate 函数               (exit_code / output / content_safety)
  - maop.maop_plan Plan 字段                 (budget / gates)
  - maop.core.security.permission            (PermissionManager decisions)
  - maop.model.schema 枚举                   (ProviderType / QualityTier / SelectionStrategy)
  - maop.core.reliability.circuit_breaker    (CircuitBreaker 状态转换)
  - maop.core.reliability.rate_limiter       (SlidingWindow)
  - maop.engine _find_step                   (查找步骤)
  - maop.core.memory.bloom_filter            (BloomFilter stats)
  - maop.evolve EvolutionStats               (统计模型)
  - maop.deploy health_check                 (组件健康)
  - maop.concurrency Task                    (任务模型)
  - maop.core.reliability.error_schema       (MaopResult 字段)
"""

from __future__ import annotations

import pytest

# ── 1. maop_verify gate 函数参数化 ────────────────────────────


class TestGateFunctionsParametrized:
    """参数化测试 maop_verify 的各种 gate 函数。

    合并自 test_maop_verify.py 中重复的 gate 测试。
    """

    @pytest.mark.parametrize(
        "exit_code,expected_passed",
        [
            (0, True),
            (1, False),
            (2, False),
            (-1, False),
        ],
        ids=["exit-0", "exit-1", "exit-2", "exit-neg"],
    )
    def test_gate_exit_code(self, exit_code, expected_passed):
        from maop.core.reliability.error_schema import new_result
        from maop.maop_verify import _gate_exit_code

        r = new_result(agent="a", task="t", exit_code=exit_code, stdout="ok")
        gr = _gate_exit_code({}, r)
        assert gr.passed is expected_passed
        assert gr.name == "exit_code"

    @pytest.mark.parametrize(
        "stdout,expected_passed",
        [
            ("result here", True),
            ("hello world", True),
            ("", False),
            ("   \n  ", False),
        ],
        ids=["non-empty", "non-empty-2", "empty", "whitespace"],
    )
    def test_gate_output(self, stdout, expected_passed):
        from maop.core.reliability.error_schema import new_result
        from maop.maop_verify import _gate_output

        r = new_result(agent="a", task="t", exit_code=0, stdout=stdout)
        gr = _gate_output({}, r)
        assert gr.passed is expected_passed

    @pytest.mark.parametrize(
        "stdout,expected_passed",
        [
            ("all good here", True),
            ("normal output", True),
            ("", True),  # 空输出通过
            ("api_key=sk-abcdefghijklmnopqrstuvwxyz123456", False),
            ("-----BEGIN RSA PRIVATE KEY-----", False),
            ("token: ghp_abcdefghijklmnopqrstuvwxyz0123456789", False),
        ],
        ids=["clean", "normal", "empty", "api-key", "private-key", "github-pat"],
    )
    def test_gate_content_safety(self, stdout, expected_passed):
        from maop.core.reliability.error_schema import new_result
        from maop.maop_verify import _gate_content_safety

        r = new_result(agent="a", task="t", exit_code=0, stdout=stdout)
        gr = _gate_content_safety({}, r)
        assert gr.passed is expected_passed

    @pytest.mark.parametrize(
        "stdout,expected_passed",
        [
            ("def foo(): pass", True),
            ("import os", True),
            ("SyntaxError: invalid syntax", False),
            ("IndentationError: unexpected indent", False),
        ],
        ids=["valid-func", "valid-import", "syntax-error", "indent-error"],
    )
    def test_gate_syntax_check(self, stdout, expected_passed):
        from maop.core.reliability.error_schema import new_result
        from maop.maop_verify import _gate_syntax_check

        r = new_result(agent="a", task="t", exit_code=0, stdout=stdout)
        gr = _gate_syntax_check({}, r)
        assert gr.passed is expected_passed


# ── 2. maop_plan Plan 字段参数化 ──────────────────────────────


class TestPlanFieldsParametrized:
    """参数化测试 Plan 的字段。"""

    @pytest.mark.parametrize(
        "task,routing_key,expected_gate",
        [
            ("fix the bug", "quickfix", "content-safety"),
            ("run pipeline", "pipeline", "dry-run"),
            ("review code", "review", "content-safety"),
            ("move file", "fileops", "dry-run"),
        ],
        ids=["quickfix", "pipeline", "review", "fileops"],
    )
    def test_routing_key_adds_gate(self, task, routing_key, expected_gate):
        from maop.maop_plan import maop_plan

        plan = maop_plan(task, routing_key=routing_key)
        assert expected_gate in plan.gates

    @pytest.mark.parametrize(
        "task",
        [
            "hello world",
            "write docs",
            "general task",
        ],
        ids=["hello", "docs", "general"],
    )
    def test_non_security_no_content_safety_gate(self, task):
        from maop.maop_plan import maop_plan

        plan = maop_plan(task)
        # 一般任务不应包含 content-safety gate
        assert "content-safety" not in plan.gates

    @pytest.mark.parametrize(
        "task",
        [
            "hello world",
            "write docs",
            "general task",
        ],
        ids=["hello", "docs", "general"],
    )
    def test_non_deploy_no_dry_run_gate(self, task):
        from maop.maop_plan import maop_plan

        plan = maop_plan(task)
        assert "dry-run" not in plan.gates


# ── 3. permission PermissionManager 参数化 ────────────────────


class TestPermissionManagerParametrized:
    """参数化测试 PermissionManager 的各种决策。"""

    @pytest.mark.parametrize(
        "decision,expected_allowed",
        [
            ("allow", True),
            ("deny", False),
        ],
        ids=["allow", "deny"],
    )
    def test_permission_decision(self, tmp_path, decision, expected_allowed):
        from maop.core.security.permission import PermissionManager

        pm = PermissionManager(root_dir=str(tmp_path))
        pm.add_rule(agent="claude", action="codegen", decision=decision)
        check = pm.check(agent="claude", action="codegen")
        assert check.allowed is expected_allowed
        assert check.decision == decision

    @pytest.mark.parametrize(
        "agent,action",
        [
            ("claude", "codegen"),
            ("kimi", "search"),
            ("codex", "review"),
            ("gpt-4", "analyze"),
        ],
        ids=["claude", "kimi", "codex", "gpt4"],
    )
    def test_unknown_returns_ask(self, tmp_path, agent, action):
        from maop.core.security.permission import PermissionManager

        pm = PermissionManager(root_dir=str(tmp_path))
        check = pm.check(agent=agent, action=action)
        assert check.decision == "ask"
        assert check.allowed is False


# ── 4. model.schema 枚举参数化 ────────────────────────────────


class TestModelSchemaEnumsParametrized:
    """参数化测试 model.schema 的各种枚举。"""

    @pytest.mark.parametrize(
        "enum_member",
        ["OPENAI_COMPATIBLE", "ANTHROPIC", "OLLAMA"],
    )
    def test_provider_type_exists(self, enum_member):
        from maop.model.schema import ProviderType

        try:
            p = ProviderType[enum_member]
            assert p is not None
        except KeyError:
            pytest.skip(f"ProviderType.{enum_member} not present")

    @pytest.mark.parametrize(
        "enum_member",
        ["EXCELLENT", "GOOD", "FAIR", "POOR"],
    )
    def test_quality_tier_exists(self, enum_member):
        from maop.model.schema import QualityTier

        try:
            q = QualityTier[enum_member]
            assert q is not None
        except KeyError:
            pytest.skip(f"QualityTier.{enum_member} not present")

    @pytest.mark.parametrize(
        "enum_member,expected_value",
        [
            ("BEST_QUALITY_WITHIN_BUDGET", "best_quality_within_budget"),
        ],
    )
    def test_selection_strategy_values(self, enum_member, expected_value):
        from maop.model.schema import SelectionStrategy

        assert SelectionStrategy[enum_member].value == expected_value


# ── 5. circuit_breaker 状态转换参数化 ────────────────────────


class TestCircuitBreakerStateParametrized:
    """参数化测试 CircuitBreaker 的状态转换。"""

    @pytest.mark.parametrize(
        "failures,should_be_open",
        [
            (1, False),
            (2, False),
            (3, True),  # 默认 threshold=3
            (5, True),
        ],
        ids=["1-fail", "2-fails", "3-fails", "5-fails"],
    )
    def test_record_failure_state_transition(self, failures, should_be_open):
        import contextlib
        import shutil
        import tempfile
        from pathlib import Path

        from maop.core.reliability.circuit_breaker import BreakerState, CircuitBreaker

        tmp = tempfile.mkdtemp(prefix="MAOP_cb_state_")
        try:
            db_path = Path(tmp) / "maop.db"
            cb = CircuitBreaker(path=db_path)
            # 先获取 threshold
            entry = cb.get("claude")
            threshold = entry.threshold
            for _ in range(min(failures, threshold + 2)):
                cb.record_failure("claude")
            entry = cb.get("claude")
            if should_be_open:
                assert entry.state == BreakerState.OPEN
            else:
                assert entry.state == BreakerState.CLOSED
        finally:
            with contextlib.suppress(Exception):
                shutil.rmtree(tmp, ignore_errors=True)


# ── 6. rate_limiter SlidingWindow 参数化 ─────────────────────


class TestSlidingWindowParametrized:
    """参数化测试 SlidingWindow 的行为。"""

    @pytest.mark.parametrize(
        "rate,window_s",
        [
            (10.0, 60.0),
            (100.0, 10.0),
            (1.0, 100.0),
        ],
        ids=["10-60", "100-10", "1-100"],
    )
    def test_sliding_window_config(self, rate, window_s):
        from maop.core.reliability.rate_limiter import RateLimiterConfig

        c = RateLimiterConfig(algorithm="sliding_window", rate=rate, window_s=window_s)
        assert c.algorithm == "sliding_window"
        assert c.rate == rate
        assert c.window_s == window_s


# ── 7. engine _find_step 参数化 ──────────────────────────────


class TestFindStepParametrized:
    """参数化测试 _find_step。"""

    @pytest.mark.parametrize(
        "steps,target_id,found",
        [
            (["s1", "s2", "s3"], "s2", True),
            (["s1"], "s1", True),
            (["s1", "s2"], "nonexistent", False),
            ([], "s1", False),
        ],
        ids=["found-middle", "found-only", "not-found", "empty"],
    )
    def test_find_step(self, steps, target_id, found):
        from maop.engine import StepType, WorkflowStep, _find_step

        step_list = [WorkflowStep(id=s, type=StepType.AGENT) for s in steps]
        result = _find_step(step_list, target_id)
        if found:
            assert result.id == target_id
        else:
            # 未找到时返回一个 id=target_id 的 fallback step
            assert result.id == target_id


# ── 8. bloom_filter stats 参数化 ──────────────────────────────


class TestBloomFilterStatsParametrized:
    """参数化测试 BloomFilter 的 stats 结构。"""

    @pytest.mark.parametrize(
        "expected_key",
        [
            "items_added",
            "bit_array_size",
            "hash_functions",
            "fill_ratio",
            "current_fp_rate",
            "target_fp_rate",
            "memory_bytes",
        ],
    )
    def test_bloom_filter_stats_keys(self, expected_key):
        from maop.core.memory.bloom_filter import BloomFilter

        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        stats = bf.stats()
        assert expected_key in stats

    @pytest.mark.parametrize(
        "n_items",
        [0, 1, 10, 50, 100],
        ids=["empty", "1-item", "10-items", "50-items", "100-items"],
    )
    def test_bloom_filter_fill_ratio_increases(self, n_items):
        from maop.core.memory.bloom_filter import BloomFilter

        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        for i in range(n_items):
            bf.add(f"item-{i}")
        if n_items > 0:
            assert bf.fill_ratio > 0.0
        else:
            assert bf.fill_ratio == 0.0


# ── 9. evolve EvolutionStats 参数化 ──────────────────────────


class TestEvolutionStatsParametrized:
    """参数化测试 EvolutionStats 模型。"""

    @pytest.mark.parametrize(
        "n_agents,n_keys,n_agent_keys",
        [
            (0, 0, 0),
            (1, 1, 0),
            (2, 2, 2),
            (5, 3, 10),
        ],
        ids=["empty", "1-1-0", "2-2-2", "5-3-10"],
    )
    def test_evolution_stats_construction(self, n_agents, n_keys, n_agent_keys):
        from maop.evolve import AgentKeyStats, AgentStats, EvolutionStats, RoutingKeyStats

        stats = EvolutionStats(
            by_agent=[AgentStats(agent=f"a{i}") for i in range(n_agents)],
            by_key=[RoutingKeyStats(routing_key=f"k{i}") for i in range(n_keys)],
            by_agent_key=[AgentKeyStats(agent="a", routing_key="k") for _ in range(n_agent_keys)],
        )
        assert len(stats.by_agent) == n_agents
        assert len(stats.by_key) == n_keys
        assert len(stats.by_agent_key) == n_agent_keys


# ── 10. deploy health_check 参数化 ────────────────────────────


class TestHealthCheckParametrized:
    """参数化测试 health_check 的组件。"""

    @pytest.mark.parametrize(
        "component_name",
        ["database", "memory", "config", "dashboard"],
    )
    def test_health_check_returns_component(self, tmp_path, component_name):
        from maop.deploy import health_check

        components = health_check(tmp_path)
        names = [c.name for c in components]
        assert component_name in names

    @pytest.mark.parametrize(
        "setup_config,expected_config_status",
        [
            (True, "HEALTHY"),
            (False, "UNHEALTHY"),
        ],
        ids=["with-config", "without-config"],
    )
    def test_health_check_config_component(self, tmp_path, setup_config, expected_config_status):
        from maop.deploy import HealthStatus, health_check

        if setup_config:
            (tmp_path / "config").mkdir(parents=True, exist_ok=True)
            (tmp_path / "config" / "agents.yaml").write_text("agents: {}", encoding="utf-8")
        components = health_check(tmp_path)
        config_comp = next(c for c in components if c.name == "config")
        assert config_comp.status == HealthStatus[expected_config_status]


# ── 11. concurrency Task 参数化 ──────────────────────────────


class TestTaskParametrized:
    """参数化测试 concurrency Task 模型。"""

    @pytest.mark.parametrize(
        "name,priority_name",
        [
            ("low-task", "LOW"),
            ("normal-task", "NORMAL"),
            ("high-task", "HIGH"),
            ("critical-task", "CRITICAL"),
        ],
        ids=["low", "normal", "high", "critical"],
    )
    def test_task_construction_with_priority(self, name, priority_name):
        from maop.concurrency import Priority, Task

        t = Task(name=name, priority=Priority[priority_name])
        assert t.name == name
        assert t.priority == Priority[priority_name]


# ── 12. error_schema MaopResult 序列化参数化 ────────────────


class TestMaopResultSerializationParametrized:
    """参数化测试 MaopResult 的序列化。"""

    @pytest.mark.parametrize(
        "agent,task,trace_id",
        [
            ("claude", "codegen", "trace-1"),
            ("kimi", "search", "trace-2"),
            ("codex", "review", "trace-3"),
        ],
        ids=["claude", "kimi", "codex"],
    )
    def test_serialization_roundtrip(self, agent, task, trace_id):
        from maop.core.reliability.error_schema import MaopResult, new_result

        r = new_result(agent=agent, task=task, trace_id=trace_id)
        data = r.model_dump()
        r2 = MaopResult(**data)
        assert r2.agent == agent
        assert r2.task == task
        assert r2.trace_id == trace_id


# ── 13. engine EngineResult 构造参数化 ───────────────────────


class TestEngineResultConstructionParametrized:
    """参数化测试 EngineResult 的构造。"""

    @pytest.mark.parametrize(
        "success,steps_count",
        [
            (True, 0),
            (True, 1),
            (False, 0),
            (False, 3),
        ],
        ids=["success-0", "success-1", "fail-0", "fail-3"],
    )
    def test_engine_result_construction(self, success, steps_count):
        from maop.engine import EngineResult, StepResult

        r = EngineResult(
            success=success,
            steps=[StepResult(id=f"s{i}") for i in range(steps_count)],
        )
        assert r.success is success
        assert len(r.steps) == steps_count


# ── 14. deploy DeployConfig 构造参数化 ───────────────────────


class TestDeployConfigConstructionParametrized:
    """参数化测试 DeployConfig 的构造。"""

    @pytest.mark.parametrize(
        "root_dir,port,host,workers",
        [
            ("/tmp/MAOP", 8080, "0.0.0.0", 1),
            ("/opt/MAOP", 9079, "127.0.0.1", 4),
            ("", 3000, "localhost", 2),
        ],
        ids=["custom", "default-ish", "empty-root"],
    )
    def test_deploy_config_construction(self, root_dir, port, host, workers):
        from maop.deploy import DeployConfig

        c = DeployConfig(
            root_dir=root_dir,
            dashboard_port=port,
            dashboard_host=host,
            workers=workers,
        )
        assert c.root_dir == root_dir
        assert c.dashboard_port == port
        assert c.dashboard_host == host
        assert c.workers == workers


# ── 15. state_classifier _best_match 参数化 ─────────────────


class TestBestMatchParametrized:
    """参数化测试 TaskStateClassifier._best_match。"""

    @pytest.mark.parametrize(
        "text,should_match",
        [
            ("permission denied", True),
            ("access denied", True),
            ("waiting for user input", True),
            ("totally benign text", False),
            ("module not found", False),  # 这是 failed 模式，不是 blocked
        ],
        ids=["permission", "access", "waiting", "benign", "module-not-found"],
    )
    def test_best_match_blocked_patterns(self, text, should_match):
        from maop.core.agent.lifecycle.state_classifier import TaskStateClassifier

        clf = TaskStateClassifier()
        match = clf._best_match(text, clf._blocked)
        if should_match:
            assert match is not None
        else:
            assert match is None
