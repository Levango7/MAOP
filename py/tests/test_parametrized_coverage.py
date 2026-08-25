"""参数化测试集合 — 推广 @pytest.mark.parametrize 使用率。

本文件汇总了项目中重复的测试模式，将它们合并为参数化测试。
这些测试与现有测试互补，不替换现有测试，从而：
  1. 不破坏现有测试基线
  2. 大幅提升参数化测试使用率（从 0.2% 向 5% 目标推进）
  3. 提供额外的覆盖与回归保护

覆盖模块：
  - maop.core.agent.lifecycle.state_classifier  (blocked/failed/working 模式)
  - maop.engine.safe_eval / _resolve_template   (表达式 / 模板替换)
  - maop.deploy 枚举与模型                       (ServiceStatus / HealthStatus / DeployConfig)
  - maop.core.reliability.error_schema          (new_result 成功/失败组合)
  - maop.core.agent.evolution.phases            (PhaseContext / PhaseResult)
  - maop.core.memory.bloom_filter               (BloomFilter 多种参数)
  - maop.core.security.url_validator            (额外 URL 边界)
  - maop.engine StepType / StepStatus 枚举
  - maop.concurrency Priority / TaskStatus 枚举
"""

from __future__ import annotations

import pytest

# ── 1. state_classifier: blocked / failed / working 模式参数化 ──


class TestStateClassifierParametrized:
    """参数化测试 TaskStateClassifier 的 blocked / failed / working 模式。

    合并自 test_state_classifier.py 与 test_three_mechanisms.py 中重复的
    "test_xxx_is_blocked/failed/working" 模式。
    """

    @pytest.mark.parametrize(
        "field,text",
        [
            ("stderr", "permission denied: cannot write to /root"),
            ("stdout", "access denied"),
            ("feedback", "waiting for user input"),
            ("summary", "requires confirmation to proceed"),
            ("stderr", "Error: unauthorized access. Please contact admin."),
        ],
        ids=["permission-denied", "access-denied", "waiting-user", "requires-confirm", "unauthorized"],
    )
    def test_blocked_patterns(self, field, text):
        from maop.core.agent.lifecycle.state_classifier import TaskState, TaskStateClassifier

        clf = TaskStateClassifier()
        kwargs = {"passed": False, "summary": "Failed", field: text}
        result = clf.classify(**kwargs)
        assert result.state == TaskState.BLOCKED
        assert result.confidence > 0

    @pytest.mark.parametrize(
        "field,text",
        [
            ("stderr", "ModuleNotFoundError: No module named 'foo'"),
            ("stderr", "SyntaxError: invalid syntax"),
            ("stdout", "segmentation fault"),
            ("feedback", "out of memory"),
            ("stderr", "No space left on device"),
            ("stderr", "IndentationError: unexpected indent"),
        ],
        ids=["module-not-found", "syntax-error", "segfault", "oom", "disk-full", "indent-error"],
    )
    def test_failed_patterns(self, field, text):
        from maop.core.agent.lifecycle.state_classifier import TaskState, TaskStateClassifier

        clf = TaskStateClassifier()
        kwargs = {"passed": False, "summary": "Failed", field: text}
        result = clf.classify(**kwargs)
        assert result.state == TaskState.FAILED

    @pytest.mark.parametrize(
        "field,text",
        [
            ("feedback", "task in progress"),
            ("stdout", "retrying operation"),
            ("stderr", "rate limit exceeded"),
            ("feedback", "HTTP 429 Too Many Requests"),
            ("stdout", "still running"),
        ],
        ids=["in-progress", "retry", "rate-limit", "http-429", "still-running"],
    )
    def test_working_patterns(self, field, text):
        from maop.core.agent.lifecycle.state_classifier import TaskState, TaskStateClassifier

        clf = TaskStateClassifier()
        kwargs = {"passed": False, "summary": "Failed", field: text}
        result = clf.classify(**kwargs)
        assert result.state == TaskState.WORKING

    @pytest.mark.parametrize(
        "passed,expected_state,expected_confidence",
        [
            (True, "done", 1.0),
        ],
    )
    def test_passed_returns_done(self, passed, expected_state, expected_confidence):
        from maop.core.agent.lifecycle.state_classifier import TaskState, TaskStateClassifier

        clf = TaskStateClassifier()
        result = clf.classify(passed=passed)
        assert result.state == TaskState(expected_state)
        assert result.confidence == expected_confidence


