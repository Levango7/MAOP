# P2/P3 前端代码审核报告 — dashboard-enterprise

> 审核时间：2026-08-26
> 审核范围：`dashboard-enterprise/src/` 全目录
> 审核维度：死路由 / 硬编码颜色 / 硬编码文案 / 组件复用率 / i18n 缺失 / ESLint warnings / 未使用组件 / 可访问性 / console 残留 / 未使用 CSS
> 严重度定义：**P2** = 影响用户体验/可维护性的中等问题；**P3** = 微小的改进建议

## 第1章 审核概述

### 1.1 项目栈

- Vue 3.5 + Vue Router 4.5 + Pinia 3.0
- Vite 8.1 + Vitest 3.0 + Playwright 1.62
- ESLint 9.15 + eslint-plugin-vue 9.28 + Prettier 3.4
- 自研 i18n（`src/i18n/*.js`，非 vue-i18n）
- 设计 token：`src/styles/themes.css`（亮）+ `src/styles/tokens.css`（暗）

### 1.2 扫描规模

| 维度 | 数量 |
|---|---|
| `src/views/*.vue` | 32 个视图 |
| `src/components/*.vue` | 23 个组件 |
| `src/i18n/*.js` | 35 个翻译文件 |
| 路由（含重定向） | 32 条 |
| i18n 已用 key | 1563 |
| i18n 已定义 key | 1865 |

### 1.3 问题统计

| 严重度 | 数量 |
|---|---|
| P2 | 18 |
| P3 | 47 |
| **合计** | **65** |

## 第2章 P2 问题（影响用户体验/可维护性）

### 2.1 死路由 / 未使用路由

> **结论**：无死路由。`router/index.js` 注册的 29 个组件路由均有对应 `.vue` 文件；`nav.js` 中 28 个导航项均在 router 中注册。`Chat.vue` / `ControlPanel.vue` / `EvolutionHistory.vue` 虽未直接被 router 引用，但分别被 `Run.vue:198-199` 和 `Evolve.vue:184` 作为子组件 import，属于合并路由（RFC-001）后的内嵌视图，非死代码。`/control`、`/chat`、`/evolution-history` 三条 301 重定向保留书签兼容，目标 `/run`、`/evolve` 均存在。

### 2.2 未使用组件

#### P2-F-01 — NotificationBell 组件完全未被引用

| 字段 | 值 |
|---|---|
| 编号 | P2-F-01 |
| 严重度 | P2 |
| 问题类型 | 未使用组件 |
| 文件:行号 | `src/components/NotificationBell.vue`（整文件，10362 字节） |
| 问题描述 | `NotificationBell.vue` 在整个 `src/` 中 0 次引用（不含自身）。通知能力已由 `/notifications` 路由的 `Notifications.vue` 承载，顶栏未挂载铃铛入口，组件成为死代码。 |
| 修复建议 | 二选一：(1) 在 `TopBar.vue` 挂载 `<NotificationBell />` 恢复未读铃铛入口；(2) 确认不再需要后删除该组件及其相关 i18n/CSS。 |

### 2.3 i18n 缺失 key

> **扫描方法**：提取 `.vue` 中所有 `$t('key')` / `t('key')` 字面量调用，与 `src/i18n/*.js` 中定义的 key 比对。共发现 **39 个真正缺失的 key**（已排除 `view.apikeys.scopeGroup.` 和 `view.quotas.` 两个动态拼接前缀）。缺失 key 集中在错误提示（`*Unavailable` / `*Failed`）和状态文案，运行时回退到 key 名本身，对非英文用户不可读。

#### P2-F-02 — DetailDrawer 缺少 `action.close` 翻译

| 字段 | 值 |
|---|---|
| 编号 | P2-F-02 |
| 严重度 | P2 |
| 问题类型 | i18n 缺失 |
| 文件:行号 | `src/components/DetailDrawer.vue:20` |
| 问题描述 | 使用 `$t('action.close')`，但所有 i18n 文件均未定义 `action.close`。 |
| 修复建议 | 在 `src/i18n/index.js` 的 `en` 与 `zh` 块各添加 `'action.close': 'Close'` / `'action.close': '关闭'`。 |

#### P2-F-03 — Agents 视图缺少 3 个通用 key

