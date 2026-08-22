# Changelog

All notable changes to MAOP (Plan-Execute-Verify) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## 发布节奏规范（M7 修复）

> **背景**：2026-08-11 至 2026-08-14 四天内发布 4 个版本（5.0.0 / 5.0.1 / 5.0.2 / 5.1.0），
> 发布节奏过快，版本质量难以保证。现制定以下发布节奏规范，约束后续发布频率。

### 发布频率约束

| 版本类型 | 频率上限 | 触发条件 | 审批要求 |
|---------|---------|---------|---------|
| **patch**（x.y.Z） | 每周最多 1 个 | 紧急 bug 修复（P0/P1） | 1 名 reviewer 批准 |
| **minor**（x.Y.0） | 每两周最多 1 个 | 新功能或非紧急修复 | 2 名 reviewer 批准 + CI 全绿 |
| **major**（X.0.0） | 每季度最多 1 个 | 破坏性变更 | 架构评审 + 3 名 reviewer 批准 |

### 发布前 checklist

1. [ ] CI 全绿（lint + type check + unit + e2e + coverage 门禁）
2. [ ] CHANGELOG.md 已更新本次版本段
3. [ ] 版本号在 pyproject.toml / __init__.py / Dockerfile / package.json 同步
4. [ ] 至少经过 1 个完整工作日的 staging 环境验证
5. [ ] 无未解决的 P0/P1 issue（紧急 hotfix 除外）
6. [ ] 发布说明已撰写（含变更摘要 + 破坏性变更 + 迁移指引）

### 禁止行为

- **禁止**同一天发布 2 个及以上版本（紧急安全 hotfix 除外，需附事后复盘）
- **禁止**跳过 staging 验证直接发布到 production
- **禁止**在 CI 红灯状态下发布

---

## [Unreleased]

### Added
- **自演化完善**：修复 `suggester.py` LLM 路径（`_SUGGESTION_PROMPT` 字面量花括号 bug）+ 新增 `narrative.py` 叙事模块 + API 端点 + 前端 `EvolutionHistory.vue` 3-tab + `EvolutionTimeline.vue` 增强 + `useMarkdown.js` / `useTextDiff.js` composable + `docs/evolution-guide.md` 使用指南
- **新手引导（P0）**：`docs/quickstart.md` 5 分钟上手文档 + `docs/README.md` 文档元索引（7 章 6 分类）+ `OnboardingWizard.vue` 3 步引导组件 + `view-onboard.js` i18n，集成到 `Overview.vue`
- **MAOP/MAOS 品牌定位统一（P1+P2）**：明确 MAOP = 个人版（免费开源）/ MAOS = 企业版（需 License），全站品牌引用统一（`view-overview.js`、`edition.py`、`docs/`、`docker-compose.yml`）+ Overview.vue 升级提示 banner + i18n 翻译补全

### Changed
- **Phase 1-3 模块拆分**：10 个大文件（`server.py`、`compliance.py`、`data_proxy.py`、`vector.py`、`llm_provider.py`、`dispatcher.py`、`maop_loop.py`、`plugin.py`、`engine.py`、`ldap_provider.py`）拆分为 re-export shim + 实现模块，保持 `from maop.xxx import Y` 零改动

### Fixed
- **全量测试 0 failed**：修复 `dispatcher.py` `otel_span` re-export 缺失 + `test_routing_trace.py` fixture（patch `dispatch_core.otel_span`）+ 22 个 enterprise 测试加 `pytest.importorskip("maop.enterprise")` 守卫 → ruff 0 + mypy 0 + pytest **7035 passed, 57 skipped, 0 failed**
- **ADR-017 enterprise branch test**：加 `importorskip` 守卫适配 Community 版无 `maop.enterprise` 模块

## [5.1.0] — 2026-08-14

### Added

#### 企业版功能（v5.0.2+）
- **许可证管理**：License 管理 UI + CRUD API + 过期预警 + 特性开关绑定
- **SSO/SAML 集成**：SAML 2.0 IdP 对接 + SP 配置 + 属性映射
- **审计日志**：全操作审计 + 审计日志查询/导出 + 不可篡改性
- **配额管理**：租户级配额（API 调用/Token/存储）+ 超额拒绝 + 用量看板
- **API Key 管理**：API Key 生成/轮转/吊销 + scope 权限绑定
- **通知中心**：邮件/Webhook 通知 + 通知模板 + 事件订阅

#### v5.1.0 新功能
- **LLM 任务拆分**：自动将复杂任务拆分为子任务 + DAG 依赖编排
- **工作流编辑器**：可视化 DAG 工作流编辑 + 节点配置 + 保存/加载
- **配置历史**：配置变更快照 + 一键回滚 + 差异对比
- **Skill 编辑器 + 市场**：Skill 在线编辑 + 模板市场 + 导入/导出
- **异常调度**：异常检测 + 自动重试策略 + 降级调度
- **Hook 配置**：Webhook Hook 配置 UI + 事件触发 + 执行日志

### Changed
- 版本号统一升级至 v5.1.0（pyproject.toml / __init__.py / Dockerfile / package.json / package-lock.json / Chart.yaml / values.yaml / controller.yaml）
- 移除 pyproject.toml addopts 的 --cov-fail-under=50，改由 ratchet 脚本渐进门禁
- 修复 /users 路由守卫缺失（补 meta.requiresEnterprise）
- 修复 Audit.test.js chart.js/jsdom unhandled rejection
- **统一错误响应格式对齐 `ErrorSchema`**：所有经 `handle_api_errors` 装饰器（含 `HTTPException`）的端点错误响应采用扁平结构 `{status, error, code, detail, request_id}`（全部 string 类型，`status` 默认 `"error"`，其余默认空串），取代历史嵌套 `{"error":{code,message}}` 描述。权威定义见 `py/maop/dashboard/error_handler.py` `ErrorSchema`。
- **⚠ Breaking（P2-1）：Engine 无 `step_executor` 时不再返回假成功**。AGENT/DAG/PLAN 步骤在未注入执行器时一律返回 `StepStatus.FAILED` + `error="No step executor configured..."`（此前 PLAN 回落路径与 AGENT/DAG 在无执行器时错误地返回 `SUCCESS` 占位文本，构成监控假阳性）。构造 `Engine()` 未传入 `step_executor` 会在日志打印 warning。下游测试改为注入 mock executor（如 `test_integration.py` 的 `_success_executor`、`test_distributed_execution.py` 的 `_mock_step_executor`）。

## [5.0.2] — 2026-08-13

### Added

- 前端设计规范文档 `docs/frontend-style-guide.md`（10 章节，沉淀迭代 A-C 组件契约）
- RFC-001 迭代 C 补全：DetailDrawer 迁移（Agents.vue memoryPanel + evolutionPanel）
- Users.vue 升级到 ListPageLayout（三态交给组件）

### Changed

