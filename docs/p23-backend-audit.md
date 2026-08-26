# P2/P3 后端代码审核报告

> 审核时间：2026-08-26
> 审核范围：MAOP `py/maop/` 源码（排除 `migrations/`、`tests/`、`__pycache__/`）
> 审核人：BackendAuditAgent（Task 488）
> 参考文档：`docs/p1-code-evaluation.md`（P1 已知暂缓项不重复报告）

## 1. 审核概览

| 维度 | 扫描方法 | 发现数 |
|------|----------|--------|
| 死代码/未使用代码 | vulture + grep 全项目引用验证 | 2 |
| TODO/FIXME/HACK/XXX | grep | 0 |
| NotImplementedError 占位 | grep + AST 上下文分析 | 0（均为合理的抽象方法） |
| 空函数体（pass） | AST 扫描 | 0（均为合理的 NoOp/默认实现） |
| 过长函数（>200 行） | AST 扫描 | 2（排除 P1 暂缓项 4 个） |
| 过长文件（>800 行） | 行数统计 | 1（排除 P1 暂缓项 1 个） |
| 圈复杂度 >20 | `ruff check --select C901` | 6（排除 P1 暂缓项 4 个） |
| 硬编码值 | grep URL/端口/超时/路径 | 5 类 |
| 缺失 docstring | AST 扫描公开函数/类 | 855（汇总） |
| 重复代码模式 | grep + 人工分析 | 5 |
| import 但未使用 | `ruff check --select F401` | 0 |

**合计：P2 问题 13 个，P3 问题 6 个。**

### P1 已知暂缓项（不重复报告）

以下项在 `docs/p1-code-evaluation.md` 中已标记为暂缓，本报告不重复列出：

- 过长函数：`maop_execute`（297 行）、`_execute_step`（293 行）、`call_tool`（261 行）、`_dispatch_impl_inner`（247 行）
- 过长文件：`core/scheduling/supervisor.py`（1422 行）
- 圈复杂度：上述 4 个函数的 CC 值（27/26/24/23）

## 2. P2 问题清单（影响可维护性/可读性的中等问题）

### P2-B-01：过长函数 `register_routers`（248 行）

- **严重度**：P2
- **问题类型**：过长函数（>200 行）
- **位置**：`dashboard/_register_routes.py:138-385`
- **问题描述**：`register_routers` 函数共 248 行，负责注册所有 Dashboard 路由。P1 已提取 `_register_enterprise_routers` 进行了部分拆分（从 327 行降至 248 行），但仍超过 200 行阈值。
- **修复建议**：继续按路由分组提取子函数，如 `_register_data_routers`、`_register_evolution_routers`、`_register_system_routers` 等，每个子函数注册一组相关路由。

### P2-B-02：过长函数 `run`（210 行）

- **严重度**：P2
- **问题类型**：过长函数（>200 行）
- **位置**：`core/agent/llm_chat/react_loop.py:319-528`
- **问题描述**：ReAct 循环的 `run` 方法共 210 行。P1 已提取 `_extract_final_answer` 和 `_append_assistant_message` 进行了部分拆分（从 225 行降至 210 行），但仍超过 200 行阈值。
- **修复建议**：考虑将 ReAct 循环的各阶段（Thought → Action → Observation → 下一轮）提取为独立的步骤方法。

### P2-B-03：过长文件 `dispatch_debate.py`（805 行）

- **严重度**：P2
- **问题类型**：过长文件（>800 行）
- **位置**：`delegate/dispatch_debate.py`（805 行）
- **问题描述**：委派辩论模块文件超过 800 行。该文件实现了多 Agent 辩论（Debate）的完整流程：辩论启动、轮次执行、投票裁决、结果聚合等。
- **修复建议**：考虑将辩论轮次执行、投票裁决、结果聚合提取为独立模块（如 `debate_rounds.py`、`debate_verdict.py`、`debate_aggregator.py`）。

### P2-B-04：圈复杂度 >20 — `api_control_maintain`（CC=24）

- **严重度**：P2
- **问题类型**：圈复杂度过高
- **位置**：`dashboard/routers/control.py:201`
- **问题描述**：`api_control_maintain` 函数圈复杂度为 24，包含大量条件分支处理不同的维护操作（清理缓存、重建索引、压缩内存、GC 等）。
- **修复建议**：将各维护操作提取为独立的处理函数，用策略模式或命令模式分派。

