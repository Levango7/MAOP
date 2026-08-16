# H2 剩余 14 项合并执行计划

> 本文档由 Task 56 生成，描述 H2 合并计划中剩余 14 项（已扣除 Task 54 完成的 6 项）的详细冲突分析、处理方案、执行顺序与验证步骤。
>
> **不执行实际合并**，仅作为执行前确认方案。确认后由后续任务按本计划逐步执行。

## 第1章 背景与范围

### 1.1 任务来源

H2 合并计划共 26 项可合并，其中 6 项已在 Task 54 中安全合并完成：

- `test_maop_execute_coverage3.py` ✅
- `test_react_loop_coverage3.py` ✅
- `test_tool_manager_coverage3.py` ✅
- `test_queue_worker_coverage.py` ✅
- `test_memory_manager_search_coverage3.py` ✅
- `test_engine_extended.py` ✅

剩余 20 项中又有 6 项已在 Task 54 完成（清单中标注"跳过"），故本计划仅覆盖 **14 项**。

### 1.2 14 项清单

| 编号 | 补丁文件 | 目标文件 | 风险等级 | 冲突类型 |
|------|---------|---------|---------|---------|
| M01 | test_dispatcher_coverage.py | test_dispatcher.py | 低 | import 合并 |
| M02 | test_dispatcher_extended.py | test_dispatcher.py | 低 | import 合并（依赖 M01） |
| M03 | test_evolve_coverage3.py | test_evolve.py | 中 | 辅助函数冲突 + import 合并 |
| M04 | test_maop_loop_coverage3.py | test_maop_loop.py | 中 | 类名冲突 + import 合并 |
| M05 | test_maop_loop_extended.py | test_maop_loop.py | 低 | import 合并（依赖 M04） |
| M06 | test_config_mutator_coverage.py | test_config_mutator_whitebox.py | 中 | 辅助函数冲突 + import 合并 |
| M07 | test_router_control_coverage.py | test_router_control.py | 高 | 8 个类名冲突 + fixture 冲突 |
| M08 | test_router_model_coverage.py | test_router_model.py | 中 | 类名冲突 + fixture 冲突 |
| M09 | test_router_system_coverage2.py | test_router_system.py | 中 | fixture 冲突 |
| M10 | test_three_layer_memory_coverage.py | test_three_layer_memory.py | 低 | 丢弃 5 个模块导入测试 |
| M11 | test_three_layer_memory_coverage3.py | test_three_layer_memory.py | 中 | 类名冲突 + import 合并（依赖 M10） |
| M12 | test_pg_persist_coverage.py | test_enterprise_pg_persist.py | 中 | fixture 冲突 + import 合并 |
| M13 | test_data_proxy_coverage3.py | test_data_proxy_coverage.py | 低 | import 合并 |
| M14 | test_core_coverage2.py | test_otel.py + test_regression.py | 中 | 拆分合并 + import 合并 |

### 1.3 风险分布汇总

- 低风险：5 项（M01, M02, M05, M10, M13）
- 中风险：8 项（M03, M04, M06, M08, M09, M11, M12, M14）
- 高风险：1 项（M07）

## 第2章 冲突分析详解

### 2.1 M01 — test_dispatcher_coverage.py → test_dispatcher.py

**主测试类**：TestSecurityEscaping, TestAgentConfig, TestDispatcher

**补丁类**：TestRetryWithBackoff, TestLazySubsystemImports, TestDispatcherSimpleMethods, TestDispatchPriority, TestNotifyRouteScorer, TestCapabilityMatchingFallback, TestCircuitBreakerFailover, TestDriverException, TestDelegateToSubagent

**冲突清单**：

表：M01 冲突分析表

| 冲突类型 | 冲突项 | 处理方案 |
|---------|-------|---------|
| 类名冲突 | 无 | — |
| Fixture 冲突 | 无 | — |
| 辅助函数冲突 | 无 | — |
| Import 冲突 | `AsyncMock, patch`（unittest.mock） | 合并到主测试 import 区 |
| Import 冲突 | `pytest` | 主测试需新增 `import pytest` |
| Import 冲突 | `AgentConfig, DispatchResult`（maop.delegate.models） | 合并到主测试 import 区 |

**处理方案**：
1. 在主测试 import 区新增：`import pytest`、`from unittest.mock import AsyncMock, MagicMock, patch`（合并）、`from maop.delegate.models import AgentConfig, DispatchResult`
2. 追加补丁的所有类（9 个）到主测试末尾
3. 删除补丁文件

**验证命令**：

```bash
命令示例：运行 dispatcher 测试验证合并正确性
```

```bash
python -m pytest py/tests/test_dispatcher.py -v --tb=short
```

### 2.2 M02 — test_dispatcher_extended.py → test_dispatcher.py（依赖 M01）

**主测试类**（M01 合并后）：原 3 个 + M01 的 9 个 = 12 个类

