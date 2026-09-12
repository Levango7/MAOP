# MAOP 平台设计规范

> Multi-Agent Orchestration Platform — 企业级多智能体编排平台前端设计规范
>
> 版本：2026-09-12 · 基于实际代码库提取 · 设计语言：MAOP "Workbench"（参照 JetBrains New UI）

---

## 1. 产品概览

### 1.1 产品定位

MAOP（Multi-Agent Orchestration Platform）是一个**多智能体编排平台**，为运行、编排、观测 AI 智能体集群提供统一控制台。前端为 Vue 3 SPA（`dashboard-enterprise`），采用信息架构重设计后的**任务流导向导航**，而非后端模块罗列。

设计语言定名为 **"Workbench"**：中性石墨灰底 × 克制信号蓝 × 1px 描边分隔 × 小圆角（8px 上限）× 无辉光。弃用 AI 模板式靛紫（#6366f1）与满屏渐变，把"科技感"交给内容本身（图表 / 图 / DAG），外壳只做安静的容器。

### 1.2 目标用户

| 角色 | 说明 | 版本 |
|------|------|------|
| 个人开发者 | 使用个人版编排自己的智能体集群 | personal |
| 企业操作员 | 监控运维集群、管理任务与成本 | enterprise |
| 企业管理员 | 管理 RBAC / SSO / 审计 / 租户 / 许可证 | enterprise |

个人版所见即所得（企业版整组隐藏）；企业版在"管理"组可见全部能力。版本切换由 `useEditionStore` 驱动，路由守卫通过 `meta.requiresEnterprise` 过滤。

### 1.3 核心能力

- **执行**：结构化任务 / 对话 / Agent 委派 / DAG 工作流编排
- **记忆**：三层记忆（工作 / 短期 / 长期）+ 向量搜索 + 知识图谱
- **能力**：Agents / 技能 / 技能市场 / 模型 / 自动调优 / MCP 工具 / 插件 / 子代理 / 路由 / 调度 / 协议 / 工作树 / 代理代理
- **运维**：监控 / 日志 / 链路追踪 / 成本 / 辩论 / 黑板 / 告警 / Webhook / 集成
- **分析**：数据分析仪表盘（趋势 / 资源 / 成本 / 瓶颈）
- **管理（企业版）**：用户 / 审计 / RBAC / 租户 / 配额 / API 密钥 / SSO / 许可证

---

## 2. 信息架构

### 2.1 导航结构（7 组任务流 + 企业版管理组）

2026-09-01 信息架构重设计：从旧结构按后端模块罗列 26 个菜单（6 个 section），改为**按用户任务流分 7 组**，每组 = 一个 section 标题 + 2~13 个子项。设计原则：用户任务流（看状态 / 做事 / 记忆 / 能力 / 运维 / 分析 / 反馈）而非后端模块罗列。

| 组 | i18n key | 图标 | 页面数 | 任务流语义 |
|----|----------|------|--------|-----------|
| 首页 | `nav.group.home` | overview | 2 | 看状态 + 任务历史 |
| 执行 | `nav.group.run` | play | 3 | 让 MAOP 做事 |
| 记忆 | `nav.group.memory` | brain | 3 | MAOP 认识你 |
| 能力 | `nav.group.capability` | bot | 13 | MAOP 能用谁 |
| 运维 | `nav.group.operate` | activity | 9 | 跑得怎样 |
| 分析 | `nav.group.analysis` | activity | 1 | 数据洞察 |
| 反馈 | `nav.group.feedback` | message-square | 1 | 用户评价 |
| 管理（企业版） | `nav.group.admin` | — | 8 | 企业治理 |
| 系统 | `nav.group.system` | gear | 3 | 平台设置 |

设置齿轮同时在 TopBar 右侧（不在侧边栏）直达；企业专属能力由 `enterprise: true` 过滤隐藏。

### 2.2 页面清单（43 页面）

#### 首页组（2）
| 路由 | 名称 | 视图 | 说明 |
|------|------|------|------|
| `/home` | overview | Overview.vue | 平台全局指标与健康状态一览 |
| `/home/tasks` | tasks | Tasks.vue | 任务历史：搜索、筛选、重跑 |

#### 执行组（3）
| 路由 | 名称 | 视图 | 说明 |
|------|------|------|------|
| `/run` | run | Run.vue | 运行任务并与智能体对话（结构化/对话双标签） |
| `/run/agents` | dispatch | ControlPanel.vue | 向指定 CLI 智能体分发任务 |
| `/run/workflow` | workflow | WorkflowEditor.vue | 可拖拽 DAG 编排智能体工作流 |

#### 记忆组（3）
| 路由 | 名称 | 视图 | 说明 |
|------|------|------|------|
| `/memory` | memory | ThreeLayerMemory.vue | 三层记忆：工作、短期、长期 |
| `/memory/search` | search | Search.vue | 跨记忆/向量/图/日志/智能体一站式搜索 |
| `/memory/graph` | knowledge-graph | KnowledgeGraph.vue | 知识图谱可视化 |

