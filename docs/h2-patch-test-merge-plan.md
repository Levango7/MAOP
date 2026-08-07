# H2 补丁测试合并评估计划

> 评估日期：2026-08-07
> 评估范围：`py/tests/` 目录下所有 coverage 补丁测试文件
> 评估目的：识别可合并到主测试文件、保留独立、可删除的补丁测试文件

## 第1章 扫描概览

### 1.1 扫描统计

| 指标 | 数值 |
|------|------|
| 测试目录总文件数（test_*.py） | 221 |
| 识别为补丁测试文件数 | 48 |
| 其中 coverage 命名文件 | 44 |
| 其中 extended 命名文件 | 4 |
| patch 命名文件 | 0 |

### 1.2 识别规则

补丁测试文件通过以下特征识别：

- 文件名包含 `coverage`、`coverage2`、`coverage3`（round 2/3 覆盖率补充）
- 文件名包含 `extended`（主测试扩展文件）
- 文件 docstring 明确标注为 "Coverage tests" 或 "Coverage boost tests"

### 1.3 分类统计

| 分类 | 数量 | 占比 |
|------|------|------|
| 可合并 | 26 | 54.2% |
| 保留独立 | 22 | 45.8% |
| 可删除 | 0 | 0.0% |

> 说明：所有 48 个补丁测试文件均包含实质性测试逻辑（非空壳/僵尸文件），无可安全删除的文件。

## 第2章 详细评估表

### 2.1 可合并文件（26 个）

以下文件有明确对应的主测试文件，内容可安全合并。

| 序号 | 补丁测试文件 | 目标模块 | 对应主测试文件 | 行数 | 合并理由 |
|------|-------------|---------|--------------|------|---------|
| 1 | test_analyzer_coverage.py | maop.core.agent.analyzer | test_analyzer.py | 334 | 分支覆盖补充，与主测试无重复 |
| 2 | test_deploy_coverage.py | maop.deploy | test_deploy.py | 218 | validate_config/health_check/PID 补充 |
| 3 | test_dispatcher_coverage.py | maop.delegate.dispatcher | test_dispatcher.py | 422+ | retry/lazy import/priority queue 分支 |
| 4 | test_drivers_coverage.py | maop.delegate.drivers | test_drivers.py | 302 | 各 driver 边界/异常路径 |
| 5 | test_runtime_coverage.py | maop.core.agent.lifecycle.runtime | test_runtime.py | 135 | LocalRuntime/IsolatedRuntime/ContainerRuntime |
| 6 | test_evolve_coverage3.py | maop.evolve | test_evolve.py | 353+ | apply/promote fallback/auto_evolve legacy |
| 7 | test_maop_loop_coverage3.py | maop.maop_loop | test_maop_loop.py | 255 | init/llm_factory/inject_memory/run 分支 |
| 8 | test_maop_execute_coverage3.py | maop.maop_execute | test_maop_execute.py | 447+ | ReAct/permission/hook/guardrail/function-call |
| 9 | test_react_loop_coverage3.py | maop.core.agent.llm_chat.react_loop | test_react_loop.py | 389+ | provider_factory/trim/call_llm/run 分支 |
| 10 | test_tool_manager_coverage3.py | maop.core.agent.tools.tool_manager | test_tool_manager.py | 384 | version parse/call exceptions/call_sync fallback |
| 11 | test_three_layer_memory_coverage.py | maop.core.memory.three_layer_memory | test_three_layer_memory.py | 209 | working/episodic/semantic 存取（含模块导入测试） |
| 12 | test_three_layer_memory_coverage3.py | maop.core.memory.three_layer_memory | test_three_layer_memory.py | 369+ | FTS5 fallback/feedback/consolidate/transform |
| 13 | test_router_control_coverage.py | maop.dashboard.routers.control | test_router_control.py | 245 | POST action endpoints 全覆盖 |
| 14 | test_router_model_coverage.py | maop.dashboard.routers.model | test_router_model.py | 302 | POST endpoints + admin-gated GETs |
| 15 | test_router_system_coverage2.py | maop.dashboard.routers.system | test_router_system.py | 323 | write endpoints + admin-gated GETs |
| 16 | test_worker_pool_coverage.py | maop.core.reliability.worker_pool | test_worker_pool.py | 232 | lifecycle/submit/wait/stats |
| 17 | test_config_mutator_coverage.py | maop.core.reliability.config_mutator | test_config_mutator_whitebox.py | 261 | mutation handlers 错误路径 |
| 18 | test_pg_persist_coverage.py | maop.enterprise.pg_persist | test_enterprise_pg_persist.py | 305 | backend-available SQL 执行路径 |
| 19 | test_queue_worker_coverage.py | maop.worker.queue_worker | test_worker.py | 285 | dispatch/maintenance/consume loop 分支 |
| 20 | test_data_proxy_coverage3.py | maop.dashboard.data_proxy | test_data_proxy_coverage.py | 828 | 异常/YAML/日志/DB 深层分支 |
| 21 | test_memory_manager_search_coverage3.py | maop.memory.manager + maop.memory.search | test_unified_memory_search.py | 570 | consolidate/FTS5/regex/vector supplement |
| 22 | test_core_coverage2.py | maop.core.monitoring.otel + maop.core.evolution.regression | test_otel.py + test_regression.py | 179 | OTel disabled 分支 + Regression runner |
| 23 | test_maop_loop_extended.py | maop.maop_loop | test_maop_loop.py | 332 | feedback loop/guardrail/verify states |
| 24 | test_guardrail_extended.py | maop.core.security.guardrail | test_guardrail.py | 185 | 所有 rule types/edge cases/persistence |
| 25 | test_dispatcher_extended.py | maop.delegate.dispatcher | test_dispatcher.py | 399 | guardrail integration/driver registry/model resolution |
| 26 | test_engine_extended.py | maop.engine | test_engine.py | 199 | topological sort/DAG/conditions/decomposition |

