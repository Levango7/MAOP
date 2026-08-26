# P2/P3 前端代码审核报告

> 审核时间：2026-08-26
> 审核范围：MAOP `dashboard-enterprise/` 前端代码
> 审核人：FrontendAuditAgent（Task 489）

## 1. 审核概览

| 维度 | 发现数 |
|------|--------|
| 死路由/未使用路由 | 0 |
| 硬编码颜色（真正硬编码） | 11 |
| 硬编码颜色（fallback 不一致） | 12 |
| 硬编码文案 | 0 |
| 未使用组件 | 1 |
| i18n 缺失 key | 39 |
| ESLint warnings | 6 |
| console 语句 | 15 |
| 未使用 CSS 类 | 3 |
| 可访问性 issues | 5 |

**合计：P2 问题 3 个，P3 问题 8 个。**

## 2. P2 问题清单

### P2-F-01：NotificationBell.vue 组件完全未使用
- **严重度**：P2
- **位置**：`src/components/NotificationBell.vue`
- **问题描述**：NotificationBell 组件在项目中 0 引用，完全未使用。
- **修复建议**：删除该组件，或在 Notifications 视图中引用。

### P2-F-02：39 个 i18n 缺失 key
- **严重度**：P2
- **位置**：`src/views/Audit.vue`、`src/views/Licenses.vue` 等多个视图
- **问题描述**：39 个 `$t()` 调用的 key 在翻译文件中缺失，如 `view.audit.deleteFailed`、`view.audit.saveFailed`、`view.licenses.loadFailed` 等。
- **修复建议**：在 `src/locales/zh.json` 和 `src/locales/en.json` 中补充缺失的 key。

### P2-F-03：11 处真正硬编码颜色（未使用 CSS 变量）
- **严重度**：P2
- **位置**：OnboardingWizard.vue (#fff, rgba(0,0,0,0.5))、DagGraph.vue (rgba(0,0,0,0.3))、NodeDetailPanel.vue (rgba(0,0,0,0.08))、CoachMarks.vue (rgba(0,0,0,.5))、TopBar.vue (rgba(255,255,255,.25))、Run.vue (rgba(0,0,0,0.15))、Settings.vue (rgba(0,0,0,0.12)×2)、WorkflowEditor.vue (rgba(0,0,0,.04))
- **问题描述**：这些颜色值直接硬编码在 CSS 中，未使用 CSS 变量。
- **修复建议**：替换为 CSS 变量（如 `var(--overlay-scrim)`、`var(--shadow-color)` 等），或在 tokens.css 中新增对应变量。

## 3. P3 问题清单

### P3-F-01：12 处 CSS 变量 fallback 颜色与 tokens 不一致
- **严重度**：P3
- **位置**：Monitor.vue (#94a3b8/#ef4444/#f59e0b/#22c55e)、Chat.vue (#999)、NodeDetailPanel.vue (#dc2626)、NotificationBell.vue/Notifications.vue/StatCard.vue (#38bdf8)、Audit.vue (#ef4444)、SkillMarket.vue (#f0f5ff/#1d4ed8)
- **问题描述**：CSS 变量的 fallback 值与 tokens.css 中定义的值不一致。
- **修复建议**：统一 fallback 值与 tokens.css 中的定义。

### P3-F-02：6 个 ESLint warnings
- **严重度**：P3
- **位置**：Docs.test.js:7 (vi unused)、OnboardingWizard.vue:12 (linebreak)、useDagProgress.js:73 (_getToken unused)、useMarkdown.js:73 (codeLang unused)、EvolutionHistory.vue:128/278 (attributes-order)
- **修复建议**：删除未使用变量、修复属性顺序、添加换行。

### P3-F-03：15 个 console.warn/error 语句
- **严重度**：P3
- **位置**：多个文件
- **问题描述**：console 语句在生产环境也会执行。
- **修复建议**：用 `import.meta.env.PROD` 包裹或使用统一 logger。

### P3-F-04：3 个未使用 CSS 类
- **严重度**：P3
- **位置**：App.vue (.nav-footer, .nf-group, .nf-label)、TopBar.vue (.topbar__siderail-btn)、Tasks.vue (.tasks-filterbar)
- **修复建议**：删除未使用的 CSS 规则。

### P3-F-05：5 个可访问性 issues（图标按钮缺 aria-label）
- **严重度**：P3
- **位置**：NodeDetailPanel.vue:6 (关闭按钮)、ApiKeys.vue:141 (复制按钮) 等
- **问题描述**：只有图标的 button 缺少 aria-label。
- **修复建议**：添加 `aria-label` 属性。

### P3-F-06：FilterBar 组件复用率低（仅 2 处使用）
- **严重度**：P3
- **位置**：FilterBar.vue
- **修复建议**：在更多列表视图中复用 FilterBar，或评估是否需要保留。

### P3-F-07：OnboardingWizard.vue 属性换行问题
- **严重度**：P3
- **位置**：OnboardingWizard.vue:12
- **修复建议**：在第一个属性前添加换行。

### P3-F-08：EvolutionHistory.vue 属性顺序问题
- **严重度**：P3
- **位置**：EvolutionHistory.vue:128, 278
- **修复建议**：调整属性顺序，`:aria-expanded` 和 `:title` 在 `@click` 之前。