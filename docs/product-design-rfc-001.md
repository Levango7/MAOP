# RFC-001: MAOP 控制台产品设计演进 — 从"功能仓库"到"工作台"

- **状态**: Draft (待审阅)
- **作者**: InsCode
- **日期**: 2026-08-12
- **影响范围**: `dashboard-enterprise/` 全部 22 个视图、`src/nav.js`、`src/components/*`、新增 3 个组件
- **预计工期**: 3 个迭代（每迭代 1 PR)

---

## 1. 罗列问题（现状诊断）

### P0 — 信息架构：按"后端模块"组织而非"用户旅程"

**现象**:21 个一级导航项按 `Core / Search & Tools / Ops / Enterprise / Help` 平铺；`Overview/Monitor` 功能重叠、`Evolve/Evolution-History` 同概念拆两页、`Control/Chat` 同一目标两种交互各占一位。

**证据**: `src/nav.js:12-41` | 新用户首次打开的认知成本约为 **5-8 分钟**(JetBrains 标准是 30 秒内能找到自己要的功能)。

**根因**: 功能是从后端自下而上长出来的，没有自上而下的"用户故事地图"。

### P1 — Overview 不是"工作的起点"

**现象**: Overview 是 10 个 StatCard + 熵监控 + 活动流 + 图表的堆叠，用户打开后**不知道接下来该点什么**。

**证据**: `views/Overview.vue` 模板结构 — Hero 区缺"健康度结论"与"下一步动作"。

### P2 — 交互心智不统一

**现象**: 列表页过滤器布局互不相同（4 种以上）；查看详情有的用 Modal 有的用 Drawer；图表 hover 行为各写各的。

**证据**: `Audit.vue / Users.vue / Tenants.vue / Tools.vue` 中 filter-bar 的实现各写一份；Card 组件有无 header 两种分支被随意使用。

### P3 — 缺少产品差异化记忆点

**现象**: MAOP 独有的 Plan-Execute-Verify 循环、Agent 演化时间线这两个**别处没有的概念**在 UI 上只是普通列表，完全没有被"讲成故事"。

---

## 2. 分析需求（目标与非目标)

### 2.1 目标

| # | 目标 | 验收标准 |
|---|------|---------|
| G1 | 新用户 30 秒内能定位"我要 运行/查看/管"里的任何一个入口 | 可用性测试：让一位未用过 MAOP 的工程师找"查看某 Agent 的运行成本"，点击深度 ≤2 |
| G2 | Overview 呈现"系统健康 → 下一步做什么 → 上下文"的三段式 | 页面首屏不出现纯装饰元素；每个区块有明确的 action |
| G3 | 同类视图交互模板唯一 | 所有列表页共用 `<ListPageLayout>`，所有详情为右侧 Drawer |
| G4 | Evolve 讲出"演化时间线"故事 | 用户能在 5 秒内理解"我的 Agent 经历了哪些版本变更" |

### 2.2 非目标（明确拒绝）

- **不重写后端 API**——所有前端改造在现有 API 之上完成
- **不引入新依赖**（除已有的 vis-network / chart.js)
- **不改动个人版 `dashboard/` 零构建版**——它是另一条产品线，本次范围仅限 enterprise

### 2.3 约束

- 每一必须是独立可回滚的 PR（不给 master 挂半成品）
- 任何删除的页面 / 路由必须 301 重定向到合并后的位置，避免外部书签 404

---

## 3. 设计方案

### 3.1 迭代 A：信息架构重构（最高优先)

#### A-1 导航重排

```
工作台 (Workbench)
  ├─ Overview              /
构建 (Build)
  ├─ Run                   /run        ← 合并 Control + Chat
  ├─ Agents                /agents
  └─ Evolution             /evolve     ← 合并 Evolve + Evolution-History
数据资产 (Assets)
  ├─ Memory                /memory
  ├─ Knowledge Graph       /knowledge-graph
  ├─ Search                /search
  ├─ Vector                /vector
  └─ Tools                 /tools
监控 (Observe)
  ├─ Health                /monitor
  ├─ Observability         /observability
  ├─ Logs                  /logs
  └─ Cost                  /cost
治理 (Govern)
  ├─ Models                /models
  ├─ Audit                 /audit
  ├─ RBAC                  /rbac
  ├─ Tenants               /tenants
  └─ Users                 /users
系统
  ├─ Settings              /settings
  └─ Docs                  /docs
```

- **合并规则**:
  - `/control`、`/chat` → `/run` 内嵌 Tab(`Chat` 是 `Run` 的一种交互模式）
  - `/evolve`、`/evolution-history` → `/evolve`（历史是演化的一个视图，改为页内 Tab `Current / History`)
- **路由兼容**: 旧路由 `<Route path="/control" redirect="/run?tab=structured">` 同理 `/chat` `/evolution-history`

#### A-2 Overview 改版

**布局改为三段式**:

```
┌─────────────────────────────────────────────────────────────┐
│ [Hero] ● Healthy  · 3 running tasks · 成本 ¥4.2/h · 刷新于 X │  ← 全宽 strip
├─────────────────────────────────────────────────────────────┤
│ [Action] ▶ Run Task │ 🤖 New Agent │ 🧠 Memory │ 📜 Logs   │  ← 4 个磁贴
├────────────────────────────────────────────┬────────────────┤
│ [Charts] Throughput 趋势 │ Cost 分布       │ [Feed] 活动流  │
│ (2/3 宽,可折叠)                           │ (1/3 宽)       │
└────────────────────────────────────────────┴────────────────┘
```

