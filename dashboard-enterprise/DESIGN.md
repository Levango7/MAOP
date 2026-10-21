---
version: alpha
name: maop-dashboard-design-analysis
description: "暗色优先的工程控制台：石墨灰叠层 #1b1d21→#31343c，单一青绿品牌色 #0c857a，靠 1px 描边而非色块分层；36px 行高、无衬线 Inter + 等宽 JetBrains Mono。"
colors:
  bg: "#1b1d21"
  bg2: "#24262b"
  surface: "#22242a"
  surface-2: "#292b32"
  surface-3: "#31343c"
  text: "#e8eaf0"
  text-muted: "#9aa3b2"
  text-faint: "#99a1b0"
  brand: "#0c857a"
  brand-solid: "#0c857a"
  brand-solid-hover: "#0a6d64"
  fail-solid: "#d0443d"
  warn-solid: "#976e18"
  success-soft: "rgba(63, 185, 80, 0.15)"
  brand-text: "#14b8a6"
  brand-strong: "#14b8a6"
  success: "#3fb950"
  warn: "#d29922"
  fail: "#fa7d76"
  danger: "#f57d70"
  info: "#4cc2ff"
  border: "#3c4048"
  border-control: "#727a8a"
  border-strong: "#4e545f"
colors-light:
  bg: "#f2f3f5"
  bg2: "#e9ebee"
  surface: "#ffffff"
  surface-2: "#f6f7f9"
  surface-3: "#eef0f4"
  text: "#1d2129"
  text-muted: "#59616e"
  text-faint: "#626a78"
  brand: "#0c857a"
  brand-solid: "#0c857a"
  brand-solid-hover: "#0a6d64"
  brand-text: "#0a746a"
  brand-strong: "#0f766e"
  success: "#19722f"
  warn: "#8a6100"
  fail: "#c8232d"
  danger: "#c8232d"
  info: "#097295"
  border: "#d4d8e0"
  border-control: "#82899a"
  border-strong: "#b9bfcc"
typography:
  page-title:
    fontFamily: Inter
    fontSize: 22px
    fontWeight: 700
    lineHeight: 1.25
  card-title:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: 600
    letterSpacing: 0.02em
  mono:
    fontFamily: "JetBrains Mono"
    fontSize: 12px
    fontWeight: 500
components:
  button-primary:
    backgroundColor: "{colors.brand-solid}"
    textColor: "#ffffff"
    hoverBackgroundColor: "{colors.brand-solid-hover}"
    borderRadius: 6px
    height: 36px
  button-secondary:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text-muted}"
    borderColor: "{colors.border-control}"
    borderRadius: 6px
    height: 36px
  button-danger:
    backgroundColor: "{colors.fail-solid}"
    textColor: "#ffffff"
    borderRadius: 6px
  button-danger-ghost:
    backgroundColor: "{colors.fail-soft}"
    textColor: "{colors.fail}"
    borderColor: "{colors.fail}"
  card:
    backgroundColor: "{colors.surface}"
    borderColor: "{colors.border}"
    borderRadius: 8px
    padding: 20px
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    borderColor: "{colors.border-control}"
    focusRingColor: "{colors.brand}"
    height: 36px
  badge-neutral:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text-muted}"
  badge-success:
    backgroundColor: "{colors.success-soft}"
    textColor: "{colors.success-strong}"
---

# MAOP Dashboard Design Contract

> 权威令牌来源：`src/styles/tokens.css`（暗色 `:root`）与 `src/styles/themes.css`
> （`[data-theme="light"]` 覆盖）。本文件解释**每个令牌的角色与禁用场景**；
> 数值冲突时以 tokens.css 为准，并立即修正本文件。
>
> 修订记录：
> - 2026-10-20 建立契约；同日修复暗/亮两套文字令牌对比度、`--brand` 实底、
>   未定义令牌漂移、Run 页 11 色 pastel 按钮，并把 WCAG 门禁接进 CI。

## 1. Overview

关键词：**克制、工程感、可复盘、平**。