### 2.2 保留独立文件（22 个）

以下文件测试逻辑独立且有价值，不适合合并。

| 序号 | 补丁测试文件 | 目标模块 | 对应主测试 | 行数 | 保留理由 |
|------|-------------|---------|-----------|------|---------|
| 1 | test_coverage_boost.py | server + system + agent_executor | 无（多模块） | 269 | 跨模块覆盖率提升，无单一主测试 |
| 2 | test_core_modules_coverage.py | tls + tool_schema + skill_version + sandbox | 部分对应 test_tls.py | 361 | 4 个独立 core 模块组合测试 |
| 3 | test_core_modules2_coverage.py | tool_manager + memory + vector + provider_health + subagent_db | 部分对应多主测试 | 307 | 6 个独立模块组合测试 |
| 4 | test_config_plugin_cli_coverage.py | config_mutator + plugin + cli + maop_execute + preemptable_worker_pool + message_queue | 无（多模块） | 360 | 6 个模块组合测试 |
| 5 | test_data_proxy_coverage.py | data_proxy + routers/data + routers/system + routers/agents | 无（多模块） | 223 | 4 个 dashboard 模块组合测试 |
| 6 | test_evolve_loop_coverage.py | evolve + maop_loop + evolution_loop + react_loop + llm_provider | 无（多模块） | 139 | 5 个模块组合测试 |
| 7 | test_mem_vector_otel_saml_coverage.py | memory/search + memory/manager + vector + otel + saml_handler | 无（多模块） | 764 | 5 个模块组合测试 |
| 8 | test_preemptable_worker_pool_coverage3.py | maop.core.reliability.preemptable_worker_pool | 无主测试 | 298 | 无对应主测试文件，独立有价值 |
| 9 | test_router_agents_coverage.py | maop.dashboard.routers.agents | 无主测试 | 462 | 23 个 Agent Platform API 端点全覆盖 |
| 10 | test_router_auth_coverage.py | maop.dashboard.routers.auth | 无主测试 | 332 | login/logout/register/users CRUD 全覆盖 |
| 11 | test_router_hook_protocol_coverage.py | hook + protocol routers | 无主测试 | 343 | 两个 router 组合测试 |
| 12 | test_router_mcp_evolve_memory_coverage.py | mcp + evolve + memory routers | 无主测试 | 363 | 三个 router 组合测试 |
| 13 | test_router_misc_coverage.py | tenant + subagent + plugin + react + permission + routing_preview routers | 无主测试 | 561 | 6 个 router 组合测试 |
| 14 | test_router_worktree_coverage.py | maop.dashboard.routers.worktree | 无主测试 | 226 | worktree CRUD 全覆盖 |
| 15 | test_routers_batch_coverage.py | 所有 dashboard routers（POST/PUT/DELETE） | 无（全局） | 571 | 批量写端点 smoke 测试，覆盖面广 |
| 16 | test_routers_smoke_coverage.py | 所有 dashboard routers（GET） | 无（全局） | 161 | 批量读端点 smoke 测试，覆盖面广 |
| 17 | test_server_coverage.py | maop.dashboard.server | 无主测试 | 247 | 顶层 FastAPI app 端点覆盖 |
| 18 | test_tool_mq_mcp_coverage.py | tool_manager + message_queue + mcp_hub + preemptable_worker_pool + maop_execute | 无（多模块） | 313 | 5 个模块组合测试 |
| 19 | test_loop_analyzer_coverage.py | maop.loop_analyzer | 无主测试 | 208 | LLM extraction prompt/parser，无对应主测试 |
| 20 | test_agent_proxy_coverage.py | maop.core.agent.delegation.agent_proxy | 无主测试 | 221 | AgentProxy registry/dispatcher，无对应主测试 |
| 21 | test_api_key_vault_coverage.py | maop.core.security.api_key_vault | 无主测试 | 142 | store/retrieve/delete/rotate，无对应主测试 |
| 22 | test_saml_handler_coverage3.py | maop.enterprise.saml_handler | 无主测试 | 523 | metadata/cert/signature/conditions/attribute |

