# MAOP Dashboard Design Rules

> **权威设计规范** — 所有 UI 改动必须遵循此文档。最后更新: 2026-07-16

---

## 1. 色彩体系

### 1.1 基础色 (CSS变量)
| 变量 | 值 | 用途 |
|------|-----|------|
| `--bg` | `#0a0e1a` | 页面背景 |
| `--bg2` | `#121826` | 外框(panel)背景 |
| `--bg3` | `#1a2236` | 中框(card)/内框(stat-box)背景 |
| `--bg4` | `#0f1525` | 悬停/hover 背景 |
| `--bg5` | `#1e293b` | 次级悬停 |
| `--border` | `#2a3a5c` | 默认边框 |
| `--border-hi` | `#4a6da8` | 悬停高亮边框 |
| `--border-in` | `rgba(42,58,92,.5)` | 内框边框(半透明) |
| `--text` | `#e2e8f0` | 主文字 |
| `--text2` | `#94a3b8` | 次文字 |
| `--text3` | `#64748b` | 弱文字 |

### 1.2 18色 Section 配色 (每个导航项/section 独立色)
| Section | 色值 | CSS变量 |
|---------|------|---------|
| overview | `#3b82f6` 蓝 | `--sc` |
| control | `#f97316` 橙 | |
| upgrade | `#22c55e` 绿 | |
| memory | `#2dd4bf` 青 | |
| evolve | `#fbbf24` 琥珀 | |
| search | `#06b6d4` 青2 | |
| monitor | `#a78bfa` 紫 | |
| models | `#ec4899` 粉 | |
| performance | `#818cf8` 靛 | |
| logs | `#f87171` 红 | |
| skills | `#8b5cf6` 深紫 | |
| mcp | `#38bdf8` 天蓝 | |
| prompts | `#fb7185` 玫红 | |
| pillars | `#facc15` 金 | |
| roles | `#34d399` 翠 | |
| modules | `#84cc16` 黄绿 | |
| workflow | `#14b8a6` 蓝绿 | |
| wfexec | `#0ea5e9` 亮蓝 | |
| architecture | `#fb923c` 橙2 | |

### 1.3 概览12指标色 (stat-box.c1~c12)
`#3b82f6, #a78bfa, #22c55e, #06b6d4, #f97316, #fbbf24, #ec4899, #2dd4bf, #818cf8, #f87171, #84cc16, #fb7185`

### 1.4 概览4卡片色 (card.cb1~cb4)
`#e879f9, #38bdf8, #fde047, #c084fc` (与12指标色不重复)

---

## 2. 边框三级体系

| 层级 | 元素 | 粗细 | 颜色 | 悬停高亮 |
|------|------|------|------|----------|
| **外框** | `.panel` | 4px | `--border` | `--border-hi` + glow shadow |
| **中框** | `.card` | 3px | `--border` | `--border-hi` + glow + translateY(-1px) |
| **内框** | `.stat-box` / `.pillar-item` 等 | 2px | `--border-in` | `--border-hi` + glow + translateY(-3px) |

### 规则
- 所有框线均需 **鼠标悬停高亮** (border-color 变化 + box-shadow 发光)
- 悬停时使用 `color-mix(in srgb, var(--sc) N%, transparent)` 生成与 section 配色一致的发光
- 概览12指标框顶部加 5px 彩色条 (`border-top: 5px solid #color`)
- 概览4卡片顶部加 5px 彩色条 (与12指标不同色)

---

## 3. 分割线体系

### 3.1 粗细度规则 (与边框三级对应)
| 层级 | 粗细 | 透明度 | 示例 |
|------|------|--------|------|
| **外框标题** | 3px | 0.7 | `.panel-title::after` |
| **中框标题** | 2px | 0.6 | `.card h3::after` |
| **内框标题** | 1px | 0.3 | `.pillar-name::after`, `.wf-phase-title::after` |
| **指标内分割** | 2px | 0.3 | `.stat-divider` (指标名与值之间) |
| **表格列名下** | 1px | 0.6 | `th` border-bottom |

### 3.2 左右边距规则 (按每行框数)
| 框数/行 | 左右边距 | CSS |
|---------|---------|-----|
| 1框占全行 | 6px | `width: calc(100% - 12px); margin-left: 6px` |
| 2框/行 (card-row) | 4px | `width: calc(100% - 8px); margin-left: 4px` |
| 3框/行 | 4px | 同上 |
| 4+框/行 | 2px | `width: calc(100% - 4px); margin-left: 2px` |