- 布局重构：flex 并排 → 层叠覆盖（顶栏全宽 fixed + 侧栏全高 fixed 覆盖顶-左侧）
- 折叠时侧栏变窄，顶栏左侧品牌区自然露出，顶栏零抖动
- z-index 层级：顶栏 10 < 侧栏 20 < 移动端 drawer 30 < 遮罩 80 < modal 90 < toast 100

### Fixed

- ESLint warnings 51→0（48 no-unused-vars + 3 vue/no-template-shadow）

## [5.0.1] — 2026-08-13

### Fixed

- **M5 回归修复**: `agent/llm_chat/llm_provider.py` 的 `_record_cost` 改为 async，与 `core/llm_provider.py` 保持一致
- **enterprise_api_guard 中间件顺序**: 未认证请求现在返回 401 而非 404（guard 先放行让 AuthMiddleware 处理认证）
- **ADR010 回归测试路径修正**: `parents[3]` → `parents[2]`，`py/MAOP/MAOP_loop.py` → `py/maop/maop_loop.py`
- **enterprise guard 测试路径修正**: `/api/v1/tenant/list` → `/api/v1/tenant/create`（命中 404 分支）
- **rules.yaml 断言改进**: 检查非注释行中的 `per_agent`，避免误匹配注释

### Changed

- **前端硬编码颜色 → CSS var token**: KnowledgeGraph/McpTopology/DagGraph/Agents/Users/Observability/pages.css 中的硬编码 hex 替换为 CSS 变量
- **前端布局间距统一**: App.vue nav-section/nav-link 使用 `--sp` token 替代硬编码 px
- **重复 CSS 规则清理**: pages.css 中 `.deg-reason` 和 `.status-dot` 重复定义去重
- **ESLint warnings 自动修复**: 310 → 51（259 个属性排序等自动修复）

### Removed

- 6 个临时日志文件（docs/_review_*.log）

## [5.0.0] — 2026-08-11

### ⚠ Breaking Changes

本版本为 major release，含不兼容变更。详见 [MIGRATION-5.0.md](docs/migration-5.0.md)。

#### Removed（废弃 ≥ 2 版本的 API）

- **`maop.dashboard.provider.create_app()`**：自 v4.0.0 起废弃，现移除。生产代码应使用 `maop.dashboard.server:app`。该函数创建的隔离 FastAPI app 与主 server 路由冲突，是 v3.x 遗留。
- **`maop.dashboard.provider._render_html()`**：自 v4.0.0 起废弃，现移除。v3.x 静态 HTML 渲染器，已被 Vue 3 SPA 取代。
- **`maop_plan.py` legacy keyword routing**：`_fallback_keyword_route()` / `_route_by_keyword()` 及 `_ROUTING_RULES`，自 v4.0.0 起标记为 DEPRECATED fallback，现移除。路由统一走 config routing，miss 时回退到 `"chat"/"claude"` 默认值。
- **`/api/batch` 端点**：`dashboard/routers/data.py` 中 `deprecated=True` 的批量端点，前端不再调用，现移除。

#### Deferred（推迟到 v6.0.0）

以下 API 虽已废弃，但因引用链复杂（多模块 re-export + 测试 mock.patch 路径绑定），在 v5.0.0 保留并加强 deprecation warning，将在 v6.0.0 移除：
- `maop.core.agent.delegation.subagent_delegation` re-export shim → `subagent_lifecycle`
- `maop.core.project_context` / `maop.core.agent.memory_ctx.project_context`

#### Changed（配置收敛）

- **短名环境变量加 DeprecationWarning**：以下短名仍可使用但会发出告警，推荐迁移到规范长名（将在 v6.0.0 移除短名）：
  - `MAOP_PORT` → `MAOP_DASH_PORT`（dashboard 监听端口）
  - `MAOP_WORKERS` → `MAOP_DASH_WORKERS`（uvicorn worker 数）
  - `MAOP_TLS` → `MAOP_TLS_ENABLED`（TLS 开关）
  - `MAOP_AUTH` → `MAOP_AUTH_ENABLED`（认证开关）
- **`MAOP_PORT` 不再被直接读取**：`.env.example` 中 `MAOP_PORT` 标记为 deprecated alias，实际端口由 `MAOP_DASH_PORT` 控制。

### Added

- **流式 Agent token 响应增强**：
  - 后端新增 `/api/stream/agent/{execution_id}` SSE 端点，支持 Agent 执行过程的 token-by-token 流式推送（区别于 `/api/chat/stream` 的 chat 流式，本端点针对 agent 任务执行）。
  - 前端新增 `useAgentTokenStream.js` composable，封装 EventSource 连接、token 累积、自动重连、AbortController 清理。
  - `Chat.vue` 集成增强：流式渲染时显示 token 计数与流速指示，`onMeta` 回调接收 `tokens` / `model` / `latency_ms` 元数据。
- **迁移指南 `docs/migration-5.0.md`**：覆盖后端 API 变更、配置环境变量迁移、Docker 部署变更、前端无变更说明。
- **ROADMAP.md 状态更新**：v4.5.0 标记为已发布，v5.0.0 标记为当前版本。
- **Phase 5b — 发布/性能/合规修复（G-08~G-17）**：
  - **G-12 SLA/支持体系**：新增 `docs/sla.md`（服务等级协议，含可用性 SLO、延迟 SLO、违约补偿）与 `docs/support-policy.md`（三级支持体系 L1/L2/L3、工单优先级、版本支持策略）。
  - **G-13 隐私政策/DPA**：新增 `docs/privacy-policy.md`（PIPL/GDPR/CCPA 合规）、`docs/terms-of-service.md`（双版许可条款）、`docs/dpa.md`（数据处理协议）、`docs/cla.md`（贡献者许可协议）。
  - **G-14 PG 高可用**：新增 `deploy/patroni/`（Patroni 3 节点集群配置 + HAProxy 读写分离 + WAL-G 备份回调）；`docker-compose.prod.yml` 增加 `patroni1/2/3` + `pg-haproxy` 服务（`--profile patroni`）；新增 `docs/runbook.md`（故障切换运维手册）。
  - **G-16 CI Playwright E2E**：`.github/workflows/ci.yml` 增加 `e2e` job（Playwright + chromium，浏览器缓存，HTML 报告上传）；`dashboard-enterprise/package.json` 增加 `test:e2e` 脚本。
  - **G-17 K8s Operator 集成测试**：`py/tests/test_k8s_operator.py` 扩展支持 kind/k3s 集成测试（`TestKindIntegration` 启动临时 kind 集群、`TestK3sIntegration` 对接 k3s、`TestStaticCRValidation` 纯 Python CRD schema 验证、`TestKubectlDryRun` 客户端 dry-run）。
  - **G-09 性能压测**：新增 `py/tests/performance/` 目录，含 `k6_maop_load.js`（k6 JavaScript 压测脚本，自定义指标 snake_case，SLO 门禁对齐 SLA）、`locust_maop_load.py`（Locust Python 压测脚本）、`test_performance_smoke.py`（压测脚本语法/SLO 对齐冒烟测试）；新增 `docs/capacity-planning.md`（容量规划与性能基准）。
  - **G-10 LDAP 真实环境验证**：新增 `py/tests/test_ldap_real_env.py`（真实 OpenLDAP 联调测试 + Docker OpenLDAP 容器化测试 + mock 单元测试）；新增 `docs/ldap-integration-guide.md`（LDAP/AD 集成指南）。