**补丁类**：TestDriverRegistry, TestGuardrailIntegration, TestModelResolution, TestEscapeForCmd, TestEscapeForPsCommand, TestDispatchResult, TestSubagentResolution, TestConfigLoaderSubagents

**冲突清单**：

表：M02 冲突分析表

| 冲突类型 | 冲突项 | 处理方案 |
|---------|-------|---------|
| 类名冲突 | 无 | — |
| Fixture 冲突 | 无 | — |
| 辅助函数冲突 | 无（`_make_config_with_subagents` 在类内） | — |
| Import 冲突 | `_DRIVERS, DispatchResult`（maop.delegate.dispatcher） | 合并到主测试 import 区 |

**处理方案**：
1. 在主测试 import 区合并：`from maop.delegate.dispatcher import (_DRIVERS, AgentConfig, Dispatcher, DispatchResult, _escape_for_cmd, _escape_for_ps_command)`
2. 追加补丁的所有类（8 个）到主测试末尾
3. 删除补丁文件

**验证命令**：

```bash
python -m pytest py/tests/test_dispatcher.py -v --tb=short
```

### 2.3 M03 — test_evolve_coverage3.py → test_evolve.py

**主测试类**：TestModels, TestLoadObservabilityData, TestComputeStats, TestGenerateSuggestions, TestEvolveEngine, TestLoadObservabilityDataFromDB, TestEvolveEngineSQLiteSource

**补丁类**：TestSuggestMerge, TestApplyBranches, TestPromoteNonAuto, TestApplyToAgentsYaml, TestSaveSuggestionsException, TestAutoEvolveFallback, TestAutoEvolveLegacy

**冲突清单**：

表：M03 冲突分析表

| 冲突类型 | 冲突项 | 处理方案 |
|---------|-------|---------|
| 类名冲突 | 无 | — |
| Fixture 冲突 | 无 | — |
| 辅助函数冲突 | `_delegation`（同名同实现） | 删除补丁的 `_delegation`，复用主测试的 |
| 辅助函数冲突 | `_write_delegations`（同名同实现） | 删除补丁的 `_write_delegations`，复用主测试的 |
| 辅助函数保留 | `_make_engine`, `_gen_suggestions`（补丁独有） | 追加到主测试辅助函数区 |
| Import 冲突 | `patch, MagicMock`（unittest.mock） | 合并到主测试 import 区 |

**处理方案**：
1. 在主测试 import 区新增：`from unittest.mock import patch, MagicMock`
2. 在主测试辅助函数区追加：`_make_engine`, `_gen_suggestions`
3. 删除补丁中的 `_delegation`, `_write_delegations`（避免重复定义）
4. 追加补丁的所有类（7 个）到主测试末尾
5. 删除补丁文件

**验证命令**：

```bash
python -m pytest py/tests/test_evolve.py -v --tb=short
```

### 2.4 M04 — test_maop_loop_coverage3.py → test_maop_loop.py

**主测试类**：TestLoopConfig, TestLoopResult, TestRequirementAnalysis, TestMaopLoopInit, TestMaopLoopRun, TestSimpleAnalyze, TestBudgetReconciliation

**补丁类**：TestMaopLoopInit, TestLlmFactory, TestRecordMetric, TestInjectMemory, TestRunMocked

**冲突清单**：

表：M04 冲突分析表

| 冲突类型 | 冲突项 | 处理方案 |
|---------|-------|---------|
| 类名冲突 | `TestMaopLoopInit` | 重命名补丁的 → `TestMaopLoopInitCoverage` |
| Fixture 冲突 | 无 | — |
| 辅助函数冲突 | 无（`_setup_loop` 在类内） | — |
| Import 冲突 | `patch, MagicMock, AsyncMock`（unittest.mock） | 合并到主测试 import 区 |

**处理方案**：
1. 在主测试 import 区新增：`from unittest.mock import patch, MagicMock, AsyncMock`
2. 重命名补丁的 `TestMaopLoopInit` → `TestMaopLoopInitCoverage`
3. 追加补丁的所有类（5 个，含重命名后的）到主测试末尾
4. 删除补丁文件

**验证命令**：

```bash
python -m pytest py/tests/test_maop_loop.py -v --tb=short
```

### 2.5 M05 — test_maop_loop_extended.py → test_maop_loop.py（依赖 M04）

**主测试类**（M04 合并后）：原 7 个 + M04 的 5 个 = 12 个类

**补丁类**：TestSimpleAnalyzeExtended, TestSimpleAnalyzeSemantic, TestFeedbackLoop, TestFallbackChain, TestVerifyPhase, TestBuildLoopResultVerifyErrored, TestLoopResultFields

**冲突清单**：

表：M05 冲突分析表

| 冲突类型 | 冲突项 | 处理方案 |
|---------|-------|---------|
| 类名冲突 | 无 | — |
| Fixture 冲突 | 无 | — |
| 辅助函数冲突 | 无（`_make_loop` 为补丁独有） | 追加到主测试辅助函数区 |
| Import 冲突 | `Path`（pathlib） | 合并到主测试 import 区 |
| Import 冲突 | `AsyncMock, MagicMock, patch`（unittest.mock） | 已在 M04 合并 |
| Import 冲突 | `LoopConfig, LoopResult, MaopLoop`（maop.maop_loop） | 合并到主测试 import 区 |
| Import 冲突 | `VerifyResult`（maop.maop_verify） | 新增到主测试 import 区 |