### 2.3 可删除文件（0 个）

本次评估未发现可安全删除的文件。所有 48 个补丁测试文件均包含：

- 实质性测试断言（非空壳）
- 独立的测试类/方法结构
- 明确的测试目标模块
- 非完全重复的测试逻辑

> 注意：`test_three_layer_memory_coverage.py` 中包含 5 个低价值的模块导入测试（`TestLLMProvider`、`TestReactLoop`、`TestMaopLoop`、`TestEvolve`、`TestEvolutionLoop`，仅测试 `import` 是否成功），但文件主体 `TestThreeLayerMemory` 有 19 个实质性测试方法，整体不应删除。合并时建议丢弃这些导入测试。

> 注意：`test_config_plugin_cli_coverage.py` 中的 `TestConfigMutator` 类与 `test_config_mutator_coverage.py` 存在部分重复（`apply_suggestion_not_found`、`apply_suggestion_not_auto_applicable`、`apply_suggestion_already_applied`），但前者还覆盖 plugin/cli/preemptable_worker_pool/message_queue 等模块，整体不应删除。合并时建议去重 ConfigMutator 部分。

## 第3章 合并操作计划

### 3.1 合并原则

1. **逐文件合并**：每次只合并一个补丁文件到主测试文件，合并后运行测试验证
2. **保留 docstring**：将补丁文件的 docstring 作为合并部分的注释保留
3. **避免类名冲突**：合并时检查类名是否与主测试文件冲突，冲突时重命名
4. **保留 fixture**：补丁文件中的 fixture 若主测试文件已有同名 fixture，则复用而非重复定义
5. **运行验证**：每次合并后运行 `pytest <主测试文件> -v` 确认无回归

### 3.2 合并优先级分组

#### 3.2.1 P0 — 低风险合并（单模块、主测试文件明确）

| 批次 | 补丁文件 | 合并目标 | 风险 |
|------|---------|---------|------|
| 1 | test_analyzer_coverage.py | test_analyzer.py | 低 |
| 2 | test_deploy_coverage.py | test_deploy.py | 低 |
| 3 | test_runtime_coverage.py | test_runtime.py | 低 |
| 4 | test_drivers_coverage.py | test_drivers.py | 低 |
| 5 | test_worker_pool_coverage.py | test_worker_pool.py | 低 |
| 6 | test_engine_extended.py | test_engine.py | 低 |
| 7 | test_guardrail_extended.py | test_guardrail.py | 低 |

#### 3.2.2 P1 — 中风险合并（单模块、需检查类名冲突）