### P2-B-05：圈复杂度 >20 — `_rule_based_analyze`（CC=24）

- **严重度**：P2
- **问题类型**：圈复杂度过高
- **位置**：`loop_analyzer.py:133`
- **问题描述**：`_rule_based_analyze` 函数圈复杂度为 24，包含大量规则匹配分支用于分析循环执行模式。
- **修复建议**：将各分析规则提取为独立的规则函数，用规则链或责任链模式组织。

### P2-B-06：圈复杂度 >20 — `_parse_llm_decomp`（CC=22）

- **严重度**：P2
- **问题类型**：圈复杂度过高
- **位置**：`core/agent/analyzer/analyzer.py:437`
- **问题描述**：`_parse_llm_decomp` 函数圈复杂度为 22，负责解析 LLM 返回的分解结果，包含多种格式容错分支。
- **修复建议**：将不同格式的解析逻辑提取为独立的解析器方法。

### P2-B-07：圈复杂度 >20 — `dispatch`（CC=22）

- **严重度**：P2
- **问题类型**：圈复杂度过高
- **位置**：`core/security/middleware.py:67`
- **问题描述**：`dispatch` 方法圈复杂度为 22，这是 ASGI 中间件的分派方法，处理认证、授权、CORS、限流等多种中间件逻辑。
- **修复建议**：将各中间件检查提取为独立的检查方法，按管道模式组合。

### P2-B-08：圈复杂度 >20 — `register_static_routes`（CC=21）

- **严重度**：P2
- **问题类型**：圈复杂度过高
- **位置**：`dashboard/_register_routes.py:433`
- **问题描述**：`register_static_routes` 函数圈复杂度为 21，负责注册静态资源路由（CSS、JS、favicon、SPA fallback 等），包含多种路径和 MIME 类型判断。
- **修复建议**：将各静态路由注册提取为独立的子函数。

### P2-B-09：圈复杂度 >20 — `agent_token_stream`（CC=21）

- **严重度**：P2
- **问题类型**：圈复杂度过高
- **位置**：`dashboard/routers/stream.py:193`
- **问题描述**：`agent_token_stream` 函数圈复杂度为 21，负责 SSE 流式推送 Agent 执行的 token 流，包含多种事件类型和错误处理分支。
- **修复建议**：将不同事件类型的处理提取为独立的事件生成器方法。

### P2-B-10：重复代码 — `_run_subprocess` / `_run_subproc` 三处独立实现

- **严重度**：P2
- **问题类型**：重复代码模式
- **位置**：
  - `dashboard/services/upgrade_service.py:42` — `_run_subproc`
  - `dashboard/routers/system/_deps.py:45` — `_run_subprocess`
  - `core/agent/lifecycle/agent_repair.py:114` — `_run_subprocess`（实例方法）
- **问题描述**：三处几乎相同的异步子进程包装函数，均使用 `asyncio.create_subprocess_exec` + `asyncio.wait_for(proc.communicate(), timeout=...)` 模式，仅在错误处理和返回值类型上略有差异（`bytes` vs `str`、超时是否返回 `(-1, "", "timeout")` 还是 `raise`）。
- **修复建议**：提取为统一的公共工具函数（如 `core/utils/async_subprocess.py`），支持配置错误处理策略（raise vs return error）。

### P2-B-11：硬编码且重复 — CORS origins 默认值

- **严重度**：P2
- **问题类型**：硬编码值 + 重复代码
- **位置**：
  - `dashboard/_middleware_stack.py:69` — `["http://localhost:9079", "http://127.0.0.1:9079", "http://localhost:8080"]`
  - `core/security/middleware.py:452` — `["http://localhost:9079", "http://127.0.0.1:9079"]`
  - `config/settings.py:89` — `default="http://localhost:9079,http://127.0.0.1:9079"`
- **问题描述**：CORS 允许源列表在 3 处硬编码，且内容不一致（`_middleware_stack.py` 额外包含 `http://localhost:8080`）。端口号 `9079` 硬编码在多处。
- **修复建议**：统一从 `config/settings.py` 的 `MAOP_CORS_ORIGINS` 配置读取，消除硬编码副本。`8080` 端口差异需确认是否为有意保留。

### P2-B-12：硬编码且重复 — PostgreSQL 默认连接 URL

