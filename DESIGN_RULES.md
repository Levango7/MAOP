# MAOP Dashboard Design Rules

> **权威设计规范** — 所有 UI 改动必须遵循此文档。最后更新: 2026-07-31

> **架构说明**: 原生 JS 仪表盘已归档至 `archive/js-dashboard/`，前端统一基于 Vue 3 + Vite 实现。源码位于 `dashboard-enterprise/`，构建产物输出至 `dashboard/dist-enterprise/`。本规范以下所有条款均针对 Vue 3 实现，原生 JS 仪表盘相关约定（per-section `--sc` 18 色体系、`togglePillarItem()` / `showCtrlMsg()` 等全局函数）随归档废弃，不再适用。

---

## 1. 色彩体系

Vue 3 实现采用 **双主题 + Design Token** 体系：暗色主题为默认（`:root`，定义于 `src/styles/tokens.css`），亮色主题通过 `[data-theme="light"]` 切换（定义于 `src/styles/themes.css`）。所有颜色均以 CSS 变量消费，禁止硬编码色值。

### 1.1 基础色 (CSS变量)

#### 1.1.1 暗色主题（默认，tokens.css :root）

| 变量 | 值 | 用途 |
|------|-----|------|
| `--bg` | `#0f172a` | 页面背景 |
| `--bg2` | `#1e293b` | 外框背景 |
| `--bg3` | `#334155` | 中框/内框背景 |
| `--bg4` | `#0f172a` | 悬停背景 |
| `--border` | `#334155` | 默认边框 |
| `--text` | `#e2e8f0` | 主文字 |
| `--text2` | `#94a3b8` | 次文字 |
| `--text3` | `#64748b` | 弱文字 |
| `--accent` | `#6366f1` | 主强调色（靛蓝） |
| `--accent2` | `#818cf8` | 次强调色 |
| `--success` | `#22c55e` | 成功 |
| `--warn` | `#f59e0b` | 警告 |
| `--fail` | `#ef4444` | 失败 |

#### 1.1.2 语义 Surface Token（新设计系统）

| 变量 | 暗色值 | 亮色值 | 用途 |
|------|--------|--------|------|
| `--surface` | `#1e293b` | `#ffffff` | 侧栏/卡片底色 |
| `--surface-2` | `#243349` | `#f4f7fb` | 悬停底色 |
| `--surface-3` | `#2b3b54` | `#eef2f8` | 三级底色 |
| `--border-strong` | `#475569` | `#b4c0d2` | 强边框 |
| `--border-subtle` | `rgba(148,163,184,.14)` | `rgba(100,116,139,.16)` | 半透明弱边框 |
| `--text-muted` | `#94a3b8` | `#5b6b82` | 次文字 |
| `--text-faint` | `#64748b` | `#8493a8` | 弱文字 |
| `--brand` | `#6366f1` | `#4f46e5` | 品牌主色 |
| `--brand-strong` | `#818cf8` | `#6366f1` | 品牌强色 |
| `--brand-soft` | `rgba(99,102,241,.14)` | `rgba(79,70,229,.10)` | 品牌柔色（激活态底） |
| `--brand-contrast` | `#ffffff` | `#ffffff` | 品牌色上文字 |

#### 1.1.3 亮色主题（themes.css [data-theme="light"]）

| 变量 | 值 | 用途 |
|------|-----|------|
| `--bg` | `#e9edf4` | 页面背景（加深以衬托白色卡片） |
| `--bg2` | `#f4f7fb` | 外框背景 |
| `--bg3` | `#e2e8f1` | 中框背景 |
| `--bg4` | `#ffffff` | 悬停背景 |
| `--border` | `#d2dae6` | 默认边框 |
| `--text` | `#1a2332` | 主文字 |
| `--brand` | `#4f46e5` | 品牌主色 |

### 1.2 图表配色（Chart Palette）

Vue 3 实现不再为每个导航项分配独立 section 色（原 18 色 `--sc` 体系已随原生 JS 仪表盘归档废弃）。全站统一使用单一品牌强调色 `--brand`（靛蓝系）+ 主题感知的图表调色板，定义于 tokens.css / themes.css：

| 变量 | 暗色值 | 亮色值 | 用途 |
|------|--------|--------|------|
| `--chart-1` | `#6366f1` | `#4f46e5` | 主数据系列 |
| `--chart-2` | `#38bdf8` | `#0284c7` | 次数据系列 |
| `--chart-3` | `#22c55e` | `#16a34a` | 成功/正向 |
| `--chart-4` | `#f59e0b` | `#d97706` | 警告/负载 |
| `--chart-5` | `#a78bfa` | `#7c3aed` | 紫色系列 |
| `--chart-6` | `#14b8a6` | `#0d9488` | 青绿系列 |
| `--chart-7` | `#ec4899` | `#db2777` | 粉色系列 |
| `--chart-8` | `#06b6d4` | `#0891b2` | 青色系列 |
| `--chart-9` | `#84cc16` | `#65a30d` | 黄绿系列 |
| `--chart-10` | `#f43f5e` | `#e11d48` | 红/失败 |
| `--chart-warn` | `#f59e0b` | `#d97706` | 性能历史告警 |
| `--chart-fail` | `#ef4444` | `#dc2626` | 连续失败强调 |