### Fixed

- 版本号统一：`py/maop/__init__.py`、`py/pyproject.toml`、`py/Dockerfile`、`dashboard-enterprise/package.json`、`dashboard-enterprise/package-lock.json`、`deploy/k8s/operator/Chart.yaml`、`deploy/k8s/operator/values.yaml`、`deploy/k8s/operator/controller.yaml` 全部从 4.5.0 统一到 5.0.0。
- `.env.example` 与代码实际环境变量对齐审计：补充 `MAOP_DASH_PORT` 规范名说明，标记 `MAOP_PORT` 为 deprecated alias。

## [4.5.0] — 2026-08-06

### Added

- **core/ 子包重构（Phase D-1）**：将 `core/`（116 模块）拆分为 9 个职责清晰的子包（`persistence`、`llm`、`vector`、`mcp`、`observability`、`security`、`config`、`runtime`、`utils`），保留 `core/__init__.py` re-export shim，现有 `from maop.core.xxx import yyy` 调用零改动通过。
- **流式 DAG 执行进度推送（Phase D-2）**：新增 `DagProgressEmitter`，在 DAG 节点状态变更时通过 EventBus 推送增量事件；新增 SSE 端点 `/api/stream/dag/{execution_id}`（支持 Last-Event-ID 断线重连）；前端 `useDagProgress.js` composable 实时渲染节点状态（pending/running/success/failed/skipped）。
- **知识图谱可视化前端（Phase D-3）**：新增 `/api/knowledge-graph` 端点聚合三层记忆（short/long/vector）实体-关系数据；新增 `KnowledgeGraphView.vue` 基于 vis-network 渲染交互式知识图谱，支持节点筛选与路径高亮。
- 新增测试套件：`test_dag_progress.py`、`test_knowledge_graph_v2.py`、`test_dag_sse_endpoint.py`、`e2e/knowledge-graph.spec.js`。

### Fixed

- **`_execute_with_retry` 源码 bug**：`py/maop/loop_executor.py` 中 `_execute_with_retry` 方法被错误定义在 `_NoopEmitter` 类中，导致 `ExecuteMixin`（`MaopLoop` 继承）调用时 `AttributeError`。已将方法移至 `ExecuteMixin` 类，修复 17 个测试失败。
- **`test_dag_sse_endpoint.py` 测试间污染**：`_disable_auth` patch `maop.core.middleware.require_admin` 模块属性，但 `stream.py` 通过 `from ... import require_admin` 在模块加载时已绑定函数引用，全量测试中 stream 模块被先前测试加载后 patch 失效。改为直接 patch `stream` 模块的 `require_admin` 引用，修复 7 个测试失败。

### Changed

- 整体测试覆盖率从 85% 提升至 87%（34234 stmts, 4556 missing, 6130 passed, 0 failed）。
- 前端 vitest 160 passed（21 files）。

## [4.4.2] — 2026-08-06

### Added

- 新增 `ROADMAP.md`：MAOP 版本规划单一真相源，覆盖 v4.4.2 / v4.5.0 / v5.0.0 方向与验收标准。
- 新增 `deliverables/engineering-assurance/v4.4.1-fix-report.md`：归档 v4.4.1 修复清单（102 项，后端 24 + 交互 6 + 前端 72 + 测试 32 用例）。
- 新增 `deliverables/engineering-assurance/env-audit-4.4.2.md`：`.env.example` 与代码实际环境变量对齐审计报告。
- 新增 `deliverables/engineering-assurance/_extract_env.py`：env 审计可复现脚本。
- 前端 e2e 测试实跑通过（`enterprise-route-guard.spec.js` 12 用例，Playwright + chromium 安装并执行）。
- 后端覆盖率测试套件扩充（新增约 800 个测试用例，覆盖路由端点、core 模块、memory 模块等），整体覆盖率从 75% 提升至 85%。
- 前端单元测试 `vitest` 138 passed（20 files）。

### Changed

- 同步 `docs/adr/016-dual-edition-architecture.md` 待完善表：SAML SSO 状态由 `Medium / fail-closed 拒绝` 更新为 `Done`（已实现 SP-initiated SSO + XML 签名验证，见 `docs/enterprise/saml-sso-guide.md`）。
- 整体测试覆盖率从 75% 提升至 85%（33187 stmts, 5099 missing, 5744 passed, 0 failed）。
- `py/maop/core/vector.py` `EmbeddingProvider` → ABC + `@abstractmethod`（文档化抽象接口）。
- `py/maop/core/runtime.py` `BaseRuntime` → ABC + `@abstractmethod`（文档化抽象接口）。
- `py/maop/dashboard/routers/agents.py` `get_agent` 统一返回 `JSONResponse`（mypy return-value 修复）。

### Fixed

- `.env.example` 与代码实际环境变量对齐：补齐 5 个代码直接读取但未文档化的 `MAOP_*` 变量，差异归零。
  - `MAOP_KEY` / `MAOP_KEY_FILE`（API key vault 主密钥，`core/api_key_vault.py`）
  - `MAOP_TLS_ALLOW_DEPRECATED`（允许废弃 TLS 版本开关，`core/tls.py`，默认 fail-closed）
  - `MAOP_DB_URL`（Alembic 迁移 SQLAlchemy URL 覆盖，`migrations/alembic/env.py`）
  - `MAOP_PLUGIN_STRICT_CHECKSUM`（插件校验和严格模式，`core/plugin.py`，默认 fail-closed）
- 经审计核实 `.env.example` 中 16 个未被 `os.getenv` 直接读取的 `MAOP_*` 变量均为 pydantic `MAOPSettings` 间接读取或向后兼容别名，非僵尸变量，保留。
- `dashboard-enterprise/playwright.config.js` baseURL/port 不一致修复（baseURL 9079→5174, webServer.port 5173→5174，与 `vite.config.js` 实际端口对齐）。
- `dashboard-enterprise/package.json` 补 `@playwright/test` devDependency。
- `dashboard-enterprise/src/stores/edition.js` 冷加载缺陷修复：新增 `loadInitialEdition()` 从 localStorage 快照读初始值，修复路由守卫死代码（首次 page.goto 守卫总看到硬编码 'enterprise' 放行）。
- `config/models.yaml` 恢复为纯 YAML 格式（被 `yaml.dump` 覆盖产生 Python 对象标签，导致 `yaml.safe_load` 加载失败）。
- `py/tests/test_circuit_breaker.py` fixture 隔离：monkeypatch `_load_agent_names_from_config` 返回空列表，强制 fallback 到 DEFAULT_AGENTS，不依赖全局 agents.yaml 配置。