- **严重度**：P2
- **问题类型**：硬编码值 + 重复代码
- **位置**：
  - `core/backends/db_utils.py:284` — `return "postgresql+psycopg2://localhost:5432/maop"`
  - `core/vector/pg_backend.py:129` — `or "postgresql+psycopg2://localhost:5432/maop"`
- **问题描述**：PostgreSQL 默认连接 URL `"postgresql+psycopg2://localhost:5432/maop"` 在 2 处硬编码（排除 `migrations/`）。端口号 `5432` 和数据库名 `maop` 硬编码。
- **修复建议**：统一使用 `config/settings.py` 中的 `MAOP_DATABASE_URL` 配置项作为唯一默认值来源。

### P2-B-13：硬编码且重复 — Redis 默认连接 URL

- **严重度**：P2
- **问题类型**：硬编码值 + 重复代码
- **位置**：
  - `worker/distributed_worker.py:87` — `redis_url: str = "redis://localhost:6379/0"`
  - `worker/distributed_worker.py:396` — `redis_url: str = "redis://localhost:6379/0"`
  - `cli.py:313` — `redis_url: str = "redis://localhost:6379/0"`
  - `cli.py:369` — `default="redis://localhost:6379/0"`
- **问题描述**：Redis 默认连接 URL `"redis://localhost:6379/0"` 在 4 处硬编码。端口号 `6379` 和数据库编号 `0` 硬编码。
- **修复建议**：统一使用 `config/settings.py` 中的 `MAOP_REDIS_URL` 配置项作为唯一默认值来源。

## 3. P3 问题清单（微小的改进建议）

### P3-B-01：死代码 — `_adaptive_agent_select`

- **严重度**：P3
- **问题类型**：死代码（未使用函数）
- **位置**：`maop_plan.py:124`
- **问题描述**：`_adaptive_agent_select(route, rk)` 函数有完整实现和 docstring（根据性能数据从路由候选中选择最佳 Agent），但全项目（含 `tests/`）无任何调用。函数上方的注释说明已改为配置驱动路由，此函数为遗留代码。
- **修复建议**：确认无外部插件引用后删除该函数。

### P3-B-02：死代码 — `check_pause_sync`

- **严重度**：P3
- **问题类型**：死代码（未使用函数）
- **位置**：`engine.py:93`
- **问题描述**：`check_pause_sync()` 函数有完整实现和 docstring（同步检查 pause 状态，若已暂停则等待直到恢复），但全项目（含 `tests/`）无任何调用。对应的异步版本 `check_pause`（`engine.py:85`）仍在使用。
- **修复建议**：确认无同步调用路径引用后删除该函数。

### P3-B-03：缺失 docstring — 855 个公开函数/类

- **严重度**：P3
- **问题类型**：缺失 docstring
- **位置**：全项目（156 个文件受影响）
- **问题描述**：855 个公开函数/类（不以 `_` 开头）缺失 docstring。受影响最多的文件：
  | 文件 | 缺失数 |
  |------|--------|
  | `dashboard/routers/data.py` | 31 |
  | `core/backends/backends.py` | 26 |
  | `dashboard/routers/notifications.py` | 26 |
  | `dashboard/routers/model.py` | 18 |
  | `core/evolution/ab_test.py` | 17 |
  | `core/agent/delegation/a2a.py` | 16 |
  | `core/mcp/mcp_hub_transport.py` | 16 |
  | 其余 149 个文件 | 705 |
- **修复建议**：优先为核心模块（`core/`、`delegate/`）的公开 API 补充 docstring；Dashboard 路由处理函数可批量添加简短 docstring。建议分批进行，每次处理一个模块。

### P3-B-04：硬编码超时值散布（63 处）

- **严重度**：P3
- **问题类型**：硬编码值
- **位置**：全项目 63 处 `timeout=10/5/30/60/120/300` 等
- **问题描述**：超时值散布在 30+ 个文件中，常用值为 `timeout=10`（子进程调用）、`timeout=30`（HTTP 请求）、`timeout=120`（升级操作）、`timeout=300`（任务等待）。这些值未集中配置，调整需逐文件修改。
- **修复建议**：在 `config/settings.py` 中增加 `MAOP_TIMEOUT_*` 配置项（如 `SUBPROCESS_TIMEOUT_S`、`HTTP_TIMEOUT_S`、`UPGRADE_TIMEOUT_S`），各处引用配置值。

### P3-B-05：硬编码健康检查 URL