#### 能力组（13）
| 路由 | 名称 | 视图 | 说明 |
|------|------|------|------|
| `/capability/agents` | agents | Agents.vue | 已发现和注册的智能体 CLI |
| `/capability/skills` | skills | Tools.vue | 内置、导入和自定义技能 |
| `/capability/market` | skill-market | SkillMarket.vue | 浏览、导入和发布可复用技能 |
| `/capability/models` | models | Models.vue | 模型注册表、提供商和路由 |
| `/capability/evolve` | evolve | Evolve.vue | 规则驱动的自动调优与变更日志 |
| `/capability/mcp` | mcp | McpManager.vue | MCP 工具注册与沙箱管理 |
| `/capability/plugins` | plugins | Plugins.vue | 安装、配置和管理运行时插件 |
| `/capability/subagents` | subagents | Subagents.vue | 子代理生命周期与委派范围 |
| `/capability/routing` | routing-rules | RoutingRules.vue | 智能体与请求路由规则 |
| `/capability/scheduling` | scheduling | Scheduling.vue | 任务调度与并发策略 |
| `/capability/protocols` | protocols | Protocols.vue | 通信协议与传输配置 |
| `/capability/worktrees` | worktrees | Worktrees.vue | Git 工作树隔离，支持并行运行 |
| `/capability/agent-proxy` | agent-proxy | AgentProxy.vue | 智能体后端反向代理与多路复用 |

#### 运维组（9）
| 路由 | 名称 | 视图 | 说明 |
|------|------|------|------|
| `/operate` | monitor | Monitor.vue | 实时指标、追踪和 SSE 流 |
| `/operate/logs` | logs | Logs.vue | 系统和智能体日志 |
| `/operate/tracing` | observability | Observability.vue | OTel 链路追踪、Prometheus 指标 |
| `/operate/cost` | cost | Cost.vue | Token 用量与消费分析 |
| `/operate/debate` | debate | Debate.vue | 多智能体讨论与共识达成 |
| `/operate/blackboard` | blackboard | Blackboard.vue | 智能体协作共享黑板 |
| `/operate/alerts` | alert-rules | AlertRules.vue | 阈值与异常告警规则编辑器 |
| `/operate/webhooks` | webhooks | Webhooks.vue | 出站 Webhook 端点与投递日志 |
| `/operate/integrations` | integrations | Integrations.vue | N8N 与外部系统集成 |

#### 分析组（1）
| 路由 | 名称 | 视图 | 说明 |
|------|------|------|------|
| `/analysis` | analysis | Analysis.vue | 数据分析仪表盘与洞察（6 标签页） |

#### 反馈组（1）
| 路由 | 名称 | 视图 | 说明 |
|------|------|------|------|
| `/feedback` | feedback | Feedback.vue | 用户反馈收集与评价 |

#### 管理组（企业版专属，8）
| 路由 | 名称 | 视图 | 守卫 |
|------|------|------|------|
| `/users` | users | Users.vue | `requiresEnterprise` |
| `/audit` | audit | Audit.vue | `requiresEnterprise` |
| `/rbac` | rbac | RBAC.vue | `requiresEnterprise` |
| `/tenants` | tenants | Tenants.vue | `requiresEnterprise` |
| `/quotas` | quotas | Quotas.vue | `requiresEnterprise` |
| `/apikeys` | apikeys | ApiKeys.vue | `requiresEnterprise` |
| `/sso` | sso | SsoProviders.vue | `requiresEnterprise` |
| `/licenses` | licenses | Licenses.vue | `requiresEnterprise` |

#### 系统组（3）
| 路由 | 名称 | 视图 | 说明 |
|------|------|------|------|
| `/settings` | settings | Settings.vue | 外观、版本和后端配置 |
| `/notifications` | notifications | Notifications.vue | 平台告警与通知中心 |
| `/docs` | docs | Docs.vue | 指南、API 参考和架构决策 |

### 2.3 路由设计

**懒加载**：所有路由组件均使用动态 `import()` 懒加载，按需拆分 chunk。

**旧路径 301 重定向**：全部旧路由保留重定向（书签 / 深链 / 外部链接兼容，零断链）。例如：
- `/` → `/home`
- `/control` → `/run?tab=structured`
- `/chat` → `/run?tab=chat`
- `/agents` → `/capability/agents`
- `/tools` → `/capability/skills`
- `/evolve` → `/capability/evolve`
- `/workflow-editor` → `/run/workflow`

**catch-all**：未知路径静默重定向到 `overview`（SPA 友好降级，不显示 404）。

**高频路由预加载**：路由就绪后在 `requestIdleCallback` 空闲时段预取 `overview` + `run` 的 chunk，缩短后续跳转首屏耗时。

**企业版守卫**：`meta.requiresEnterprise` 路由在 `beforeEach` 中检查 `useEditionStore`。冷加载安全默认为 `personal`（后端未就绪时绝不放行企业版路由），后端 `/api/info/config` 就绪后异步 hydrate store。

---

## 3. 设计系统

### 3.1 Design Tokens

Token 定义在 `src/styles/tokens.css`（暗色为默认 `:root`），亮色主题在 `src/styles/themes.css`（`[data-theme="light"]`）。所有 token 以 CSS 自定义属性暴露，组件通过 `var(--token)` 消费。

#### 3.1.1 色彩系统

**品牌色**（克制信号蓝，Azure-500 灵感，非模板紫）：

| Token | 暗色 | 亮色 | 用途 |
|-------|------|------|------|
| `--brand` | `#3574f0` | `#3574f0` | 品牌主色 |
| `--brand-strong` | `#5793f7` | `#2d63c8` | 品牌高亮变体 |
| `--brand-faint` | `rgba(53,116,240,.10)` | `rgba(53,116,240,.08)` | 品牌极淡背景 |
| `--brand-soft` | `rgba(53,116,240,.18)` | `rgba(53,116,240,.12)` | 品牌柔和背景 |
| `--brand-contrast` | `#ffffff` | `#ffffff` | 品牌色上可读文字 |