这是一个多智能体编排控制台，不是营销页。层级靠 1px 描边与 3-4% 的亮度递进，
不靠重阴影、渐变或大色块。颜色只有一个含义明确的品牌青绿；红/黄/绿只表示状态，
绝不表示"这个按钮属于另一类功能"。任何一屏里，用户应当先看到内容和主操作，
再看到中性工具栏，最后才是维护类动作。

## 2. Colors

| Token | 暗色 | 亮色 | 角色 | 禁用 |
|---|---|---|---|---|
| `--bg` | `#1b1d21` | `#f2f3f5` | 页面底色 | 卡片底 |
| `--bg2` | `#24262b` | `#e9ebee` | 侧栏 / 沉底输入框 | 承载正文大段文字 |
| `--surface` | `#22242a` | `#ffffff` | 卡片底 | 页面背景 |
| `--surface-2` | `#292b32` | `#f6f7f9` | 二级按钮、徽标中性底、hover 行 | 大面积背景 |
| `--surface-3` | `#31343c` | `#eef0f4` | 再上一层 hover / 选中 | 卡片底 |
| `--text` | `#e8eaf0` | `#1d2129` | 正文与标题 | 装饰 |
| `--text-muted` | `#9aa3b2` | `#59616e` | 次级正文、控件默认文字 | 标题 |
| `--text-faint` | `#99a1b0` | `#626a78` | 标签、时间戳、页脚 | 正文段落 |
| `--brand` | `#0c857a` | `#0c857a` | **承载白字的品牌实底/边框** | 作为文字色 |
| `--brand-solid` | 同 `--brand` | 同 `--brand` | 主按钮实底（语义别名） | 大面积装饰 |
| `--brand-solid-hover` | `#0a6d64` | `#0a6d64` | 主按钮 hover（加深，不是提亮） | 默认态 |
| `--brand-text` | `#14b8a6` | `#0a746a` | **品牌色作文字**（链接、激活文字） | 实底填充 |
| `--brand-strong` | `#14b8a6` | `#0f766e` | 亮/暗点缀、图标、边框 | **垫白字**（白字仅 2.49:1） |
| `--success` | `#3fb950` | `#19722f` | 成功文字/图标 | 实底垫白字 |
| `--warn` | `#d29922` | `#8a6100` | 警告文字/图标 | 实底垫白字 |
| `--fail` | `#fa7d76` | `#c8232d` | 失败文字/图标 | 实底垫白字 |
| `--danger` | `#f57d70` | `#c8232d` | 错误文本与边框 | 实底垫白字 |
| `--info` | `#4cc2ff` | `#097295` | 信息文字/图标 | 实底垫白字 |
| `--border` | `#3c4048` | `#d4d8e0` | 容器描边（装饰） | 可交互控件边界 |
| `--border-control` | `#727a8a` | `#82899a` | **可交互边界**（SC 1.4.11 ≥3:1） | 大段分隔线 |
| `--border-strong` | `#4e545f` | `#b9bfcc` | hover 边框、装饰强调 | 唯一控件边界 |

规则：

1. **一个 accent**。品牌青绿只出现在主操作、激活态、焦点环与品牌标识上。
2. **语义色只做状态**。`success/warn/fail/info` 不用于功能分类；红只表示危险/失败。
3. **实底与文字拆开**。实底垫白字用 `--brand-solid`（及 `--fail-solid`/`--warn-solid`）；
   品牌色作文字用 `--brand-text`。一个值不可能同时满足两个方向（见 §8）。
4. **柔底徽标文字用 `*-strong`**。`--warn` 直接压在 `--warn-soft` 上会被 alpha 稀释到
   4.2-4.3:1；徽标文字统一用 `success-strong/warn-strong/fail-strong/info-strong`。
5. **可交互边界用 `--border-control`**（两者在 bg/surface/surface-2 上均 ≥3:1）；
   `--border` 只做容器分隔，`--border-strong` 只做 hover/装饰。

## 3. Typography

字体栈：`--font-sans: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, ...`；
等宽：`--font-mono: 'JetBrains Mono', 'SF Mono', ui-monospace, 'Cascadia Code', ...`。
两个字体均未随包分发，系统未安装时回落到系统字体（见 Known Gaps）。

