# MAOP 前端设计规范

> 本规范沉淀自产品设计 RFC-001 迭代 A–C 的组件契约与设计模式，是 MAOP 企业控制台前端唯一的交互/视觉事实源。

## 1. 概述

### 1.1 文档目的

统一 MAOP 前端的交互模式、组件选择和视觉节奏。迭代 A 完成信息架构重排，迭代 B 引入差异化组件（EvolutionTimeline、CommandPalette、CoachMarks），迭代 C 抹平交互差异（ListPageLayout、FilterBar、DetailDrawer、chartTokens/chartOptions）。本规范把三轮迭代沉淀的组件用法、三态规则、token 节奏文档化，避免新视图重新发明轮子。

### 1.2 适用范围

适用于 `dashboard-enterprise/` 下所有 Vue 视图（`src/views/*.vue`）与组件（`src/components/*.vue`）。所有列表/管理类页面、详情查看交互、数据表格、过滤器、图表都必须消费本规范定义的组件与 token。

### 1.3 更新规则

- 新增组件或修改交互模式时，必须同步更新本规范。
- 修改 token 数值时，以 `src/styles/tokens.css` 为唯一事实源，本规范第 7、8 章随之同步。
- 组件 API 变更时，更新对应章节的「API 速查」并标注迁移影响。
- 任何视图不得绕过本规范手写 loading/error/empty 三态、手写过滤器、硬编码 px 或颜色值。

## 2. 列表页骨架 — ListPageLayout

### 2.1 何时使用

所有列表/管理类页面统一使用 `ListPageLayout` 作为页面骨架，包括但不限于 Tenants、Audit、Users 等管理视图。它收敛了原本散落在各视图手写的「页头 / 统计条 / 过滤器 / 三态主体」结构，提供唯一模板。

### 2.2 API 速查

#### 2.2.1 Props

表：ListPageLayout props 参数说明表

| Prop | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `loading` | Boolean | `false` | 加载态，交给内部 Skeleton |
| `error` | String | `''` | 错误信息，非空时进入错误态 |
| `empty` | Boolean | `false` | 空态标记（通常传 `!rows.length`） |
| `filterSchema` | Array | `[]` | 过滤器声明，见第 5 章 |
| `searchKey` | String | `''` | 文本搜索字段名，对应 filters 对象的 key |
| `searchPlaceholder` | String | `''` | 搜索框 placeholder |
| `resultsLabel` | String | `''` | 结果计数文案（如 `"12 rows"`） |
| `errorTitle` | String | `''` | 错误态标题 |
| `emptyTitle` | String | `''` | 空态标题 |
| `emptyDesc` | String | `''` | 空态描述 |
| `loadingLines` | Number | `6` | Skeleton 行数 |
| `filters` | Object | `null` | 可选，父视图用 `v-model:filters` 持有同一引用 |

#### 2.2.2 Slots

表：ListPageLayout slots 说明表

| Slot | 作用域 | 说明 |
|------|--------|------|
| `badges` | — | 透传给 PageHeader 的徽标区 |
| `actions` | — | 透传给 PageHeader 的操作按钮区 |
| `stats` | — | 统计条（可选，整行渲染） |
| `content` | `{ filters }` | 主体内容，暴露内部 filters 供视图做过滤 |
| `itemsEmpty` | — | 自定义空态（仅当 `empty && $slots.itemsEmpty` 时生效） |
| `error` | `{ error }` | 自定义错误态（默认用 EmptyState 兜底） |
| `loading` | — | 自定义加载态（默认用 Skeleton 兜底） |

### 2.3 三态规则

三态判定优先级（源码顺序）：`error` → `loading` → `empty(#itemsEmpty)` → `empty(EmptyState)` → `content`。

- **loading** → 交给 ListPageLayout 的 Skeleton（`loadingLines` 行）。**禁止视图手写 loading**。
- **error** → 交给 ListPageLayout 的 EmptyState（`icon="alert-triangle"` `tone="fail"`）。**禁止视图手写 error**。需要自定义时用 `#error` slot。
- **empty** → 交给 ListPageLayout 的 EmptyState（`icon="inbox"`），或用 `#itemsEmpty` slot 覆盖。