**处理方案**：
1. 在主测试 import 区新增：`from pathlib import Path`、`from maop.maop_loop import LoopConfig, LoopResult, MaopLoop`、`from maop.maop_verify import VerifyResult`
2. 在主测试辅助函数区追加：`_make_loop`
3. 追加补丁的所有类（7 个）到主测试末尾
4. 删除补丁文件

**验证命令**：

```bash
python -m pytest py/tests/test_maop_loop.py -v --tb=short
```

### 2.6 M06 — test_config_mutator_coverage.py → test_config_mutator_whitebox.py

**主测试**：无类（5 个顶层测试函数）

**补丁类**：TestApplySuggestionErrors, TestMutateRouting, TestMutateTimeout, TestMutateDisableAgent, TestMutateEmptyRouting, TestMutateAddCapability, TestMutateAdjustRettries, TestMutateSwitchModel, TestMutationResultModel

**冲突清单**：

表：M06 冲突分析表

| 冲突类型 | 冲突项 | 处理方案 |
|---------|-------|---------|
| 类名冲突 | 无 | — |
| Fixture 冲突 | 无 | — |
| 辅助函数冲突 | `_write_agents_yaml`（同名，实现等价） | 删除补丁的，复用主测试的 |
| 辅助函数冲突 | `_write_suggestions`（同名，实现等价） | 删除补丁的，复用主测试的 |
| Import 冲突 | `MutationResult`（maop.core.reliability.config_mutator） | 合并到主测试 import 区 |

**处理方案**：
1. 在主测试 import 区合并：`from maop.core.reliability.config_mutator import ConfigMutator, MutationResult`
2. 删除补丁中的 `_write_agents_yaml`, `_write_suggestions`（避免重复定义）
3. 追加补丁的所有类（9 个）到主测试末尾
4. 删除补丁文件

**注意**：补丁的 `_write_suggestions` 使用 `json.dumps(suggestions)`，主测试使用 `json.dumps(suggestions, ensure_ascii=False, indent=2)`，差异不影响功能。补丁的 `_write_agents_yaml` 在函数内部 import yaml，主测试在顶部 import yaml，复用主测试版本更优。

**验证命令**：

```bash
python -m pytest py/tests/test_config_mutator_whitebox.py -v --tb=short
```

### 2.7 M07 — test_router_control_coverage.py → test_router_control.py（高风险）

**主测试类**：TestControlStatus, TestControlRunPost, TestControlPause, TestControlResume, TestControlStop, TestControlValidate, TestControlDoctor, TestControlCancel, TestControlRefresh, TestControlClearCache, TestProviderHealth, TestControlMaintain

**补丁类**：TestControlStatus, TestControlRun, TestControlPauseResume, TestControlStop, TestControlValidate, TestControlDoctor, TestControlCancel, TestControlRefresh, TestControlClearCache, TestControlProviderHealth, TestControlMaintain

**冲突清单**：

表：M07 类名冲突表

| 补丁类名 | 冲突状态 | 重命名方案 |
|---------|---------|-----------|
| TestControlStatus | ⚠️ 冲突 | → TestControlStatusCoverage |
| TestControlRun | 无冲突 | — |
| TestControlPauseResume | 无冲突 | — |
| TestControlStop | ⚠️ 冲突 | → TestControlStopCoverage |
| TestControlValidate | ⚠️ 冲突 | → TestControlValidateCoverage |
| TestControlDoctor | ⚠️ 冲突 | → TestControlDoctorCoverage |
| TestControlCancel | ⚠️ 冲突 | → TestControlCancelCoverage |
| TestControlRefresh | ⚠️ 冲突 | → TestControlRefreshCoverage |
| TestControlClearCache | ⚠️ 冲突 | → TestControlClearCacheCoverage |
| TestControlProviderHealth | 无冲突（主测试为 TestProviderHealth） | — |
| TestControlMaintain | ⚠️ 冲突 | → TestControlMaintainCoverage |

表：M07 fixture 与 import 冲突表

| 冲突类型 | 冲突项 | 处理方案 |
|---------|-------|---------|
| Fixture 冲突 | `client`（同名） | 重命名补丁的 → `client_coverage` |
| Fixture 保留 | `control_env`（补丁独有） | 追加到主测试 fixture 区 |
| Import 冲突 | 无（补丁 import 是主测试子集） | — |

**处理方案**：
1. 重命名补丁的 `client` fixture → `client_coverage`
2. 重命名补丁的 8 个冲突类（添加 `Coverage` 后缀）
3. 在所有重命名的类中，将 `client` 参数替换为 `client_coverage`
4. 追加补丁的 `control_env` fixture 和所有类（含重命名后的）到主测试末尾
5. 删除补丁文件