**语义状态色**：

| Token | 暗色 | 亮色 | 用途 |
|-------|------|------|------|
| `--success` | `#3fb950` | `#1f883d` | 成功 |
| `--warn` | `#d29922` | `#bf8700` | 警告 |
| `--fail` | `#f85149` | `#d1242f` | 失败（图表/徽标指示色） |
| `--danger` | `#f05545` | `#d1242f` | 危险（文本/边框，满足 WCAG AA 4.5:1） |
| `--info` | `#4cc2ff` | `#0a7ea4` | 信息 |

**语义状态对**（背景 + 文字成对出现，用于热力图、状态徽标、图表网格）：

| 背景 Token | 文字 Token | 暗色背景示例 |
|-----------|-----------|-------------|
| `--success-bg` | `--success-strong` | `rgba(63,185,80,.22)` / `#7ee787` |
| `--warn-bg` | `--warn-strong` | `rgba(210,153,34,.20)` / `#e3b341` |
| `--fail-bg` | `--fail-strong` | `rgba(248,81,73,.20)` / `#ff7b72` |
| `--info-bg` | `--info-strong` | `rgba(76,194,255,.18)` / `#79c0ff` |
| `--neutral-bg` | `--neutral-strong` | `rgba(154,163,178,.12)` / `var(--text-muted)` |

**图表色板**（10 色，克制的高斯冷色谱，与品牌蓝同族）：

| Token | 暗色 | 亮色 |
|-------|------|------|
| `--chart-1` | `#3574f0` | `#3574f0` |
| `--chart-2` | `#4cc2ff` | `#0a7ea4` |
| `--chart-3` | `#3fb950` | `#1f883d` |
| `--chart-4` | `#d29922` | `#bf8700` |
| `--chart-5` | `#9e8cfc` | `#7c63e0` |
| `--chart-6` | `#39c5cf` | `#0d9db8` |
| `--chart-7` | `#f778ba` | `#d12470` |
| `--chart-8` | `#56d4dd` | `#0891b2` |
| `--chart-9` | `#7ee787` | `#4e8000` |
| `--chart-10` | `#ff7b72` | `#cf222e` |

KPI 网格使用 `ACCENTS` 数组引用 `var(--chart-1)` ~ `var(--chart-10)`，确保 10 张卡片的彩色左边框不重复。

**表面层级**（层级差压缩到 3-4% 亮度，弱对比让界面"平"下来，层级靠 1px 描边而非色块堆叠）：

| Token | 暗色 | 亮色 | 用途 |
|-------|------|------|------|
| `--bg` | `#1b1d21` | `#f2f3f5` | 应用底色（workbench-100） |
| `--surface` | `#22242a` | `#ffffff` | 卡片底 |
| `--surface-2` | `#292b32` | `#f6f7f9` | 次级表面 |
| `--surface-3` | `#31343c` | `#eef0f4` | 三级表面 |
| `--surface-4` | `#3a3e47` | `#e4e7ec` | 四级表面 |
| `--surface-hover` | `rgba(255,255,255,.06)` | `rgba(0,0,0,.04)` | hover 底色 |

**描边**：

| Token | 暗色 | 用途 |
|-------|------|------|
| `--border` | `#3c4048` | 中性灰描边 |
| `--border-strong` | `#4e545f` | 强描边 |
| `--border-subtle` | `rgba(163,173,190,.13)` | 柔和描边 |
| `--border-faint` | `rgba(163,173,190,.07)` | 极淡描边 |

**文字**：

| Token | 暗色 | 亮色 | 用途 |
|-------|------|------|------|
| `--text` | `#e8eaf0` | `#1d2129` | 主文字 |
| `--text-muted` | `#9aa3b2` | `#59616e` | 次要文字 |
| `--text-faint` | `#8a93a3` | `#6b7382` | 淡文字（满足 WCAG AA 4.5:1） |

#### 3.1.2 间距系统

`--sp-1` ~ `--sp-10`，遵循"内紧外松"原则（组内距离 < 组间距离）：

| Token | comfortable | compact | 场景 |
|-------|-------------|---------|------|
| `--sp-1` | 4px | 3px | 最小间隔 |
| `--sp-2` | 8px | 6px | 卡片内部元素间隔 |
| `--sp-3` | 12px | 10px | 卡片内部元素间隔 |
| `--sp-4` | 16px | 12px | 页头→首屏内容、卡片↔卡片平级 |
| `--sp-5` | 20px | 16px | — |
| `--sp-6` | 24px | 20px | 逻辑换段（独立段落） |
| `--sp-7` | 32px | 24px | — |
| `--sp-8` | 40px | 32px | EmptyState 内边距 |
| `--sp-9` | 48px | — | — |
| `--sp-10` | 56px | — | — |

#### 3.1.3 字号系统