| 字段 | 值 |
|---|---|
| 编号 | P2-F-03 |
| 严重度 | P2 |
| 问题类型 | i18n 缺失 |
| 文件:行号 | `src/views/Agents.vue:121`（`common.provider`）、`:385`（`common.disabled`）、`:704`（`view.agents.checkFailed`） |
| 问题描述 | 三处 `t()` 调用的 key 未在 i18n 中定义。 |
| 修复建议 | 在 `view-agents.js` 或 `index.js` 补齐 `common.provider`、`common.disabled`、`view.agents.checkFailed` 的中英文翻译。 |

#### P2-F-04 — Audit 视图缺少 7 个错误提示 key

| 字段 | 值 |
|---|---|
| 编号 | P2-F-04 |
| 严重度 | P2 |
| 问题类型 | i18n 缺失 |
| 文件:行号 | `src/views/Audit.vue:611,637,646,656,679,697,711` |
| 问题描述 | 缺失 key：`view.audit.rulesUnavailable`、`view.audit.saveFailed`、`view.audit.toggleFailed`、`view.audit.deleteFailed`、`view.audit.historyUnavailable`、`view.audit.summaryUnavailable`、`view.audit.eventsUnavailable`。这些是数据加载/操作失败时的回退文案，缺失时用户看到 key 名。 |
| 修复建议 | 在 `src/i18n/view-audit.js` 的 `en` 与 `zh` 块补齐这 7 个 key。 |

#### P2-F-05 — Licenses 视图缺少 4 个 key

| 字段 | 值 |
|---|---|
| 编号 | P2-F-05 |
| 严重度 | P2 |
| 问题类型 | i18n 缺失 |
| 文件:行号 | `src/views/Licenses.vue:175,417,493,510` |
| 问题描述 | 缺失 key：`view.licenses.status`、`view.licenses.loadFailed`、`view.licenses.renewFailed`、`view.licenses.revokeFailed`。 |
| 修复建议 | 在 `src/i18n/view-licenses.js` 补齐 4 个 key 的中英文翻译。 |

#### P2-F-06 — Models 视图缺少 7 个错误提示 key

| 字段 | 值 |
|---|---|
| 编号 | P2-F-06 |
| 严重度 | P2 |
| 问题类型 | i18n 缺失 |
| 文件:行号 | `src/views/Models.vue:238,242,246,250,263,267,271` |
| 问题描述 | 缺失 key：`view.models.registryUnavailable`、`view.models.modelsUnavailable`、`view.models.providersUnavailable`、`view.models.agentsUnavailable`、`view.models.availabilityUnavailable`、`view.models.policiesUnavailable`、`view.models.budgetUnavailable`。 |
| 修复建议 | 在 `src/i18n/view-models.js` 补齐 7 个 key。 |

#### P2-F-07 — Notifications 视图缺少 6 个 key

| 字段 | 值 |
|---|---|
| 编号 | P2-F-07 |
| 严重度 | P2 |
| 问题类型 | i18n 缺失 |
| 文件:行号 | `src/views/Notifications.vue:415,436,463,474,485,524` |
| 问题描述 | 缺失 key：`view.notifications.notificationsUnavailable`、`view.notifications.loadMoreFailed`、`view.notifications.markReadFailed`、`view.notifications.markAllReadFailed`、`view.notifications.deleteFailed`、`view.notifications.saveFailed`。 |
| 修复建议 | 在 `src/i18n/view-notifications.js` 补齐 6 个 key。 |

#### P2-F-08 — Overview / RBAC / Tenants / Users 视图缺少 9 个 key

| 字段 | 值 |
|---|---|
| 编号 | P2-F-08 |
| 严重度 | P2 |
| 问题类型 | i18n 缺失 |
| 文件:行号 | `src/views/Overview.vue:404`（`view.overview.loadFailed`）；`src/views/RBAC.vue:194,205`（`view.rbac.grantFailed`、`view.rbac.revokeFailed`）；`src/views/Tenants.vue:123,146,157,166,176`（5 个 `view.tenants.*Failed`）；`src/views/Users.vue:6,196,208`（`common.required`、`view.users.failed`、`view.users.networkError`） |
| 问题描述 | 4 个视图共缺失 9 个 key，均为错误提示或表单校验文案。 |
| 修复建议 | 分别在 `view-overview.js`、`view-rbac.js`、`view-tenants.js`、`view-users.js` 补齐。 |