| 层级 | Token | 字号 | 字重 | 行高 | 用途 |
|---|---|---|---|---|---|
| 页标题 | `--fs-2xl` | 22px | 700 | 1.25 | 每页唯一 |
| 卡片标题 | `--fs-md` | 14px | 600 | 1.4 | Card 标题 |
| 正文 | `--fs-base` | 13px | 400 | 1.55 | 默认 |
| 紧凑正文 | `--fs-sm` | 12px | 400/600 | 1.5 | 表格、按钮 |
| 标签 | `--fs-xs` | 11px | 600 | 1.4 | 徽标、辅助标签 |
| 等宽 | `--fs-sm` | 12px | 500 | 1.5 | ID、时间、代码 |

数字列使用 `font-variant-numeric: tabular-nums`。不要用字号小于 11px 的正文；
`--fs-2xs`(10px)/`--fs-3xs`(9px) 仅限图标旁注与上标。

## 4. Layout

- 侧栏固定 `--sidebar-w: 232px`，折叠为 rail；顶栏 `--topbar-h: 56px`。
- 内容行高基准 `--row-h: 36px`（JetBrains 密度），小按钮 28-32px。
- 间距刻度 `--sp-1..10 = 4/8/12/16/20/24/32/40/48/56`。
- 节奏：页头→内容 `--sp-4`；卡片↔卡片 `--sp-4`；卡片内元素 `--sp-2/3`；
  逻辑换段 `--sp-6`。**内紧外松**——组内距离必须小于组间距离。
- 页面内容最大宽度由容器控制，不横向铺满 4K；表格可横向滚动而不挤压列。

## 5. Elevation & Depth

三级平面：`bg → surface → surface-2/surface-3`，每级亮度差约 3-4%，配 1px `--border`。
阴影只用于真正浮起的元素：

- `--shadow-sm/md`：卡片 hover、下拉。
- `--shadow-pop`：气泡、popover。
- `--shadow-modal`：模态。
- `--shadow-brand`：仅主按钮（青绿光晕），每屏最多一个。

禁止用大阴影替代层级；禁止在平面内再叠渐变卡片。

## 6. Shapes

圆角上限 8px：卡片 `--r-lg`(8px)，控件 `--r-md`(6px)，标签/小徽标 `--r-sm`(4px)，
胶囊用 `--r-full`。不允许出现 12px 以上的卡片圆角或"全圆角按钮"。

## 7. Components

### 按钮

| 类型 | 样式 | 用途 |
|---|---|---|
| Primary | `--brand-solid` 底 + 白字；hover `--brand-solid-hover` | 每屏一个主操作（如"运行任务"） |
| Secondary | `--surface-2` 底 + `--text-muted` 字 + `--border-control` 边 | 其余所有同级操作 |
| Danger solid | `--fail-solid` 底 + 白字 | 确认弹窗中的破坏性确认 |
| Danger ghost | `--fail-soft` 底 + `--fail` 字 + `--fail` 边 | 行内"停止/删除" |
| Ghost | 透明底 + `--border` | 取消、关闭 |

状态：hover 提升一级背景或加深实底；`disabled` 统一 `opacity: .45` + `not-allowed`；
键盘焦点 `outline: 2px solid var(--brand); outline-offset: 2px`。

**层级规则**：同级动作只用中性按钮。给 5 个按钮染 5 种颜色等于没有层级。
维护类动作必须排在内容之后，且不得使用主按钮样式。

### 卡片

`--surface` 底、1px `--border`、`--r-lg`、内边距 `--sp-5`(20px)。标题 14/600，可带
16px 图标。卡片之间间距 `--sp-4`，不嵌套超过两层。

### 输入框

`--surface` 底（沉底输入用 `--bg`）、`--border-control` 边、`--r-md`、高 `--row-h`。
聚焦：2px `--brand` 外环 + 1px offset。错误态 `--danger` 边；错误文字用 `--fail`。

### 徽标