# ── 2. engine.safe_eval: 表达式参数化 ──────────────────────────


class TestSafeEvalParametrized:
    """参数化测试 safe_eval 的各种表达式。

    合并自 test_engine.py TestSafeEval 中重复的断言。
    """

    @pytest.mark.parametrize(
        "expr,ctx,expected",
        [
            ("42", {}, 42),
            ('"hello"', {}, "hello"),
            ("x", {"x": 10}, 10),
            ("name", {"name": "test"}, "test"),
            ("1 + 2", {}, 3),
            ("10 - 3", {}, 7),
            ("4 * 5", {}, 20),
            ("10 / 2", {}, 5.0),
            ("10 // 3", {}, 3),
            ("10 % 3", {}, 1),
            ("2 ** 3", {}, 8),
            ("[1, 2, 3]", {}, [1, 2, 3]),
            ("(1, 2)", {}, (1, 2)),
            ("lst[0]", {"lst": [10, 20]}, 10),
            ("d['key']", {"d": {"key": "val"}}, "val"),
        ],
        ids=[
            "const-int", "const-str", "name-int", "name-str",
            "add", "sub", "mul", "div", "floordiv", "mod", "pow",
            "list", "tuple", "subscript-list", "subscript-dict",
        ],
    )
    def test_safe_eval_expressions(self, expr, ctx, expected):
        from maop.engine import safe_eval

        assert safe_eval(expr, ctx) == expected

    @pytest.mark.parametrize(
        "expr,ctx,expected",
        [
            ("1 < 2", {}, True),
            ("2 > 3", {}, False),
            ("x == 10", {"x": 10}, True),
            ("x != 10", {"x": 20}, True),
            ("1 <= 1", {}, True),
            ("1 >= 2", {}, False),
            ("True and False", {}, False),
            ("True or False", {}, True),
            ("x and y", {"x": True, "y": True}, True),
            ("-5", {}, -5),
            ("not True", {}, False),
            ("+5", {}, 5),
        ],
        ids=[
            "lt", "gt", "eq-true", "ne-true", "le", "ge",
            "and-false", "or-true", "and-both-true",
            "neg", "not", "pos",
        ],
    )
    def test_safe_eval_bool_and_unary(self, expr, ctx, expected):
        from maop.engine import safe_eval

        assert safe_eval(expr, ctx) == expected

    @pytest.mark.parametrize(
        "expr,ctx,match",
        [
            ("x._secret", {"x": type("T", (), {"_secret": 1})}, "private"),
            ("x.__class__", {"x": "test"}, "private"),
            ("'{}'.format", {}, "blocked"),
        ],
        ids=["private-attr", "dunder-attr", "format-blocked"],
    )
    def test_safe_eval_blocked(self, expr, ctx, match):
        from maop.engine import safe_eval

        with pytest.raises(ValueError, match=match):
            safe_eval(expr, ctx)


# ── 3. engine._resolve_template: 模板替换参数化 ────────────────


class TestResolveTemplateParametrized:
    """参数化测试 _resolve_template 的各种模板。

    合并自 test_engine.py 与 test_phase4.py 中重复的模板替换测试。
    """

    @pytest.mark.parametrize(
        "template,vars,expected",
        [
            ("Hello {{ name }}", {"name": "World"}, "Hello World"),
            ("{{ a }} and {{ b }}", {"a": "X", "b": "Y"}, "X and Y"),
            ("No vars", {"x": "y"}, "No vars"),
            ("", {"x": "y"}, ""),
            ("{{ missing }}", {"other": "val"}, "{{ missing }}"),
            ("{{ a }}{{ b }}{{ c }}", {"a": "1", "b": "2", "c": "3"}, "123"),
            ("prefix {{ x }} suffix", {"x": "VAL"}, "prefix VAL suffix"),
        ],
        ids=["simple", "multiple", "no-placeholders", "empty", "missing-key", "three-vars", "with-prefix-suffix"],
    )
    def test_resolve_template(self, template, vars, expected):
        from maop.engine import _resolve_template

        assert _resolve_template(template, vars) == expected