### 2.4 硬编码颜色（真正硬编码，未走 token）

#### P2-F-09 — OnboardingWizard 遮罩与文字硬编码颜色

| 字段 | 值 |
|---|---|
| 编号 | P2-F-09 |
| 严重度 | P2 |
| 问题类型 | 硬编码颜色 |
| 文件:行号 | `src/components/OnboardingWizard.vue:86,95,96` |
| 问题描述 | `background: rgba(0,0,0,0.5)`（遮罩）、`color: #fff`（步骤点 active）、`color: #fff`（步骤点 done）均为硬编码，未走 `--overlay-scrim` / `--brand-contrast` token，暗色主题下遮罩过浅、白字与亮色背景对比度不足。 |
| 修复建议 | 改为 `background: var(--overlay-scrim, rgba(0,0,0,0.5))`、`color: var(--brand-contrast, #fff)`。 |

#### P2-F-10 — DagGraph 节点遮罩硬编码

| 字段 | 值 |
|---|---|
| 编号 | P2-F-10 |
| 严重度 | P2 |
| 问题类型 | 硬编码颜色 |
| 文件:行号 | `src/components/DagGraph.vue:383` |
| 问题描述 | `background: rgba(0, 0, 0, 0.3);` 硬编码遮罩色，未走 token。 |
| 修复建议 | 改为 `background: var(--overlay-scrim, rgba(0,0,0,0.3));`。 |

#### P2-F-11 — 多处 box-shadow 颜色硬编码

| 字段 | 值 |
|---|---|
| 编号 | P2-F-11 |
| 严重度 | P2 |
| 问题类型 | 硬编码颜色 |
| 文件:行号 | `src/components/NodeDetailPanel.vue:73`、`src/components/CoachMarks.vue:185`、`src/components/TopBar.vue:363`、`src/views/Run.vue:448`、`src/views/Settings.vue:917,1106`、`src/views/WorkflowEditor.vue:678` |
| 问题描述 | 7 处 `box-shadow` 直接使用 `rgba(0,0,0,*)` 或 `rgba(255,255,255,*)`，未走 `--shadow-*` token，暗色主题下阴影过重或不可见。 |
| 修复建议 | 抽取为 `--shadow-card`、`--shadow-modal`、`--shadow-inset-highlight` 等 token，在 `themes.css`/`tokens.css` 分别定义亮暗值。 |

### 2.5 可访问性（仅图标按钮缺少 aria-label）

> **扫描方法**：提取 template 中所有 `<button>`，标记既无 `aria-label`/`:aria-label` 又无 `title`/`:title` 的按钮。共 196 个候选，其中绝大多数含可见文本（如 `{{ t('common.cancel') }}`），无需 aria-label。以下仅列出**纯图标按钮**（无文本子节点）的真正缺陷。

#### P2-F-12 — NodeDetailPanel 关闭按钮缺少 aria-label

| 字段 | 值 |
|---|---|
| 编号 | P2-F-12 |
| 严重度 | P2 |
| 问题类型 | 可访问性 |
| 文件:行号 | `src/components/NodeDetailPanel.vue:6` |
| 问题描述 | `<button class="ndp-close" @click="$emit('close')"><AppIcon name="x" :size="16" /></button>` 仅含图标，无 `aria-label`，屏幕阅读器读不出"关闭"。 |
| 修复建议 | 添加 `:aria-label="t('common.close')"`。 |

#### P2-F-13 — ApiKeys 复制按钮缺少 aria-label

| 字段 | 值 |
|---|---|
| 编号 | P2-F-13 |
| 严重度 | P2 |
| 问题类型 | 可访问性 |
| 文件:行号 | `src/views/ApiKeys.vue:141` |
| 问题描述 | `<button class="btn btn--sm" type="button" @click="copyKey">` 仅含图标，无 `aria-label`。 |
| 修复建议 | 添加 `:aria-label="t('view.apikeys.copy')"`。 |

#### P2-F-14 — ControlPanel 操作按钮缺少 aria-label