**验证命令**：

```bash
python -m pytest py/tests/test_router_control.py -v --tb=short
```

### 2.8 M08 — test_router_model_coverage.py → test_router_model.py

**主测试类**：TestModelAgents, TestModelQuota, TestModelSwitch, TestModelRegistry, TestModelList, TestModelProviders, TestModelSelect, TestModelBudget, TestModelPolicies

**补丁类**：TestModelSwitch, TestModelProviderAdd, TestModelProviderDelete, TestModelAdd, TestModelDelete, TestApiKeyStore, TestApiKeyDelete, TestApiKeyList, TestModelHealthCheck, TestModelGetEndpoints

**冲突清单**：

表：M08 冲突分析表

| 冲突类型 | 冲突项 | 处理方案 |
|---------|-------|---------|
| 类名冲突 | `TestModelSwitch` | 重命名补丁的 → `TestModelSwitchCoverage` |
| Fixture 冲突 | `client`（同名） | 重命名补丁的 → `client_coverage` |
| Fixture 保留 | `model_env`（补丁独有） | 追加到主测试 fixture 区 |
| Import 冲突 | 无（补丁 import 是主测试子集） | — |

**处理方案**：
1. 重命名补丁的 `client` fixture → `client_coverage`
2. 重命名补丁的 `TestModelSwitch` → `TestModelSwitchCoverage`
3. 在 `TestModelSwitchCoverage` 中，将 `client` 参数替换为 `client_coverage`
4. 追加补丁的 `model_env` fixture 和所有类（含重命名后的）到主测试末尾
5. 删除补丁文件

**验证命令**：

```bash
python -m pytest py/tests/test_router_model.py -v --tb=short
```

### 2.9 M09 — test_router_system_coverage2.py → test_router_system.py

**主测试类**：TestSubsystems, TestFrameworkStatus, TestFrameworkLogs, TestFrameworkConfig, TestAgentConfig, TestAgentUpgradeGet, TestWorkflowList, TestOverview, TestAuditEvents, TestRouting, TestSecurityConfig

**补丁类**：TestAgentConfigUpdate, TestAgentUpgrade, TestWorkflowRun, TestSystemResources, TestSystemDiagnostics, TestSystemGetEndpoints

**冲突清单**：

表：M09 冲突分析表

| 冲突类型 | 冲突项 | 处理方案 |
|---------|-------|---------|
| 类名冲突 | 无 | — |
| Fixture 冲突 | `client`（同名） | 重命名补丁的 → `client_coverage` |
| Fixture 保留 | `system_env`（补丁独有） | 追加到主测试 fixture 区 |
| Import 冲突 | 无（补丁 import 是主测试子集） | — |

**处理方案**：
1. 重命名补丁的 `client` fixture → `client_coverage`
2. 在补丁的所有类中，将 `client` 参数替换为 `client_coverage`
3. 追加补丁的 `system_env` fixture 和所有类（6 个）到主测试末尾
4. 删除补丁文件

**验证命令**：

```bash
python -m pytest py/tests/test_router_system.py -v --tb=short
```

### 2.10 M10 — test_three_layer_memory_coverage.py → test_three_layer_memory.py

**主测试类**：TestWorkingMemory, TestEpisodicMemory, TestConsolidation, TestTransform, TestQualityDimensions, TestNegativeFeedback, TestSubmitFeedback, TestMultiHeadContext, TestSessionSummaryCompression, TestFTS5Search, TestOverflowToEpisodic

**补丁类**：TestThreeLayerMemory, TestLLMProvider, TestReactLoop, TestMaopLoop, TestEvolve, TestEvolutionLoop

**冲突清单**：

表：M10 冲突分析表

| 冲突类型 | 冲突项 | 处理方案 |
|---------|-------|---------|
| 类名冲突 | 无 | — |
| Fixture 冲突 | 无 | — |
| 辅助函数冲突 | 无 | — |
| Import 冲突 | 无（补丁仅 `import pytest`） | — |
| 特殊处理 | 5 个模块导入测试类 | **丢弃** |

**需丢弃的 5 个模块导入测试类**：
- `TestLLMProvider` — 仅 `import maop.core.agent.llm_chat.llm_provider` + 断言 `maop.core.llm_provider is not None`
- `TestReactLoop` — 仅 `import maop.core.agent.llm_chat.react_loop` + 断言 `maop.core.react_loop is not None`
- `TestMaopLoop` — 仅 `import maop.maop_loop` + 断言 `maop.maop_loop is not None`
- `TestEvolve` — 仅 `import maop.evolve` + 断言 `maop.evolve is not None`
- `TestEvolutionLoop` — 仅 `import maop.core.evolution.evolution_loop` + 断言 `maop.core.evolution_loop is not None`

**丢弃理由**：这些测试仅验证模块可导入，属于"凑覆盖率"的低价值测试。合并后由其他专项测试文件覆盖这些模块的实际功能，模块导入本身在 conftest.py 阶段已隐式验证。