- Hero 区组件化：`<HeroStrip>`，数据源 `/api/overview/hero`（需后端新增聚合接口；若本期不做后端，先用现有 `/api/health` + `/api/cost/summary` 前端拼）
- Feed 高度限制在首屏内：不再滚动超出视口

#### A-3 验收指标

- 任一 Test：从 `/` 出发，到 `/evolve` `/logs` `/users` 的点击深度均为 2
- `/control` `/chat` 旧 URL 能 301 到 `/run`
- Lighthouse "Best Practices" 分数不下降

### 3.2 迭代 B：产品差异化（Wow moment)

#### B-1 Evolve 页时间线

把 `Evolve.vue` 从表格改为横向时间线：

```
v1.0 ──●──────●──────●──── vCurrent
       │      │      │
    Prompt  策略   工具
    优化    更新   扩展
    +15%    +8%    +22%
```

- 数据层：复用现有 `evolution_history` 数据，仅需在前端做 aggregation
- 悬停节点弹出 detail card（复用现有 `<NodeDetailPanel>`，改造为 Timeline 更合适的格式）

#### B-2 首次进入 Coach Marks

打开 `/` 且 localStorage 无 `maop_onboarding_done`: 4 步非阻塞高亮提示（可 ESC 关闭）。使用纯 CSS+JS 实现，不引第三方库（约 120 行）。

#### B-3 全局搜索 Cmd+K

- 快捷键监听挂在 `App.vue` 顶层
- 弹层搜索路由 nav + agents 列表（模糊匹配）
- 本期实现**壳**:UI + 快捷键 + 路由跳转；算法可以后续升级

### 3.3 迭代 C：交互抹平

#### C-1 创建 `<ListPageLayout>` 新组件

统一所有列表页骨架：

```
<PageHeader />
<FilterBar :filterSchema="..."  />     ← 新组件, 声明式驱动
<DataTable 或 CardGrid />
<PaginationFooter />
```

迁移顺序： `Agents → Users → Tenants → Tools → Models → Audit`(6 个页面，每页约 -120 行重复代码）

#### C-2 详情抽屉统一

- 新建 `<DetailDrawer>`：右侧滑出，480px 宽，ESC/点击遮罩关闭，焦点捕获
- 散弹枪替换：所有 `v-if="selected"` Modal 改为 Drawer
- 保留 Modal 仅用于：破坏性确认（删除）、全局表单（登录）

#### C-3 图表 hover 规范

在 `styles/tokens.css` 增加：

```css
--chart-hover-zoom: 1.15;
--chart-grid-dash: 4 4;
```

写进 `docs/frontend-style-guide.md`：
- 所有折线 hover 点放大 ×1.15 + 十字参考线
- 所有 bar 高亮当前柱，非当前透明度降至 0.4

---

## 4. 审核方案（自审清单)

| 检查项 | 结果 |
|--------|------|
| 是否破坏现有 API? | ❌ 否 — 全部前端改造 |
| 是否向后兼容路由？ | ✅ 旧路由 301 重定向 |
| 对 160 个前端测试影响？ | ⚠️ 迭代 C 会涉及 15-20 个测试的 selector 更新，已规划同步修复 |
| 对 i18n 影响？ | ✅ nav.js 只增 subtitle i18n key,labels 沿用现有 key |
| 单迭代工作量？ | A ≈ 2天 / B ≈ 2天 / C ≈ 3天，每迭代独立可发布 |
| 回滚策略？ | 每迭代一个 git tag;`git revert <merge-commit>` 即可整体回滚 |
| 是否引发本质风险？ | 仅 B-1 的 Timeline 需要新聚合接口，若后端跟不上，降级为前端聚合 |

---

## 5. 拆分任务（迭代进入 Sprint 前）

> 审核通过后，由我把每个迭代拆为可执行 GitHub Issues 级别的任务。

| 迭代 | 任务包 | 预计时长 |
|------|--------|---------|
| **A** | A1 路由与 nav 重排 / A2 旧路由 301 / A3 合并 Control+Chat → Run / A4 合并 Evolve / A5 Overview 三段式改造 / A6 HeroStrip API 契约 / A7 e2e 测试更新 | 6-8 小时 |
| **B** | B1 Evolve 时间线 SVG 渲染器 / B2 时间线数据聚合器 / B3 Coach Marks 组件 / B4 Cmd+K 壳 / B5 键盘 shortcut 文档 | 6-8 小时 |
| **C** | C1 ListPageLayout 基座 / C2 FilterBar 声明式驱动 / C3 6 个列表页迁移 / C4 DetailDrawer 组件 / C5 详情模态迁移 / C6 chart hover 规范落地 | 8-10 小时 |

---

## 6. 待你回复

请过目本 RFC，针对以下 3 点回复：

1. **导航重排的 6 分组**是否合理？有想改名/合并/移动的吗？
2. **Overview 三段式**布局你认可吗？还是希望 Hero 区更突出(比如占首屏 1/2)?
3. **迭代 C 的 DetailDrawer 取代 Modal**是否符合你的预期？

任意一点有异议我可以调整 RFC；无异议回复"通过"，我即开始任务拆分与执行。