### 3.3 统一规则
- 分割线颜色 = `var(--sc, var(--accent))` (跟随 section 配色)
- 分割线圆角 = `1px`
- 分割线为独立元素或 `::after` 伪元素
- **不含 info-table 的 card**: 中框分割线 2px / 0.6 透明度
- **含 info-table 的 card**: 中框分割线 2px / 0.35 透明度 (弱化)

---

## 4. 布局规则

### 4.1 导航分组 (5组×18项)
| 组 | 项 |
|----|-----|
| **展示** | 概览 |
| **操作** | 控制面板, Agent升级, 记忆系统, 自进化, 搜索, 工作流 |
| **监控** | Agent监控, 大模型, 性能指标, 日志 |
| **工具** | Skills, MCP, 提示词 |
| **说明** | 四大工程, 角色, 模块, 架构, 工作流程 |

### 4.2 概览指标
- 12个指标框: 6列×2行网格 (`grid-template-columns: repeat(6, 1fr)`)
- 每框: 指标名(上, 白色) → 窄分割线 → 指标值(下, 大字体22px)
- 4个信息卡片: 2×2 布局 (`card-row`)

### 4.3 子系统健康
- 4个工程组上下排列 (单列 `1fr`)
- 每组内: 4列×2行 = 8项

### 4.4 引擎状态
- 8个指标一行排列 (`repeat(8, 1fr)`)

### 4.5 滚动
- 左侧导航: `position: sticky; top: 0; height: 100vh; overflow-y: auto`
- 右侧主区: `height: 100vh; overflow-y: auto`
- 页面整体: `overflow: hidden` (仅主区滚动)
- topbar: `position: sticky; top: 0` (固定顶部)

---

## 5. 折叠/展开机制

### 5.1 半隐藏框 (工作流程/角色)
- 折叠时: 仅显示标题行 + 窄分割线隐藏
- 点击展开: 显示分割线 + 下方内容
- 同行其他框不受影响

### 5.2 四大工程折叠卡片
- `togglePillarItem()`: 点击内框标题切换展开/折叠
- 展开时显示详细说明文字

---

## 6. 按钮体系

### 6.1 尺寸
| 类 | padding | 字号 | 用途 |
|----|---------|------|------|
| `.btn-lg` | 12px 24px | 14px | 主操作 |
| `.btn-md` | 10px 20px | 13px | 常规 |
| `.btn-sm` | 8px 16px | 12px | 辅助 |

### 6.2 颜色 (渐变背景)
| 类 | 渐变 |
|----|------|
| `.btn-blue` | `#3b82f6 → #2563eb` |
| `.btn-green` | `#22c55e → #16a34a` |
| `.btn-orange` | `#f97316 → #ea580c` |
| `.btn-red` | `#ef4444 → #dc2626` |
| `.btn-purple` | `#a78bfa → #8b5cf6` |

### 6.3 悬停效果
- 上浮: `transform: translateY(-2px)`
- 发光: `box-shadow: 0 4px 12px rgba(color, .4)`

---

## 7. 数据展示规则

### 7.1 禁止直接显示原始 JSON
- 记忆系统/神经机制/深度记忆等区域: 将原始 JSON 转为结构化展示
- 使用: 指标卡片、状态徽章、结构化列表、表格

### 7.2 空状态处理
- 数据为空时显示占位文字 (`.empty` class)
- 不显示 `null`/`undefined`/`{}`

### 7.3 表格
- 列名: 12px, `--text2` 色, `font-weight: 600`
- 单元格: 13px, `--text` 色
- 行悬停: 背景 `--bg4`
- 列名下分割线: 1px `--border`

---

## 8. 字体规则
- 系统字体: `'Segoe UI', Inter, -apple-system, sans-serif`
- 基础字号: 14px (`html { font-size: 14px }`)
- 指标值: 22px / 800
- 标题: 17px / 700
- 卡片标题: 14px / 600
- 正文: 13px
- 次文字: 12px
- 弱文字/标签: 11px

---

## 9. 动画/过渡
- 所有交互元素: `transition: all .2s` 或 `.25s`
- 脉冲指示器: `@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }` 2s 循环
- 悬停发光: `box-shadow` + `filter: drop-shadow()`
- 导航图标悬停: `filter: drop-shadow(0 0 4px var(--sc))`

---

## 10. 禁止事项
- ❌ 禁止使用 `alert()` / `confirm()` — 用页面内联状态提示 (`showCtrlMsg()`)
- ❌ 禁止直接显示原始 JSON — 转为结构化展示
- ❌ 禁止硬编码魔法数字 — 从后端 API 获取真实数据
- ❌ 禁止上构建框架 (Vue/Lit/React) — 拆多文件 + `<script>` 顺序加载
- ❌ 禁止在 dashboard/ 放调试截图 — 截图是临时文件，用完即删