**处理方案**：
1. 仅追加补丁的 `TestThreeLayerMemory` 类（1 个）到主测试末尾
2. 丢弃 5 个模块导入测试类
3. 删除补丁文件

**验证命令**：

```bash
python -m pytest py/tests/test_three_layer_memory.py -v --tb=short
```

### 2.11 M11 — test_three_layer_memory_coverage3.py → test_three_layer_memory.py（依赖 M10）

**主测试类**（M10 合并后）：原 11 个 + M10 的 1 个 = 12 个类

**补丁类**：TestParseQd, TestMigrateLegacy, TestOnEvictException, TestEpisodicSearchFtsFallback, TestStoreBranches, TestQueryMemoryEntriesSuccess, TestEpisodicUpdateFeedback, TestSubmitFeedback, TestConsolidateBranches, TestAccessConsolidation, TestTransformBranches, TestGatherContextItems, TestTransformMultiHead

**冲突清单**：

表：M11 冲突分析表

| 冲突类型 | 冲突项 | 处理方案 |
|---------|-------|---------|
| 类名冲突 | `TestSubmitFeedback` | 重命名补丁的 → `TestSubmitFeedbackCoverage` |
| Fixture 冲突 | 无 | — |
| 辅助函数冲突 | 无（`_make_mem` 为补丁独有） | 追加到主测试辅助函数区 |
| Import 冲突 | `Path`（pathlib） | 合并到主测试 import 区 |
| Import 冲突 | `patch`（unittest.mock） | 合并到主测试 import 区 |

**处理方案**：
1. 在主测试 import 区新增：`from pathlib import Path`、`from unittest.mock import patch`
2. 在主测试辅助函数区追加：`_make_mem`
3. 重命名补丁的 `TestSubmitFeedback` → `TestSubmitFeedbackCoverage`
4. 追加补丁的所有类（13 个，含重命名后的）到主测试末尾
5. 删除补丁文件

**验证命令**：

```bash
python -m pytest py/tests/test_three_layer_memory.py -v --tb=short
```

### 2.12 M12 — test_pg_persist_coverage.py → test_enterprise_pg_persist.py

**主测试**：无类（17 个顶层测试函数）

**补丁类**：TestPgRBACStoreWithBackend, TestPgTenantStoreWithBackend, TestPgAuditStoreWithBackend, TestGetPgBackendImportError

**冲突清单**：

表：M12 冲突分析表

| 冲突类型 | 冲突项 | 处理方案 |
|---------|-------|---------|
| 类名冲突 | 无 | — |
| Fixture 冲突 | `enterprise_mode`（同名同实现，均 autouse=True） | 删除补丁的，复用主测试的 |
| 辅助函数冲突 | 无（`_mock_backend` 为补丁独有） | 追加到主测试辅助函数区 |
| Import 冲突 | `MagicMock, patch`（unittest.mock） | 合并到主测试 import 区 |

**处理方案**：
1. 在主测试 import 区新增：`from unittest.mock import MagicMock, patch`
2. 在主测试辅助函数区追加：`_mock_backend`
3. 删除补丁中的 `enterprise_mode` fixture（避免重复定义，主测试的已 autouse）
4. 追加补丁的所有类（4 个）到主测试末尾
5. 删除补丁文件

**验证命令**：

```bash
python -m pytest py/tests/test_enterprise_pg_persist.py -v --tb=short
```

### 2.13 M13 — test_data_proxy_coverage3.py → test_data_proxy_coverage.py

**主测试类**：TestDataProxy, TestDataProxySync, TestProxyStats

**补丁类**：TestEnsureDbSchemaError, TestReportWithData, TestAgentStatsWithCB, TestLiveExceptionBranches, TestDelegationPeriodStatsParsing, TestMemoryStatsEpisodic, TestGuardrailReportYaml, TestSnapshotExceptions, TestQueueStatsSyncException, TestToolsAndManagersException, TestCoordinationReportExceptions, TestSkillsListException, TestVersionsCheckImportError, TestProvidersReportException, TestMcpYamlParsing, TestReadDelegationsJson, TestReadCheckerLogs, TestLogsGetRouting, TestSuccessPaths, TestAgentStatsConfigFallback, TestDelegationPeriodStatsNonDict, TestQueueStatsSyncSuccess, TestGraphNodesEdges

**冲突清单**：

表：M13 冲突分析表

| 冲突类型 | 冲突项 | 处理方案 |
|---------|-------|---------|
| 类名冲突 | 无 | — |
| Fixture 冲突 | 无 | — |
| 辅助函数冲突 | 无（`_make_proxy`, `_init_maop_db`, `_insert_rows` 为补丁独有） | 追加到主测试辅助函数区 |
| Import 冲突 | `json, sqlite3` | 合并到主测试 import 区 |
| Import 冲突 | `Path`（pathlib） | 合并到主测试 import 区 |
| Import 冲突 | `patch`（unittest.mock） | 合并到主测试 import 区 |