| 字段 | 值 |
|---|---|
| 编号 | P2-F-14 |
| 严重度 | P2 |
| 问题类型 | 可访问性 |
| 文件:行号 | `src/views/ControlPanel.vue:47,59` |
| 问题描述 | `:47` 的 stop 按钮和 `:59` 的 checkUpgrade 按钮均仅含图标，无 `aria-label`。 |
| 修复建议 | 分别添加 `:aria-label="t('view.control.stop')"` 和 `:aria-label="t('view.control.checkUpgrade')"`。 |

#### P2-F-15 — Agents 操作按钮缺少 aria-label

| 字段 | 值 |
|---|---|
| 编号 | P2-F-15 |
| 严重度 | P2 |
| 问题类型 | 可访问性 |
| 文件:行号 | `src/views/Agents.vue:281,314` |
| 问题描述 | `:281` 的 addMemory 按钮和 `:314` 的 clearMemory 按钮仅含图标，无 `aria-label`。 |
| 修复建议 | 分别添加 `:aria-label="t('view.agents.addMemory')"` 和 `:aria-label="t('view.agents.clearMemory')"`。 |

#### P2-F-16 — Audit 导出/实时按钮缺少 aria-label

| 字段 | 值 |
|---|---|
| 编号 | P2-F-16 |
| 严重度 | P2 |
| 问题类型 | 可访问性 |
| 文件:行号 | `src/views/Audit.vue:44,47,120,150` |
| 问题描述 | 4 个按钮（exportCsv、exportJson、toggleLive、openRuleEditor）仅含图标，无 `aria-label`。 |
| 修复建议 | 分别添加对应的 `:aria-label`。 |

#### P2-F-17 — Cost / EvolutionHistory / Evolve 操作按钮缺少 aria-label

| 字段 | 值 |
|---|---|
| 编号 | P2-F-17 |
| 严重度 | P2 |
| 问题类型 | 可访问性 |
| 文件:行号 | `src/views/Cost.vue:10,51`、`src/views/EvolutionHistory.vue:11,89,245`、`src/views/Evolve.vue:10` |
| 问题描述 | 6 个按钮（refresh、startEdit、loadAll、close、approve、triggerEvolve）仅含图标，无 `aria-label`。 |
| 修复建议 | 分别添加对应的 `:aria-label`。 |

#### P2-F-18 — KnowledgeGraph / Logs / Models 等视图图标按钮缺少 aria-label

| 字段 | 值 |
|---|---|
| 编号 | P2-F-18 |
| 严重度 | P2 |
| 问题类型 | 可访问性 |
| 文件:行号 | `src/views/KnowledgeGraph.vue:17,20,100`、`src/views/Logs.vue:10`、`src/views/Models.vue:5`、`src/views/Monitor.vue`（refresh 按钮）、`src/views/Quotas.vue`（refresh 按钮）、`src/views/Search.vue`（refresh 按钮）、`src/views/VectorSearch.vue`（refresh 按钮） |
| 问题描述 | 多个视图的 refresh/工具按钮仅含图标，无 `aria-label`，屏幕阅读器无法识别用途。 |
| 修复建议 | 统一为所有仅含图标的 `<button>` 添加 `:aria-label`，复用 `common.refresh` 等已有 key。 |

## 第3章 P3 问题（微小改进建议）

### 3.1 硬编码颜色（var() fallback 与 token 不一致）

> 这些 `var(--token, fallback)` 形式的 fallback 值与 `tokens.css`/`themes.css` 中定义的 token 值不一致。当 token 未定义时（如降级环境），fallback 会与设计系统脱节。属低风险，归 P3。

#### P3-F-01 — Monitor.vue fallback 颜色与 token 不一致

| 字段 | 值 |
|---|---|
| 编号 | P3-F-01 |
| 严重度 | P3 |
| 问题类型 | 硬编码颜色（fallback 不一致） |
| 文件:行号 | `src/views/Monitor.vue:507,529,536,539,591,594,598,601,605,608,618` |
| 问题描述 | 11 处 fallback 与 token 不一致：`--text-faint` 用 `#94a3b8`（token 为 `#8a93a3`）、`--fail` 用 `#ef4444`（token 为 `#f85149`）、`--warn` 用 `#f59e0b`（token 为 `#d29922`）、`--success` 用 `#22c55e`（token 为 `#3fb950`）。 |
| 修复建议 | 统一 fallback 为 token 中定义的值，或移除 fallback 依赖 token 必定义。 |