### 1.3 概览指标色

概览页指标卡使用 `--chart-1` ~ `--chart-10` 循环取色，由主题 token 驱动，不再硬编码 hex。原 `stat-box.c1~c12` / `card.cb1~cb4` 类名约定随原生 JS 仪表盘归档废弃，统一并入图表调色板。

---

## 2. 边框体系

Vue 3 实现统一使用 1px 边框 + token 驱动，原三级边框（外框 4px / 中框 3px / 内框 2px）体系已简化。所有边框颜色消费 `--border` / `--border-strong` / `--border-subtle`，悬停高亮通过 `--brand-soft` 底色 + `--brand` 边框色表达。

| 元素 | 粗细 | 颜色 | 悬停高亮 |
|------|------|------|----------|
| 侧栏/卡片 | 1px | `--border` | `--surface-2` 底色 |
| 激活导航项 | 3px 左边框 | `--brand` | `--brand-soft` 底色 + `--brand-strong` 文字 |
| 分区分隔 | 1px | `--border` | — |

### 规则
- 悬停态使用 token 驱动的底色变化（`--surface-2`），不再使用 box-shadow 发光
- 激活态导航项：`border-left: 3px solid var(--brand)` + `background: var(--brand-soft)` + `color: var(--brand-strong)`
- 焦点环：`:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px }`

---

## 3. 分割线体系

### 3.1 粗细度规则

| 层级 | 粗细 | 颜色 | 示例 |
|------|------|------|------|
| 分区标题 | 1px | `--border` | `.nav-section` border-top |
| 表头下 | 1px | `--border` | `th` border-bottom |
| 卡片分隔 | 1px | `--border-subtle` | 卡片内分区 |

### 3.2 统一规则
- 分割线颜色统一消费 `--border` 或 `--border-subtle`（半透明）
- 不再使用 `var(--sc, var(--accent))` 跟随 section 配色（section 色体系已废弃）
- 圆角由 `--r-sm` / `--r-md` / `--r-lg` token 控制

---

## 4. 布局规则

### 4.1 导航分组 (4组×17项)

导航结构定义于 `src/nav.js`（单一数据源，侧栏与 PageHeader 共享）。路由定义于 `src/router/index.js`。

| 组 | 项 | 路由 |
|----|-----|------|
| 核心 (nav.core) | 概览, 控制面板, 对话, Agent, 三层记忆, 自进化 | `/`, `/control`, `/chat`, `/agents`, `/memory`, `/evolve` |
| 搜索工具 (nav.searchTools) | 搜索, 向量检索, 工具, 大模型 | `/search`, `/vector`, `/tools`, `/models` |
| 运维 (nav.ops) | 日志, 监控, 成本 | `/logs`, `/monitor`, `/cost` |
| 企业 (nav.enterprise) | 审计, RBAC, 租户, 设置 | `/audit`, `/rbac`, `/tenants`, `/settings` |

说明：
- `/audit`、`/rbac`、`/tenants` 标记 `meta.requiresEnterprise: true`，由路由守卫拦截非企业版访问
- 原生 JS 仪表盘的「四大工程 / 工作流 / Skills / MCP / 提示词 / 角色 / 模块 / 架构 / 工作流程」等页面已随 `archive/js-dashboard/` 归档，不再存在于 Vue 3 导航
- 侧栏支持 rail（折叠至 `--rail-w: 64px`）与移动端抽屉模式（< 900px）

### 4.2 概览指标
- 指标卡使用 token 驱动布局，间距消费 `--sp-*` 尺度
- 指标值字号 `--fs-2xl` (24px) / 800

### 4.3 滚动
- 左侧导航: `position: sticky; top: 0; height: 100vh; overflow-y: auto`
- 右侧主区: `flex: 1; overflow-y: auto`
- 内容壳: `max-width: var(--maxw)` (1440px) 居中

### 4.4 响应式
- 移动端断点: 900px（`MOBILE_BREAKPOINT`）
- < 900px: 侧栏变为抽屉，汉堡按钮触发，背景遮罩点击关闭

---

## 5. 折叠/展开机制

### 5.1 侧栏 Rail 模式
- 由 `useUiStore()` (Pinia) 管理 `ui.rail` 状态
- 切换方法: `ui.toggleRail()`（App.vue 调用）
- 折叠时宽度 `--rail-w: 64px`，仅显示图标

