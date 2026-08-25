"""参数化测试集合 5 — 推广 @pytest.mark.parametrize 使用率（第五批，补足目标）。

覆盖模块：
  - maop.core.reliability.circuit_breaker BreakerState  (枚举值)
  - maop.engine StepResult                              (状态构造)
  - maop.core.agent.lifecycle.state_classifier TaskState (枚举值)
  - maop.deploy DeployConfig                            (更多字段组合)
  - maop.concurrency Task                               (默认值)
  - maop.core.reliability.error_schema MaopResult       (agent/task 组合)
  - maop.evolve AgentKeyStats                           (字段组合)
"""

from __future__ import annotations

import pytest

# ── 1. circuit_breaker BreakerState 枚举值参数化 ────────────


class TestBreakerStateValuesParametrized:
    """参数化测试 BreakerState 枚举值。"""

    @pytest.mark.parametrize(
        "enum_member,expected_value",
        [
            ("CLOSED", "closed"),
            ("OPEN", "open"),
            ("HALF_OPEN", "half-open"),
        ],
    )
    def test_breaker_state_values(self, enum_member, expected_value):
        from maop.core.reliability.circuit_breaker import BreakerState

        assert BreakerState[enum_member].value == expected_value


# ── 2. engine StepResult 状态构造参数化 ─────────────────────


class TestStepResultStatusParametrized:
    """参数化测试 StepResult 的状态构造。"""

    @pytest.mark.parametrize(
        "status_name",
        ["PENDING", "RUNNING", "SUCCESS", "FAILED", "SKIPPED"],
    )
    def test_step_result_with_status(self, status_name):
        from maop.engine import StepResult, StepStatus

        r = StepResult(id="s1", status=StepStatus[status_name])
        assert r.id == "s1"
        assert r.status == StepStatus[status_name]


# ── 3. state_classifier TaskState 枚举值参数化 ──────────────


class TestTaskStateValuesParametrized:
    """参数化测试 TaskState 枚举值。"""

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


# ── 4. deploy DeployConfig log_level 参数化 ─────────────────


class TestDeployConfigLogLevelParametrized:
    """参数化测试 DeployConfig 的 log_level 参数。"""

    @pytest.mark.parametrize(
        "log_level",
        ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        ids=["debug", "info", "warning", "error", "critical"],
    )
    def test_deploy_config_log_level(self, log_level):
        from maop.deploy import DeployConfig

        c = DeployConfig(log_level=log_level)
        assert c.log_level == log_level


# ── 5. concurrency Task 默认值参数化 ────────────────────────


class TestTaskDefaultsParametrized:
    """参数化测试 concurrency Task 的默认值。"""

    @pytest.mark.parametrize(
        "name",
        ["task-1", "task-2", "task-3", "task-4", "task-5"],
    )
    def test_task_default_priority(self, name):
        from maop.concurrency import Priority, Task

        t = Task(name=name)
        assert t.name == name
        # 默认优先级应为 NORMAL
        assert t.priority == Priority.NORMAL


# ── 6. error_schema MaopResult agent/task 组合参数化 ───────


class TestMaopResultAgentTaskParametrized:
    """参数化测试 MaopResult 的 agent/task 组合。"""

    @pytest.mark.parametrize(
        "agent,task",
        [
            ("claude", "fix bug"),
            ("kimi", "search docs"),
            ("codex", "review code"),
            ("gpt-4", "analyze data"),
            ("llama", "generate text"),
        ],
        ids=["claude-fix", "kimi-search", "codex-review", "gpt4-analyze", "llama-generate"],
    )
    def test_new_result_agent_task(self, agent, task):
        from maop.core.reliability.error_schema import new_result

        r = new_result(agent=agent, task=task)
        assert r.agent == agent
        assert r.task == task
        assert r.ok is True  # 默认成功


# ── 7. evolve AgentKeyStats 字段组合参数化 ─────────────────


class TestAgentKeyStatsParametrized:
    """参数化测试 AgentKeyStats 的字段组合。"""

    @pytest.mark.parametrize(
        "agent,routing_key,total,success",
        [
            ("claude", "codegen", 10, 8),
            ("kimi", "search", 5, 3),
            ("codex", "review", 20, 15),
            ("gpt-4", "analyze", 100, 90),
        ],
        ids=["claude-codegen", "kimi-search", "codex-review", "gpt4-analyze"],
    )
    def test_agent_key_stats_construction(self, agent, routing_key, total, success):
        from maop.evolve import AgentKeyStats

        stats = AgentKeyStats(
            agent=agent, routing_key=routing_key, total=total, success=success,
        )
        assert stats.agent == agent
        assert stats.routing_key == routing_key
        assert stats.total == total
        assert stats.success == success


# ── 8. deploy SystemStatus status 参数化 ───────────────────


class TestSystemStatusStatusParametrized:
    """参数化测试 SystemStatus 的 status 字段。"""

    @pytest.mark.parametrize(
        "status_name",
        ["STOPPED", "STARTING", "RUNNING", "STOPPING", "ERROR"],
    )
    def test_system_status_with_status(self, status_name):
        from maop.deploy import ServiceStatus, SystemStatus

        s = SystemStatus(status=ServiceStatus[status_name])
        assert s.status == ServiceStatus[status_name]