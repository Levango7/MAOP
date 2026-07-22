# MAOP Dashboard — Design System v4.1

## 一、卡片三层嵌套（背景+边框联动）

```
L1 外层包装 → bg2(深)  + bw-thick(3px)
  └─ L2 中层容器 → bg3(中)  + bw-mid(2px)
       └─ L3 内层卡片 → bg4(浅)  + bw-thin(1px)
```

| 层级 | CSS类 | 背景 | 边框 | 示例 |
|------|-------|------|------|------|
| L1 | `.s` `.section-card` | `--bg2` | `--bw-thick` 3px | 13大角色卡片、工作流大卡片 |
| L2 | `.arch-layer` `.wf-phase` `.role-phase` | `--bg3` | `--bw-mid` 2px | 架构5层、工作流3阶段、角色流程映射 |
| L3 | `.ac` `.wf-card` `.ep-card` `.fc` | `--bg4` | `--bw-thin` 1px | 角色卡、流程卡、错误卡、FAULT卡 |

**hover规则**：L3细边框 → `var(--accent)` 蓝色高亮

## 二、背景色四阶

| 阶 | 变量 | 色值 | 适用 |
|----|------|------|------|
| 最深 | `--bg` | `#0a0e1a` | 页面body、左侧导航栏 |
| 深 | `--bg2` | `#0f1729` | L1外层大卡片 |
| 中 | `--bg3` | `#16203a` | L2中层容器 |
| 浅 | `--bg4` | `#1a2540` | L3内层卡片、hover态、tooltip |

## 三、边框三级

| 级别 | 粗细 | 变量 | 适用 |
|------|------|------|------|
| 粗 | 3px | `--bw-thick` | L1大外框 |
| 中 | 2px | `--bw-mid` | L2层容器 |
| 细 | 1px | `--bw-thin` | L3内卡片 |

## 四、图标六系

| 系 | CSS类 | 尺寸 | 样式 | 适用 |
|----|-------|------|------|------|
| ① nav | `.nav-icon` | 24px | 角色专属色+白字SVG | 左侧导航、章节标题前缀 |
| ② stat | `.stat-icon` | 28px | 彩色半透明bg+白字SVG | 概览9格数据卡片 |
| ③ action | `.cbtn-icon` | 20px | 青(#06b6d4)半透明bg+SVG | 控制面板/日志按钮 |
| ④ role | `.role-ico` | 36/28px | 角色色+SVG | 13角色卡、工作流角色 |
| ⑤ status | `.d` | 6px圆点 | 绿=on 灰=off | 状态指示灯 |
| ⑥ section | `.sec-icon` | 24px | 面板专属色+SVG | 功能面板标题(记忆紫/工具红/日志粉/Agent橙) |

## 五、分割线四场景

| 场景 | CSS | 说明 |
|------|-----|------|
| 区块间 | `.section-divider` | 渐变透明线 `margin:16px 0 24px` 大章节间 |
| 卡片标题下 | `.card-title+.sep` | `margin:6px 12px 12px` 左右各12px |
| 面板标题下 | `inline sep` | `margin:4px 6px 8px` 左右各6px |
| 层标签下 | `.layer-label` | `border-bottom:1px` 全长 |

## 六、命名规则（6级细化）

| 级别 | 格式 | 字号/色 | 示例 |
|------|------|---------|------|
| 1章标题 | `🔷 Emoji 中文 English Name` | 20px bold/--text | `🤖 架构 Architecture Layers` |
| 2卡标题 | `🔷 Emoji 中文 — 说明·关键词` | 13px bold/--text | `🧩 全阶段支撑 — 5角色贯穿全层` |
| 3子标题 | `🔷 Emoji 操作名` | 12px 600/--text2 | `⚡ 执行操作` |
| 4角色名 | `中文角色名` | 12-13px bold/--text | `Router` |
| 5脚本名 | `脚本.ps1` | 10px/--text3 跟在角色名后 | `delegate.ps1` |
| 6说明字 | `职能说明 / 描述文字` | 10-11px/--text3 | `路由分配器` |

## 七、变量速查

```css
--bw-thick:3px; --bw-mid:2px; --bw-thin:1px;
--bg:#0a0e1a; --bg2:#0f1729; --bg3:#16203a; --bg4:#1a2540;
--text:#e2e8f0; --text2:#8899bb; --text3:#5a6a8a;
--accent:#3b82f6; --success:#22c55e; --warn:#f59e0b; --fail:#ef4444;
```