| Token | comfortable | compact | 用途 |
|-------|-------------|---------|------|
| `--fs-3xs` | 9px | 9px | — |
| `--fs-2xs` | 10px | 10px | — |
| `--fs-xs` | 11px | 11px | Badge、Segmented-sm |
| `--fs-sm` | 12px | 12px | 副标题、描述 |
| `--fs-base` | 13px | 12px | 正文基准 |
| `--fs-md` | 14px | 13px | 卡片标题 |
| `--fs-lg` | 16px | 15px | — |
| `--fs-lg-plus` | 17px | — | — |
| `--fs-xl` | 18px | 18px | — |
| `--fs-2xl` | 22px | 22px | 页标题 |

字体族：
- `--font-sans`: Inter 优先（工程感），降级到 system / PingFang SC / Microsoft YaHei
- `--font-mono`: JetBrains Mono 优先，降级到 SF Mono / Cascadia Code / Consolas

#### 3.1.4 圆角系统

JetBrains 哲学：上限 8px，控件 6px，标签 4px。

| Token | 值 | 用途 |
|-------|-----|------|
| `--r-xs` | 3px | — |
| `--r-sm` | 4px | 标签 |
| `--r-md` | 6px | 控件 |
| `--r-lg` | 8px | 卡片 |
| `--r-xl` | 10px | — |
| `--r-full` | 999px | 胶囊（Badge） |
| `--r` | `var(--r-md)` | 默认 |

#### 3.1.5 动画系统

三档动画时长（微交互 / 常规过渡 / 大区域）：

| Token | 值 | 用途 |
|-------|-----|------|
| `--motion-fast` | 120ms | 微交互 |
| `--motion-normal` | 250ms | 常规过渡（替换硬编码 .15s/.25s） |
| `--motion` | 180ms | legacy 别名（fast 与 normal 之间） |
| `--motion-slow` | 280ms | 大区域 / 进度条 |

缓动函数：
- `--ease`: `cubic-bezier(.4, 0, .2, 1)` — 标准缓动
- `--ease-out`: `cubic-bezier(.16, 1, .3, 1)` — 出场缓动

**动画时长规范**：所有 CSS `transition` / `animation` 时长必须引用上述 motion token，禁止硬编码 `ms` 值。如需中间值，使用 token 的整数倍数（如 `calc(var(--motion-fast) * 2)`）。

**prefers-reduced-motion 全局降级**（来源：2026-09-11-prefers-reduced-motion-global-block-and-max-duration）：

`tokens.css` 末尾已实现全局降级块：
```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .001ms !important;
    scroll-behavior: auto !important;
  }
}
```
> 注意：使用 `.001ms` 而非 `0s`（部分浏览器对 `0s` 有兼容性问题，`.001ms` 是业界通用值）。`animation-iteration-count: 1` 停止循环动画的无限迭代。

**预定义 keyframes**：
- `maop-pulse-once`：单次脉冲（刷新按钮状态变化，非永久循环）
- `maop-view-in`：视图进入（opacity + translateY）
- `maop-shimmer`：Skeleton 闪烁
- `maop-cursor-blink`：光标闪烁（opacity 闪烁，替代位移）

#### 3.1.6 阴影系统

JetBrains 哲学：阴影只服务浮层（popup / modal），卡片不靠阴影抬升。卡片层级来自 1px 描边 + 明度差。