#### P3-F-02 — Chat.vue fallback 颜色与 token 不一致

| 字段 | 值 |
|---|---|
| 编号 | P3-F-02 |
| 严重度 | P3 |
| 问题类型 | 硬编码颜色（fallback 不一致） |
| 文件:行号 | `src/views/Chat.vue:453` |
| 问题描述 | `var(--text-faint, #999)` 的 fallback `#999` 与 token `#8a93a3` 不一致，且 `#999` 在亮色背景上对比度不足。 |
| 修复建议 | 改为 `var(--text-faint, #8a93a3)`。 |

#### P3-F-03 — NodeDetailPanel / NotificationBell / Notifications / StatCard fallback 不一致

| 字段 | 值 |
|---|---|
| 编号 | P3-F-03 |
| 严重度 | P3 |
| 问题类型 | 硬编码颜色（fallback 不一致） |
| 文件:行号 | `src/components/NodeDetailPanel.vue:124`（`--fail` 用 `#dc2626`，token 为 `#f85149`）；`src/components/NotificationBell.vue:312`、`src/views/Notifications.vue:576,587`、`src/components/StatCard.vue:61`（`--info` 用 `#38bdf8`，token 为 `#4cc2ff`） |
| 问题描述 | 5 处 `--fail`/`--info` fallback 与 token 不一致。 |
| 修复建议 | 统一 fallback 为 token 值。 |

#### P3-F-04 — Audit / SkillMarket fallback 不一致

| 字段 | 值 |
|---|---|
| 编号 | P3-F-04 |
| 严重度 | P3 |
| 问题类型 | 硬编码颜色（fallback 不一致） |
| 文件:行号 | `src/views/Audit.vue:439`（`--chart-5` 用 `#ef4444`，token 为 `#9e8cfc`）；`src/views/SkillMarket.vue:224`（`--info-soft` 用 `#f0f5ff`，未在 token 中定义）；`src/views/SkillMarket.vue:243`（`--info-strong` 用 `#1d4ed8`，未在 token 中定义） |
| 问题描述 | 3 处 fallback 与 token 不一致或 token 未定义。 |
| 修复建议 | 统一 fallback；若 `--info-soft`/`--info-strong` 需使用，应在 `tokens.css`/`themes.css` 中补定义。 |

### 3.2 硬编码文案

> **结论**：无硬编码文案。template 部分未发现中文/英文纯文本节点，所有可见文案均通过 `$t()` / `t()` 走 i18n。注释中的中文不计。

### 3.3 组件复用率

#### P3-F-05 — FilterBar 复用率低

| 字段 | 值 |
|---|---|
| 编号 | P3-F-05 |
| 严重度 | P3 |
| 问题类型 | 组件复用率低 |
| 文件:行号 | `src/components/FilterBar.vue` |
| 问题描述 | `FilterBar` 仅被 2 个视图使用（`Audit.vue`、`Notifications.vue`），而 `ListPageLayout` 已被 14 个视图使用。部分视图（如 `Tasks.vue`、`ApiKeys.vue`）自行实现过滤栏，未复用 `FilterBar`。 |
| 修复建议 | 评估 `Tasks.vue`、`ApiKeys.vue` 等视图的过滤栏是否可迁移到 `FilterBar`，提升复用率。 |

#### P3-F-06 — 组件复用率统计

| 字段 | 值 |
|---|---|
| 编号 | P3-F-06 |
| 严重度 | P3 |
| 问题类型 | 组件复用率统计 |
| 文件:行号 | N/A |
| 问题描述 | 统一组件复用率：`ListPageLayout` 14、`PageHeader` 11、`EmptyState` 11、`Card` 12、`Badge` 19、`Skeleton` 10、`StatCard` 7、`DataTable` 7、`DetailDrawer` 8、`Segmented` 5、`FilterBar` 2、`AppIcon` 37。`Toast`/`AppFooter`/`CoachMarks`/`TopBar`/`CommandPalette` 各 1 次（顶层挂载，正常）。 |
| 修复建议 | `DetailDrawer`（8 次）可进一步推广到 `ApiKeys`/`Licenses`/`SsoProviders`/`Users` 等仍用自研 modal 的视图，统一"查看详情"交互。 |

### 3.4 ESLint warnings