**处理方案**：
1. 在主测试 import 区新增：`import json`、`import sqlite3`、`from pathlib import Path`、`from unittest.mock import patch`
2. 在主测试辅助函数区追加：`_make_proxy`, `_init_maop_db`, `_insert_rows`
3. 追加补丁的所有类（23 个）到主测试末尾
4. 删除补丁文件

**验证命令**：

```bash
python -m pytest py/tests/test_data_proxy_coverage.py -v --tb=short
```

### 2.14 M14 — test_core_coverage2.py → test_otel.py + test_regression.py（拆分）

**主测试 test_otel.py 类**：TestNoopTracer, TestIsEnabled, TestGetTracer, TestSpan, TestInjectExtractContext

**主测试 test_regression.py 类**：TestTestCase, TestTestResult, TestRegressionReport, TestPersonaConfig, TestPersonaSimulator, TestRegressionTestRunner

**补丁类**：
- `TestOtelDisabled` → 拆分到 test_otel.py
- `TestRegressionRunner` → 拆分到 test_regression.py

**冲突清单**：

表：M14 冲突分析表

| 冲突类型 | 冲突项 | 处理方案 |
|---------|-------|---------|
| 类名冲突 | 无 | — |
| Fixture 冲突 | 无 | — |
| 辅助函数冲突 | 无（`_dispatch_result` 为补丁独有，仅 TestRegressionRunner 使用） | 追加到 test_regression.py 辅助函数区 |
| Import 冲突（test_otel.py） | 无（TestOtelDisabled 仅用 `monkeypatch` + `otel` 模块） | — |
| Import 冲突（test_regression.py） | `SimpleNamespace`（types） | 新增到 test_regression.py import 区 |
| Import 冲突（test_regression.py） | `AsyncMock`（unittest.mock） | 新增到 test_regression.py import 区 |
| Import 冲突（test_regression.py） | `new_result`（maop.core.reliability.error_schema） | 新增到 test_regression.py import 区 |
| Import 冲突（test_regression.py） | `RegressionReport, RegressionTestRunner, TestCase`（已在主测试 import） | 复用 |

**处理方案**：
1. **拆分到 test_otel.py**：
   - 追加 `TestOtelDisabled` 类到 test_otel.py 末尾
   - 无需新增 import（`monkeypatch` 是 pytest 内置 fixture，`otel` 模块已 import）
2. **拆分到 test_regression.py**：
   - 在 import 区新增：`from types import SimpleNamespace`、`from unittest.mock import AsyncMock`、`from maop.core.reliability.error_schema import new_result`
   - 在辅助函数区追加：`_dispatch_result`
   - 追加 `TestRegressionRunner` 类到 test_regression.py 末尾
3. 删除补丁文件

**验证命令**：

```bash
python -m pytest py/tests/test_otel.py py/tests/test_regression.py -v --tb=short
```

## 第3章 合并执行顺序

### 3.1 依赖关系图

图：合并依赖关系图

```
独立项（无依赖）：
  M03 → test_evolve.py
  M06 → test_config_mutator_whitebox.py
  M07 → test_router_control.py
  M08 → test_router_model.py
  M09 → test_router_system.py
  M12 → test_enterprise_pg_persist.py
  M13 → test_data_proxy_coverage.py
  M14 → test_otel.py + test_regression.py

依赖链 1（test_dispatcher.py）：
  M01 → M02

依赖链 2（test_maop_loop.py）：
  M04 → M05

依赖链 3（test_three_layer_memory.py）：
  M10 → M11
```

### 3.2 执行阶段划分

**Phase 1 — 低风险独立项（5 项，无冲突或仅 import 合并）**

| 步骤 | 编号 | 操作 | 预计耗时 |
|------|------|------|---------|
| Step 1 | M13 | test_data_proxy_coverage3.py → test_data_proxy_coverage.py | 5 min |
| Step 2 | M10 | test_three_layer_memory_coverage.py → test_three_layer_memory.py（丢弃 5 个模块导入测试） | 5 min |

**Phase 2 — 中风险独立项（6 项，类名/fixture/辅助函数冲突）**

| 步骤 | 编号 | 操作 | 预计耗时 |
|------|------|------|---------|
| Step 3 | M03 | test_evolve_coverage3.py → test_evolve.py（辅助函数去重） | 8 min |
| Step 4 | M06 | test_config_mutator_coverage.py → test_config_mutator_whitebox.py（辅助函数去重） | 8 min |
| Step 5 | M08 | test_router_model_coverage.py → test_router_model.py（类名+fixture 重命名） | 10 min |
| Step 6 | M09 | test_router_system_coverage2.py → test_router_system.py（fixture 重命名） | 8 min |
| Step 7 | M12 | test_pg_persist_coverage.py → test_enterprise_pg_persist.py（fixture 去重） | 8 min |
| Step 8 | M14 | test_core_coverage2.py → test_otel.py + test_regression.py（拆分） | 12 min |

**Phase 3 — 多补丁合并同一目标（3 条依赖链，6 项）**