| Token | 暗色 | 用途 |
|-------|------|------|
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,.24)` | 小浮层 |
| `--shadow-md` | `0 2px 8px rgba(0,0,0,.30)` | 中浮层 |
| `--shadow-lg` | `0 12px 28px rgba(0,0,0,.42)` | 大浮层 |
| `--shadow-pop` | `0 8px 24px rgba(0,0,0,.40)` | 弹出层 |
| `--shadow-modal` | `0 12px 40px rgba(0,0,0,.50)` | 模态框 |
| `--shadow-card` | `0 4px 16px rgba(0,0,0,.30)` | 卡片（保留，但 Card 组件不使用常驻阴影） |
| `--shadow-brand` | `0 2px 10px rgba(53,116,240,.30)` | 品牌色阴影 |

**无辉光策略**：`--card-sheen`、`--topbar-glow`、`--app-ambient` 等全部置为 `none` / `transparent`。辉光是"AI 模板感"的最大来源，分隔靠 1px 实线描边。

#### 3.1.7 布局尺寸

| Token | 值 | 用途 |
|-------|-----|------|
| `--sidebar-w` | 232px | 侧边栏宽度 |
| `--rail-w` | 64px | 收起后轨道宽度 |
| `--content-pad` | 24px | 内容区内边距 |
| `--maxw` | 1440px | 最大内容宽度 |
| `--row-h` | 36px | 行高基准（JB 密度，非 44px） |
| `--topbar-h` | 56px | 顶栏高度 |

#### 3.1.8 Z-index 层级

| Token | 值 | 用途 |
|-------|-----|------|
| `--z-base` | 0 | 基础 |
| `--z-sticky` | 30 | sticky 定位 |
| `--z-sidebar` | 40 | 侧边栏 |
| `--z-drawer` | 40 | 抽屉 |
| `--z-topbar` | 50 | sticky 顶栏 |
| `--z-topbar-fixed` | 10 | fixed 顶栏（低于侧栏） |
| `--z-overlay` | 80 | 遮罩 |
| `--z-modal` | 90 | 模态框 |
| `--z-toast` | 100 | Toast |

#### 3.1.9 密度系统

通过 `[data-density="compact"]` 切换，整体收紧约 20%：
- 间距：`--sp-4/5/6` = 12/16/20
- 行高：`--row-h` = 36px
- 内容内边距：`--content-pad` = 20px
- 字号略缩：base 12 / 2xl 22
- 顶栏略矮：`--topbar-h` = 64px

所有组件均消费这些 token，切换 density 即全站一致生效（无逐视图特例）。

---

### 3.2 组件库

组件库位于 `src/components/`，共 **24 个组件**，通过 `src/components/index.js` 集中导出。

#### 3.2.1 基础组件

| 组件 | 文件 | 说明 | 关键 Props |
|------|------|------|-----------|
| **Card** | Card.vue | 内容卡片，1px 描边 + 明度差分层级，无常驻阴影 | `title`, `subtitle`, `icon`, `badge`, `badgeTone`, `padded`, `marginBottom`, `clickable` |
| **Badge** | Badge.vue | 胶囊标签，6 种 tone | `label`, `tone` (neutral\|brand\|success\|warn\|fail\|info), `icon` |
| **AppIcon** | AppIcon.vue | 自包含线性图标集（Lucide path data 内联，无运行时依赖） | `name`, `size` (默认 18) |
| **StatCard** | StatCard.vue | KPI 统计卡片 | `label`, `value`, `unit`, `icon`, `tone`, `accent`, `loading`, `yoy`, `mom` |
| **Toast** | Toast.vue | 全局提示 | — |

**AppIcon 图标集**：图标继承 `currentColor`，自动跟随周围文字色与主题。包含 60+ 图标，按类别分组：
- 导航：overview, play, pause, chat, bot, user, brain, sparkles, search, box, wrench, gauge, scroll, activity, dollar, clipboard, shield, building, gear
- 状态：sun, moon, power, refresh, check, check-circle, x, x-circle, alert-triangle, filter, alert, loader, info
- 方向：chevron-right, arrow-up, arrow-down, chevrondown, arrow-left-right
- 资源：cpu, server, trash, database, route, plus, zap, clock, archive, message-square, code, file, beaker, network, link
- 版本控制：git-branch, git-compare
- 其他：star, paperclip, send, panelleft, panelright, download, radio, globe, external, external-link, book-open, file-text, compass, plug, inbox, share2, rotate-ccw

**图标尺寸规范**（来源：2026-09-10-react-icon-size-prop-hardcoded-audit）：统一通过 `size` prop 传递数值（px），禁止在 CSS 中硬编码图标尺寸。常用尺寸：12（Badge 内）、14（操作按钮）、15（刷新按钮）、16（卡片标题/快捷操作）、18（默认）、20（PEV 阶段）、34（EmptyState）。

#### 3.2.2 布局组件

| 组件 | 文件 | 说明 |
|------|------|------|
| **ListPageLayout** | ListPageLayout.vue | 统一列表页骨架，收敛页头/统计/过滤/三态结构 |
| **PageHeader** | PageHeader.vue | 页面标题头，含 badges / actions 插槽 |
| **DetailDrawer** | DetailDrawer.vue | 详情抽屉 |
| **TopBar** | TopBar.vue | 全局顶栏（fixed，z-index:10） |
| **AppFooter** | AppFooter.vue | 全局页脚 |

**ListPageLayout** 是最核心的布局组件，收敛 22 个视图各自手写的页头/统计/过滤/三态结构，提供唯一模板：

```vue
<ListPageLayout
  :loading="loading" :error="error" :empty="!rows.length"
  :filter-schema="[...]" search-key="query"
  error-title="Failed" empty-title="No data">
  <template #actions><!-- Segmented + 刷新按钮 --></template>
  <template #content><DataTable :rows="rows" /></template>
</ListPageLayout>
```

规则：
- 加载 / 错误 / 空态 → 交给本组件的三态，禁止在视图里再手写
- 过滤器 → 用 `filterSchema` 声明，不再手写 select/input
- 内容 → 放进 `#content`；特殊空态可用 `#itemsEmpty` 覆盖

#### 3.2.3 数据组件

| 组件 | 文件 | 说明 | 关键 Props |
|------|------|------|-----------|
| **DataTable** | DataTable.vue | 数据表格 | `rows`, `columns`, `loading`, `sortable`, `emptyText`, `maxHeight`, `compact` |
| **FilterBar** | FilterBar.vue | 声明式过滤栏 | `modelValue`, `schema`, `searchKey`, `searchPlaceholder`, `resultsLabel` |
| **Segmented** | Segmented.vue | 分段控件（单选标签组），支持键盘 roving tabindex | `modelValue`, `options` ({value,label,icon}), `size` (sm\|md), `equal` |
| **DagGraph** | DagGraph.vue | DAG 进度图（SSE 流式） | — |
| **McpTopology** | McpTopology.vue | MCP 拓扑可视化 | — |
| **EvolutionTimeline** | EvolutionTimeline.vue | 调优历史时间线 | — |
| **NodeDetailPanel** | NodeDetailPanel.vue | DAG 节点详情面板 | — |

#### 3.2.4 反馈组件

| 组件 | 文件 | 说明 | 关键 Props |
|------|------|------|-----------|
| **Skeleton** | Skeleton.vue | 骨架屏加载态 | `lines`, `block`, `height` |
| **EmptyState** | EmptyState.vue | 空态/错误态，含图标容器 + 标题 + 描述 + 操作 | `icon`, `title`, `description`, `tone` (fail\|success\|warn\|info) |
| **ConfirmDialog** | ConfirmDialog.vue | 确认对话框 | — |
| **CoachMarks** | CoachMarks.vue | 分步聚光首访引导（唯一首访引导） | — |
| **CommandPalette** | CommandPalette.vue | 命令面板（Ctrl+K） | — |
| **OnboardingWizard** | OnboardingWizard.vue | 引导向手（保留但不再挂载，由 CoachMarks 替代） | — |