| 批次 | 补丁文件 | 合并目标 | 风险点 |
|------|---------|---------|--------|
| 8 | test_dispatcher_coverage.py | test_dispatcher.py | 类名需检查 |
| 9 | test_dispatcher_extended.py | test_dispatcher.py | 与批次 8 合并后需再次检查 |
| 10 | test_evolve_coverage3.py | test_evolve.py | 辅助函数需检查 |
| 11 | test_maop_loop_coverage3.py | test_maop_loop.py | 类名需检查 |
| 12 | test_maop_loop_extended.py | test_maop_loop.py | 与批次 11 合并后需检查 |
| 13 | test_maop_execute_coverage3.py | test_maop_execute.py | mock 辅助函数需检查 |
| 14 | test_react_loop_coverage3.py | test_react_loop.py | 辅助函数需检查 |
| 15 | test_tool_manager_coverage3.py | test_tool_manager.py | DB migration 测试需检查 |
| 16 | test_config_mutator_coverage.py | test_config_mutator_whitebox.py | 类名需检查 |
| 17 | test_queue_worker_coverage.py | test_worker.py | 模块差异需确认 |

#### 3.2.3 P2 — 中风险合并（router 类、需检查 fixture 冲突）

| 批次 | 补丁文件 | 合并目标 | 风险点 |
|------|---------|---------|--------|
| 18 | test_router_control_coverage.py | test_router_control.py | fixture/client 需检查 |
| 19 | test_router_model_coverage.py | test_router_model.py | fixture/model_env 需检查 |
| 20 | test_router_system_coverage2.py | test_router_system.py | fixture/system_env 需检查 |

#### 3.2.4 P3 — 较高风险合并（多目标或特殊处理）

| 批次 | 补丁文件 | 合并目标 | 风险点 |
|------|---------|---------|--------|
| 21 | test_three_layer_memory_coverage.py | test_three_layer_memory.py | 丢弃 5 个模块导入测试 |
| 22 | test_three_layer_memory_coverage3.py | test_three_layer_memory.py | 与批次 21 合并后需检查 |
| 23 | test_pg_persist_coverage.py | test_enterprise_pg_persist.py | enterprise fixture 需检查 |
| 24 | test_data_proxy_coverage3.py | test_data_proxy_coverage.py | 保留独立文件作为合并目标 |
| 25 | test_memory_manager_search_coverage3.py | test_unified_memory_search.py | 模块映射需确认 |
| 26 | test_core_coverage2.py | test_otel.py + test_regression.py | 拆分为两部分合并 |

### 3.3 合并步骤模板

每个文件合并遵循以下步骤：

1. 读取补丁文件和主测试文件的完整内容
2. 检查类名冲突：`grep "class Test" <主测试文件>` vs `grep "class Test" <补丁文件>`
3. 检查 fixture 冲突：`grep "@pytest.fixture" <主测试文件>` vs `grep "@pytest.fixture" <补丁文件>`
4. 检查辅助函数冲突：`grep "^def " <主测试文件>` vs `grep "^def " <补丁文件>`
5. 将补丁文件的测试类追加到主测试文件末尾（添加分隔注释）
6. 合并必要的 import 语句
7. 运行 `pytest <主测试文件> -v --tb=short` 验证
8. 验证通过后删除补丁文件
9. 运行 `pytest <主测试文件> -v --tb=short` 再次验证

### 3.4 风险点汇总

| 风险类别 | 涉及文件数 | 说明 |
|---------|-----------|------|
| 类名冲突 | ~15 | TestRetryWithBackoff 等类名可能在主测试中已存在 |
| Fixture 冲突 | ~6 | router 类测试的 client/auth_env fixture 可能冲突 |
| 辅助函数冲突 | ~8 | _agent_config/_ok_result 等辅助函数可能重名 |
| Import 冲突 | ~5 | 补丁文件可能有主测试文件未导入的模块 |
| 多目标合并 | 1 | test_core_coverage2.py 需拆分到两个主测试文件 |
| 低价值测试丢弃 | 1 | test_three_layer_memory_coverage.py 的 5 个导入测试 |
| 部分重复去重 | 1 | test_config_plugin_cli_coverage.py 的 ConfigMutator 部分 |

## 第4章 删除清单

本次评估未发现可安全删除的文件。

所有补丁测试文件均通过以下质量检查：

- [x] 文件非空且包含测试类/方法
- [x] 包含实质性断言（assert 语句）
- [x] 有明确的测试目标模块（docstring 或 import 声明）
- [x] 非完全重复（每个文件至少有 1 个主测试文件中不存在的测试方法）

## 第5章 保留清单

以下 22 个文件建议保留独立，不执行合并操作。

### 5.1 多模块组合测试（18 个）

这些文件测试多个模块的组合行为，无法合并到单一主测试文件：

