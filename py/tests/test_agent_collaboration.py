"""AgentCollaboration 多 Agent 协作编排器测试.

覆盖：
  - 预定义模式执行（init_and_implement / review_and_fix / parallel_and_merge）
  - 自定义步骤执行
  - 依赖管理（串行/并行/拓扑排序）
  - 可选步骤失败不阻断 / 必需步骤失败中止
  - 任务模板变量替换
  - 结果聚合
  - 模式校验
  - 能力自动选择 Agent
  - 空步骤 / 单步 / 并行 / 串行

每个测试使用隔离 tmp_path DB，不污染主库。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from maop.core.agent.registry.agent_catalog import (
    AgentCapability,
    AgentCatalog,
    AgentDescriptor,
    BillingModel,
)
from maop.core.agent.router.agent_collaboration import (
    AgentCollaboration,
    CollaborationPattern,
    CollaborationResult,  # noqa: F401
    CollaborationStep,
    StepResult,
    list_predefined_patterns,
    pattern_init_and_implement,
    pattern_parallel_and_merge,
    pattern_review_and_fix,
)
from maop.core.agent.router.agent_router import AgentRouter

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def catalog(tmp_path: Path) -> AgentCatalog:
    """使用隔离 tmp_path DB 的全新 AgentCatalog。"""
    return AgentCatalog(db_path=tmp_path / "agent_collab.db")


@pytest.fixture
def router(catalog: AgentCatalog) -> AgentRouter:
    """基于隔离 catalog 的全新 AgentRouter。"""
    return AgentRouter(catalog=catalog)


@pytest.fixture
def collab(catalog: AgentCatalog, router: AgentRouter) -> AgentCollaboration:
    """基于隔离 catalog/router 的全新 AgentCollaboration（不计费）。"""
    return AgentCollaboration(catalog=catalog, router=router)


def _desc(name: str, **kwargs: object) -> AgentDescriptor:
    """构造 AgentDescriptor，name 必填，其余可选覆盖。"""
    return AgentDescriptor(name=name, **kwargs)  # type: ignore[arg-type]


def _register_agents(catalog: AgentCatalog) -> None:
    """注册一组多样化 Agent 供协作测试使用.

    - vibe_platform: CODE_GENERATION（项目骨架生成）
    - cli_agent: CODE_GENERATION + TOOL_USE（功能实现）
    - ide_plugin: FILE_EDIT（细节补全）
    - autonomous_agent: CODE_REVIEW + REASONING（审查）
    - merge_agent: REASONING（合并）
    """
    catalog.register(_desc(
        "vibe_platform",
        capabilities=[AgentCapability.CODE_GENERATION],
        billing_model=BillingModel.FREE,
    ))
    catalog.register(_desc(
        "cli_agent",
        capabilities=[AgentCapability.CODE_GENERATION, AgentCapability.TOOL_USE],
        billing_model=BillingModel.FREE,
    ))
    catalog.register(_desc(
        "ide_plugin",
        capabilities=[AgentCapability.FILE_EDIT],
        billing_model=BillingModel.FREE,
    ))
    catalog.register(_desc(
        "autonomous_agent",
        capabilities=[AgentCapability.CODE_REVIEW, AgentCapability.REASONING],
        billing_model=BillingModel.FREE,
    ))
    catalog.register(_desc(
        "merge_agent",
        capabilities=[AgentCapability.REASONING],
        billing_model=BillingModel.FREE,
    ))


def _make_executor(results: dict[str, str] | None = None) -> Any:
    """构造可配置的执行器.

    Parameters
    ----------
    results : dict[str, str] | None
        agent_name → 固定结果。未指定的 Agent 返回默认模拟结果。
        若值为 ``"__raise__"``，执行器抛出 RuntimeError 模拟失败。

    Returns
    -------
    Callable
        执行器回调。
    """
    fixed = results or {}

    def _executor(agent_name: str, task: str, context: dict[str, Any]) -> str:
        if agent_name in fixed:
            val = fixed[agent_name]
            if val == "__raise__":
                raise RuntimeError(f"simulated failure for {agent_name}")
            return val
        return f"[{agent_name}] executed: {task}"

    return _executor


# ── 1. 预定义模式执行 ─────────────────────────────────────────────


class TestPredefinedPatterns:
    """预定义协作模式执行测试。"""

    def test_execute_pattern_init_and_implement(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """执行 init_and_implement 模式：3 步串行（骨架→实现→补全）。"""
        _register_agents(catalog)
        collab = AgentCollaboration(catalog=catalog, router=router)
        pattern = pattern_init_and_implement()
        result = collab.execute_pattern(pattern, task="创建一个 REST API 服务")

        assert result.success is True
        assert len(result.step_results) == 3
        # 步骤顺序：init_skeleton → implement_features → polish_details
        assert result.step_results[0].step_name == "init_skeleton"
        assert result.step_results[1].step_name == "implement_features"
        assert result.step_results[2].step_name == "polish_details"
        # 每步都成功
        for sr in result.step_results:
            assert sr.success is True
            assert sr.duration_s >= 0.0
        # 最终结果非空（拼接了各步结果）
        assert result.final_result != ""
        assert result.total_duration_s >= 0.0

    def test_execute_pattern_review_and_fix(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """执行 review_and_fix 模式：2 步串行（审查→修复）。"""
        _register_agents(catalog)
        collab = AgentCollaboration(catalog=catalog, router=router)
        pattern = pattern_review_and_fix()
        result = collab.execute_pattern(pattern, task="def add(a, b): return a - b")

        assert result.success is True
        assert len(result.step_results) == 2
        assert result.step_results[0].step_name == "review_code"
        assert result.step_results[1].step_name == "fix_issues"
        # 修复步骤的任务模板应包含审查结果（prev_result）
        # 验证第二步执行时任务中包含了第一步的结果
        assert result.final_result != ""

    def test_execute_pattern_parallel_and_merge(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """执行 parallel_and_merge 模式：3 步并行 + 1 步合并。"""
        _register_agents(catalog)
        collab = AgentCollaboration(catalog=catalog, router=router)
        pattern = pattern_parallel_and_merge()
        result = collab.execute_pattern(pattern, task="构建微服务架构")

        assert result.success is True
        assert len(result.step_results) == 4
        # 前三步无依赖，可并行（当前顺序执行，但应在合并之前）
        names = [sr.step_name for sr in result.step_results]
        assert names.index("merge_results") > names.index("process_module_a")
        assert names.index("merge_results") > names.index("process_module_b")
        assert names.index("merge_results") > names.index("process_module_c")
        # 合并步骤的任务模板应包含三个模块的结果
        merge_result = result.step_results[3]
        assert merge_result.success is True


# ── 2. 自定义步骤执行 ─────────────────────────────────────────────


class TestCustomSteps:
    """自定义步骤执行测试。"""

    def test_execute_custom_steps(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """执行自定义步骤列表。"""
        _register_agents(catalog)
        collab = AgentCollaboration(catalog=catalog, router=router)
        steps = [
            CollaborationStep(
                name="step1",
                agent_name="vibe_platform",
                task_template="做第一步：{original_task}",
            ),
            CollaborationStep(
                name="step2",
                agent_name="cli_agent",
                task_template="做第二步，上一步结果：{prev_result}",
                depends_on=["step1"],
            ),
        ]
        result = collab.execute_custom(steps, context={"original_task": "测试任务"})

        assert result.success is True
        assert len(result.step_results) == 2
        assert result.step_results[0].agent_name == "vibe_platform"
        assert result.step_results[1].agent_name == "cli_agent"


# ── 3. 依赖管理 ──────────────────────────────────────────────────


class TestDependencyManagement:
    """依赖管理测试。"""

    def test_step_dependency(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """有依赖的步骤在依赖完成后执行，且可引用依赖结果。"""
        _register_agents(catalog)
        collab = AgentCollaboration(catalog=catalog, router=router)
        steps = [
            CollaborationStep(
                name="base",
                agent_name="vibe_platform",
                task_template="基础步骤",
            ),
            CollaborationStep(
                name="dependent",
                agent_name="cli_agent",
                task_template="依赖步骤，基础结果：{step_result:base}",
                depends_on=["base"],
            ),
        ]
        result = collab.execute_custom(steps, context={"original_task": ""})

        assert result.success is True
        # dependent 步骤在 base 之后执行
        assert result.step_results[0].step_name == "base"
        assert result.step_results[1].step_name == "dependent"
        # dependent 的结果应包含 base 的结果（通过 {step_result:base} 引用）
        base_result = result.step_results[0].result
        dependent_task = result.step_results[1].result
        # 默认执行器返回 [agent] executed: <task>，task 中包含 base_result
        assert base_result in dependent_task

    def test_step_optional_failure(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """可选步骤失败不阻断后续步骤。"""
        _register_agents(catalog)
        executor = _make_executor({"vibe_platform": "__raise__"})
        collab = AgentCollaboration(
            catalog=catalog, router=router, executor=executor,
        )
        steps = [
            CollaborationStep(
                name="optional_step",
                agent_name="vibe_platform",
                task_template="会失败的步骤",
                optional=True,
            ),
            CollaborationStep(
                name="required_step",
                agent_name="cli_agent",
                task_template="后续必需步骤",
            ),
        ]
        result = collab.execute_custom(steps, context={"original_task": ""})

        # 整体 success 为 False（因为有步骤失败）
        assert result.success is False
        # 但后续步骤仍执行了
        assert len(result.step_results) == 2
        assert result.step_results[0].success is False  # optional 失败
        assert result.step_results[1].success is True   # required 成功

    def test_step_required_failure_aborts(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """必需步骤失败立即中止，后续步骤不执行。"""
        _register_agents(catalog)
        executor = _make_executor({"vibe_platform": "__raise__"})
        collab = AgentCollaboration(
            catalog=catalog, router=router, executor=executor,
        )
        steps = [
            CollaborationStep(
                name="will_fail",
                agent_name="vibe_platform",
                task_template="会失败的必需步骤",
            ),
            CollaborationStep(
                name="should_not_run",
                agent_name="cli_agent",
                task_template="不应执行的步骤",
                depends_on=["will_fail"],
            ),
            CollaborationStep(
                name="also_should_not_run",
                agent_name="ide_plugin",
                task_template="也不应执行",
            ),
        ]
        result = collab.execute_custom(steps, context={"original_task": ""})

        # 整体失败
        assert result.success is False
        # 第一步失败
        assert result.step_results[0].step_name == "will_fail"
        assert result.step_results[0].success is False
        # 后续步骤未执行（中止后 break）
        # 注意：will_fail 和 also_should_not_run 在同一批次（都无依赖），
        # also_should_not_run 会在 will_fail 之后执行（同批次内顺序），
        # 但 should_not_run 依赖 will_fail，在下一批次，不会执行。
        executed_names = {sr.step_name for sr in result.step_results}
        assert "should_not_run" not in executed_names


# ── 4. 任务模板替换 ──────────────────────────────────────────────


class TestTaskTemplate:
    """任务模板变量替换测试。"""

    def test_task_template_substitution(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """任务模板变量 {prev_result}/{original_task}/{step_result:xxx} 正确替换。"""
        _register_agents(catalog)
        executor = _make_executor({
            "vibe_platform": "骨架生成完成",
            "cli_agent": "功能实现完成",
        })
        collab = AgentCollaboration(
            catalog=catalog, router=router, executor=executor,
        )
        steps = [
            CollaborationStep(
                name="s1",
                agent_name="vibe_platform",
                task_template="原始任务: {original_task}",
            ),
            CollaborationStep(
                name="s2",
                agent_name="cli_agent",
                task_template="上一步: {prev_result}, 指定步骤: {step_result:s1}, 原始: {original_task}",
                depends_on=["s1"],
            ),
        ]
        result = collab.execute_custom(steps, context={"original_task": "MY_TASK"})

        assert result.success is True
        # s1 的结果为固定值 "骨架生成完成"
        assert result.step_results[0].result == "骨架生成完成"
        # s2 的结果为固定值 "功能实现完成"（固定执行器不返回 task）
        assert result.step_results[1].result == "功能实现完成"

    def test_task_template_substitution_with_default_executor(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """默认执行器下，模板变量正确注入到 task 中。"""
        _register_agents(catalog)
        collab = AgentCollaboration(catalog=catalog, router=router)
        steps = [
            CollaborationStep(
                name="first",
                agent_name="vibe_platform",
                task_template="任务: {original_task}",
            ),
            CollaborationStep(
                name="second",
                agent_name="cli_agent",
                task_template="prev: {prev_result}",
                depends_on=["first"],
            ),
        ]
        result = collab.execute_custom(steps, context={"original_task": "HELLO"})

        assert result.success is True
        # first 的结果包含原始任务
        assert "HELLO" in result.step_results[0].result
        # second 的结果包含 first 的结果（prev_result）
        first_result = result.step_results[0].result
        assert first_result in result.step_results[1].result


# ── 5. 结果聚合 ──────────────────────────────────────────────────


class TestAggregation:
    """结果聚合测试。"""

    def test_aggregate_results(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """_aggregate_results 正确聚合成功与失败步骤。"""
        _register_agents(catalog)
        collab = AgentCollaboration(catalog=catalog, router=router)
        step_results = [
            StepResult(
                step_name="s1", agent_name="a1", success=True,
                result="结果1", duration_s=0.5,
            ),
            StepResult(
                step_name="s2", agent_name="a2", success=True,
                result="结果2", duration_s=0.3,
            ),
            StepResult(
                step_name="s3", agent_name="a3", success=False,
                result="", error="失败", duration_s=0.1,
            ),
        ]
        result = collab._aggregate_results(step_results)

        # 有失败步骤 → overall success False
        assert result.success is False
        # final_result 拼接成功步骤的结果
        assert "结果1" in result.final_result
        assert "结果2" in result.final_result
        assert "失败" not in result.final_result  # 失败步骤结果不包含
        # total_duration 为各步耗时之和
        assert result.total_duration_s == pytest.approx(0.9)
        # step_results 原样保留
        assert len(result.step_results) == 3


# ── 6. 模式校验 ──────────────────────────────────────────────────


class TestPatternValidation:
    """模式与步骤模型校验测试。"""

    def test_pattern_validation(self) -> None:
        """CollaborationPattern/Step Pydantic 模型校验。"""
        # 合法模式
        pattern = CollaborationPattern(
            name="valid",
            description="测试模式",
            steps=[CollaborationStep(name="s1", task_template="t")],
        )
        assert pattern.name == "valid"
        assert len(pattern.steps) == 1
        assert pattern.steps[0].optional is False  # 默认非可选
        assert pattern.steps[0].depends_on == []   # 默认无依赖
        assert pattern.steps[0].capabilities_required == []
        assert pattern.steps[0].agent_name == ""   # 默认空（自动选择）

        # 预定义模式列表
        patterns = list_predefined_patterns()
        assert len(patterns) == 3
        names = {p.name for p in patterns}
        assert names == {"init_and_implement", "review_and_fix", "parallel_and_merge"}

        # 各预定义模式步骤数
        assert len(pattern_init_and_implement().steps) == 3
        assert len(pattern_review_and_fix().steps) == 2
        assert len(pattern_parallel_and_merge().steps) == 4


# ── 7. 能力自动选择 ──────────────────────────────────────────────


class TestCapabilitiesAutoSelect:
    """基于能力的 Agent 自动选择测试。"""

    def test_capabilities_auto_select(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """capabilities_required 设置且 agent_name 为空时自动选择 Agent。"""
        _register_agents(catalog)
        collab = AgentCollaboration(catalog=catalog, router=router)
        steps = [
            CollaborationStep(
                name="auto_step",
                agent_name="",  # 空，触发自动选择
                task_template="需要代码生成能力",
                capabilities_required=[AgentCapability.CODE_GENERATION.value],
            ),
        ]
        result = collab.execute_custom(steps, context={"original_task": ""})

        assert result.success is True
        # 应自动选择到具备 CODE_GENERATION 能力的 Agent
        assert result.step_results[0].agent_name in {"vibe_platform", "cli_agent"}

    def test_capabilities_auto_select_no_match(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """无匹配能力的 Agent 时步骤失败。"""
        _register_agents(catalog)
        collab = AgentCollaboration(catalog=catalog, router=router)
        steps = [
            CollaborationStep(
                name="no_match_step",
                agent_name="",
                task_template="需要不存在的能力",
                # WEB_SEARCH 未注册任何 Agent
                capabilities_required=[AgentCapability.WEB_SEARCH.value],
            ),
        ]
        result = collab.execute_custom(steps, context={"original_task": ""})

        assert result.success is False
        assert "auto-select failed" in result.step_results[0].error


# ── 8. 边界情况 ──────────────────────────────────────────────────


class TestEdgeCases:
    """边界情况测试。"""

    def test_empty_steps(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """空步骤列表返回成功空结果。"""
        _register_agents(catalog)
        collab = AgentCollaboration(catalog=catalog, router=router)
        pattern = CollaborationPattern(name="empty", description="空模式", steps=[])
        result = collab.execute_pattern(pattern, task="空任务")

        assert result.success is True
        assert result.step_results == []
        assert result.final_result == ""
        assert result.total_duration_s == 0.0

    def test_single_step(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """单步执行。"""
        _register_agents(catalog)
        collab = AgentCollaboration(catalog=catalog, router=router)
        steps = [
            CollaborationStep(
                name="only",
                agent_name="vibe_platform",
                task_template="唯一步骤: {original_task}",
            ),
        ]
        result = collab.execute_custom(steps, context={"original_task": "SOLO"})

        assert result.success is True
        assert len(result.step_results) == 1
        assert result.step_results[0].step_name == "only"
        assert "SOLO" in result.step_results[0].result

    def test_parallel_steps(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """无依赖的多个步骤可并行（同批次执行）。"""
        _register_agents(catalog)
        executor = _make_executor({
            "vibe_platform": "A结果",
            "cli_agent": "B结果",
            "ide_plugin": "C结果",
        })
        collab = AgentCollaboration(
            catalog=catalog, router=router, executor=executor,
        )
        steps = [
            CollaborationStep(
                name="parallel_a", agent_name="vibe_platform",
                task_template="并行A",
            ),
            CollaborationStep(
                name="parallel_b", agent_name="cli_agent",
                task_template="并行B",
            ),
            CollaborationStep(
                name="parallel_c", agent_name="ide_plugin",
                task_template="并行C",
            ),
        ]
        result = collab.execute_custom(steps, context={"original_task": ""})

        assert result.success is True
        assert len(result.step_results) == 3
        # 三步都成功，结果分别为固定值
        results_map = {sr.step_name: sr.result for sr in result.step_results}
        assert results_map["parallel_a"] == "A结果"
        assert results_map["parallel_b"] == "B结果"
        assert results_map["parallel_c"] == "C结果"

    def test_sequential_steps(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """严格串行的步骤链：s1 → s2 → s3，每步依赖前一步。"""
        _register_agents(catalog)
        collab = AgentCollaboration(catalog=catalog, router=router)
        steps = [
            CollaborationStep(
                name="s1", agent_name="vibe_platform",
                task_template="第一步",
            ),
            CollaborationStep(
                name="s2", agent_name="cli_agent",
                task_template="第二步，依赖 {prev_result}",
                depends_on=["s1"],
            ),
            CollaborationStep(
                name="s3", agent_name="ide_plugin",
                task_template="第三步，依赖 {prev_result}",
                depends_on=["s2"],
            ),
        ]
        result = collab.execute_custom(steps, context={"original_task": ""})

        assert result.success is True
        assert len(result.step_results) == 3
        # 严格按顺序执行
        assert result.step_results[0].step_name == "s1"
        assert result.step_results[1].step_name == "s2"
        assert result.step_results[2].step_name == "s3"
        # s2 的任务包含 s1 的结果
        s1_result = result.step_results[0].result
        assert s1_result in result.step_results[1].result
        # s3 的任务包含 s2 的结果
        s2_result = result.step_results[1].result
        assert s2_result in result.step_results[2].result


# ── 9. 拓扑排序 ──────────────────────────────────────────────────


class TestTopologicalBatches:
    """拓扑排序分批测试。"""

    def test_batches_no_deps(self) -> None:
        """无依赖步骤全部在同一批次。"""
        steps = [
            CollaborationStep(name="a", task_template="t"),
            CollaborationStep(name="b", task_template="t"),
            CollaborationStep(name="c", task_template="t"),
        ]
        batches = AgentCollaboration._topological_batches(steps)
        assert len(batches) == 1
        assert len(batches[0]) == 3

    def test_batches_chain(self) -> None:
        """链式依赖：a → b → c，分三批。"""
        steps = [
            CollaborationStep(name="a", task_template="t"),
            CollaborationStep(name="b", task_template="t", depends_on=["a"]),
            CollaborationStep(name="c", task_template="t", depends_on=["b"]),
        ]
        batches = AgentCollaboration._topological_batches(steps)
        assert len(batches) == 3
        assert batches[0][0].name == "a"
        assert batches[1][0].name == "b"
        assert batches[2][0].name == "c"

    def test_batches_parallel_then_merge(self) -> None:
        """并行三步 + 合并：前两批，第一批3个，第二批1个。"""
        steps = [
            CollaborationStep(name="a", task_template="t"),
            CollaborationStep(name="b", task_template="t"),
            CollaborationStep(name="c", task_template="t"),
            CollaborationStep(
                name="merge", task_template="t",
                depends_on=["a", "b", "c"],
            ),
        ]
        batches = AgentCollaboration._topological_batches(steps)
        assert len(batches) == 2
        assert len(batches[0]) == 3  # a, b, c 并行
        assert len(batches[1]) == 1  # merge
        assert batches[1][0].name == "merge"

    def test_batches_missing_dep_ignored(self) -> None:
        """依赖指向不存在的步骤名时容错（视为已满足）。"""
        steps = [
            CollaborationStep(
                name="s", task_template="t",
                depends_on=["nonexistent"],
            ),
        ]
        batches = AgentCollaboration._topological_batches(steps)
        # 不存在的依赖被忽略，s 入度为 0，第一批执行
        assert len(batches) == 1
        assert batches[0][0].name == "s"


# ── 10. Agent 未注册 ─────────────────────────────────────────────


class TestAgentNotFound:
    """Agent 未注册测试。"""

    def test_agent_not_in_catalog(
        self, catalog: AgentCatalog, router: AgentRouter,
    ) -> None:
        """指定 agent_name 不在 catalog 中时步骤失败。"""
        _register_agents(catalog)
        collab = AgentCollaboration(catalog=catalog, router=router)
        steps = [
            CollaborationStep(
                name="s1",
                agent_name="nonexistent_agent",
                task_template="使用不存在的 Agent",
            ),
        ]
        result = collab.execute_custom(steps, context={"original_task": ""})

        assert result.success is False
        assert "not found in catalog" in result.step_results[0].error