`--r-full`，11px/600，柔底 + `*-strong` 文字 + 同色 30% 边框。
中性徽标用 `--surface-2` + `--text-muted`。状态三态：success / warn / fail；
"未结束"必须是中性色，不许猜成成功或失败。

### 导航

默认 `--text-muted`；hover `--text` + `--surface-2`。激活项：`--text`（保证可读）+
`--brand-soft` 底 + 左侧 3px `--brand` 色条 + 品牌色图标。品牌色只做非文本指示。

## 8. Do's and Don'ts

✅ 一屏一个主按钮；其余中性。
✅ 实底白字用 `--brand-solid`/`--fail-solid`/`--warn-solid`。
✅ 品牌色文字用 `--brand-text`；状态徽标文字用 `*-strong`。
✅ 可交互控件用 `--border-control` 描边。
✅ 新令牌先加进 tokens.css + themes.css，再在 `design/contrast-pairs.txt` 登记配对。

❌ 用 `--brand` 或 `--brand-strong` 当文字色（暗色下仅 4.14/4.58:1）。
❌ 用 `--brand-strong` 垫白字（2.49:1）或在 hover 时把实底提亮。
❌ 给同级按钮各配一种颜色（2026-10-20 已删除 11 色 `tone-*` pastel 调色板）。
❌ 用 `color: var(--x)` 引用未定义令牌（`--text-primary`/`--surface-sunken` 等曾静默失效）。
❌ 用 alpha 稀释做浅底后再用同色系基础色写字（徽标必须用 `*-strong`）。
❌ 卡片套卡片再叠渐变/重阴影。

## 9. Responsive Behavior

- ≤1100px：顶栏隐藏刷新文字与品牌描述，保留图标；内容内边距降到 `--sp-4`。
- ≤900px：侧栏变为抽屉/rail；页脚居中；表格允许横向滚动。
- 触摸目标最小 36px（`--row-h`）；小按钮仅用于桌面密集行，移动端不得低于 32px。
- 图表容器使用固定纵横比，不因视口变窄把标签压成竖排。

## 10. Iteration Guide

1. 改任何颜色/字号 → 先改 tokens.css / themes.css，再改组件。
2. 跑 `npm run design:contrast`（自测 + 164 对 WCAG 检查；低于 164 对会判红）。
3. 跑 `npm run lint`、`npx vue-tsc --noEmit`、相关 `npx vitest run <file> --pool=threads`。
4. 需要视觉验证时：`npx vite build && npx vite preview --port 5181`，用真实浏览器看
   暗/亮两套主题；仅看代码或令牌表不足以证明"界面对"。
5. 一次只改一个组件族；把新规则写回本文件（含修订记录），不要让文档落后于代码。

## 11. Known Gaps

- **字体未打包**：Inter / JetBrains Mono 仅在本机安装时生效；CI 与多数用户看到的是系统字体。
- **图表调色板不受文本门禁覆盖**：`--chart-1..10` 按数据可视化设计，未逐对验证；
  它们只应作描边/填充，图例文字用文本令牌。2026-10-21 已清除 Agents/Users 中把
  `--chart-*` 当文字色的徽标（实测暗色最低 3.99:1、亮色 2.95:1）。
- **页面级实测覆盖 18 个主要路由（暗/亮各一遍）**；其余路由与 hover/active 态未逐页做
  浏览器对比度审计。
- **hover/active 态未穷举**：门禁只验证默认态令牌配对；hover 组合靠组件规则约束。
- **柔底复合的自动门禁只覆盖 5 组徽标**；Agents 的记忆类型/优先级/决策阶段徽标与
  Users 角色徽标已在 2026-10-21 统一为 `*-strong`（按令牌复合计算：暗 4.56–7.22、
  亮 4.52–6.28）；tooltip、diff、热力图等其余柔底组合尚未逐条进配对清单。
- **遗留令牌**：`--text2/--text3/--accent/--accent2/--surface-4` 目前零引用或仅兼容保留，
  新代码禁用；`--surface-4` 尚未纳入配对清单。
- **`--border-strong` 不满足 3:1**（暗 2.22 / 亮 1.6）：按本契约为装饰用途；
  可交互边界必须用 `--border-control`。