## [4.4.1] — 2026-07-31

### Fixed

- **后端 P0-P3 修复（24 项）**
  - 恢复 `data/migrations/` SQL 文件；Docker 环境变量名统一（`MAOP_*_BACKEND`）
  - 依赖 CVE 升级（cryptography<49, python-dotenv>=1.2.2, fastapi>=0.115）
  - 移除私钥 `enterprise/keys/private_key.pem`；audit 路由冲突修复
  - 10 个文件 36 处硬编码 .db 路径统一到 `get_db_path()`
  - Ruff 6 个 unused import + Mypy 15 个类型错误修复
  - 生产 compose 补充 `cap_drop: ALL` + `no-new-privileges`
  - CI 覆盖率阈值 60→80；`GRAFANA_PASSWORD` 弱默认值修复

- **前后端交互修复（6 项）**
  - Audit summary 嵌套结构对齐（`d.summary.total` / `d.summary.total_events`）
  - Audit events 字段映射（time/timestamp, level/severity, target/resource）
  - Settings config 补充 6 个缺失字段（dash_workers/root_dir/data_dir/db_path/memory_db_path/rate_limit_burst）
  - Tenants list 补充 usage 数据；Chat mapMsg 从 metadata 提取 image/model/tokens
  - SSE done 事件补充 tokens 和 model 字段

- **前端深度修复（72 项）**
  - P0: useToast 解构错误修复（6 个视图 toast 功能恢复）
  - P0: Models.vue reactive→ref 误用修复（下拉框恢复可用）
  - P0: i18n `t()` 支持插值参数 `{var}` 替换
  - P0: Card overflow 裁剪、z-index 断裂、未定义 token（--sp-10/--r/--info/--z-*）
  - P0: section/feature-grid/edition-* CSS 类冲突修复
  - P1: PageHeader 顶层 await import 改同步 import
  - P1: Chat button 嵌套修复（HTML 规范合规）
  - P1: 浅色主题 15+ 处硬编码颜色替换为语义 token
  - P1: CSP 添加到 index.html；对比度修复（--text-faint/--text3 加深）
  - P1: img alt 补充；图片上传 5MB 大小限制
  - P2: computed sort 缓存修改修复；WebSocket 重连计数重置
  - P2: Cost.vue NaN 防御；Monitor.vue diagnostics 空值防御
  - P2: useToast timer 清理；Chat AbortController + onUnmounted
  - P3: 10 处 `== null` → `=== null`；console.log 清理

### Added

- 5 个新测试文件（i18n/useToast/Audit/Chat/Models），32 个用例
- 覆盖率 ≥ 80%

## [4.4.0] — 2026-07-31

### Added

- **OmniRoute 升级为默认 LLM 出口（Phase 2）** — 显式 `default_provider` 字段方案：
  - `models.yaml` 新增顶层 `default_provider` 和 `default_model` 字段。
  - `ModelSelector` 新增 Step 1.5：当 `default_provider` 设置时，优先选择该 provider 的模型作为 primary。
  - OmniRoute 作为 fallback agent 加入 9 个 routing key（codegen, chat, refactor, review, planning, quickfix, docgen, techdoc, verify）。
  - `ModelRegistry.get_default_model()` 和 `get_default_provider()` 方法。
  - `LLMProviderFactory._get_default_model()` 辅助方法，`chat_with_fallback()` 在无 model 时使用默认 model。
  - `ModelRegistryConfig` 新增 `default_provider` / `default_model` Pydantic 字段。
- Enterprise license validation (Ed25519 signature + 7-day grace period)
- **前端统一 Vue3** — 原生 JS Dashboard 归档至 `archive/js-dashboard/`，个人版和企业版共享同一 Vue3 SPA（17 页面）
- **统一审计路由** — `audit.py` 同时支持企业版（EnterpriseAuditLogger）和个人版（AuditLog），消除路由冲突
- **`__main__.py`** — 支持 `python -m maop` 入口
- **n8n 纳入企业版 API 守卫** — `/api/n8n` 加入 `_ENTERPRISE_API_PREFIXES`

### Changed

- Expanded `omniroute-auto-coding` capabilities: +chat, quickfix, docgen, techdoc, verify
- Expanded `omniroute-auto-reasoning` capabilities: +chat, verify
- **Docker 环境变量名修正** — `MAOP_BACKEND_*` → `MAOP_*_BACKEND`（与代码一致）
- **依赖升级** — `cryptography<49`（CVE 修复）、`python-dotenv>=1.2.2`、`fastapi>=0.115`
- **生产 Docker compose 安全加固** — 所有服务补充 `cap_drop: ALL` + `no-new-privileges`
- **36 处硬编码 .db 路径统一到 `get_db_path()`** — 10 个模块、36 处替换
- **迁移 SQL 文件恢复** — `data/migrations/` 目录重建

### Security

- **移除 `enterprise/keys/private_key.pem`** — 私钥不再随包分发
- **audit 路由冲突修复** — `system.py` 的 audit 端点移至 `audit.py`，企业版审计功能不再被覆盖

## [4.3.0] — 2026-07-25

### Added

- **安全审计修复（7 Critical + 2 High）** — 全面消除 RCE、认证绕过与事件循环阻塞风险：
  - Redis backend 序列化由 `pickle` 改为 JSON，消除反序列化 RCE 风险。
  - 认证环境变量名统一：`MAOP_AUTH_ENABLED` → `MAOP_AUTH`。
  - 路径大小写 bug 修复（`py/MAOP` → `py/maop`），恢复 Linux 兼容性。
  - async 端点 `subprocess.run` → `asyncio.create_subprocess_exec`，避免阻塞事件循环。
  - Swagger / OpenAPI 文档在生产环境禁用。
  - `DEFAULT_AGENTS` 改为从 `config/agents.yaml` 动态加载。
  - CI 覆盖率门槛由 40% 提升至 60%。
- **PreemptableWorkerPool 完善** — 实现 `wait()` 方法、跟踪 watcher 任务、异常不再被静默吞掉。
- **hot_reload 哈希算法升级** — MD5 → SHA-256，与项目完整性标准一致。
- **`.env.example` 补充** — 8 类环境变量文档化。
- **生产 compose 凭证策略** — 弱凭证默认值改为强制要求覆盖。

### Fixed

- `docker-compose.prod.yml` 中 5 处弱凭证（PostgreSQL / Vault / Redis / Grafana）。
- MCP Marketplace SSRF 漏洞（URL scheme 白名单）。
- MCP stdio 命令注入（命令白名单）。
- MCP dashboard 权限绕过（`user_context` 传递）。
- 认证默认配置（`MAOP_AUTH_DISABLED_ADMIN=0`）。
- Prometheus 配置挂载与服务名解析。
- dashboard / worker 结构化 JSON 日志支持。
- HAManager dual mode（Redis + Memory）协同。

---

## [4.2.0] — 2026-07-25

### Added

