# H2 合并计划可行性核对报告

> 核对日期：2026-08-07
> 核对范围：H2 补丁测试合并计划中标记为"可合并"的 26 个文件
> 核对目的：在执行实际合并前，逐一验证每个可合并项是否真的安全
> 核对维度：类名冲突 / Fixture 冲突 / 辅助函数冲突 / Import 冲突 / 主测试文件存在性
> 关联文档：`docs/h2-patch-test-merge-plan.md`

## 第1章 核对方法

### 1.1 检查项定义

| 检查项 | 方法 | 判定标准 |
|--------|------|---------|
| 主测试文件存在性 | `Path.exists()` 检查 | 主测试文件必须存在于 `py/tests/` |
| 类名冲突 | 提取 `^class\s+(\w+)` 比对 | 补丁文件类名 ∩ 主测试文件类名 = ∅ |
| Fixture 冲突 | 提取 `@pytest.fixture` 装饰的函数名比对 | 同名 fixture 视为冲突（需复用而非重复定义） |
| 辅助函数冲突 | 提取顶层 `^def\s+(\w+)` 比对 | 同名顶层函数视为冲突 |
| Import 冲突 | 提取 `import` / `from` 模块名比对 | 补丁独有的非标准库 import 需补充到主测试文件 |

### 1.2 安全性评估级别

| 级别 | 含义 |
|------|------|
| `safe` | 无任何冲突，可直接合并 |
| `needs_handling` | 存在冲突或特殊处理要求，需先处理冲突再合并 |
| `not_recommended` | 主测试文件不存在或存在根本性障碍，不推荐合并 |

### 1.3 核对脚本

- 脚本路径：`F:\Nexus\MAOP\check_merge_conflicts.py`
- 输出方式：逐项打印详细冲突清单 + 汇总表 + 统计

## 第2章 文件存在性核对