---

### 3.3 暗色模式

#### 3.3.1 CSS 变量切换机制

- **暗色为默认**：token 定义在 `:root`，`color-scheme: dark`
- **亮色通过 `[data-theme="light"]` 激活**：在 `<html>` 元素上设置 `data-theme="light"` 即可切换
- **所有语义 token 双主题定义**：`--bg`、`--surface`、`--text`、`--brand`、`--chart-*` 等在 `tokens.css` 和 `themes.css` 中分别定义
- **图表色板主题感知**：`--chart-1` ~ `--chart-10` 在亮暗色下有不同值，图表通过 `cssVar()` / `cssVarAlpha()` 工具函数读取

#### 3.3.2 注意事项

1. **legacy 别名保留**：`--bg`、`--bg2`、`--border`、`--text` 等旧别名保留向后兼容，新代码应使用语义 token（`--surface`、`--border-strong`、`--text-muted`）
2. **`--danger` vs `--fail`**：`--danger` 用于错误/危险状态的"文本与边框"（需满足 WCAG AA 4.5:1）；`--fail` 用于图表/徽标等"指示色"（视觉一致性优先）
3. **主题感知图标 data URI**：select 箭头等内联 SVG 图标通过 `--icon-chevron` token 按主题分别定义完整 data URI
4. **无辉光策略**：`--card-sheen`、`--topbar-glow`、`--app-ambient` 等在双主题下均置为 `none` / `transparent`

---

## 4. 页面规范

### 4.1 布局模式

#### 4.1.1 ListPageLayout 标准布局

绝大多数列表/管理页面采用 `ListPageLayout` 标准布局（如 McpManager、Analysis、Agents、Tools 等）：

```
┌─ PageHeader ────────────────────────────────────┐
│  [Badge] 页标题                    [Segmented] [刷新] │
├─────────────────────────────────────────────────┤
│  [可选] 统计条                                    │
├─────────────────────────────────────────────────┤
│  [可选] FilterBar（声明式过滤）                    │
├─────────────────────────────────────────────────┤
│  三态主体:                                        │
│    error   → EmptyState(tone=fail)               │
│    loading → Skeleton                            │
│    empty   → EmptyState / #itemsEmpty 自定义      │
│    data    → #content 插槽                        │
└─────────────────────────────────────────────────┘
```

#### 4.1.2 仪表盘布局

Overview.vue 采用分层仪表盘布局：

- **层 1 — Hero strip**：健康结论 + 关键运行态（一屏之内给答案），含状态点 + 状态标签 + KPI 摘要 + 新鲜度
- **企业版差异化 — Plan-Execute-Verify 三阶段工作流**：仅企业版渲染，纯静态描述（route → play → check-circle）
- **层 2 — Action 磁贴**：4 个最高频入口（运行任务 / 对话 / Agents / 日志）
- **KPI grid**：StatCard 网格 + 内联 SVG sparkline 迷你火花线图（7 天趋势）
- **层 3 — 图表 (2/3) + 活动流 (1/3) 并排**
- **系统信息 / 失败排名 / 降级**：Card 堆叠

#### 4.1.3 多标签页布局

管理/分析页面常用 `Segmented` 组件实现多标签页切换（如 McpManager 的 servers/tools/stats/concurrency，Analysis 的 summary/agents/trends/resources/cost/bottlenecks）。标签页内容通过 `v-show` 切换（保持组件状态，不销毁）。

### 4.2 响应式断点

渐进式列隐藏策略：

| 断点 | 行为 |
|------|------|
| 默认（>900px） | 全列显示 |
| ≤900px（平板） | 隐藏次要列 |
| ≤640px（手机） | 仅保留核心列，表格转为卡片堆叠 |

### 4.3 三态处理

所有数据页面统一处理 loading / error / empty 三态：

| 状态 | 组件 | 示例 |
|------|------|------|
| **loading** | `<Skeleton>` | `<Skeleton block height="44px" />` 或 `<Skeleton :lines="6" block />` |
| **error** | `<EmptyState icon="alert-triangle" tone="fail" :title="..." :description="error" />` | 含重试操作 |
| **empty** | `<EmptyState icon="..." :title="..." :description="..." />` | 含操作引导 |

三态优先级：error > loading > empty > data。`ListPageLayout` 内置三态处理，视图无需手写。

### 4.4 模态框

#### 4.4.1 v-modal-a11y 指令

模态框使用 `v-modal-a11y` 自定义指令实现无障碍：
- 焦点陷阱（Tab 键在模态内循环）
- ESC 键关闭（触发 `@modal:escape` 事件）
- `aria-modal="true"` + `role="dialog"`
- 点击遮罩关闭（`@click.self`）

```vue
<Teleport to="body">
  <div v-if="show" v-modal-a11y class="modal-overlay" @click.self="close" @modal:escape="close">
    <div class="modal" role="dialog" aria-modal="true">
      <div class="modal__head"><!-- 标题 + 关闭按钮 --></div>
      <div class="modal__body"><!-- 表单内容 --></div>
    </div>
  </div>
</Teleport>
```