- **Phase δ：MCP 生态扩展** — MCP Marketplace（注册表 / 搜索 / 安装 / 校验）、MCP 权限审计日志、结果缓存与并发控制、命令白名单与严格模式。
- **Phase γ：智能调度系统** — SLA 感知优先级队列、多目标优化（Pareto 前沿 + TOPSIS）、软抢占 `PreemptableWorkerPool`、路由决策记录与查询。
- **Phase β：自我演化闭环** — 执行历史分析器、Prompt A/B 测试框架、Agent 策略学习器、缓存策略进化器。
- **Phase α：可观测性深化** — OpenTelemetry 集成、Grafana SLO 仪表盘、结构化 JSON 日志、SLO 告警规则。

### Fixed

- P0 timeseries bug 修复。
- CircuitBreaker 测试隔离。
- flaky 并发测试稳定化。
- Dashboard MCP router 注入 δ 组件。

---

## [4.1.0] — 2026-07-25

### Added

- **Phase 3：PostgreSQL + Vault + HA 架构升级**
  - PostgreSQL 生产化集成（95%）—— 连接池、混合存储、迁移管理。
  - Vault 密钥管理集成（85%）—— 动态密钥、lease 续期、密钥轮换。
  - 分布式 HA 高可用（90%）—— Redis 租约选举、fencing token、自动故障转移。
  - Redis Backend（Cache / Queue / Lock）三合一。
  - ADR-015：分布式 HA 设计文档；ADR-014：HA / backend 文档对齐实际实现。
- **企业版模块测试（87 个新测试）** — 覆盖审计、RBAC、租户、HA、容器、TLS 自动配置、PG 持久化层。
- **多因素路由评分算法** — dashboard 实时数据 + Copilot agent。
- **Stage 2 工程化** — API 版本化、类型注解、缓存、Alembic 迁移。
- **Stage 1 文档** — 用例、e2e 测试、契约测试、算法注释。
- **采纳审计报告建议** — 迁移、工具版本化、错误 schema、备份 WAL。

### Changed

- `docker-compose` 使用根目录作为单一来源；`py/docker-compose.yml` 已删除。
- 前端统一为 Vue3 SPA（`dist-enterprise`）。

### Fixed

- **CI 修复** — Docker build 在 `main` 和 `master` 分支均触发。
- **R3 / R4 项目审计修复** — 合计 8 P0 + 18 P1 + 15 P2 阻塞与关键问题（含 `deploy.py` SQLite 连接泄漏、`auth_login` 返回类型一致性、`data/data/` 嵌套、ADR-006 标题等）。
- **aiohttp session 泄漏 + codex driver `IndexError`**。
- **change_tracker `rglob` 性能 bug + `bloom_filter` 导入 + 部署脚本**。
- **依赖管理 + `ERR_ABORTED` 根因 + 8 处测试失败**。
- **10 处遗留测试失败 + CI e2e 支持**。
- **`DeprecationWarning` 静默 + `maop_loop` 测试提速**。

---

## [4.0.0] — 2026-07-19

### New Features

- **Plugin System** (`core/plugin.py`) — Full plugin lifecycle manager with discovery, loading, starting, stopping, and hot-reload. Plugins declare metadata in `maop-plugin.yaml` manifests, expose `maop_plugin_init`/`maop_plugin_shutdown` entry points, and can register hooks via HookManager bridge. SQLite persistence for plugin state. Dashboard API at `/api/plugins/*`.

- **Real-time Cost Tracker** (`core/cost_tracker.py`) — Per-call token usage recording with automatic cost calculation for 7+ models (GPT-4o, Claude 3.5, DeepSeek, etc.). Aggregated summaries by model/agent/session. Daily/monthly budget monitoring with HookManager alert integration. Customizable pricing. Dashboard API at `/api/cost/*`.

- **ReAct Loop Engine** (`core/react_loop.py`) — Thought→Action→Observation micro-cycle for autonomous agent reasoning. Integrates FunctionCallBridge and ChangeTracker. Configurable max iterations and tool selection.

- **Change Tracker** (`core/change_tracker.py`) — File system snapshot/diff/rollback with unauthorized change detection. Change log persistence. Dashboard API at `/api/react/snapshots/*` and `/api/react/diff`.

- **Artifact Store** (`core/artifact_store.py`) — Versioned artifact storage with save/load/history/restore/tag/diff. Blob file persistence. Dashboard API at `/api/react/artifacts/*`.

- **Session Manager** (`core/session.py`) — Full session CRUD with status management, token budget tracking, and SQLite persistence.

- **Conversation Manager** (`core/conversation.py`) — Multi-turn message history with context window sliding, auto-compression, and message search.

- **Project Context** (`core/project_context.py`) — Project structure tree, tech stack detection, config file reading, instruction file injection, git status.

- **MCP Client Runtime** (`core/mcp_client.py` + `mcp_transport.py` + `mcp_registry.py`) — Full MCP protocol client with Stdio/SSE transport, tool discovery, and execution.

- **Function Calling Bridge** (`core/function_call.py`) — Unified OpenAI/Anthropic/Ollama function calling → MAOP ToolCall → MCP/ToolManager execution → result re-injection loop.

- **Structured Output Parser** (`core/output_parser.py`) — JSON extraction from code blocks/raw/embedded, Pydantic validation, schema verification gate.

- **Streaming Integration** (`core/streaming.py`) — SubprocessStreamer + StreamRegistry, driver streamer support, SSE endpoint.

- **Permission Manager** (`core/permission.py`) — Allow/ask/deny policy, HumanProxy integration into maop_execute + maop_loop.

- **Hook Decision Influence** — HookResult now supports decision (allow/deny/modify) + modified_data, enabling hooks to veto or transform pipeline data.

### Changed

- `maop_execute.py` — Added tools/provider/max_tool_rounds + react_mode/react_max_iterations parameters, function_call re-injection loop, ReAct mode branch.
- `maop_verify.py` — Added `_gate_schema` verification gate for structured output validation.
- `error_schema.py` — MaopResult now has `structured_output` field.
- `dashboard/server.py` — Registered 7 new routers (stream, permission, mcp, session, react, plugin, cost).
- Version bumped from 3.5.0 → 4.0.0.

### Tests

- 43 tests in `test_plugin_cost.py` (PluginManager: 19, CostTracker: 24)
- 31 tests in `test_react_loop.py` (ReactLoop + ChangeTracker + ArtifactStore)
- 36 tests in `test_session.py` (Session + Conversation + ProjectContext)
- 25 tests in `test_function_call.py` (FunctionCallBridge + ToolSchemaGenerator)
- 27 tests in `test_output_parser.py` (OutputParser + SchemaGate)
- Total new tests: **162**

---

## [3.5.0] — 2026-07-18

### New Features

- **Mavis subagent merge** — Restructured `agents.yaml`: removed `mavis-coder`, `mavis-general`, `mavis-verifier` as standalone agents; merged into `mavis.subagents` configuration. Subagents are now only reachable via parent delegation (`mavis/verifier`, `mavis/coder`, `mavis/general`).

