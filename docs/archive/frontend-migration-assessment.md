# MAOP 前端迁移评估：原生 JS → Vue 3

## 现状

| 维度 | 当前 |
|------|------|
| 框架 | **无框架**，原生 JS + Chart.js |
| 构建 | Vite 8.1 |
| 页面 | 22 个（overview/agents/chat/memory/evolve/monitor/cost/...） |
| API 层 | `core/api.js` — fetch 封装（fetchJSON/postJSON/...） |
| 实时 | `core/websocket.js` — 原生 WebSocket |
| 路由 | 无 SPA 路由，scrollIntoView 切换 section |
| 状态 | 无全局状态管理，每个页面独立 fetch |
| 认证 | localStorage + Bearer token |
| 代码量 | ~22 页面 × ~100-200 行/页 ≈ **3000-4000 行** |

## Vue 3 迁移方案

### 技术栈

| 选型 | 方案 | 理由 |
|------|------|------|
| 框架 | Vue 3.5 + Composition API | 轻量、SFC 直观 |
| UI 库 | **Naive UI** | 中文友好、主题定制强、表格/表单组件丰富 |
| 路由 | Vue Router 4 | SPA 路由，替代 scrollIntoView |
| 状态 | Pinia | 轻量状态管理，替代分散的 fetch |
| HTTP | Axios 或复用现有 fetch 封装 | — |
| 图表 | Chart.js（vue-chartjs 包装） | 已有 Chart.js 代码，迁移成本最低 |
| 构建 | Vite（不变） | 已在用 |

### 工程量估算

| 任务 | 页面数 | 预估工时 | 说明 |
|------|--------|---------|------|
| 项目脚手架 + 路由 + Pinia + Naive UI | — | 4h | vue create + 配置 |
| `core/api.js` → composable | — | 2h | `useApi()` composable |
| `core/websocket.js` → composable | — | 2h | `useWebSocket()` composable |
| 认证模块 | — | 3h | `useAuth()` + 登录页 + 路由守卫 |
| 布局组件（侧边栏 + 顶栏） | — | 4h | Naive UI Layout |
| **页面迁移**（22 个） | 22 | 40h | 平均 2h/页，含组件拆分 |
| 图表迁移（5 个） | 5 | 6h | vue-chartjs 包装 |
| 集成测试 + 修复 | — | 8h | — |
| **合计** | — | **~70h** | 约 2 周（1 人全职） |

### 页面迁移映射（22 页）

| 原页面 | Vue 组件 | 复杂度 | 说明 |
|--------|---------|--------|------|
| overview | `OverviewPage.vue` | 中 | 多卡片 + WebSocket 实时刷新 |
| control | `ControlPage.vue` | 低 | 按钮组 + action |
| chat | `ChatPage.vue` | 高 | 消息列表 + 输入 + 流式 |
| agents | `AgentsPage.vue` | 中 | 表格 + 状态切换 |
| memory | `MemoryPage.vue` | 中 | 搜索 + 列表 + 注意力计算 |
| evolve | `EvolvePage.vue` | 高 | 多 tab + 分析/建议/导出 |
| monitor | `MonitorPage.vue` | 高 | 5 个 Chart.js 图表 |
| cost | `CostPage.vue` | 中 | 2 个图表 |
| search | `SearchPage.vue` | 中 | 搜索框 + 结果列表 |
| logs | `LogsPage.vue` | 低 | 表格 + 筛选 |
| tools (skills/mcp/prompts) | `ToolsPage.vue` | 中 | 3 个 tab |
| info (pillars/roles/...) | `InfoPage.vue` | 中 | 6 个 tab |
| session | `SessionPage.vue` | 低 | 列表 |
| hook | `HookPage.vue` | 低 | 列表 + 编辑 |
| react | `ReactPage.vue` | 低 | 列表 |
| knowledge | `KnowledgePage.vue` | 低 | 列表 |
| audit | `AuditPage.vue` | 低 | 表格 |
| plugin | `PluginPage.vue` | 低 | 列表 |
| subagent | `SubagentPage.vue` | 低 | 列表 |
| permission | `PermissionPage.vue` | 低 | 列表 |
| protocol | `ProtocolPage.vue` | 低 | 列表 |
| worktree | `WorktreePage.vue` | 低 | 列表 |

### 迁移策略

**推荐：渐进式迁移（Strangler Fig Pattern）**

1. **Phase 1**：新建 Vue 3 项目，迁移布局 + 路由 + 认证 + 5 个核心页面（overview/agents/chat/monitor/cost）
2. **Phase 2**：迁移剩余 17 个页面
3. **Phase 3**：移除旧 `dashboard-vite/` 代码

优势：每个 Phase 都可独立部署验证，不中断现有功能。

### 不迁移的理由（如果暂缓）

- 当前原生 JS 方案**功能完整、构建正常**
- 22 个页面都是简单的数据展示 + 表单，无复杂交互
- 迁移投入 ~70h，短期内 ROI 不高
- 建议在**新增复杂交互需求**（如拖拽编排、实时协作）时再迁移

## 结论

| 场景 | 建议 |
|------|------|
| 短期（1-2 月） | **不迁移**，当前方案够用 |
| 中期（3-6 月） | 迁移 Vue 3 + Naive UI，提升可维护性 |
| 长期 | Vue 3 是正确方向，原生 JS 不可持续 |

**触发迁移的条件**：需要新增 3+ 个复杂交互页面、或需要组件复用、或团队规模 >1 人开发前端。