# ADR-003: Dashboard 假数据兜底清除

## Status
Accepted

## Date
2026-07-10

## Decider
MAOP Core Team

## Context
Dashboard 前端 `index.html` 中 6 个渲染函数在 API 返回空时，用**内联假数据**填充面板：
- `renderSC`：显示 "317次 / 78% / 234ms / 1,247条"
- `renderAvail`：显示 "15个可用 / 83%"
- `renderTS`：用 `Math.random()` 生成 7 天假时序数据
- `renderLT`：显示硬编码的 2026-07-08 任务历史
- `renderFT`：显示假失败榜 `[qoder:8, hermes:6…]`
- `renderEvolve`：字段为 0 时注入 "317 / 78%"

这导致——当后端服务挂了或未运行时——Dashboard 看起来一切正常。对监控工具体系，这是最严重的信任缺陷。

此外，存在一个 11 行的 `const MOCK_*` 常量块（AGENTS/MCP/SKILLS…），经确认零引用，属于死代码。

## Decision
1. 删除全部 6 处 active 假数据注入，改为显示空态或无数据提示（"无数据 — 后端未返回XX"）
2. 新增 `toggleBackendBanner()`：在 `load()`/`refresh()` 中检测 12 个数据源是否全部为空，若全部为空则显示顶部红色横幅"未获取到后端数据"
3. 删除 11 行未引用 `const MOCK_*` 死代码
4. 删除无用 CSS 规则（canvas 相关）

## Consequences
- **变得容易**：后端挂了能立刻发现 → 运维可信度从 4 拉到 8
- **变得容易**：不会再被假数据误导做决策
- **风险**：首次加载无数据时面板显示空白，可能让新用户以为"没装上"——横幅会明确提示，缓解此问题