#### 4.4.2 确认对话框

使用 `ConfirmDialog` 组件处理删除等危险操作确认。

---

## 5. API 对接规范

### 5.1 useApiStore 使用

API 调用统一通过 `useApiStore`（Pinia Setup Store），提供 `get` / `post` / `put` / `delete` 四个方法：

```js
const api = useApiStore();

// GET
const res = await api.get('/api/info/activity?limit=8');
if (res && res.status === 'ok' && Array.isArray(res.events)) { ... }

// POST
const res = await api.post('/api/mcp/servers', { name, url, transport });

// PUT
await api.put(`/api/mcp/servers/${id}`, { name, url });

// DELETE
await api.del(`/api/mcp/servers/${id}`);
```

**认证机制**：
- Token 由后端 httpOnly cookie 管理（`Set-Cookie: maop_token=...; HttpOnly; Secure; SameSite=Strict`）
- 前端通过 `credentials: 'include'` 自动携带 cookie，不直接接触 token
- 401 时自动尝试 refresh token（`/api/auth/refresh`），失败则清除登录态并触发 `maop:unauthorized` 事件

**超时控制**：统一 30s 超时（`AbortController`），防止后端无响应时前端请求永久挂起。

### 5.2 端点命名约定

- 公开端点：`/api/info/config`（版本信息，无需认证）
- 认证端点：`/api/auth/refresh`、`/api/auth/logout`
- 业务端点：`/api/{module}/{action}`，如 `/api/mcp/servers`、`/api/info/activity`

### 5.3 响应格式

统一 JSON 响应：
- 成功：`{ status: 'ok', ...data }`
- 失败：HTTP 非 2xx，body 含 `{ error: 'message' }`，前端抛 `Error(errBody.error || 'API {url}: {status}')`

---

## 6. 视觉规范

### 6.1 图表

#### 6.1.1 内联 SVG 实现

MAOP 采用**内联 SVG** 实现图表，而非依赖重量级图表库（部分页面如 Overview 使用 `vue-chartjs`，但 McpManager / Analysis 的时间序列、饼图、火花线均为内联 SVG）：

- **火花线（Sparkline）**：`viewBox="0 0 100 24"`，path 计算 area + line
- **时间序列图**：`viewBox` 动态计算，含网格线 + area path + line path + 数据点 circle
- **饼图**：`viewBox` 基于 `PIE_R * 2`，slice 用 path 弧线绘制
- 所有图表含 `role="img"` + `aria-label` 无障碍标注

#### 6.1.2 图表色板

通过 `cssVar()` / `cssVarAlpha()` 工具函数（`src/utils/chartTokens.js`）读取 CSS 变量，确保图表跟随主题切换。基础图表选项在 `src/utils/chartOptions.js` 的 `baseLineOptions` 中定义。

KPI 网格使用 10 色去重色板（`ACCENTS` 数组引用 `var(--chart-1)` ~ `var(--chart-10)`），确保彩色左边框不重复。

### 6.2 动画

#### 6.2.1 CSS @keyframes

预定义 keyframes（在 `tokens.css` 中）：
- `maop-pulse-once`：单次脉冲（刷新按钮状态变化）
- `maop-view-in`：视图进入过渡（opacity + translateY）
- `maop-shimmer`：Skeleton 闪烁
- `maop-cursor-blink`：光标闪烁

#### 6.2.2 transition

组件过渡统一使用 motion token：
```css
transition: border-color var(--motion) var(--ease), background var(--motion) var(--ease);
```

Card hover 只加深描边，不上浮/不投影（"静止感"是工具感与模板感的分水岭）。

#### 6.2.3 prefers-reduced-motion

全局降级块已在 `tokens.css` 中实现（见 3.1.5）。所有动画在用户启用"减少动效"偏好时降级为近瞬时（`.001ms`），循环动画迭代次数设为 1。

### 6.3 图标

#### 6.3.1 AppIcon 组件

自包含线性图标集，Lucide path data 内联在组件中（vendored，无运行时依赖）：
- 继承 `currentColor`，自动跟随周围文字色与主题
- 通过 `size` prop 控制尺寸（数值 px），禁止 CSS 硬编码
- `aria-hidden="true"`（装饰性图标）

#### 6.3.2 图标使用规范

- 导航图标：16px（侧边栏）
- 卡片标题图标：16px（含 30px 容器 + brand-soft 背景）
- 操作按钮图标：14-15px
- Badge 内图标：12px
- EmptyState 图标：34px（含 56px 圆形容器）
- PEV 阶段图标：20px

---

## 7. i18n 规范

### 7.1 翻译键命名

采用点分层级命名：

| 前缀 | 用途 | 示例 |
|------|------|------|
| `nav.*` | 导航标签与副标题 | `nav.overview`、`nav.overview.subtitle`、`nav.group.home` |
| `view.{page}.{section}.{key}` | 页面内文案 | `view.mcp.servers.title`、`view.analysis.tab.summary` |
| `common.*` | 跨页面复用词汇 | `common.refresh`、`common.loading`、`common.retry` |
| `status.*` | 状态标签 | `status.live`、`status.offline` |
| `auth.*` | 认证相关 | `auth.signIn`、`auth.loginFailed` |
| `settings.*` | 设置页 | `settings.theme`、`settings.density` |
| `a11y.*` | 无障碍标签 | `a11y.toggleNavigation`、`a11y.commandPalette` |
| `coach.*` | 引导标记 | `coach.actions.title`、`coach.nav.body` |
| `palette.*` | 命令面板 | `palette.placeholder` |
| `footer.*` | 页脚 | `footer.tagline`、`footer.copyright` |
| `topbar.*` | 顶栏 | `topbar.systemName`、`topbar.role.admin` |
| `edition.*` | 版本切换 | `edition.switchFailed` |
| `users.*` | 用户管理 | `users.registerUser`、`users.confirmDelete` |
| `dag.*` | DAG 组件 | `dag.emptyHint`、`dag.connecting` |