> 运行 `npm run lint` 输出：`6 problems (0 errors, 6 warnings)`，全部归 P3。

#### P3-F-07 — Docs.test.js 未使用 vi

| 字段 | 值 |
|---|---|
| 编号 | P3-F-07 |
| 严重度 | P3 |
| 问题类型 | ESLint warning |
| 文件:行号 | `src/__tests__/Docs.test.js:7` |
| 问题描述 | `'vi' is defined but never used`（no-unused-vars）。 |
| 修复建议 | 删除未使用的 `vi` import，或补充使用 `vi` 的测试用例。 |

#### P3-F-08 — OnboardingWizard 首属性换行

| 字段 | 值 |
|---|---|
| 编号 | P3-F-08 |
| 严重度 | P3 |
| 问题类型 | ESLint warning |
| 文件:行号 | `src/components/OnboardingWizard.vue:12` |
| 问题描述 | `Expected a linebreak before this attribute`（vue/first-attribute-linebreak）。 |
| 修复建议 | 运行 `npm run lint:fix` 自动修复。 |

#### P3-F-09 — useDagProgress 未使用 _getToken

| 字段 | 值 |
|---|---|
| 编号 | P3-F-09 |
| 严重度 | P3 |
| 问题类型 | ESLint warning |
| 文件:行号 | `src/composables/useDagProgress.js:73` |
| 问题描述 | `'_getToken' is defined but never used`（no-unused-vars）。下划线前缀通常表示有意未使用，但 ESLint 仍报警。 |
| 修复建议 | 若确为内部辅助函数，添加 `// eslint-disable-next-line no-unused-vars`；若为死代码则删除。 |

#### P3-F-10 — useMarkdown 未使用 codeLang

| 字段 | 值 |
|---|---|
| 编号 | P3-F-10 |
| 严重度 | P3 |
| 问题类型 | ESLint warning |
| 文件:行号 | `src/composables/useMarkdown.js:73` |
| 问题描述 | `'codeLang' is assigned a value but never used`（no-unused-vars）。 |
| 修复建议 | 删除未使用的 `codeLang` 变量赋值。 |

#### P3-F-11 — EvolutionHistory 属性顺序

| 字段 | 值 |
|---|---|
| 编号 | P3-F-11 |
| 严重度 | P3 |
| 问题类型 | ESLint warning |
| 文件:行号 | `src/views/EvolutionHistory.vue:128,278` |
| 问题描述 | `:128` `Attribute ":aria-expanded" should go before "@click"`、`:278` `Attribute ":title" should go before "@click"`（vue/attributes-order）。 |
| 修复建议 | 运行 `npm run lint:fix` 自动修复。 |

### 3.5 console.log / debugger 残留

> **结论**：无 `console.log` / `console.debug` / `debugger`。15 处 `console.warn` / `console.error` 均为 catch 块中的错误日志，语义合理。但生产环境会暴露错误细节到控制台，建议统一 logger。

#### P3-F-12 — console.warn/error 未统一 logger

| 字段 | 值 |
|---|---|
| 编号 | P3-F-12 |
| 严重度 | P3 |
| 问题类型 | console 拋留（改进建议） |
| 文件:行号 | `src/App.vue:115`、`src/views/Chat.vue:438`、`src/views/Monitor.vue:311,396`、`src/views/Observability.vue:274,284,294`、`src/views/Overview.vue:241`、`src/views/Search.vue:132`、`src/views/ThreeLayerMemory.vue:245,273`、`src/views/Users.vue:156`、`src/stores/edition.js:48,57`、`src/stores/realtime.js:69` |
| 问题描述 | 15 处 `console.warn`/`console.error` 分散在各视图/store，未走统一 logger，生产环境无法按级别过滤或上报。 |
| 修复建议 | 引入 `src/utils/logger.js` 统一封装，生产环境抑制 `warn` 或上报到监控后端；替换所有 `console.warn/error` 为 `logger.warn/error`。 |

### 3.6 未使用 CSS

> **扫描方法**：提取各 `.vue` 的 `<style>` 中定义的 class，与 template 中 `class=""`、`:class=""`、script 中字符串字面量、动态拼接（`` `prefix-${var}` ``）比对。过滤 BEM 修饰符（`--`）、Vue 过渡 class（`*-enter-active` 等）、状态 class（`is-*`/`has-*`/`active`/`on` 等）。最终经人工验证确认 5 个真正未使用的 class。