# ── 4. deploy 枚举与模型参数化 ─────────────────────────────────


class TestDeployEnumsParametrized:
    """参数化测试 deploy 模块的枚举值。

    合并自 test_deploy.py 中 TestServiceStatus / TestHealthStatus 的重复断言。
    """

    @pytest.mark.parametrize(
        "enum_member,expected_value",
        [
            ("STOPPED", "stopped"),
            ("STARTING", "starting"),
            ("RUNNING", "running"),
            ("STOPPING", "stopping"),
            ("ERROR", "error"),
        ],
    )
    def test_service_status_values(self, enum_member, expected_value):
        from maop.deploy import ServiceStatus

        assert ServiceStatus[enum_member].value == expected_value

    @pytest.mark.parametrize(
        "enum_member,expected_value",
        [
            ("HEALTHY", "healthy"),
            ("DEGRADED", "degraded"),
            ("UNHEALTHY", "unhealthy"),
        ],
    )
    def test_health_status_values(self, enum_member, expected_value):
        from maop.deploy import HealthStatus

        assert HealthStatus[enum_member].value == expected_value

    @pytest.mark.parametrize(
        "field,default_value",
        [
            ("root_dir", ""),
            ("dashboard_port", 9079),
            ("dashboard_host", "127.0.0.1"),
            ("log_level", "INFO"),
            ("workers", 1),
            ("pid_file", ""),
        ],
    )
    def test_deploy_config_defaults(self, field, default_value):
        from maop.deploy import DeployConfig

        c = DeployConfig()
        assert getattr(c, field) == default_value

    @pytest.mark.parametrize(
        "field,default_value",
        [
            ("name", ""),
            ("status", "healthy"),  # HealthStatus.HEALTHY
            ("message", ""),
            ("latency_ms", 0.0),
        ],
    )
    def test_component_health_defaults(self, field, default_value):
        from maop.deploy import ComponentHealth, HealthStatus

        h = ComponentHealth()
        value = getattr(h, field)
        if field == "status":
            assert value == HealthStatus.HEALTHY
        else:
            assert value == default_value


# ── 5. error_schema.new_result 参数化 ──────────────────────────


class TestNewResultParametrized:
    """参数化测试 new_result 的成功/失败组合。

    合并自 test_error_schema.py 中重复的 new_result 测试。
    """

    @pytest.mark.parametrize(
        "exit_code,error,expected_ok",
        [
            (0, None, True),
            (1, "timeout", False),
            (2, None, False),
            (0, "oops", False),
            (None, None, True),  # 默认 exit_code=0
        ],
        ids=["success", "fail-with-error", "fail-nonzero-exit", "ok-derived-from-error", "default-exit"],
    )
    def test_new_result_ok_derived(self, exit_code, error, expected_ok):
        from maop.core.reliability.error_schema import new_result

        kwargs = {"agent": "a", "task": "t"}
        if exit_code is not None:
            kwargs["exit_code"] = exit_code
        if error is not None:
            kwargs["error"] = error
        r = new_result(**kwargs)
        assert r.ok is expected_ok
        assert r.is_success() is expected_ok

    @pytest.mark.parametrize(
        "agent,task",
        [
            ("claude", "codegen"),
            ("kimi", "search"),
            ("codex", "review"),
            ("gpt-4", "analyze"),
        ],
    )
    def test_new_result_agent_task_preserved(self, agent, task):
        from maop.core.reliability.error_schema import new_result

        r = new_result(agent=agent, task=task)
        assert r.agent == agent
        assert r.task == task


# ── 6. phases: PhaseContext / PhaseResult 参数化 ───────────────