**参数化翻译**：使用 `{param}` 占位符，如 `common.secondsAgo: '{n}s ago'`，调用 `t('common.secondsAgo', { n: s })`。

### 7.2 自动收集机制

i18n 字典分两部分：

1. **coreMessages**（`src/i18n/index.js`）：shell 字符串（nav、status、auth、footer、settings、common、coach、palette、a11y）
2. **view 字典**（`src/i18n/view-*.js`）：每个视图拥有自己的字符串文件，导出 `{ messages: { en, zh } }`

**自动收集**：通过 `import.meta.glob('./view-*.js', { eager: true })` 同步加载所有 view 字典，合并为最终 `messages`：

```js
const viewModules = import.meta.glob('./view-*.js', { eager: true });
const viewMessages = { en: {}, zh: {} };
for (const mod of Object.values(viewModules)) {
  const m = mod.messages || (mod.default && mod.default.messages);
  if (m && m.en) Object.assign(viewMessages.en, m.en);
  if (m && m.zh) Object.assign(viewMessages.zh, m.zh);
}
export const messages = {
  en: { ...coreMessages.en, ...viewMessages.en },
  zh: { ...coreMessages.zh, ...viewMessages.zh },
};
```

**优势**：视图可添加键而无需修改 `index.js`（无编辑冲突，滚动可增量进行）。当前已有 **49 个 view 字典文件**。

**eager 加载原因**：i18n 字典必须在首屏渲染前就绪，否则 `t()` 在初次渲染时返回原始 key，导致 UI 闪烁。语言包总体积小（< 50KB gzipped），懒加载的 chunk 数量开销反而大于内容本身。

### 7.3 中英双语

- **`en` 为源真基准**：每个 `t()` 包裹的 UI 字符串在 `en` 中必须存在
- **`zh` 降级回退**：未翻译的 `zh` 键回退到 `en`，UI 永不显示原始 key 或中断
- **locale 持久化**：locale 存储在共享 ui store（持久化到 localStorage + 镜像到 `<html data-lang>`），切换语言响应式重渲染所有 `t()` 调用
- **品牌名保留英文**：`topbar.systemName: 'MAOP'` 在 en/zh 中一致

### 7.4 useI18n 使用

```js
import { useI18n } from '../i18n';
const { t, locale, setLocale } = useI18n();

// 基本用法
t('view.mcp.servers.title')

// 参数化
t('common.secondsAgo', { n: 30 })
```

---

## 附录 A：设计原则速查

| 原则 | 实践 |
|------|------|
| **Workbench 而非 AI 模板** | 石墨灰底 + 信号蓝，弃用靛紫与渐变，无辉光 |
| **1px 描边分层** | 卡片层级靠描边 + 明度差，不靠阴影 |
| **内紧外松** | 组内间距 < 组间间距（`--sp-2/3` vs `--sp-4/6`） |
| **三态统一** | loading → Skeleton，error/empty → EmptyState，禁止手写 |
| **声明式过滤** | FilterBar + filterSchema，禁止手写 select/input |
| **Token 驱动** | 所有颜色/间距/字号/圆角/动画引用 token，禁止硬编码 |
| **主题感知** | 双主题 token 定义，图表通过 cssVar() 读取 |
| **无障碍** | v-modal-a11y 焦点陷阱，Segmented roving tabindex，aria-label，:focus-visible |
| **prefers-reduced-motion** | 全局降级块，.001ms + iteration-count:1 |
| **i18n 全覆盖** | 所有 UI 字符串经 t() 包裹，en 为基准，zh 降级回退 |
| **旧路径零断链** | 301 重定向保留所有旧路由 |

## 附录 B：文件索引

| 文件 | 用途 |
|------|------|
| `src/router/index.js` | 路由定义 + 旧路径重定向 + 企业版守卫 + 高频预加载 |
| `src/nav.js` | 导航结构（单一真相源）+ 版本过滤 + 页面元数据解析 |
| `src/styles/tokens.css` | Design tokens（暗色默认）+ density + base reset + keyframes |
| `src/styles/themes.css` | 亮色主题（`[data-theme="light"]`） |
| `src/components/index.js` | 组件库集中导出 |
| `src/stores/api.js` | useApiStore（get/post/put/delete + 认证 + 超时） |
| `src/stores/edition.js` | useEditionStore（版本管理 + 降级） |
| `src/stores/ui.js` | useUiStore（locale + theme + density 持久化） |
| `src/i18n/index.js` | coreMessages + 自动收集 + useI18n |
| `src/i18n/view-*.js` | 各视图字典（49 个文件） |
| `src/utils/chartTokens.js` | 图表 token 读取工具（cssVar / cssVarAlpha） |
| `src/utils/chartOptions.js` | 图表基础选项（baseLineOptions） |