#### P3-F-13 — App.vue 未使用 CSS

| 字段 | 值 |
|---|---|
| 编号 | P3-F-13 |
| 严重度 | P3 |
| 问题类型 | 未使用 CSS |
| 文件:行号 | `src/App.vue:438,442,444` |
| 问题描述 | `.nav-footer`、`.nf-group`、`.nf-label` 三个 class 在 CSS 中定义，但 template 中无任何引用（含动态拼接）。疑为旧版侧栏底部导航残留。 |
| 修复建议 | 确认无历史依赖后删除这 3 个 class 的 CSS 规则。 |

#### P3-F-14 — TopBar.vue 未使用 CSS

| 字段 | 值 |
|---|---|
| 编号 | P3-F-14 |
| 严重度 | P3 |
| 问题类型 | 未使用 CSS |
| 文件:行号 | `src/components/TopBar.vue:195` |
| 问题描述 | `.topbar__siderail-btn`（含 `:hover`、`:active` 共 3 条规则）在 CSS 中定义，但 template 中无引用。疑为折叠态侧栏按钮的未完成功能残留。 |
| 修复建议 | 确认无计划使用后删除；若计划实现 siderail 折叠态，补全 template 引用。 |

#### P3-F-15 — Tasks.vue 未使用 CSS

| 字段 | 值 |
|---|---|
| 编号 | P3-F-15 |
| 严重度 | P3 |
| 问题类型 | 未使用 CSS |
| 文件:行号 | `src/views/Tasks.vue:349` |
| 问题描述 | `.tasks-filterbar` 在 CSS 中定义，但 template 中无引用。`Tasks.vue` 的过滤栏可能已迁移到 `ListPageLayout` 的 slot 或自研实现。 |
| 修复建议 | 确认后删除该 class 的 CSS 规则。 |

### 3.7 可访问性（input 缺少 aria-label）

> 以下 `<input>` 无 `aria-label` 也无关联 `<label for>`，依赖 `placeholder` 提供可访问名。`placeholder` 在 WCAG 2.1 下不作为可访问名（accessibility name）的可靠来源，屏幕阅读器可能不朗读。

#### P3-F-16 — 多视图 input 缺少 aria-label

| 字段 | 值 |
|---|---|
| 编号 | P3-F-16 |
| 严重度 | P3 |
| 问题类型 | 可访问性（input 无 aria-label） |
| 文件:行号 | `src/views/ApiKeys.vue:88,96,104,108,162,170,178,182`、`src/views/Audit.vue:198,200,208`、`src/views/Chat.vue:113`、`src/views/Cost.vue:66,70,74`、`src/views/Licenses.vue:80,84,102,106,115,145`、`src/views/Logs.vue:19`、`src/views/Models.vue`（多处）、`src/views/Quotas.vue`（多处）、`src/views/Search.vue`、`src/views/Settings.vue`（多处）、`src/views/SsoProviders.vue`（多处）、`src/views/Users.vue`（多处）、`src/views/WorkflowEditor.vue`（多处） |
| 问题描述 | 30+ 个 `<input>` 仅靠 `placeholder` 提示，无 `aria-label` 或 `<label for>` 关联。 |
| 修复建议 | 为每个 `<input>` 添加 `:aria-label="t('...')"`，或用 `<label :for="id">` 包裹/关联。 |

### 3.8 死路由 / 硬编码文案

> **结论**：无 P3 级问题。死路由扫描无发现（见 2.1）；硬编码文案扫描无发现（见 3.2）。

## 第4章 修复优先级建议

### 4.1 优先修复（P2，影响用户）

1. **i18n 缺失 key（P2-F-02 ~ P2-F-08）**：39 个缺失 key 集中在错误提示，运行时回退到 key 名，对非英文用户不可读。建议一次性补齐所有缺失 key，工作量小、收益明显。
2. **未使用组件 NotificationBell（P2-F-01）**：决定挂载或删除，避免死代码。
3. **可访问性 aria-label（P2-F-12 ~ P2-F-18）**：仅图标按钮缺少 `aria-label`，屏幕阅读器无法识别。逐个添加 `:aria-label` 即可。
4. **硬编码颜色（P2-F-09 ~ P2-F-11）**：`OnboardingWizard`/`DagGraph` 的遮罩色和 7 处 `box-shadow` 未走 token，暗色主题下视觉异常。抽取为 token。