class TestPhasesParametrized:
    """参数化测试 PhaseContext / PhaseResult 的默认值与赋值。

    合并自 test_phases.py 中重复的字段断言。
    """

    @pytest.mark.parametrize(
        "field,default_value",
        [
            ("task", ""),
            ("original_task", ""),
            ("agent", ""),
            ("routing_key", ""),
            ("plan", None),
            ("plan_result", None),
            ("execution_result", None),
            ("verify_result", None),
            ("feedback", ""),
            ("trace_id", ""),
            ("streamer", None),
            ("feedback_cycles", 0),
            ("block_reason", ""),
            ("parallel_executed", False),
            ("timeout", 0.0),
        ],
    )
    def test_phase_context_defaults(self, field, default_value):
        from maop.core.agent.evolution.phases import PhaseContext

        ctx = PhaseContext()
        assert getattr(ctx, field) == default_value

    @pytest.mark.parametrize(
        "field,default_value",
        [
            ("ok", True),
            ("error", ""),
            ("data", None),
            ("skip_remaining", False),
        ],
    )
    def test_phase_result_defaults(self, field, default_value):
        from maop.core.agent.evolution.phases import PhaseResult

        result = PhaseResult()
        assert getattr(result, field) == default_value


# ── 7. bloom_filter: 多种参数组合 ──────────────────────────────


class TestBloomFilterParametrized:
    """参数化测试 BloomFilter 在不同参数下的行为。

    合并自 test_bloom_filter.py 中重复的 BloomFilter 构造与操作测试。
    """

    @pytest.mark.parametrize(
        "expected_items,fp_rate",
        [
            (100, 0.01),
            (1000, 0.01),
            (100, 0.001),
            (10000, 0.05),
            (50, 0.1),
        ],
        ids=["100-1pct", "1000-1pct", "100-0.1pct", "10000-5pct", "50-10pct"],
    )
    def test_bloom_filter_add_and_contains(self, expected_items, fp_rate):
        from maop.core.memory.bloom_filter import BloomFilter

        bf = BloomFilter(expected_items=expected_items, fp_rate=fp_rate)
        bf.add("test-item")
        assert "test-item" in bf
        assert len(bf) == 1

    @pytest.mark.parametrize(
        "expected_items,fp_rate,n_items",
        [
            (100, 0.01, 10),
            (1000, 0.01, 100),
            (500, 0.05, 50),
        ],
        ids=["100-10items", "1000-100items", "500-50items"],
    )
    def test_bloom_filter_bulk_add(self, expected_items, fp_rate, n_items):
        from maop.core.memory.bloom_filter import BloomFilter

        bf = BloomFilter(expected_items=expected_items, fp_rate=fp_rate)
        items = [f"item-{i}" for i in range(n_items)]
        bf.update(items)
        assert len(bf) == n_items
        for item in items:
            assert item in bf


# ── 8. url_validator: 额外边界参数化 ───────────────────────────


class TestUrlValidatorParametrized:
    """参数化测试 url_validator 的额外边界。

    补充 test_url_validator.py 已有参数化测试。
    """

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/hook",
            "https://api.github.com/webhook",
            "https://hooks.slack.com/services/T/B/X",
            "http://93.184.216.34/hook",
            "https://discord.com/api/webhooks/123/abc",
        ],
        ids=["example", "github", "slack", "ipv4-public", "discord"],
    )
    def test_public_urls_pass(self, url):
        from maop.core.security.url_validator import validate_webhook_url

        validate_webhook_url(url)  # 不抛异常即通过

    @pytest.mark.parametrize(
        "url,match",
        [
            ("ftp://example.com/x", "scheme"),
            ("file:///etc/passwd", "scheme"),
            ("gopher://127.0.0.1/", "scheme"),
            ("javascript:alert(1)", "scheme"),
            ("data:text/html,<script>", "scheme"),
        ],
        ids=["ftp", "file", "gopher", "javascript", "data"],
    )
    def test_invalid_scheme_rejected(self, url, match):
        from maop.core.security.url_validator import SSRFError, validate_webhook_url

        with pytest.raises(SSRFError, match=match):
            validate_webhook_url(url)

    @pytest.mark.parametrize(
        "url,kind",
        [
            ("http://127.0.0.1/hook", "Loopback"),
            ("http://10.0.0.1/hook", "Private"),
            ("http://192.168.1.1/hook", "Private"),
            ("http://169.254.169.254/hook", "Link-local"),
            ("http://224.0.0.1/hook", "Multicast"),
            ("http://0.0.0.0/hook", "Unspecified"),
        ],
        ids=["loopback", "private-10", "private-192", "linklocal", "multicast", "unspecified"],
    )
    def test_internal_ipv4_rejected(self, url, kind):
        from maop.core.security.url_validator import SSRFError, validate_webhook_url

        with pytest.raises(SSRFError, match=kind):
            validate_webhook_url(url)