- **Subagent routing support** — `Dispatcher._resolve_agent()` now supports `parent/child` format (e.g. `mavis/verifier`). When a `/` is detected, the parent's `cli` + child's `cli_args` are combined to build the final `AgentConfig`. Routing table entries `mavis/verifier` for `verify` and `review` routes now resolve correctly.

- **ConfigLoader subagents parsing** — `ConfigLoader.load()` now correctly parses the `subagents` section from `agents.yaml` into `AgentDef.subagents: dict[str, SubagentDef]`. Added `SubagentDef` Pydantic model with `cli_args`, `capabilities`, `description`, `model_display` fields.

### Changed

- **agents.yaml routing** — `review.fallback` changed from `mavis-verifier` to `mavis/verifier`; `verify.primary` changed from `mavis-verifier` to `mavis/verifier`.

### Tests

- Added 9 tests in `test_dispatcher_extended.py`: subagent resolution (parent/child, coder, unknown child/parent, caching, parent still works), dispatch integration, ConfigLoader subagents parsing.

---

## [3.3.0] — 2026-07-18

### New Features

- **Subagent hierarchical delegation** — Added `core/subagent.py` (SubagentManager) with parent/child lifecycle tracking, depth-limited recursive delegation, and inter-agent message passing. Integrated into `Dispatcher.delegate_to_subagent()` for recursive agent→agent→agent dispatch. Dashboard API: `/api/subagent/spawn`, `/api/subagent/terminate`, `/api/subagent/children`, `/api/subagent/tree`, `/api/subagent/send`, `/api/subagent/receive`, `/api/subagent/purge`.

- **Worktree parallel workspaces** — Added `core/worktree.py` (WorktreeManager) with git worktree-based filesystem isolation for parallel task execution. Automatic branch creation, stale worktree cleanup, and fallback directory copy when git is unavailable. Integrated into `WorkerPool._run_task()` for automatic worktree creation/cleanup per task. Dashboard API: `/api/worktree/create`, `/api/worktree/remove`, `/api/worktree/list`, `/api/worktree/cleanup`.

- **Protocol registry system** — Added `core/protocol.py` (ProtocolRegistry) for dynamic agent communication protocol registration with schema validation, versioning, and protocol-validated messaging. Supports runtime protocol write without code changes. Dashboard API: `/api/protocol/register`, `/api/protocol/unregister`, `/api/protocol/validate`, `/api/protocol/send`, `/api/protocol/messages`.

- **LLM Provider enhancements** — Four sub-features:
  - **Ollama local model support** — Added `ProviderType.OLLAMA` and `ProtocolType.OLLAMA_CHAT` to schema, with `thinking_to_api_params()` mapping for Ollama's `options.num_predict`. Added `ollama` provider to `models.yaml` (disabled by default).
  - **Provider/Model dynamic CRUD** — Added `add_provider()`, `remove_provider()`, `add_model()`, `remove_model()`, `save()` to `ModelRegistry`. Dashboard API: `/api/model/provider/add`, `/api/model/provider/delete`, `/api/model/add`, `/api/model/delete`.
  - **API Key encrypted vault** — Added `core/api_key_vault.py` (ApiKeyVault) with Fernet symmetric encryption, MAOP_KEY env / `.enc_key` file key management, and plaintext fallback. Dashboard API: `/api/model/key/store`, `/api/model/key/delete`, `/api/model/key/list`.
  - **Runtime health check** — Added `core/provider_health.py` (ProviderHealthChecker) with actual API call verification via httpx, latency measurement, and model list discovery. Dashboard API: `/api/model/health/check`.

### Tests

- Added `test_subagent.py` (14 tests): spawn, terminate, get, list_children, tree, messaging.
- Added `test_worktree.py` (10 tests, 9 skip if no git): create, remove, get, list, cleanup.
- Added `test_protocol.py` (20 tests): register, unregister, get, list, validate, messaging.
- Added `test_provider_enhanced.py` (24 tests): Ollama types, CRUD, vault, health check.

### Bug Fixes

- **worktree.py FileNotFoundError** — `_git()` now catches `FileNotFoundError` when git is not in PATH instead of crashing.
- **registry.py save() Enum serialization** — Fixed yaml.dump producing Python object tags for Enum values; now serializes `.value` strings.

---

## [3.2.3] — 2026-07-18

### Security (P0)

- **SQL injection in message_queue._count()** — Added `_VALID_TABLES` whitelist to prevent arbitrary table name injection.
- **Path injection in db_backup.py VACUUM INTO** — Added regex validation for backup path components + single-quote rejection.
- **Missing checksum validation in migration.py** — Added SHA256 checksum verification for migration SQL files.
- **GET /api/control/run → POST** — Converted state-changing control endpoint from GET to POST; added independent `/api/control/pause`, `/api/control/resume`, `/api/control/stop` endpoints.
- **Unrestricted agent upgrade in system.py** — Added package name whitelist for agent upgrade operations.
- **SHA-256 legacy auth compatibility** — Removed SHA-256 password hash fallback from `auth.py`; only bcrypt is accepted.
- **Guardrail fail-open** — Changed guardrail evaluation to fail-closed: exceptions during rule evaluation now block execution instead of allowing it.
- **CI Python 3.14** — Pinned CI to Python 3.12/3.13 (3.14 not yet stable).
- **docker-compose.yml build context** — Fixed build context path to point to correct `py/` directory.

### Hardening (P1)

- **Password leak to stderr** — Removed password plaintext logging from `auth.py` stderr output.
- **CORS tightened** — Restricted CORS allowlist from wildcard to explicit origins.
- **Shared db_utils.py** — Extracted common SQLite connection management into `core/db_utils.py` (WAL mode, foreign keys, busy timeout).
- **Shared _find_project_root()** — Unified project root discovery across modules.
- **Extended core module tests** — Added 58 new tests across 4 test files: `test_maop_loop_extended.py` (13), `test_guardrail_extended.py` (20), `test_engine_extended.py` (14), `test_dispatcher_extended.py` (11).
- **ADR-010 regression tests** — Added 9 regression tests in `test_adr010_regression.py`.
- **Dockerfile version sync** — Updated Dockerfile to version 3.2.2.
- **doc-pipeline Python driver** — Added Python CLI driver support for doc-pipeline workflow.

### Optimization & Quality (P2)

- **VectorStore performance** — numpy-accelerated cosine similarity, batch loading with `executemany`, triple cache (`_cache`/`_text_cache`/`_meta_cache`).
- **Dashboard router error handling** — Added `@handle_api_errors` decorator to `evolve.py`, `memory.py`, `model.py` routers.
- **CI consolidation** — Merged two CI workflow files into single `ci.yml`.
- **Coverage gate** — Added `--cov-fail-under=40` to CI pytest command.
- **kv_store connection mode** — Unified to WAL mode with foreign key enforcement.
- **TLS version enforcement** — `settings.py` now rejects TLSv1/TLSv1_1 connections.
- **CJK token estimation** — `context_compressor.py` now uses character-based estimation for CJK text.
- **maop.ps1 PS 5.1 compatibility** — Fixed PowerShell 5.1 compatibility issues in entry script.