- **严重度**：P3
- **问题类型**：硬编码值
- **位置**：`deploy.py:232` — `"http://127.0.0.1:9079/api/health"`
- **问题描述**：Dashboard 健康检查 URL 和端口号 `9079` 硬编码在 `deploy.py` 的 `check_health` 函数中，未从配置读取。
- **修复建议**：从 `config/settings.py` 读取 Dashboard 监听地址和端口构建健康检查 URL。

### P3-B-06：重复代码 — OTel endpoint 默认值重复

- **严重度**：P3
- **问题类型**：重复代码
- **位置**：
  - `dashboard/routers/observability.py:232` — `os.getenv("MAOP_OTEL_ENDPOINT", "http://localhost:4317")`
  - `core/monitoring/otel.py:106` — `os.getenv("MAOP_OTEL_ENDPOINT", "http://localhost:4317")`
  - `core/monitoring/otel.py:141` — `os.getenv("MAOP_OTEL_ENDPOINT", "http://localhost:4317")`
- **问题描述**：OTel endpoint 的 `os.getenv` 调用和默认值 `"http://localhost:4317"` 在 3 处重复。虽然默认值合理（OTel 标准端口），但重复调用应提取为公共函数。
- **修复建议**：在 `core/monitoring/otel.py` 中提取 `get_otel_endpoint()` 公共函数，其他位置引用。

## 4. 未发现问题的维度

以下维度经扫描未发现问题，特此记录：

| 维度 | 扫描结果 | 说明 |
|------|----------|------|
| TODO/FIXME/HACK/XXX 注释 | 0 处 | 代码中无遗留的 TODO/FIXME 标记 |
| NotImplementedError 占位 | 0 处（10 处均为合理抽象方法） | 所有 `raise NotImplementedError` 均在 `@abstractmethod` 装饰的抽象基类方法中，或为运行时模式守卫（如 `memory/facade.py:287` 的 chat-only 守卫） |
| 空函数体（pass） | 0 处（20 处均为合理实现） | 所有 `pass` 函数体均为 NoOp 模式（`_NoopSpan`）、默认实现（插件生命周期钩子）、或自动提交模式（PG `commit`/`rollback`） |
| import 但未使用 | 0 处 | `ruff check --select F401` 全部通过 |
| 重复定义 | 0 处 | `ruff check --select F811` 全部通过 |

## 5. 审核方法说明

| 维度 | 工具/方法 |
|------|-----------|
| 死代码 | `vulture --min-confidence 60` + grep 全项目（含 `tests/`）引用验证 |
| TODO/FIXME | `grep -rE "(TODO\|FIXME\|HACK\|XXX)\b"` |
| NotImplementedError | `grep "raise NotImplementedError"` + AST 上下文分析 |
| 空函数体 | AST 扫描（`body` 仅含 `Pass` 或 `docstring+Pass`） |
| 过长函数 | AST 扫描（`end_lineno - lineno + 1 > 200`） |
| 过长文件 | `Get-Content | Measure-Object -Line` |
| 圈复杂度 | `ruff check --select C901 --output-format=json`，筛选 CC>20 |
| 硬编码值 | `grep` 搜索 URL、端口号、超时值、路径 |
| 缺失 docstring | AST 扫描公开函数/类（不以 `_` 开头，排除 `@abstractmethod`） |
| 重复代码 | `grep` 模式匹配 + 人工对比分析 |
| import 未使用 | `ruff check --select F401` |

## 6. 修复优先级建议

### 优先处理（P2，影响可维护性）

1. **P2-B-10**（`_run_subprocess` 重复实现）— 提取公共工具函数，消除 3 处重复
2. **P2-B-11/12/13**（硬编码且重复的 CORS/PG/Redis URL）— 统一配置来源，消除硬编码副本
3. **P2-B-03**（`dispatch_debate.py` 805 行）— 按职责拆分模块
4. **P2-B-04~09**（圈复杂度 >20 的 6 个函数）— 按策略/管道模式拆分

### 后续处理（P3，微小改进）

5. **P3-B-01/02**（死代码）— 删除 2 个未使用函数
6. **P3-B-06**（OTel endpoint 重复）— 提取公共函数
7. **P3-B-05**（硬编码健康检查 URL）— 从配置读取
8. **P3-B-04**（超时值散布）— 集中配置
9. **P3-B-03**（缺失 docstring）— 分批补充，优先核心模块