1. **test_coverage_boost.py** — server + system + agent_executor 跨模块覆盖率提升
2. **test_core_modules_coverage.py** — tls + tool_schema + skill_version + sandbox
3. **test_core_modules2_coverage.py** — tool_manager + memory + vector + provider_health + subagent_db
4. **test_config_plugin_cli_coverage.py** — config_mutator + plugin + cli + maop_execute + preemptable_worker_pool + message_queue
5. **test_data_proxy_coverage.py** — data_proxy + routers/data + routers/system + routers/agents
6. **test_evolve_loop_coverage.py** — evolve + maop_loop + evolution_loop + react_loop + llm_provider
7. **test_mem_vector_otel_saml_coverage.py** — memory/search + memory/manager + vector + otel + saml_handler
8. **test_preemptable_worker_pool_coverage3.py** — preemptable_worker_pool（无对应主测试）
9. **test_router_agents_coverage.py** — routers/agents（23 个 API 端点）
10. **test_router_auth_coverage.py** — routers/auth（login/logout/register/users CRUD）
11. **test_router_hook_protocol_coverage.py** — hook + protocol routers
12. **test_router_mcp_evolve_memory_coverage.py** — mcp + evolve + memory routers
13. **test_router_misc_coverage.py** — tenant + subagent + plugin + react + permission + routing_preview routers
14. **test_router_worktree_coverage.py** — routers/worktree（CRUD 全覆盖）
15. **test_routers_batch_coverage.py** — 所有 routers POST/PUT/DELETE 批量 smoke 测试
16. **test_routers_smoke_coverage.py** — 所有 routers GET 批量 smoke 测试
17. **test_server_coverage.py** — dashboard/server 顶层 FastAPI app
18. **test_tool_mq_mcp_coverage.py** — tool_manager + message_queue + mcp_hub + preemptable_worker_pool + maop_execute

### 5.2 无对应主测试的独立模块测试（4 个）

这些文件测试的模块在 `py/tests/` 中没有对应的主测试文件：

19. **test_loop_analyzer_coverage.py** — maop.loop_analyzer（LLM extraction prompt/parser）
20. **test_agent_proxy_coverage.py** — maop.core.agent.delegation.agent_proxy（AgentProxy registry/dispatcher）
21. **test_api_key_vault_coverage.py** — maop.core.security.api_key_vault（store/retrieve/delete/rotate）
22. **test_saml_handler_coverage3.py** — maop.enterprise.saml_handler（metadata/cert/signature/conditions）

### 5.3 保留建议

对于保留独立的文件，建议后续考虑以下优化方向：

- **创建主测试文件**：为无对应主测试的模块（loop_analyzer、agent_proxy、api_key_vault、saml_handler）创建正式的主测试文件，然后将 coverage 文件合并进去
- **拆分多模块文件**：将多模块组合测试文件拆分为单模块测试文件，提升可维护性
- **重命名规范**：保留独立的文件可考虑去除 `coverage`/`coverage3` 后缀，改为更清晰的命名（如 `test_router_agents_api.py`）

## 第6章 执行建议

### 6.1 执行顺序

建议按 P0 → P1 → P2 → P3 的优先级顺序执行合并，每批合并完成后运行完整测试套件验证：

```
pytest py/tests/ -v --tb=short -x
```

### 6.2 验证检查点

每个合并批次完成后，检查以下指标：

- 合并后的主测试文件全部通过
- 总测试用例数未减少
- 覆盖率未下降（可运行 `pytest --cov=maop` 对比）
- 无 import 错误或 fixture 冲突

### 6.3 回滚策略

若合并后测试失败且无法快速修复：

1. `git checkout <主测试文件>` 恢复主测试文件
2. `git checkout <补丁文件>` 恢复补丁文件
3. 记录失败原因，调整合并策略后重试

### 6.4 预期收益

| 指标 | 合并前 | 合并后（预期） | 变化 |
|------|--------|--------------|------|
| 测试文件总数 | 221 | 195 | -26 |
| 补丁测试文件数 | 48 | 22 | -26 |
| 主测试文件平均行数 | ~150 | ~250 | +100 |
| 测试可发现性 | 低（分散） | 高（集中） | 提升 |

合并后收益：

- 减少文件数量，降低维护开销
- 相关测试集中管理，提升可发现性
- 消除 coverage 补丁的临时性命名，统一测试规范
- 保留独立的 22 个文件仍有明确价值，不影响覆盖率