### 2.4 过滤器规则

过滤器通过 `filterSchema` 声明 + `searchKey` 指定搜索字段，由内部 `FilterBar` 渲染。**禁止视图手写 `<select>` / `<input>` 过滤器**。详见第 5 章。

### 2.5 代码示例

代码示例：Tenants.vue 的 ListPageLayout 用法（Vue）

```vue
<ListPageLayout
  :loading="loading"
  :error="error"
  :empty="!tenants.length"
  :error-title="t('view.tenants.loadError')"
  :empty-title="t('view.tenants.noTenants')"
  :empty-desc="t('view.tenants.noTenantsDesc')"
  :loading-lines="3"
>
  <template #badges>
    <Badge tone="brand">{{ t('view.tenants.enterprise') }}</Badge>
  </template>
  <template #actions>
    <button class="btn btn--primary" @click="openCreate">
      <AppIcon name="building" :size="15" /> {{ t('view.tenants.createTenant') }}
    </button>
  </template>

  <template #content>
    <!-- 主体内容：卡片网格 / DataTable 等 -->
    <div class="tenant-grid">…</div>
  </template>
</ListPageLayout>
```

带过滤器的声明式用法：

代码示例：ListPageLayout + filterSchema 声明式过滤（Vue）

```vue
<ListPageLayout
  :loading="loading"
  :error="error"
  :empty="!filteredRows.length"
  :filter-schema="[
    { key: 'level', label: 'Level', options: [
      { value: 'info' }, { value: 'warning' }, { value: 'critical' }
    ] },
    { key: 'status', label: 'Status', options: [
      { value: 'active' }, { value: 'suspended' }
    ] }
  ]"
  search-key="query"
  search-placeholder="Filter by actor…"
  :results-label="`${filteredRows.length} rows`"
  v-model:filters="filters"
>
  <template #content="{ filters }">
    <DataTable :rows="applyFilters(rows, filters)" :columns="cols" />
  </template>
</ListPageLayout>
```

### 2.6 已迁移页面

- `views/Tenants.vue`
- `views/Audit.vue`
- `views/Users.vue`

## 3. 详情查看 — DetailDrawer

### 3.1 何时使用

查看详情类交互统一使用 `DetailDrawer`，右侧滑出，面板宽度 `min(480px, 92vw)`。适用于只读详情展示（如 Agent 记忆面板、自进化结果面板）。

### 3.2 何时不用

- **新建 / 编辑表单** → 使用全屏 Modal（保持现状，如 Tenants 创建、Users 注册/编辑）。
- **破坏性确认** → 使用小型居中 Modal（保持现状，如 Agent 删除确认）。

详见第 9 章弹窗交互约定。

### 3.3 API 速查

#### 3.3.1 Props

表：DetailDrawer props 参数说明表

| Prop | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `open` | Boolean | `false` | 是否展开 |
| `title` | String | `''` | 面板标题（同时作为 `aria-label`） |
| `icon` | String | `''` | 标题前图标名（AppIcon name） |

#### 3.3.2 Events

表：DetailDrawer events 说明表

| Event | 载荷 | 触发时机 |
|-------|------|----------|
| `close` | — | Esc 键 / 遮罩点击 / 关闭按钮 |

#### 3.3.3 Slots

表：DetailDrawer slots 说明表

| Slot | 说明 |
|------|------|
| `default` | 面板主体（可滚动） |
| `footer` | 底部固定区（可选，如操作按钮） |

### 3.4 特性

- **焦点 trap**：打开时焦点移入面板，Tab 循环不泄漏到背后（scrim 拦截 + 首末元素 trap）。
- **Esc 关闭**：监听 `keydown`，Escape 触发 `close`。
- **遮罩点击关闭**：scrim `@click` 触发 `close`。
- **焦点还原**：关闭后焦点回到打开前的元素。
- **Teleport 到 body**：不依赖父布局的 z-index / overflow。
- **过渡动画**：面板 `translateX(100%)` 滑入，时长 220ms。

### 3.5 代码示例

代码示例：Agents.vue 的 DetailDrawer 用法（Vue）