| 步骤 | 编号 | 操作 | 依赖 | 预计耗时 |
|------|------|------|------|---------|
| Step 9 | M01 | test_dispatcher_coverage.py → test_dispatcher.py | — | 5 min |
| Step 10 | M02 | test_dispatcher_extended.py → test_dispatcher.py | M01 | 5 min |
| Step 11 | M04 | test_maop_loop_coverage3.py → test_maop_loop.py（类名重命名） | — | 8 min |
| Step 12 | M05 | test_maop_loop_extended.py → test_maop_loop.py | M04 | 5 min |
| Step 13 | M11 | test_three_layer_memory_coverage3.py → test_three_layer_memory.py（类名重命名） | M10 | 8 min |

**Phase 4 — 高风险项（1 项）**

| 步骤 | 编号 | 操作 | 预计耗时 |
|------|------|------|---------|
| Step 14 | M07 | test_router_control_coverage.py → test_router_control.py（8 个类名+fixture 重命名） | 15 min |

### 3.3 总耗时估算

- Phase 1：10 min
- Phase 2：54 min
- Phase 3：31 min
- Phase 4：15 min
- **总计**：约 110 min（含每步验证）

## 第4章 验证策略

### 4.1 单步验证

每个步骤完成后，立即运行对应的 pytest 命令验证：

```bash
命令示例：单步验证模板
```

```bash
python -m pytest py/tests/<target_file>.py -v --tb=short
```

**通过标准**：
- 所有测试用例通过（无 FAILED）
- 无 import 错误
- 无 fixture 冲突错误
- 测试用例数量 ≥ 合并前主测试 + 补丁的用例数之和

### 4.2 阶段验证

每个 Phase 完成后，运行该阶段涉及的所有目标文件测试：

```bash
命令示例：Phase 验证模板
```

```bash
python -m pytest py/tests/<target1>.py py/tests/<target2>.py ... -v --tb=short
```

### 4.3 全量验证

所有 14 项合并完成后，运行全量测试验证无回归：

```bash
命令示例：全量验证
```

```bash
python -m pytest py/tests/ -v --tb=short -x
```

### 4.4 覆盖率验证

合并完成后，运行覆盖率验证确保未丢失覆盖率：

```bash
命令示例：覆盖率验证
```

```bash
python -m pytest py/tests/ --cov=maop --cov-report=term-missing
```

**通过标准**：总覆盖率 ≥ 合并前基线（参考 `_final_cov5.txt`）。

## 第5章 风险评估与回滚策略

### 5.1 风险评估

表：风险评估矩阵

| 风险等级 | 项数 | 编号 | 主要风险点 | 缓解措施 |
|---------|------|------|-----------|---------|
| 低 | 5 | M01, M02, M05, M10, M13 | 仅 import 合并或丢弃低价值测试 | 单步验证即可 |
| 中 | 8 | M03, M04, M06, M08, M09, M11, M12, M14 | 类名/fixture/辅助函数冲突 | 重命名+去重+单步验证 |
| 高 | 1 | M07 | 8 个类名冲突 + fixture 冲突 | 逐类重命名+替换参数+单步验证 |

### 5.2 关键风险点

**风险点 1：辅助函数去重可能改变行为**
- 涉及项：M03（`_delegation`, `_write_delegations`）、M06（`_write_agents_yaml`, `_write_suggestions`）、M12（`enterprise_mode`）
- 风险：补丁的辅助函数实现可能与主测试略有差异
- 缓解：已逐项对比实现，确认等价后删除补丁版本

**风险点 2：类名重命名可能遗漏引用**
- 涉及项：M04（1 个）、M07（8 个）、M08（1 个）、M11（1 个）
- 风险：类内方法可能引用同类名（如 `self.__class__.__name__`）
- 缓解：已检查所有类，确认无自引用；重命名后单步验证

**风险点 3：fixture 重命名可能遗漏参数替换**
- 涉及项：M07、M08、M09（`client` → `client_coverage`）
- 风险：类方法签名中的 `client` 参数未替换为 `client_coverage`
- 缓解：合并时全局搜索替换，单步验证会立即暴露遗漏

**风险点 4：M14 拆分合并可能遗漏 import**
- 涉及项：M14
- 风险：`TestOtelDisabled` 或 `TestRegressionRunner` 的 import 未正确拆分到对应文件
- 缓解：已逐项分析 import 依赖，单步验证会立即暴露遗漏

**风险点 5：M10 丢弃模块导入测试可能降低覆盖率**
- 涉及项：M10
- 风险：丢弃 5 个模块导入测试可能降低模块导入行的覆盖率
- 缓解：这些模块由其他专项测试文件隐式导入，覆盖率影响可忽略

### 5.3 回滚策略

**单步回滚**：
- 每步合并前，使用 `git stash` 或复制目标文件到临时备份
- 若验证失败，恢复目标文件到合并前状态
- 重新分析冲突，调整方案后重试