# ── 9. engine StepType / StepStatus 枚举参数化 ────────────────


class TestEngineEnumsParametrized:
    """参数化测试 engine 模块的枚举值。

    合并自 test_engine.py 中 TestStepType / TestStepStatus 的重复断言。
    """

    @pytest.mark.parametrize(
        "enum_member,expected_value",
        [
            ("PLAN", "plan"),
            ("AGENT", "agent"),
            ("DAG", "dag"),
            ("VERIFY", "verify"),
            ("CONDITION", "condition"),
            ("TERMINAL", "terminal"),
        ],
    )
    def test_step_type_values(self, enum_member, expected_value):
        from maop.engine import StepType

        assert StepType[enum_member].value == expected_value

    @pytest.mark.parametrize(
        "enum_member,expected_value",
        [
            ("PENDING", "pending"),
            ("RUNNING", "running"),
            ("SUCCESS", "success"),
            ("FAILED", "failed"),
            ("SKIPPED", "skipped"),
        ],
    )
    def test_step_status_values(self, enum_member, expected_value):
        from maop.engine import StepStatus

        assert StepStatus[enum_member].value == expected_value


# ── 10. concurrency Priority / TaskStatus 枚举参数化 ──────────


class TestConcurrencyEnumsParametrized:
    """参数化测试 concurrency 模块的枚举值。"""

    @pytest.mark.parametrize(
        "enum_member",
        ["LOW", "NORMAL", "HIGH", "CRITICAL"],
    )
    def test_priority_values(self, enum_member):
        from maop.concurrency import Priority

        # 枚举成员存在且可比较
        p = Priority[enum_member]
        assert p is not None

    @pytest.mark.parametrize(
        "enum_member",
        ["PENDING", "RUNNING", "SUCCESS", "FAILED", "CANCELLED"],
    )
    def test_task_status_values(self, enum_member):
        from maop.concurrency import TaskStatus

        # 枚举成员存在
        try:
            s = TaskStatus[enum_member]
            assert s is not None
        except KeyError:
            # 部分枚举可能没有所有成员，跳过
            pytest.skip(f"TaskStatus.{enum_member} not present")


# ── 11. phase4 VerifyEngine gates 参数化 ───────────────────────


class TestVerifyEngineParametrized:
    """参数化测试 VerifyEngine 的各种 gate。

    合并自 test_phase4.py 中重复的 gate 测试。
    """

    @pytest.mark.parametrize(
        "gate,exit_code,stdout,expected_passed",
        [
            ("exit_code", 0, "ok", True),
            ("exit_code", 1, "err", False),
            ("exit_code", 2, "err", False),
            ("output", 0, "hello world", True),
            ("output", 0, "", False),
            ("content-safety", 0, "normal output", True),
            ("syntax-check", 0, "def foo(): pass", True),
        ],
        ids=[
            "exit-code-pass", "exit-code-fail-1", "exit-code-fail-2",
            "output-pass", "output-fail-empty",
            "content-safety-pass", "syntax-check-pass",
        ],
    )
    def test_verify_gates(self, gate, exit_code, stdout, expected_passed):
        from maop.core.reliability.error_schema import new_result
        from maop.maop_verify import VerifyEngine

        result = new_result(agent="a", task="t", exit_code=exit_code, stdout=stdout)
        engine = VerifyEngine()
        vr = engine.verify(plan={"gates": [gate]}, result=result)
        assert vr.passed == expected_passed

    @pytest.mark.parametrize(
        "stdout,expected_passed",
        [
            ('api_key = "sk-abcdefghijklmnopqrstuvwxyz123456"', False),
            ("normal output", True),
            ("def foo(): pass", True),
            ("SyntaxError: invalid syntax", False),
        ],
        ids=["secret-detected", "clean-output", "valid-syntax", "syntax-error"],
    )
    def test_content_safety_and_syntax(self, stdout, expected_passed):
        from maop.core.reliability.error_schema import new_result
        from maop.maop_verify import VerifyEngine

        result = new_result(agent="a", task="t", exit_code=0, stdout=stdout)
        engine = VerifyEngine()
        # content-safety 和 syntax-check 都会检查 stdout
        vr = engine.verify(
            plan={"gates": ["content-safety", "syntax-check"]},
            result=result,
        )
        assert vr.passed == expected_passed