### Deep Audit Fixes

- **S-01: db_backup.py VACUUM INTO injection** — Added single-quote rejection for backup_path to prevent SQL escape.
- **S-02: auth.py JWT key persistence** — `JWTHandler.__init__` now calls `load_jwt_secret()` instead of generating ephemeral key, ensuring JWT tokens survive restarts.
- **S-03: auth.py APIKeyStore thread safety** — `_get_conn()` now protected by `self._lock` to prevent race condition.
- **S-06: model.py model/switch validation** — Added `models.yaml` lookup to validate new_model before writing to `agents.yaml`.
- **S-08: middleware.py public paths** — Added `/api/auth/login` and `/api/auth/status` to public paths list.

### Integration Fixes

- **test_router_control.py** — Merged duplicate `TestControlRunPost` classes; aligned tests with POST handler behavior (no-op → default task); migrated pause/resume/stop tests to independent endpoints; converted maintain/provider-health tests from GET to POST.
- **test_dashboard_auth.py** — Updated SHA-256 compatibility tests to verify legacy hashes are rejected.
- **evolve.py @handle_api_errors** — Added missing error handler decorator to `/api/evolve/analyze` endpoint.
- **system.py indentation** — Fixed indentation error introduced during P0-4 refactoring.

### Deep Audit Fixes (Round 2)

- **S-04: dispatcher.py PS cli_args injection** — Added regex whitelist validation for `cli_args` template; rejects unsafe characters before PowerShell execution.
- **S-05: system.py pip whitelist hardening** — Changed `_get_allowed_packages()` to intersect dynamic agent CLI list with a hardcoded safe package set, preventing `agents.yaml` modification from bypassing the whitelist.
- **S-07: auth.py login brute-force protection** — Added per-username login failure tracking; locks account for 15 minutes after 5 consecutive failures.
- **S-09: tls.py placeholder cert rejection** — `create_ssl_context()` now checks for placeholder comment lines in cert files and refuses to load them.
- **S-10: data.py query() internal API** — Renamed `query()` to `_query()` with warning docstring; updated all internal and test callers.
- **S-11: kv_store.py connection leak** — Added `close()` method and `__del__` destructor to KVStore for proper connection cleanup.
- **P-03: DataBridge singleton instances** — Cached `ToolManager`, `SandboxManager`, `HumanProxy` instances in `DataBridge.__init__` instead of creating new ones per request.
- **P-04: DataBridge queue stats** — Merged 3 independent `SELECT COUNT(*)` queries into single `GROUP BY status` query.
- **P-08: system.py overview cache** — Added 60-second TTL cache to `/api/overview` endpoint to avoid re-scanning source files on every request.
- **A-01: Unified _connect()** — Replaced duplicated `_connect()` context managers in `vector.py`, `message_queue.py`, `sandbox.py`, `data.py`, `tool_manager.py`, `human_proxy.py` with `db_utils.sqlite_connect()`.
- **A-02: Deduplicated validate_identifier** — `data.py` now imports `validate_identifier` from `db_utils` instead of re-implementing it.
- **T-01: test_db_utils.py** — 17 new tests covering `validate_identifier` and `sqlite_connect` (WAL, foreign keys, rollback, row factory).
- **T-02: test_sandbox.py** — 10 new tests covering SandboxManager create/get/list/cleanup/run.
- **T-03: test_runtime.py** — 12 new tests covering `_resolve_cmd`, `LocalRuntime`, `IsolatedRuntime`, `RuntimeConfig`.

---

## [3.2.2] — 2026-07-17

### Fixed

- **cache_guard.py SingleFlight deadlock** — `_wait()` was called inside `with self._mutex` but itself tries to acquire `_mutex`, causing a non-reentrant lock deadlock. Fixed by extracting event reference inside lock, calling `_wait()` outside.
- **cache_guard.py SingleFlight result cleanup race** — `finally` block deleted results before waiters could read them. Fixed by lazy cleanup on next call.

### Added

- **934 new tests** (763→1697 total, all passing, zero regression):
  - `test_store.py` (57) — MemoryStore: store/search/facets/JSON search/trace/trajectory/inject/stats/prune
  - `test_vector.py` (46) — VectorStore: cosine similarity, embedding, indexing, search, persistence
  - `test_analyzer.py` (49) — DependencyDAG, topo sort, parallel groups, cycles, rule decomposition, strategy selection
  - `test_context_compressor.py` (40) — All 9 section extractors, compress/to_prompt, trim-to-budget
  - `test_timeseries.py` (38) — Record/batch, raw & aggregated query, downsampling, retention policy
  - `test_kv_store.py` (52) — Get/set, TTL, CAS, namespaces, bulk ops, stats
  - `test_monitoring.py` (40) — Metrics recording, alerting, health checks
  - `test_tool_manager.py` (32) — Tool registration, execution, validation
  - `test_cache_guard.py` (23) — SingleFlight, cache guard, dedup
  - `test_rate_limiter.py` (28) — Token bucket, sliding window, burst control
  - `test_state_classifier.py` (29) — Task state classification, transition rules
  - `test_human_proxy.py` (30) — Human-in-the-loop proxy, queue management
  - `test_auth.py` (35) — Authentication, token management, RBAC
  - `test_registry.py` (53) — ProviderRegistry, ModelRegistry, agent model resolution
  - `test_budget.py` (30) — BudgetGuard: spend tracking, alerts, reconciliation
  - `test_selector.py` (26) — Model selection, fallback chain, routing
  - `test_schema.py` (35) — All Pydantic models, enums, serialization
  - `test_provider.py` (36) — DashboardState, agent status, delegation counting
  - `test_loader.py` (33) — Config loading, YAML parsing, reload detection
  - `test_hot_reload.py` (29) — File hashing, change detection, watch loop
  - `test_evolve.py` (38) — EvolveEngine: analyze/suggest/apply/promote/status
  - `test_prompt_manager.py` (37) — Template CRUD, render, search, export/import
  - `test_concurrency.py` (33) — TaskQueue, TaskPool, SSEStreamer, TokenStreamer
  - `test_consolidator.py` (23) — Dream pipeline, summary building, consolidation
  - `test_cli.py` (20) — Argument parsing, command dispatch, all subcommands

---

## [3.2.1] — 2026-07-16

### Fixed

- **maop_execute trace_id propagation** — `maop_execute()` now sets `result.trace_id` after dispatch, ensuring trace IDs are always present on returned `MaopResult` objects for observability.
- **pyproject.toml dependencies** — Added missing `python-dotenv` (pydantic-settings dep), `mmh3` (MurmurHash3), and `sentence-transformers` (optional ML, under `[ml]`).

### Added