所有 26 个补丁文件和 25 个主测试文件（第 22 项有两个目标文件）均存在于 `F:\Nexus\MAOP\py\tests\` 目录中。

| 类别 | 数量 | 存在性 |
|------|------|--------|
| 补丁文件 | 26 | 全部存在 |
| 主测试文件 | 25（第 22 项有 2 个目标） | 全部存在 |
| 合计 | 51 | 全部存在 |

> 结论：文件存在性检查 100% 通过，无缺失文件。

## 第3章 26 个可合并项详细核对表

### 3.1 项 01 — test_analyzer_coverage.py → test_analyzer.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 334 行 / 主 366 行） |
| 类名冲突 | **有** → `TestSelectStrategy` |
| Fixture 冲突 | 无 |
| 辅助函数冲突 | 无 |
| Import 冲突 | 需补充 `json`（标准库，影响小） |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | `TestSelectStrategy` 类名在主测试文件已存在，直接合并会导致类定义重复。需重命名为 `TestSelectStrategyCoverage` 或检查是否为相同测试逻辑（若是则丢弃补丁版本）。 |

### 3.2 项 02 — test_deploy_coverage.py → test_deploy.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 218 行 / 主 128 行） |
| 类名冲突 | **有** → `TestHealthCheck`, `TestPidManagement`, `TestValidateConfig` |
| Fixture 冲突 | 无 |
| 辅助函数冲突 | 无（补丁独有 `_create_valid_project`） |
| Import 冲突 | 需补充 `sqlite3` |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | 3 个类名冲突，需逐一比对测试方法是否重复。若重复则丢弃补丁版本中重复的测试方法，仅合并新增方法；若不重复则重命名类。 |

### 3.3 项 03 — test_dispatcher_coverage.py → test_dispatcher.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 429 行 / 主 212 行） |
| 类名冲突 | 无 |
| Fixture 冲突 | 无 |
| 辅助函数冲突 | 无（补丁独有 `_agent_config`, `_ok_result`） |
| Import 冲突 | 需补充 `maop.delegate.models` |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | 无直接冲突，但 `test_dispatcher.py` 是多补丁合并目标（项 03 + 项 25），需按顺序合并并每次重新检查冲突。 |

### 3.4 项 04 — test_drivers_coverage.py → test_drivers.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 302 行 / 主 128 行） |
| 类名冲突 | 无 |
| Fixture 冲突 | 无 |
| 辅助函数冲突 | **有** → `_config` |
| Import 冲突 | 需补充 `asyncio`, `inspect`, `json`, `unittest.mock` |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | `_config` 辅助函数在主测试文件已存在。需比对函数体是否相同：若相同则丢弃补丁版本；若不同则重命名为 `_config_coverage`。 |

### 3.5 项 05 — test_runtime_coverage.py → test_runtime.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 135 行 / 主 95 行） |
| 类名冲突 | **有** → `TestIsolatedRuntime`, `TestLocalRuntime`, `TestResolveCmd` |
| Fixture 冲突 | 无（主测试有 `runtime` fixture，补丁无） |
| 辅助函数冲突 | 无 |
| Import 冲突 | 无 |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | 3 个类名冲突。补丁文件 135 行 vs 主 95 行，可能补丁版本是更完整的覆盖。需逐一比对测试方法，合并新增方法到主类中，丢弃重复方法。 |

### 3.6 项 06 — test_evolve_coverage3.py → test_evolve.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 584 行 / 主 483 行） |
| 类名冲突 | 无 |
| Fixture 冲突 | 无（主测试有 `engine`, `evolve_root` fixture，补丁无） |
| 辅助函数冲突 | **有** → `_delegation`, `_write_delegations` |
| Import 冲突 | 需补充 `yaml` |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | 2 个辅助函数冲突。需比对函数体：若相同则丢弃补丁版本；若不同则重命名。`yaml` import 需补充到主测试文件。 |

### 3.7 项 07 — test_maop_loop_coverage3.py → test_maop_loop.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 255 行 / 主 325 行） |
| 类名冲突 | **有** → `TestMaopLoopInit` |
| Fixture 冲突 | 无 |
| 辅助函数冲突 | 无 |
| Import 冲突 | 需补充 `maop.config.loader` |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | `TestMaopLoopInit` 类名冲突。且 `test_maop_loop.py` 是多补丁合并目标（项 07 + 项 23），需顺序合并。 |

### 3.8 项 08 — test_maop_execute_coverage3.py → test_maop_execute.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 475 行 / 主 178 行） |
| 类名冲突 | 无 |
| Fixture 冲突 | 无（主测试有 `mock_dispatcher`, `mock_guardrail` fixture，补丁无） |
| 辅助函数冲突 | 无（补丁独有 `_allow_permission`, `_mock_streaming`, `_no_hooks`, `_pass_guardrail`） |
| Import 冲突 | 需补充 `json`（标准库） |
| 合并安全性评估 | **safe** |
| 潜在风险 | 无冲突。仅需补充 `json` import。 |

### 3.9 项 09 — test_react_loop_coverage3.py → test_react_loop.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 460 行 / 主 286 行） |
| 类名冲突 | 无 |
| Fixture 冲突 | 无 |
| 辅助函数冲突 | 无（补丁独有 `_mock_dispatch_result`） |
| Import 冲突 | 需补充 `json`, `pytest`, `unittest.mock`（均为标准库或测试框架） |
| 合并安全性评估 | **safe** |
| 潜在风险 | 无冲突。 |

### 3.10 项 10 — test_tool_manager_coverage3.py → test_tool_manager.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 384 行 / 主 278 行） |
| 类名冲突 | 无 |
| Fixture 冲突 | 无（主测试有 `mgr` fixture，补丁无） |
| 辅助函数冲突 | 无 |
| Import 冲突 | 需补充 `asyncio`, `builtins`, `maop.core.backends.db_utils`, `unittest.mock` |
| 合并安全性评估 | **safe** |
| 潜在风险 | 无冲突。需补充 `maop.core.backends.db_utils` 业务模块 import。 |

### 3.11 项 11 — test_three_layer_memory_coverage.py → test_three_layer_memory.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 209 行 / 主 511 行） |
| 类名冲突 | 无 |
| Fixture 冲突 | 无（主测试有 `mem_env` fixture，补丁无） |
| 辅助函数冲突 | 无 |
| Import 冲突 | 需补充 5 个模块：`maop.core.agent.llm_chat.llm_provider`, `maop.core.agent.llm_chat.react_loop`, `maop.core.evolution.evolution_loop`, `maop.evolve`, `maop.maop_loop` |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | 补丁文件包含 5 个低价值模块导入测试类（`TestLLMProvider`, `TestReactLoop`, `TestMaopLoop`, `TestEvolve`, `TestEvolutionLoop`），仅测试 import 是否成功，**合并时必须丢弃**。仅合并 `TestThreeLayerMemory` 主体类。多补丁合并目标（项 11 + 项 12），需顺序合并。 |

### 3.12 项 12 — test_three_layer_memory_coverage3.py → test_three_layer_memory.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 370 行 / 主 511 行） |
| 类名冲突 | **有** → `TestSubmitFeedback` |
| Fixture 冲突 | 无（主测试有 `mem_env` fixture，补丁无） |
| 辅助函数冲突 | 无（补丁独有 `_make_mem`） |
| Import 冲突 | 需补充 `maop.core.memory.three_layer_memory_types`, `maop.memory.manager`, `maop.memory.shared_db`, `sqlite3` |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | `TestSubmitFeedback` 类名冲突。需比对测试方法，合并新增方法到主类。多补丁合并目标（项 11 + 项 12），需在项 11 合并后重新检查。 |

### 3.13 项 13 — test_router_control_coverage.py → test_router_control.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 245 行 / 主 294 行） |
| 类名冲突 | **有** → 8 个类：`TestControlCancel`, `TestControlClearCache`, `TestControlDoctor`, `TestControlMaintain`, `TestControlRefresh`, `TestControlStatus`, `TestControlStop`, `TestControlValidate` |
| Fixture 冲突 | **有** → `client` |
| 辅助函数冲突 | **有** → `client`（fixture 函数） |
| Import 冲突 | 需补充 `maop.dashboard.routers.control`, `maop.dashboard.routers.state` |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | **冲突最严重项之一**。8 个类名冲突 + fixture 冲突。需逐一比对 8 个冲突类的测试方法，仅合并新增方法。`client` fixture 需复用主测试文件的版本（丢弃补丁版本）。 |

### 3.14 项 14 — test_router_model_coverage.py → test_router_model.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 302 行 / 主 377 行） |
| 类名冲突 | **有** → `TestModelSwitch` |
| Fixture 冲突 | **有** → `client` |
| 辅助函数冲突 | **有** → `client`（fixture 函数） |
| Import 冲突 | 无 |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | `TestModelSwitch` 类名冲突 + `client` fixture 冲突。需比对 `TestModelSwitch` 的测试方法，合并新增方法。`client` fixture 复用主测试版本。 |

### 3.15 项 15 — test_router_system_coverage2.py → test_router_system.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 323 行 / 主 438 行） |
| 类名冲突 | 无 |
| Fixture 冲突 | **有** → `client` |
| 辅助函数冲突 | **有** → `client`（fixture 函数） |
| Import 冲突 | 需补充 `maop.dashboard.routers.state` |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | `client` fixture 冲突。复用主测试文件的 `client` fixture，丢弃补丁版本的 `client` 和 `system_env`（若 `system_env` 在主测试中无对应则需保留并重命名以避免与 `tmp_root` 混淆）。 |

### 3.16 项 16 — test_worker_pool_coverage.py → test_worker_pool.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 232 行 / 主 188 行） |
| 类名冲突 | **有** → `TestWorkerPoolLifecycle`, `TestWorkerPoolStats` |
| Fixture 冲突 | 无 |
| 辅助函数冲突 | 无 |
| Import 冲突 | 需补充 `unittest.mock` |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | 2 个类名冲突。需比对测试方法，合并新增方法到主类。 |

### 3.17 项 17 — test_config_mutator_coverage.py → test_config_mutator_whitebox.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 261 行 / 主 141 行） |
| 类名冲突 | 无（主测试文件无类定义，仅有顶层测试函数） |
| Fixture 冲突 | 无 |
| 辅助函数冲突 | **有** → `_write_agents_yaml`, `_write_suggestions` |
| Import 冲突 | 无 |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | 2 个辅助函数冲突。主测试文件采用函数式测试风格（无类），补丁文件采用类式测试风格。需比对辅助函数体：若相同则丢弃补丁版本；若不同则重命名。 |

### 3.18 项 18 — test_pg_persist_coverage.py → test_enterprise_pg_persist.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 305 行 / 主 161 行） |
| 类名冲突 | 无（主测试文件无类定义） |
| Fixture 冲突 | **有** → `enterprise_mode` |
| 辅助函数冲突 | **有** → `enterprise_mode`（fixture 函数） |
| Import 冲突 | 需补充 `unittest.mock` |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | `enterprise_mode` fixture 冲突。复用主测试文件的 `enterprise_mode` fixture，丢弃补丁版本。补丁独有 `_mock_backend` 辅助函数需保留。 |

### 3.19 项 19 — test_queue_worker_coverage.py → test_worker.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 285 行 / 主 150 行） |
| 类名冲突 | 无 |
| Fixture 冲突 | 无 |
| 辅助函数冲突 | 无 |
| Import 冲突 | 无 |
| 合并安全性评估 | **safe** |
| 潜在风险 | 无冲突。注意：补丁文件测试 `maop.worker.queue_worker`，主测试文件 `test_worker.py` 测试 `AgentExecutor` 和 `QueueWorker`，模块相关但需确认合并后测试逻辑协调。 |

### 3.20 项 20 — test_data_proxy_coverage3.py → test_data_proxy_coverage.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 828 行 / 主 223 行） |
| 类名冲突 | 无 |
| Fixture 冲突 | 无 |
| 辅助函数冲突 | 无（补丁独有 `_init_maop_db`, `_insert_rows`, `_make_proxy`） |
| Import 冲突 | 需补充 `maop.core.backends.data`, `maop.core.backends.db_utils`, `sqlite3` |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | **目标文件本身也是 coverage 文件**（`test_data_proxy_coverage.py` 在保留独立清单中，项 5）。合并后该文件仍保留独立，但内容会膨胀到 1000+ 行。需确认此合并目标是否符合最终架构（是否应创建 `test_data_proxy.py` 主测试文件作为合并目标）。 |

### 3.21 项 21 — test_memory_manager_search_coverage3.py → test_unified_memory_search.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 570 行 / 主 160 行） |
| 类名冲突 | 无 |
| Fixture 冲突 | 无（主测试有 `setup` fixture，补丁无） |
| 辅助函数冲突 | 无 |
| Import 冲突 | 需补充 `maop.core.backends.db_utils`, `maop.memory.manager`, `maop.memory.shared_db`, `maop.memory.store` |
| 合并安全性评估 | **safe** |
| 潜在风险 | 无冲突。需补充 4 个业务模块 import。 |

### 3.22 项 22 — test_core_coverage2.py → test_otel.py + test_regression.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 179 行 / test_otel.py 95 行 + test_regression.py 126 行） |
| 类名冲突 | 无（`TestOtelDisabled` 和 `TestRegressionRunner` 均不在目标文件中） |
| Fixture 冲突 | 无 |
| 辅助函数冲突 | 无（补丁独有 `_dispatch_result`） |
| Import 冲突 | 需补充 `maop.core.reliability.error_schema`, `types` |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | **多目标合并**。需将补丁文件拆分为两部分：`TestOtelDisabled` 类合并到 `test_otel.py`，`TestRegressionRunner` 类合并到 `test_regression.py`。`_dispatch_result` 辅助函数需根据使用方合并到对应文件。 |

### 3.23 项 23 — test_maop_loop_extended.py → test_maop_loop.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 332 行 / 主 325 行） |
| 类名冲突 | 无 |
| Fixture 冲突 | 无 |
| 辅助函数冲突 | 无（补丁独有 `_make_loop`） |
| Import 冲突 | 需补充 `maop.core.agent.evolution.phases`, `maop.maop_verify` |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | 无直接冲突，但 `test_maop_loop.py` 是多补丁合并目标（项 07 + 项 23），需在项 07 合并后重新检查。 |

### 3.24 项 24 — test_guardrail_extended.py → test_guardrail.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 185 行 / 主 73 行） |
| 类名冲突 | 无 |
| Fixture 冲突 | **有** → `config_path`, `guardrail` |
| 辅助函数冲突 | **有** → `config_path`, `guardrail`（fixture 函数） |
| Import 冲突 | 需补充 `__future__`（可忽略） |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | 2 个 fixture 冲突。需比对 fixture 函数体：若相同则复用主测试版本；若不同则重命名为 `config_path_extended`, `guardrail_extended`。 |

### 3.25 项 25 — test_dispatcher_extended.py → test_dispatcher.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 399 行 / 主 212 行） |
| 类名冲突 | 无 |
| Fixture 冲突 | 无 |
| 辅助函数冲突 | 无 |
| Import 冲突 | 需补充 `maop.config.loader` |
| 合并安全性评估 | **needs_handling** |
| 潜在风险 | 无直接冲突，但 `test_dispatcher.py` 是多补丁合并目标（项 03 + 项 25），需在项 03 合并后重新检查。 |

### 3.26 项 26 — test_engine_extended.py → test_engine.py

| 检查项 | 结果 |
|--------|------|
| 主测试文件存在 | 是（补丁 199 行 / 主 159 行） |
| 类名冲突 | 无 |
| Fixture 冲突 | 无 |
| 辅助函数冲突 | 无 |
| Import 冲突 | 无 |
| 合并安全性评估 | **safe** |
| 潜在风险 | 无冲突。 |

## 第4章 汇总表

### 4.1 26 个可合并项核对汇总

| 序号 | 补丁文件 | 主测试文件 | 存在 | 类冲突 | Fix冲突 | 函冲突 | Imp冲突 | 评估 |
|------|---------|-----------|------|--------|---------|--------|---------|------|
| 01 | test_analyzer_coverage.py | test_analyzer.py | 是 | 有 | 无 | 无 | 有 | needs_handling |
| 02 | test_deploy_coverage.py | test_deploy.py | 是 | 有 | 无 | 无 | 有 | needs_handling |
| 03 | test_dispatcher_coverage.py | test_dispatcher.py | 是 | 无 | 无 | 无 | 有 | needs_handling |
| 04 | test_drivers_coverage.py | test_drivers.py | 是 | 无 | 无 | 有 | 有 | needs_handling |
| 05 | test_runtime_coverage.py | test_runtime.py | 是 | 有 | 无 | 无 | 无 | needs_handling |
| 06 | test_evolve_coverage3.py | test_evolve.py | 是 | 无 | 无 | 有 | 有 | needs_handling |
| 07 | test_maop_loop_coverage3.py | test_maop_loop.py | 是 | 有 | 无 | 无 | 有 | needs_handling |
| 08 | test_maop_execute_coverage3.py | test_maop_execute.py | 是 | 无 | 无 | 无 | 有 | **safe** |
| 09 | test_react_loop_coverage3.py | test_react_loop.py | 是 | 无 | 无 | 无 | 有 | **safe** |
| 10 | test_tool_manager_coverage3.py | test_tool_manager.py | 是 | 无 | 无 | 无 | 有 | **safe** |
| 11 | test_three_layer_memory_coverage.py | test_three_layer_memory.py | 是 | 无 | 无 | 无 | 有 | needs_handling |
| 12 | test_three_layer_memory_coverage3.py | test_three_layer_memory.py | 是 | 有 | 无 | 无 | 有 | needs_handling |
| 13 | test_router_control_coverage.py | test_router_control.py | 是 | 有 | 有 | 有 | 有 | needs_handling |
| 14 | test_router_model_coverage.py | test_router_model.py | 是 | 有 | 有 | 有 | 无 | needs_handling |
| 15 | test_router_system_coverage2.py | test_router_system.py | 是 | 无 | 有 | 有 | 有 | needs_handling |
| 16 | test_worker_pool_coverage.py | test_worker_pool.py | 是 | 有 | 无 | 无 | 有 | needs_handling |
| 17 | test_config_mutator_coverage.py | test_config_mutator_whitebox.py | 是 | 无 | 无 | 有 | 无 | needs_handling |
| 18 | test_pg_persist_coverage.py | test_enterprise_pg_persist.py | 是 | 无 | 有 | 有 | 有 | needs_handling |
| 19 | test_queue_worker_coverage.py | test_worker.py | 是 | 无 | 无 | 无 | 无 | **safe** |
| 20 | test_data_proxy_coverage3.py | test_data_proxy_coverage.py | 是 | 无 | 无 | 无 | 有 | needs_handling |
| 21 | test_memory_manager_search_coverage3.py | test_unified_memory_search.py | 是 | 无 | 无 | 无 | 有 | **safe** |
| 22 | test_core_coverage2.py | test_otel.py + test_regression.py | 是 | 无 | 无 | 无 | 有 | needs_handling |
| 23 | test_maop_loop_extended.py | test_maop_loop.py | 是 | 无 | 无 | 无 | 有 | needs_handling |
| 24 | test_guardrail_extended.py | test_guardrail.py | 是 | 无 | 有 | 有 | 有 | needs_handling |
| 25 | test_dispatcher_extended.py | test_dispatcher.py | 是 | 无 | 无 | 无 | 有 | needs_handling |
| 26 | test_engine_extended.py | test_engine.py | 是 | 无 | 无 | 无 | 无 | **safe** |

### 4.2 统计

| 评估级别 | 数量 | 占比 |
|---------|------|------|
| safe（安全合并） | 6 | 23.1% |
| needs_handling（需处理冲突） | 20 | 76.9% |
| not_recommended（不推荐合并） | 0 | 0.0% |
| **合计** | **26** | **100%** |

### 4.3 冲突类型分布

| 冲突类型 | 涉及项数 | 涉及项序号 |
|---------|---------|-----------|
| 类名冲突 | 9 | 01, 02, 05, 07, 12, 13, 14, 16 |
| Fixture 冲突 | 6 | 13, 14, 15, 18, 24 |
| 辅助函数冲突 | 8 | 04, 06, 13, 14, 15, 17, 18, 24 |
| Import 冲突（需补充） | 19 | 01, 02, 03, 04, 06, 07, 10, 11, 12, 13, 15, 16, 18, 20, 21, 22, 23, 24, 25 |
| 多补丁合并同目标 | 6 | 03+25, 07+23, 11+12 |
| 多目标拆分合并 | 1 | 22 |
| 低价值测试丢弃 | 1 | 11 |
| 目标本身是 coverage | 1 | 20 |

> 注：Import 冲突中大部分为标准库（`json`, `asyncio`, `unittest.mock` 等）或测试框架自身，影响较小。需重点关注的是业务模块 import：`maop.delegate.models`, `maop.config.loader`, `maop.core.backends.db_utils`, `maop.memory.manager`, `maop.memory.shared_db`, `maop.memory.store`, `maop.dashboard.routers.state`, `maop.core.backends.data`, `maop.core.memory.three_layer_memory_types`, `maop.core.agent.evolution.phases`, `maop.maop_verify`, `maop.core.reliability.error_schema`, `maop.core.agent.llm_chat.llm_provider`, `maop.core.agent.llm_chat.react_loop`, `maop.core.evolution.evolution_loop`, `maop.evolve`, `maop.maop_loop`。

## 第5章 需要特殊处理的合并项清单

### 5.1 可直接安全合并的 6 项（safe）

| 序号 | 补丁文件 | 主测试文件 | 备注 |
|------|---------|-----------|------|
| 08 | test_maop_execute_coverage3.py | test_maop_execute.py | 仅需补充 `json` import |
| 09 | test_react_loop_coverage3.py | test_react_loop.py | 仅需补充标准库 import |
| 10 | test_tool_manager_coverage3.py | test_tool_manager.py | 需补充 `maop.core.backends.db_utils` |
| 19 | test_queue_worker_coverage.py | test_worker.py | 完全无冲突 |
| 21 | test_memory_manager_search_coverage3.py | test_unified_memory_search.py | 需补充 4 个业务模块 import |
| 26 | test_engine_extended.py | test_engine.py | 完全无冲突 |

### 5.2 需处理类名冲突的 9 项

| 序号 | 补丁文件 | 主测试文件 | 冲突类名 | 处理建议 |
|------|---------|-----------|---------|---------|
| 01 | test_analyzer_coverage.py | test_analyzer.py | `TestSelectStrategy` | 比对方法，合并新增方法到主类，丢弃重复方法 |
| 02 | test_deploy_coverage.py | test_deploy.py | `TestHealthCheck`, `TestPidManagement`, `TestValidateConfig` | 比对 3 个类的方法，合并新增方法 |
| 05 | test_runtime_coverage.py | test_runtime.py | `TestIsolatedRuntime`, `TestLocalRuntime`, `TestResolveCmd` | 比对 3 个类的方法，合并新增方法 |
| 07 | test_maop_loop_coverage3.py | test_maop_loop.py | `TestMaopLoopInit` | 比对方法，合并新增方法 |
| 12 | test_three_layer_memory_coverage3.py | test_three_layer_memory.py | `TestSubmitFeedback` | 比对方法，合并新增方法 |
| 13 | test_router_control_coverage.py | test_router_control.py | 8 个 `TestControl*` 类 | **高优先级处理**，逐一比对 8 个类 |
| 14 | test_router_model_coverage.py | test_router_model.py | `TestModelSwitch` | 比对方法，合并新增方法 |
| 16 | test_worker_pool_coverage.py | test_worker_pool.py | `TestWorkerPoolLifecycle`, `TestWorkerPoolStats` | 比对 2 个类的方法 |

### 5.3 需处理 Fixture/辅助函数冲突的项

| 序号 | 补丁文件 | 主测试文件 | 冲突项 | 处理建议 |
|------|---------|-----------|--------|---------|
| 04 | test_drivers_coverage.py | test_drivers.py | `_config` 函数 | 比对函数体，相同则丢弃补丁版本 |
| 06 | test_evolve_coverage3.py | test_evolve.py | `_delegation`, `_write_delegations` 函数 | 比对函数体，相同则丢弃补丁版本 |
| 13 | test_router_control_coverage.py | test_router_control.py | `client` fixture | 复用主测试版本 |
| 14 | test_router_model_coverage.py | test_router_model.py | `client` fixture | 复用主测试版本 |
| 15 | test_router_system_coverage2.py | test_router_system.py | `client` fixture | 复用主测试版本 |
| 17 | test_config_mutator_coverage.py | test_config_mutator_whitebox.py | `_write_agents_yaml`, `_write_suggestions` 函数 | 比对函数体，相同则丢弃补丁版本 |
| 18 | test_pg_persist_coverage.py | test_enterprise_pg_persist.py | `enterprise_mode` fixture | 复用主测试版本 |
| 24 | test_guardrail_extended.py | test_guardrail.py | `config_path`, `guardrail` fixture | 比对函数体，相同则复用 |

### 5.4 多补丁合并同目标的 3 组（6 项）

| 目标文件 | 涉及项 | 顺序建议 |
|---------|--------|---------|
| test_dispatcher.py | 项 03 + 项 25 | 先合并项 03（coverage），再合并项 25（extended），每次合并后重新检查冲突 |
| test_maop_loop.py | 项 07 + 项 23 | 先合并项 07（coverage3），再合并项 23（extended），每次合并后重新检查冲突 |
| test_three_layer_memory.py | 项 11 + 项 12 | 先合并项 11（丢弃 5 个导入测试），再合并项 12，每次合并后重新检查冲突 |

### 5.5 需特殊处理的 3 项

| 序号 | 补丁文件 | 特殊处理要求 |
|------|---------|------------|
| 11 | test_three_layer_memory_coverage.py | **必须丢弃** 5 个低价值模块导入测试类：`TestLLMProvider`, `TestReactLoop`, `TestMaopLoop`, `TestEvolve`, `TestEvolutionLoop`。仅合并 `TestThreeLayerMemory` 主体类。 |
| 20 | test_data_proxy_coverage3.py → test_data_proxy_coverage.py | **目标文件本身也是 coverage 文件**（在保留独立清单中）。合并后该文件将膨胀至 1000+ 行。建议考虑：是否应先创建正式的 `test_data_proxy.py` 主测试文件作为最终合并目标，而非合并到另一个 coverage 文件。 |
| 22 | test_core_coverage2.py | **多目标拆分合并**。需将补丁文件拆分：`TestOtelDisabled` 类 → `test_otel.py`，`TestRegressionRunner` 类 → `test_regression.py`。`_dispatch_result` 辅助函数根据使用方合并到对应文件。 |

## 第6章 风险分级与执行建议

### 6.1 风险分级

| 风险级别 | 项数 | 涉及项 | 说明 |
|---------|------|--------|------|
| **极低风险** | 6 | 08, 09, 10, 19, 21, 26 | 无任何冲突，仅需补充 import |
| **低风险** | 5 | 03, 11, 23, 25, 20 | 无直接冲突，但需特殊处理（多补丁顺序/丢弃测试/目标确认） |
| **中风险** | 8 | 04, 06, 07, 12, 15, 16, 17, 18 | 单一类型冲突（类名或函数或 fixture），处理明确 |
| **高风险** | 4 | 02, 05, 13, 14 | 多类名冲突或多种冲突叠加，需逐一比对测试方法 |
| **特殊处理** | 3 | 11, 20, 22 | 需丢弃测试/确认目标/拆分合并 |

### 6.2 执行建议

1. **先执行 6 个 safe 项**：项 08, 09, 10, 19, 21, 26 可立即合并，仅补充 import
2. **按 P0→P1→P2→P3 顺序处理 needs_handling 项**：每项合并后运行 `pytest <主测试文件> -v --tb=short` 验证
3. **多补丁合并目标需顺序执行**：
   - `test_dispatcher.py`：项 03 → 项 25
   - `test_maop_loop.py`：项 07 → 项 23
   - `test_three_layer_memory.py`：项 11 → 项 12
4. **高风险项（项 13）需重点处理**：8 个类名冲突 + fixture 冲突，建议单独评审
5. **项 20 需架构决策**：是否创建 `test_data_proxy.py` 主测试文件作为合并目标
6. **项 22 需拆分操作**：将补丁文件按类拆分到两个目标文件

### 6.3 验证检查点

每个合并项完成后，检查以下指标：

- [ ] 合并后的主测试文件 `pytest <主测试文件> -v --tb=short` 全部通过
- [ ] 总测试用例数未减少（合并前 vs 合并后）
- [ ] 无 import 错误或 fixture 冲突
- [ ] 无类定义重复（`grep "class Test" <主测试文件>` 无重复）

## 第7章 总体结论

### 7.1 核对结论

| 维度 | 结论 |
|------|------|
| 文件存在性 | ✅ 全部 51 个文件存在（26 补丁 + 25 主测试） |
| 类名冲突 | ⚠️ 9 项存在类名冲突，需逐一比对测试方法 |
| Fixture 冲突 | ⚠️ 6 项存在 fixture 冲突，需复用主测试版本 |
| 辅助函数冲突 | ⚠️ 8 项存在辅助函数冲突，需比对函数体 |
| Import 冲突 | ⚠️ 19 项需补充 import（大部分为标准库，影响小） |
| 多补丁合并 | ⚠️ 3 组 6 项需顺序合并 |
| 多目标拆分 | ⚠️ 1 项需拆分合并 |
| 低价值测试丢弃 | ⚠️ 1 项需丢弃 5 个导入测试 |
| 目标文件合理性 | ⚠️ 1 项目标本身是 coverage 文件，需架构决策 |

### 7.2 总体评估

**可以执行合并计划，但需按以下策略：**

1. **6 个 safe 项可立即合并**（项 08, 09, 10, 19, 21, 26）
2. **20 个 needs_handling 项需先处理冲突再合并**，处理方式明确：
   - 类名冲突：比对测试方法，合并新增方法到主类，丢弃重复方法
   - Fixture 冲突：复用主测试文件的 fixture，丢弃补丁版本
   - 辅助函数冲突：比对函数体，相同则丢弃补丁版本，不同则重命名
   - Import 冲突：补充缺失的 import 到主测试文件
3. **0 个 not_recommended 项**，无根本性障碍
4. **特殊处理 3 项**：
   - 项 11：丢弃 5 个低价值导入测试
   - 项 20：建议架构决策（是否创建 `test_data_proxy.py`）
   - 项 22：拆分合并到两个目标文件

### 7.3 最终建议

**合并计划总体可行，但需修正合并计划文档中的风险估计：**

- 原计划估计"类名冲突 ~15 项"，实际为 **9 项**
- 原计划估计"Fixture 冲突 ~6 项"，实际为 **6 项**（符合）
- 原计划估计"辅助函数冲突 ~8 项"，实际为 **8 项**（符合）
- 原计划估计"Import 冲突 ~5 项"，实际为 **19 项**（**显著低估**，需重点关注业务模块 import）

**建议执行顺序：**

1. P0：先合并 6 个 safe 项（项 08, 09, 10, 19, 21, 26）
2. P1：处理多补丁合并目标（项 03→25, 07→23, 11→12）
3. P2：处理单一冲突项（项 04, 06, 15, 16, 17, 18）
4. P3：处理多类名冲突项（项 01, 02, 05, 14）
5. P4：处理高风险项（项 13）
6. P5：处理特殊项（项 20 架构决策, 22 拆分合并）

**核对完成，合并计划可安全执行，但需按上述策略处理 20 项冲突。**

---

> 核对脚本：`F:\Nexus\MAOP\check_merge_conflicts.py`
> 核对结果输出：脚本运行日志（见核对过程）
> 报告生成时间：2026-08-07