### 5.2 主题与密度切换
- 主题切换: `ui.toggleTheme()`（`light` / `dark`，通过 `[data-theme]` 属性切换）
- 密度切换: `ui.toggleDensity()`（`comfortable` / `compact`，通过 `[data-density]` 属性切换）
- 状态持久化于 Pinia store + localStorage

说明：原 `togglePillarItem()`（原生 JS 内框折叠）随 `archive/js-dashboard/` 归档废弃。Vue 3 实现中各视图的折叠/展开状态由组件内 `ref()` 或 Pinia store 管理，不再有全局同名函数。

---

## 6. 按钮体系

### 6.1 尺寸
| 类 | padding | 字号 | 用途 |
|----|---------|------|------|
| `.btn-lg` | 12px 24px | 14px | 主操作 |
| `.btn-md` | 10px 20px | 13px | 常规 |
| `.btn-sm` | 8px 16px | 12px | 辅助 |

### 6.2 颜色
按钮统一消费 token，不再使用硬编码渐变：

| 类 | 背景 | 用途 |
|----|------|------|
| `.btn-primary` | `--brand` | 主操作 |
| `.btn-success` | `--success` | 成功操作 |
| `.btn-warn` | `--warn` | 警告操作 |
| `.btn-fail` | `--fail` | 危险操作 |

### 6.3 悬停效果
- 上浮: `transform: translateY(-2px)`
- 阴影: `box-shadow: var(--shadow-md)`
- 过渡: `transition: all var(--motion) var(--ease)`

---

## 7. 数据展示规则

### 7.1 禁止直接显示原始 JSON
- 记忆系统/神经机制/深度记忆等区域: 将原始 JSON 转为结构化展示
- 使用: 指标卡片、状态徽章、结构化列表、表格
- Vue 3 实现通过组件化封装（如 `ThreeLayerMemory.vue`）保证展示一致性

### 7.2 空状态处理
- 数据为空时显示占位文字（i18n key 驱动）
- 不显示 `null`/`undefined`/`{}`

### 7.3 表格
- 列名: `--fs-sm` (12px), `--text-muted` 色, `font-weight: 600`
- 单元格: `--fs-base` (13px), `--text` 色
- 行悬停: 背景 `--surface-2`
- 列名下分割线: 1px `--border`

---

## 8. 字体规则
- 系统字体: `var(--font-sans)` = `-apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', Roboto, 'PingFang SC', 'Microsoft YaHei', sans-serif`
- 等宽字体: `var(--font-mono)` = `'SF Mono', ui-monospace, 'Cascadia Code', Menlo, Consolas, monospace`
- 字号尺度 (token 驱动):

| token | 值 | 用途 |
|-------|-----|------|
| `--fs-xs` | 11px | 弱文字/标签 |
| `--fs-sm` | 12px | 次文字 |
| `--fs-base` | 13px | 正文 |
| `--fs-md` | 14px | 卡片标题 |
| `--fs-lg` | 16px | 副标题 |
| `--fs-xl` | 20px | 标题 |
| `--fs-2xl` | 24px | 指标值/页标题 |

- 基础字号: `--fs-base` (13px)（`body { font-size: var(--fs-base) }`）

---

## 9. 动画/过渡
- 所有交互元素: `transition: all var(--motion) var(--ease)`（180ms）
- 快速过渡: `--motion-fast` (120ms)；慢速: `--motion-slow` (280ms)
- 缓动: `--ease` = `cubic-bezier(.4, 0, .2, 1)`；`--ease-out` = `cubic-bezier(.16, 1, .3, 1)`
- 视图进入: `@keyframes maop-view-in`（opacity + translateY 6px → 0），`.view-enter` 类应用
- 单次脉冲: `@keyframes maop-pulse-once`（状态变更触发，非永久循环）
- 骨架屏: `@keyframes maop-shimmer`
- 尊重 `prefers-reduced-motion: reduce`（禁用动画）

---

## 10. 禁止事项
- ❌ 禁止使用 `alert()` / `confirm()` — 用 `<Toast />` 组件或页面内联状态提示
- ❌ 禁止直接显示原始 JSON — 转为结构化展示
- ❌ 禁止硬编码魔法数字 — 从后端 API 获取真实数据
- ❌ 禁止硬编码色值 — 统一消费 `src/styles/tokens.css` / `themes.css` 中的 CSS 变量
- ✅ 主前端使用 Vue 3 + Vite（源码 `dashboard-enterprise/`，构建产物 `dashboard/dist-enterprise/`）
- ❌ 禁止引入第二套前端框架（Lit/React/Svelte 等）— 单一 Vue 3 技术栈
- ❌ 禁止在 dashboard/ 放调试截图 — 截图是临时文件，用完即删
- ❌ 禁止复活原生 JS 仪表盘的 per-section `--sc` 18 色体系 — 已归档废弃