### 4.2 择机修复（P3，提升质量）

1. **ESLint warnings（P3-F-07 ~ P3-F-11）**：运行 `npm run lint:fix` 可自动修复 3 个，剩余 3 个手动处理。
2. **fallback 颜色不一致（P3-F-01 ~ P3-F-04）**：统一 `var()` fallback 为 token 值。
3. **未使用 CSS（P3-F-13 ~ P3-F-15）**：删除 5 个死 class。
4. **console 统一 logger（P3-F-12）**：引入 `logger.js` 统一封装。
5. **input aria-label（P3-F-16）**：30+ 个 input 补 `aria-label`。
6. **FilterBar 复用率（P3-F-05）**：评估迁移可行性。

## 第5章 附录

### 5.1 扫描脚本

本次审核使用以下临时脚本（位于项目根目录，审核后应删除）：

- `__audit_scan.py` — 综合扫描（中文/颜色/console/组件/i18n/a11y/CSS）
- `__audit_components2.py` — 组件引用计数（含 index.js 具名导入）
- `__audit_i18n.py` — i18n 缺失 key 深入分析
- `__audit_css2.py` — 未使用 CSS 扫描（修正 template 提取）
- `__audit_a11y.py` — 可访问性深入扫描
- `__audit_results.json` — 综合扫描原始结果

### 5.2 i18n 缺失 key 完整清单

```
action.close
common.disabled
common.provider
common.required
view.agents.checkFailed
view.audit.deleteFailed
view.audit.eventsUnavailable
view.audit.historyUnavailable
view.audit.rulesUnavailable
view.audit.saveFailed
view.audit.summaryUnavailable
view.audit.toggleFailed
view.licenses.loadFailed
view.licenses.renewFailed
view.licenses.revokeFailed
view.licenses.status
view.models.agentsUnavailable
view.models.availabilityUnavailable
view.models.budgetUnavailable
view.models.modelsUnavailable
view.models.policiesUnavailable
view.models.providersUnavailable
view.models.registryUnavailable
view.notifications.deleteFailed
view.notifications.loadMoreFailed
view.notifications.markAllReadFailed
view.notifications.markReadFailed
view.notifications.notificationsUnavailable
view.notifications.saveFailed
view.overview.loadFailed
view.rbac.grantFailed
view.rbac.revokeFailed
view.tenants.activateFailed
view.tenants.createFailed
view.tenants.deleteFailed
view.tenants.loadFailed
view.tenants.suspendFailed
view.users.failed
view.users.networkError
```

> 注：`view.apikeys.scopeGroup.` 和 `view.quotas.` 为动态拼接前缀（`t('view.apikeys.scopeGroup.' + g.group)`），其子 key（如 `view.apikeys.scopeGroup.agents`）已在 i18n 中定义，不计入缺失。

### 5.3 组件引用计数完整表

| 组件 | 引用数 | 状态 |
|---|---|---|
| AppIcon | 56 | 正常 |
| PageHeader | 55 | 正常 |
| EmptyState | 43 | 正常 |
| Badge | 33 | 正常 |
| Card | 25 | 正常 |
| ListPageLayout | 25 | 正常 |
| Skeleton | 23 | 正常 |
| DataTable | 21 | 正常 |
| StatCard | 21 | 正常 |
| Segmented | 15 | 正常 |
| DetailDrawer | 15 | 正常 |
| FilterBar | 6 | 偏低 |
| DagGraph | 3 | 正常（Monitor.vue） |
| NodeDetailPanel | 3 | 正常（DagGraph.vue） |
| EvolutionTimeline | 3 | 正常 |
| TopBar | 3 | 正常（App.vue） |
| Toast | 2 | 正常（App.vue + index.js） |
| CommandPalette | 2 | 正常（App.vue） |
| McpTopology | 2 | 正常（Tools.vue） |
| AppFooter | 1 | 正常（App.vue） |
| CoachMarks | 1 | 正常（App.vue） |
| OnboardingWizard | 1 | 正常（Overview.vue） |
| NotificationBell | 0 | **未使用** |