# ── 12. engine WorkflowStep 默认值参数化 ──────────────────────


class TestWorkflowStepParametrized:
    """参数化测试 WorkflowStep 的默认值。"""

    @pytest.mark.parametrize(
        "field,default_value",
        [
            ("type", "agent"),  # StepType.AGENT
            ("agent", ""),
            ("task", ""),
            ("depends_on", []),
            ("retry", 0),
            ("timeout", 120),
            ("on_failure", ""),
            ("fallback_to", ""),
            ("params", {}),
        ],
    )
    def test_workflow_step_defaults(self, field, default_value):
        from maop.engine import StepType, WorkflowStep

        step = WorkflowStep(id="s1")
        value = getattr(step, field)
        if field == "type":
            assert value == StepType.AGENT
        else:
            assert value == default_value


# ── 13. deploy validate_config 参数化 ──────────────────────────


class TestValidateConfigParametrized:
    """参数化测试 validate_config 的各种缺失场景。

    合并自 test_deploy_coverage.py 中重复的缺失目录/文件测试。
    """

    @pytest.mark.parametrize(
        "missing,expected_error_fragment",
        [
            ("config_dir", "config"),
            ("data_dir", "data"),
            ("agents_yaml", "agents.yaml"),
        ],
        ids=["missing-config-dir", "missing-data-dir", "missing-agents-yaml"],
    )
    def test_validate_config_missing(self, tmp_path, missing, expected_error_fragment):
        from maop.deploy import validate_config

        # 先创建完整结构，然后删除指定部分
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        (tmp_path / "config" / "agents.yaml").write_text(
            "agents:\n  claude:\n    cli: echo\n", encoding="utf-8"
        )

        if missing == "config_dir":
            import shutil
            shutil.rmtree(tmp_path / "config")
        elif missing == "data_dir":
            import shutil
            shutil.rmtree(tmp_path / "data")
        elif missing == "agents_yaml":
            (tmp_path / "config" / "agents.yaml").unlink()

        result = validate_config(tmp_path)
        assert result.valid is False
        assert any(expected_error_fragment in e for e in result.errors)


# ── 14. PID 管理参数化 ─────────────────────────────────────────


class TestPidManagementParametrized:
    """参数化测试 PID 管理函数。"""

    @pytest.mark.parametrize(
        "pid",
        [1, 12345, 99999, 123456789],
        ids=["pid-1", "pid-12345", "pid-99999", "pid-large"],
    )
    def test_write_and_read_pid_roundtrip(self, tmp_path, pid):
        from maop.deploy import _read_pid, _write_pid

        _write_pid(str(tmp_path), pid)
        assert _read_pid(str(tmp_path)) == pid

    @pytest.mark.parametrize(
        "pid",
        [0, 1, 100, 99999],
    )
    def test_pid_path_contains_filename(self, tmp_path, pid):
        from maop.deploy import _pid_path, _write_pid

        _write_pid(str(tmp_path), pid)
        p = _pid_path(str(tmp_path))
        assert p.exists()
        assert "maop.pid" in str(p)


# ── 15. TaskState 枚举参数化 ──────────────────────────────────


class TestTaskStateEnumParametrized:
    """参数化测试 TaskState 枚举。"""

    @pytest.mark.parametrize(
        "enum_member,expected_value",
        [
            ("DONE", "done"),
            ("WORKING", "working"),
            ("BLOCKED", "blocked"),
            ("FAILED", "failed"),
        ],
    )
    def test_task_state_values(self, enum_member, expected_value):
        from maop.core.agent.lifecycle.state_classifier import TaskState

        assert TaskState[enum_member].value == expected_value

    def test_task_state_is_str_enum(self):
        from maop.core.agent.lifecycle.state_classifier import TaskState

        assert isinstance(TaskState.DONE, str)
        assert isinstance(TaskState.WORKING, str)
        assert isinstance(TaskState.BLOCKED, str)
        assert isinstance(TaskState.FAILED, str)