```vue
<!-- 记忆面板 -->
<DetailDrawer
  :open="memoryPanel.visible"
  :title="t('view.agents.memoryFor', { name: memoryPanel.agentName })"
  icon="brain"
  @close="memoryPanel.visible = false"
>
  <div class="memory-panel__toolbar">
    <button class="act-btn small" @click="reloadMemory(memoryPanel.agentName)">
      <AppIcon name="refresh" :size="14" />
    </button>
  </div>
  <!-- 主体内容 -->
</DetailDrawer>

<!-- 自进化结果面板 -->
<DetailDrawer
  :open="evolutionPanel.visible"
  :title="t('view.agents.evolutionFor', { name: evolutionPanel.agentName })"
  icon="sparkles"
  @close="evolutionPanel.visible = false"
>
  <p v-if="evolutionPanel.result" class="evolution-summary">
    {{ evolutionPanel.result.summary }}
  </p>
  <!-- 建议列表 / 自动应用项 -->
</DetailDrawer>
```

### 3.6 已使用位置

- `views/Agents.vue` — `memoryPanel`（记忆面板）+ `evolutionPanel`（自进化结果面板）

## 4. 数据表格 — DataTable

### 4.1 何时使用

标准表格展示场景使用 `DataTable`，支持列排序、以及 `badge` / `bool-icon` / `num` / `time` 等内置列类型渲染。

### 4.2 何时不用

需要自定义列渲染（头像、操作按钮组、富文本等）时，改用 `ListPageLayout` + 自定义 grid/flex 布局，避免在 DataTable 里塞大量插槽。

### 4.3 API 速查

表：DataTable props 参数说明表

| Prop | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `columns` | Array | `null` | 列定义 `[{ key, label, align?, type?, width?, sortable? }]`；未提供时从首行推导（最多 6 列） |
| `rows` | Array | `[]` | 数据行 |
| `loading` | Boolean | `false` | 加载态，显示行骨架 |
| `rowKey` | String | `'id'` | 行唯一键字段 |
| `emptyText` | String | `'No data'` | 空表文案 |
| `sortable` | Boolean | `false` | 开启列排序 |
| `compact` | Boolean | `false` | 紧凑模式（缩小行内边距） |

### 4.4 列类型

表：DataTable 列 type 渲染说明表

| `type` | 渲染 | 说明 |
|--------|------|------|
| `text`（默认） | 纯文本 | 超长省略号截断 |
| `badge` | Badge 组件 | 自动按值推断 tone（success/fail/warn/info/neutral） |
| `bool-icon` | check / x 图标 | true → `--success`，false → `--fail` |
| `num` | 等宽数字 | `tabular-nums` + `--font-mono` |
| `time` | 相对时间 | `Ns ago` / `Nm ago` / `Nh ago` / `Nd ago` |

### 4.5 已使用位置

- `views/VectorSearch.vue`
- `views/EvolutionHistory.vue`
- `views/RBAC.vue`
- `views/Evolve.vue`

## 5. 过滤器 — FilterBar

### 5.1 何时使用

列表页需要筛选时，**不直接使用 FilterBar**，而是通过 `ListPageLayout` 的 `filterSchema` + `searchKey` 声明，由 ListPageLayout 内部渲染 FilterBar。FilterBar 是纯表现层组件，不请求数据、不持有状态。

### 5.2 对象突变契约

`filters` 对象由 ListPageLayout 内部管理（或父视图通过 `v-model:filters` 持有同一引用）。FilterBar 采用**字段级直接 mutate** 语义：组件内部直接写 `modelValue[key] = val`，而非替换整个对象。因此父组件无需写 `@update:model-value` 处理，响应式状态自动同步。ListPageLayout 已遵守此契约（通过 `:model-value="filters"` 传递同一 reactive 对象）。

### 5.3 schema 格式

表：FilterBar schema 项字段说明表

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `key` | String | 是 | 对应 filters 对象的字段名 |
| `label` | String | 是 | 下拉占位文案 / `aria-label` |
| `options` | Array | 否 | 选项列表 `[{ value, label? }]`，提供时渲染为 `<select>` |