- **100 new tests** (663→763 total, all passing):
  - `test_fallback.py` (16) — FallbackManager: chain building, failure tracking, should_fallback, reset
  - `test_quota.py` (15) — QuotaEnforcer: sliding window, check/consume, usage stats
  - `test_maop_plan.py` (21) — Task routing: keyword matching, gate selection, budget config
  - `test_maop_verify.py` (24) — Verification gates: exit_code, output, content-safety, syntax-check, lint, dry-run
  - `test_maop_execute.py` (14) — Execution: dispatch, guardrail pre/post, trace_id, error handling

---

## [3.2.0] — 2026-07-16

### Summary

MAOP completes its transformation from a PowerShell-based multi-agent prototype to a
production-ready Python orchestration package. The PowerShell engine is archived to
`archive/ps-legacy/` (EOL v4.0). Python is now the sole runtime.

### Added

- **Model Management** — `model/registry.py`, `model/selector.py`, `model/budget.py`,
  `model/quota.py`, `model/fallback.py`, `model/schema.py` (7 modules).
  Policy-driven model selection with budget guards, quota tracking, and fallback chains.
- **Control Plane** — `control/plane.py`, `control/audit.py` (3 modules).
  Process-level job management with audit logging.
- **Contract Testing** — `tests/contract/` (4 test files, 92 tests).
  Behavioral, dispatcher, model API, and control API contracts.
- **Dashboard Routers** — `dashboard/routers/` package with 6 router modules
  (data, control, model, evolve, memory, system). 83 API endpoints.
- **Data Migrations** — `core/migration.py` + `data/migrations/001_init.sql`.
  Version-tracked schema migrations with up/down SQL and SHA256 checksums.
- **Dynamic Router (Python)** — `core/dynamic_router.py`.
  Port of `dynamic-router.ps1` with agent health scoring and 30s cache.
- **Safe Expression Evaluator** — `engine.py` `safe_eval()` using `ast` module.
  Replaces bare `eval()` with whitelist-based AST node evaluation.
- **WebSocket Snapshot Cache** — 5s TTL cache on `_ws_push_loop` to avoid
  redundant DataBridge queries during 15s broadcast intervals.
- **Chart.js Local Vendor** — `dashboard/js/vendor/chart.umd.min.js` (205 KB).
  Eliminates CDN dependency for air-gapped deployments.
- **Requirements Lock** — `py/requirements.lock` with full transitive dependency pinning.
- **Design Rules** — `dashboard/DESIGN_RULES.md` documenting the 3-level border hierarchy
  and divider width rules (outer 3px / middle 2px / inner 1px).
- **CHANGELOG.md** — This file.

### Changed

- **README** — Fully rewritten to reflect Python-first architecture, 6-layer structure,
  port 9079, 7 sub-packages, 711 tests, ~80 API endpoints, 18 agents, 13 gate scripts.
- **Version Unification** — `pyproject.toml`, `__init__.py`, `index.html` all at 3.2.0.
- **Dependency Pinning** — All `pyproject.toml` dependencies changed from `>=` to `==`.
- **agents.yaml** — `doc-pipeline` workflow migrated from `driver: wrapper` (PS) to
  `driver: cli` (Python). Model fields normalized with `model_ref` for precise lookup.
- **Model Registry** — `resolve_agent_model()` accepts `model_ref` parameter for exact
  model reference; heuristic fallback emits `logger.warning`.
- **Dashboard Frontend** — Monolithic `app.js` split into 11 modular files (`js/app-*.js`).
  Version branding updated from `v7` to `v3.2`.
- **maop.ps1** — PS fallback path replaced with archive notice and `exit 1`.
  Python-first routing is the only path.
- **hot_reload.py** — Watch list updated: `agents.yaml` + `rules.yaml` + `models.yaml`
  (removed `routing.yaml`).

### Removed

- **PowerShell Engine** — 61 `.ps1` files moved to `archive/ps-legacy/`.
  `src/` directory cleared of engine scripts.
- **Startup Wrappers** — `start_dashboard.py`, `start.bat`, `start-server.ps1`,
  `run_dashboard.py` moved to `archive/`. Canonical entry: `python -m maop.dashboard.server`.
- **Dead Code** — `_invoke_ps_fallback()` and `fallback_to_ps` parameter removed from
  `data_bridge.py`. `app.js.bak` and `app.js.legacy` deleted.
- **Residual Files** — `circuit-breaker.json` deleted (pure legacy, no Python reads/writes).
  `human-queue.json` reset to empty (stale data from 2026-07-03).
  `py/.tmp/` cache directory removed.
- **eval()** — Bare `eval(condition_expr, {"__builtins__": {}}, context)` replaced by
  `safe_eval(condition_expr, context)` with AST whitelist.

### Security

- **Command Injection** — 4 Critical PS injection vectors closed (delegate-plugin.ps1).
  Python dispatcher uses `shlex.quote()` / `subprocess` list args (no shell=True).
- **Path Traversal** — Gate name validation (`^[a-zA-Z0-9_-]+$`), memory ID validation,
  sandbox workdir confinement.
- **Expression Safety** — `safe_eval` blocks function calls, attribute chains to
  `__class__`/`__subclasses__`, and all builtin access. 12 expression tests + 5 attack
  vectors verified.
- **API Security** — TLS support, API key auth middleware, rate limiting (30 RPS / 60 burst),
  CORS allowlist.

### Tests

- **711 total tests** (up from 547).
- **561 passed** in system Python (without fastapi/pytest-asyncio).
- **Full pass** with dev dependencies installed: `pip install -e ".[dev]"`.

---

## [3.1.0] — 2026-07-14

### Added

- Dashboard v7 UI rewrite with 18 navigation items in 5 groups.
- Six-layer architecture: CLI → MaopLoop → Engine → Services → Infrastructure → Data.
- 42 Python modules (up from 13).
- Docker multi-stage build, docker-compose, graceful shutdown.
- GitHub Actions CI/CD pipeline.
- Pydantic-based configuration with `.env` support.

### Changed

- PowerShell-to-Python migration: 8 phases completed.
- 220 tests all green (0 failures).

---

## [3.0.0] — 2026-07-10

### Added

- Initial Python engine: `maop_loop.py`, `engine.py`, `dispatcher.py`, `guardrail.py`.
- SQLite-backed persistence: `maop.db`, `memory.db`, `queue.db`.
- FastAPI dashboard server (port 9079).
- Circuit breaker, DAG engine, memory store with vector search.

### Changed

- Architecture shift from PowerShell-first to Python-first.
- Config-driven agent registration via `agents.yaml`.

---

## [2.x] — 2026-06-01 to 2026-07-09

PowerShell era. 48 scripts in `src/`, 13 gate scripts in `src/gates/`.
See `archive/ps-legacy/` for historical code. These versions are EOL and unsupported.

---

## [1.x] — 2026-05-01 to 2026-05-31

Initial prototype. Single-file `maop.ps1` with basic Plan-Execute-Verify loop.
No dashboard, no model management, no contract tests.