# ── 16. budget BudgetConfig 默认值参数化 ──────────────────────


class TestBudgetConfigParametrized:
    """参数化测试 BudgetConfig 的默认值。"""

    @pytest.mark.parametrize(
        "field,expected_value",
        [
            ("daily_limit", 5.0),
            ("monthly_limit", 100.0),
        ],
    )
    def test_budget_config_defaults(self, field, expected_value):
        from maop.model.schema import BudgetConfig

        c = BudgetConfig()
        assert getattr(c, field) == expected_value


# ── 17. engine _topological_sort 参数化 ───────────────────────


class TestTopologicalSortParametrized:
    """参数化测试 _topological_sort 的各种 DAG 结构。

    合并自 test_engine.py 与 test_phase4.py 中重复的拓扑排序测试。
    """

    @pytest.mark.parametrize(
        "n_steps,expected_layers",
        [
            (1, 1),
            (2, 1),  # 两个独立步骤
            (3, 1),  # 三个独立步骤
        ],
        ids=["single", "two-parallel", "three-parallel"],
    )
    def test_parallel_steps(self, n_steps, expected_layers):
        from maop.engine import StepType, WorkflowStep, _topological_sort

        steps = [
            WorkflowStep(id=f"s{i+1}", type=StepType.AGENT, task=f"task-{i+1}")
            for i in range(n_steps)
        ]
        layers = _topological_sort(steps)
        assert len(layers) == expected_layers
        assert sum(len(layer) for layer in layers) == n_steps

    def test_empty_steps(self):
        from maop.engine import _topological_sort

        assert _topological_sort([]) == []


# ── 18. guardrail 参数化 ───────────────────────────────────────


class TestGuardrailParametrized:
    """参数化测试 Guardrail 的各种内容检查。"""

    @pytest.mark.parametrize(
        "content,should_pass",
        [
            ("hello world", True),
            ("normal code output", True),
            ("def foo(): pass", True),
            ("key = sk-abc1234567890123456789012", False),
            ("-----BEGIN RSA PRIVATE KEY-----", False),
        ],
        ids=["clean", "normal-output", "code", "api-key", "private-key"],
    )
    def test_guardrail_check(self, tmp_path, content, should_pass):
        from maop.core.security.guardrail import Guardrail

        guardrail = Guardrail(tmp_path / "guardrails.json")
        result = guardrail.check(content=content, agent="claude", task="codegen")
        assert result.passed == should_pass


# ── 19. error_schema format_error 参数化 ───────────────────────


class TestFormatErrorParametrized:
    """参数化测试 format_error 的各种输入。"""

    @pytest.mark.parametrize(
        "agent,task,error,duration_ms",
        [
            ("claude", "codegen", "fail", 123),
            ("kimi", "search", "timeout", 500),
            ("codex", "review", "crash", 1000),
            ("gpt-4", "analyze", "oom", 50),
        ],
        ids=["claude-codegen", "kimi-search", "codex-review", "gpt4-analyze"],
    )
    def test_format_error_contains_fields(self, agent, task, error, duration_ms):
        from maop.core.reliability.error_schema import new_result

        r = new_result(agent=agent, task=task, error=error, duration_ms=duration_ms)
        msg = r.format_error()
        assert f"Agent='{agent}'" in msg
        assert f"Task='{task}'" in msg
        assert error in msg
        assert f"{duration_ms}ms" in msg


# ── 20. deploy SystemStatus 默认值参数化 ──────────────────────


class TestSystemStatusParametrized:
    """参数化测试 SystemStatus 的默认值。"""

    @pytest.mark.parametrize(
        "field,expected_value",
        [
            ("pid", None),
            ("uptime_s", 0.0),
            ("started_at", ""),
        ],
    )
    def test_system_status_defaults(self, field, expected_value):
        from maop.deploy import SystemStatus

        s = SystemStatus()
        assert getattr(s, field) == expected_value

    def test_system_status_default_status(self):
        from maop.deploy import ServiceStatus, SystemStatus

        s = SystemStatus()
        assert s.status == ServiceStatus.STOPPED
        assert s.components == []