渲染规则：

- `searchKey` 指定的字段 → 渲染为文本搜索框（带 search 图标）。
- schema 中带 `options` 的项 → 渲染为 `<select>` 下拉。
- `resultsLabel` 非空 → 右侧显示结果计数。
- `#extra` slot → 额外自定义控件。

### 5.4 代码示例

代码示例：FilterBar 声明式过滤（Vue）

```vue
<FilterBar
  :model-value="filters"
  :schema="[
    { key: 'level', label: 'Level', options: [
      { value: 'info' }, { value: 'warning' }, { value: 'critical' }
    ] }
  ]"
  search-key="actor"
  search-placeholder="Filter by actor…"
  :results-label="`${n} rows`"
/>
```

## 6. 图表 — chartTokens + chartOptions

### 6.1 何时使用

所有 chart.js 图表（vue-chartjs 封装，如 `<Line>`）必须消费 `chartTokens` 与 `chartOptions`，确保颜色跟随主题、hover 行为全站统一。

### 6.2 chartTokens.js — CSS var → 主题映射

读取 CSS 自定义属性，让图表颜色跟随当前 dark/light 主题，**禁止在图表里硬编码 hex**。每次调用实时读取，主题切换无需 remount。

表：chartTokens 导出函数说明表

| 函数 | 签名 | 说明 |
|------|------|------|
| `cssVar(name, fallback='')` | 返回字符串 | 读取 CSS var，空时回退 fallback |
| `cssVarAlpha(name, alpha=0.12)` | 返回字符串 | 返回带 alpha 的颜色（hex8 / rgba） |

```js
import { cssVar, cssVarAlpha } from '../composables/chartTokens.js';
cssVar('--chart-1')            // 当前主题的 chart-1 颜色
cssVar('--chart-1', '#3574f0') // 样式未加载完时的安全回退
cssVarAlpha('--chart-1', 0.12) // 带 12% 透明度的填充色
```

### 6.3 chartOptions.js — 共享 option 工厂

单仓唯一事实源，所有时序图的 hover 行为统一，不再逐页写死。

表：chartOptions 导出函数说明表

| 函数 | 参数 | 说明 |
|------|------|------|
| `baseLineOptions({ muted, grid, maxTicks=8, legendVisible=true })` | 可选覆盖 | 返回 chart.js line 配置 |

工厂产出的关键约定：

- `interaction.mode = 'index'` + `intersect = false` → 鼠标扫过即显示当前索引对应的所有数据集 tooltip，不要求精确点在线上。
- `pointHoverRadius = 5`（无 hover 时为 0，保持线条干净）。
- tooltip 统一样式：`backgroundColor = var(--surface-2)`、`borderColor = var(--border)`、`titleColor = var(--text)`、`bodyColor = var(--text-muted)`、`cornerRadius = var(--r-md)`。
- legend 顶部、`boxWidth: 12`、字号 11。
- x 轴无网格线、`maxTicksLimit` 可调；y 轴网格用 `--border-subtle`、`beginAtZero: true`。

### 6.4 hover 规范

统一 tooltip 样式：`trigger` 等价于 chart.js 的 `interaction.mode: 'index'` + `intersect: false`，背景色取 `var(--surface-2)`，边框取 `var(--border)`。**禁止逐视图覆盖 tooltip 配置**。双 y 轴等特例可不套 `baseLineOptions` 单轴工厂，但 hover 交互必须遵循 `mode: 'index'` 规范（见 `Evolve.vue` 注释）。

### 6.5 代码示例

代码示例：Overview.vue 的图表 option 组装（Vue）

```js
import { cssVar, cssVarAlpha } from '../composables/chartTokens.js';
import { baseLineOptions } from '../composables/chartOptions.js';

function chartBrand()     { return cssVar('--chart-1', '#3574f0'); }
function chartMuted()     { return cssVar('--text-muted', '#9aa3b2'); }
function chartGridColor() { return cssVar('--border-subtle', 'rgba(163,173,190,.15)'); }
function chartBrandFill() { return cssVarAlpha('--chart-1', .14); }

const chartOptions = computed(() => baseLineOptions({
  muted: chartMuted(),
  grid: chartGridColor(),
}));
```