**阶段回滚**：
- 每个 Phase 开始前，创建 git commit 或 tag 作为检查点
- 若阶段验证失败，`git reset` 到阶段开始前的 commit
- 重新执行该阶段

**全量回滚**：
- 若全量验证发现严重回归，`git reset` 到合并开始前的 commit
- 重新评估合并方案，可能需要拆分为更小的批次

### 5.4 回滚命令模板

```bash
命令示例：回滚命令模板
```

```bash
# 单步回滚（恢复目标文件）
git checkout -- py/tests/<target_file>.py

# 阶段回滚（恢复到阶段开始前）
git reset --hard <phase_checkpoint_tag>

# 全量回滚（恢复到合并开始前）
git reset --hard <merge_start_tag>
```

## 第6章 合并后清理

### 6.1 删除补丁文件清单

合并完成后，删除以下 14 个补丁文件：

```
py/tests/test_dispatcher_coverage.py
py/tests/test_dispatcher_extended.py
py/tests/test_evolve_coverage3.py
py/tests/test_maop_loop_coverage3.py
py/tests/test_maop_loop_extended.py
py/tests/test_config_mutator_coverage.py
py/tests/test_router_control_coverage.py
py/tests/test_router_model_coverage.py
py/tests/test_router_system_coverage2.py
py/tests/test_three_layer_memory_coverage.py
py/tests/test_three_layer_memory_coverage3.py
py/tests/test_pg_persist_coverage.py
py/tests/test_data_proxy_coverage3.py
py/tests/test_core_coverage2.py
```

### 6.2 更新文档

合并完成后，更新以下文档：
- `docs/archive/merge-plans/h2-merge-feasibility-check-report.md` — 标记 14 项为"已合并"
- `docs/archive/merge-plans/h2-patch-test-merge-plan.md` — 更新合并进度

## 第7章 总结

### 7.1 冲突分析汇总

表：14 项冲突分析汇总

| 编号 | 类名冲突 | Fixture 冲突 | 辅助函数冲突 | Import 合并 | 特殊处理 | 风险 |
|------|---------|-------------|-------------|------------|---------|------|
| M01 | 0 | 0 | 0 | 3 项 | — | 低 |
| M02 | 0 | 0 | 0 | 1 项 | — | 低 |
| M03 | 0 | 0 | 2 项（去重） | 1 项 | — | 中 |
| M04 | 1 项（重命名） | 0 | 0 | 1 项 | — | 中 |
| M05 | 0 | 0 | 0 | 3 项 | — | 低 |
| M06 | 0 | 0 | 2 项（去重） | 1 项 | — | 中 |
| M07 | 8 项（重命名） | 1 项（重命名） | 0 | 0 | — | 高 |
| M08 | 1 项（重命名） | 1 项（重命名） | 0 | 0 | — | 中 |
| M09 | 0 | 1 项（重命名） | 0 | 0 | — | 中 |
| M10 | 0 | 0 | 0 | 0 | 丢弃 5 个模块导入测试 | 低 |
| M11 | 1 项（重命名） | 0 | 0 | 2 项 | — | 中 |
| M12 | 0 | 1 项（去重） | 0 | 1 项 | — | 中 |
| M13 | 0 | 0 | 0 | 4 项 | — | 低 |
| M14 | 0 | 0 | 0 | 3 项（拆分） | 拆分到 2 个文件 | 中 |
| **合计** | **11 项** | **4 项** | **4 项** | **20 项** | **1 项特殊** | — |

### 7.2 处理方案概要

- **类名冲突（11 项）**：统一采用添加 `Coverage` 后缀的重命名方案
- **Fixture 冲突（4 项）**：
  - 同名同实现 → 删除补丁版本，复用主测试的（M03, M06, M12）
  - 同名不同实现 → 重命名补丁的（M07, M08, M09）
- **辅助函数冲突（4 项）**：同名同实现 → 删除补丁版本，复用主测试的
- **Import 合并（20 项）**：合并到主测试 import 区，避免重复
- **特殊处理（1 项）**：M10 丢弃 5 个低价值模块导入测试；M14 拆分到 2 个目标文件

### 7.3 整体风险评估

- **整体风险**：中低
- **主要风险集中在**：M07（8 个类名冲突 + fixture 冲突），需特别关注
- **依赖链风险**：3 条依赖链（M01→M02, M04→M05, M10→M11）需严格按顺序执行
- **回滚能力**：每步可独立回滚，阶段有检查点，全量有起始 tag

### 7.4 建议的合并执行顺序

1. **Phase 1（低风险独立项）**：M13 → M10
2. **Phase 2（中风险独立项）**：M03 → M06 → M08 → M09 → M12 → M14
3. **Phase 3（多补丁依赖链）**：M01 → M02 → M04 → M05 → M11
4. **Phase 4（高风险项）**：M07

**关键原则**：
- 先易后难，先独立后依赖
- 每步验证，每阶段检查点
- M07 放最后，集中精力处理高风险项
- 依赖链严格按顺序，前一步失败则不继续后一步