### 6.6 已使用位置

- `views/Evolve.vue` — `cssVar` / `cssVarAlpha`（双 y 轴特例，hover 遵循 chartOptions 规范）
- `views/Overview.vue` — `cssVar` / `cssVarAlpha` / `baseLineOptions`

## 7. 间距节奏表

基于 `src/styles/tokens.css` 的 CSS var token 系统。**禁止硬编码 px 值，必须使用 CSS var token。**

### 7.1 间距 scale

表：间距 token 与像素对照表

| Token | px | 典型场景 |
|-------|----|----------|
| `--sp-1` | 4 | 标签内隙、图标与文字微距 |
| `--sp-2` | 8 | 卡片内部元素间隔（紧凑） |
| `--sp-3` | 12 | 卡片内部元素间隔（常规） |
| `--sp-4` | 16 | 页头 → 首屏内容；卡片 ↔ 卡片（平级 gap） |
| `--sp-5` | 20 | comfortable 密度下卡片内边距 |
| `--sp-6` | 24 | 逻辑换段（独立段落强分组） |
| `--sp-7` | 32 | 大区块间距 |
| `--sp-8` | 40 | 页面级大分隔 |
| `--sp-9` | 48 | — |
| `--sp-10` | 56 | — |

### 7.2 字号 scale

表：字号 token 与像素对照表

| Token | px | 用途 |
|-------|----|------|
| `--fs-xs` | 11 | 表头、辅助文案 |
| `--fs-sm` | 12 | 次要正文、控件 |
| `--fs-base` | 13 | 正文基准 |
| `--fs-md` | 14 | 强调正文、Drawer 标题 |
| `--fs-lg` | 16 | 卡片标题 |
| `--fs-xl` | 18 | 区块标题 |
| `--fs-2xl` | 22 | 页标题 |

### 7.3 圆角 scale

表：圆角 token 与像素对照表

| Token | px | 用途 |
|-------|----|------|
| `--r-sm` | 4 | 标签、小控件 |
| `--r-md` | 6 | 控件、输入框 |
| `--r-lg` | 8 | 卡片、FilterBar |
| `--r-xl` | 10 | 大卡片 |
| `--r-full` | 999 | 胶囊、头像 |

### 7.4 使用规则

- **禁止硬编码 px 值**，必须使用上述 CSS var token。
- 节奏原则：**内紧外松**。组内距离 < 组间距离，否则视觉上无法分组（接近律）。若某处「几乎挨在一起」，多半是错用了 `--sp-3` 作区块外边距。
- compact 密度（`[data-density="compact"]`）会整体收紧约 20%，所有组件消费同一 token，切换即全站一致生效，无逐视图特例。

## 8. 颜色 token 系统

基于 `src/styles/tokens.css`。**禁止硬编码颜色值，必须使用 CSS var token。** 设计语言参照 JetBrains New UI：中性石墨灰底 × 克制信号蓝 × 1px 描边分隔 × 小圆角 × 无辉光。

### 8.1 语义 token

表：语义颜色 token 说明表

| Token | 说明 |
|-------|------|
| `--brand` | 品牌主色（工程感信号蓝 `#3574f0`） |
| `--brand-strong` | 品牌高亮变体 |
| `--surface` | 卡片底色 |
| `--surface-2` / `--surface-3` / `--surface-4` | 表面层级递进 |
| `--text` | 主文字 |
| `--text-muted` / `--text-faint` | 次要 / 更弱文字 |
| `--border` | 标准描边 |
| `--border-strong` / `--border-subtle` / `--border-faint` | 描边强度变体 |
| `--success` | 成功 |
| `--fail` | 失败 |
| `--warn` | 警告 |
| `--info` | 信息 |

### 8.2 状态 token（badge / 背景成对）

表：状态软色 token 说明表

| Token | 说明 |
|-------|------|
| `--brand-soft` | 品牌软背景（badge / focus ring） |
| `--success-soft` / `--success-bg` / `--success-strong` | 成功软背景 / 状态背景 / 高亮文字 |
| `--fail-soft` / `--fail-bg` / `--fail-strong` | 失败同族 |
| `--warn-soft` / `--warn-bg` / `--warn-strong` | 警告同族 |
| `--info-soft` / `--info-bg` / `--info-strong` | 信息同族 |
| `--neutral-bg` / `--neutral-strong` | 中性状态 |

### 8.3 图表色板

`--chart-1` ~ `--chart-10`：克制的高斯冷色谱，与品牌蓝同族。另有 `--chart-warn` / `--chart-fail`。图表必须通过 `chartTokens.cssVar()` 读取，禁止硬编码。

### 8.4 使用规则

- **禁止硬编码颜色值**，必须使用 CSS var token。
- 辉光策略：`--card-sheen` / `--topbar-glow` 等全部置 `none`/透明，分隔靠 1px 实线描边，不靠光晕抬升层级。
- 暗态为默认（`:root`），亮态在 `themes.css` 的 `[data-theme="light"]` 下覆盖。所有组件消费语义 token 即自动支持主题切换。

## 9. 弹窗交互约定

迭代 C 确立的弹窗分流：查看详情走 Drawer，表单走全屏 Modal，确认走小 Modal，全局命令走 CommandPalette，首次引导走 CoachMarks。

表：弹窗场景与组件对照表

| 场景 | 组件 | 示例位置 |
|------|------|----------|
| 查看详情 | `DetailDrawer`（右侧滑出 480px） | `Agents.vue` memoryPanel / evolutionPanel |
| 新建 / 编辑表单 | 全屏 Modal（`.modal-overlay` + `.modal`） | `Users.vue` 注册/编辑、`Tenants.vue` 创建 |
| 破坏性确认 | 小型居中 Modal | `Agents.vue` removeConfirm |
| 全局命令 | `CommandPalette` | `App.vue` Cmd+K |
| 首次引导 | `CoachMarks` | `App.vue` 4 步引导 |

约定：

- DetailDrawer 自带焦点 trap / Esc / 遮罩点击 / 焦点还原，**不要在视图里重复实现**。
- 全屏 Modal 需自行用 `v-modal-a11y` 指令处理 Esc 与遮罩点击（见 `Tenants.vue` 创建弹窗）。
- 破坏性确认目前用原生 `confirm()` 或小型 Modal，统一向小型居中 Modal 收敛。

## 10. 导航分组

### 10.1 6 旅程分组

导航单一事实源定义在 `src/nav.js`，sidebar（`App.vue`）与页头（`PageHeader.vue`）均从此读取，保证侧栏图标/标签与页标题/图标/副标题始终同步。

表：导航旅程分组说明表

| 分组 | i18n key | 包含路由 |
|------|----------|----------|
| 工作台 | `nav.workbench` | `/`（Overview）、`/monitor` |
| 构建 | `nav.build` | `/run`、`/agents`、`/evolve` |
| 资产 | `nav.assets` | `/memory`、`/knowledge-graph`、`/search`、`/vector`、`/tools` |
| 观测 | `nav.observe` | `/observability`、`/logs`、`/cost` |
| 治理 | `nav.govern` | `/models`、`/audit`、`/rbac`、`/tenants`、`/users` |
| 系统 | `nav.system` | `/settings`、`/docs` |

### 10.2 路由合并与 301 重定向

迭代 A 合并了 Control + Chat → `/run`（双 Tab），Evolve + EvolutionHistory → `/evolve`（双 Tab）。旧深链在 `src/router/index.js` 做 301 重定向，保留书签与外部链接可用。

表：旧路由重定向对照表

| 旧路由 | 重定向目标 |
|--------|------------|
| `/control` | `/run?tab=structured` |
| `/chat` | `/run?tab=chat` |
| `/evolution-history` | `/evolve?tab=history` |

### 10.3 版本过滤

`nav.js` 中 `enterprise: true` 的项（audit / rbac / tenants / users）在个人版通过 `filterNavByEdition()` 渲染层过滤隐藏——所见即所得，不会出现「点了却被弹走」的体验。`nav.js` 保持单一事实源不变，过滤在 `App.vue` 模板层做。