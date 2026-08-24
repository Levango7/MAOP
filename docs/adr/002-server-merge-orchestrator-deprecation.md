# ADR-002: Server 合并 & Orchestrator 废弃

## Status
Accepted

## Date
2026-07-10

## Decider
MAOP Core Team

## Context
项目存在两套并行实现：

**执行引擎：**
- `src/orchestrator.ps1`（84行）：朴素 Plan→Execute（单agent+2次重试）→Verify（Eval agent 说 PASS/FAIL）
- `src/maop-loop.ps1`（485行）：Plan（maop-plan 路由表）→Execute（fallback 链+迭代重试）→Verify（多 gate 脚本+反馈循环）→Memory→Evolve

`orchestrator.ps1` 是 `maop-loop.ps1` 的退化子集，缺 fallback 链、反馈循环、记忆注入、自演化分析。

**Dashboard Server：**
- `dashboard/server.ps1`（203行）：24 API 路由 + 11 控制面板操作，有图谱 nodes/edges/neighbors 和向量 search 但没有批量/安全/任务追踪
- `dashboard/server-v2.ps1`（508行）：批量端点/SSE/ActiveJobs 追踪/Mutex 缓存/路径穿越防护，但缺图谱浏览和向量检索

两个 server 功能互补但互不替代。README 指向 v1，但 `delegate-plugin.ps1` 拉起 v2。执行器也存在分歧：v1 的 run 调 `orchestrator.ps1`，v2 的 run 调 `maop-loop.ps1`。

## Decision
1. **Server**：将 server.ps1 的独有路由（graph nodes/edges/neighbors, vector list/search, mcp/servers/tools）迁入 server-v2.ps1。server-v2 为 canonical。server.ps1 顶部加 DEPRECATED 标记，保留作回退参考。
2. **Orchestrator**：orchestrator.ps1 顶部加 DEPRECATED 注释块。maop-loop.ps1 为 canonical 执行引擎。
3. **入口脚本**：`maop.ps1` 的 standard mode 改为调用 `maop-loop.ps1`；`server.ps1` 的 `/api/control/run` 改为调 `maop-loop.ps1` 并读 task body。
4. **README**：更新文档指向 canonical 文件，加废弃说明。

## Consequences
- **变得容易**：单一执行引擎 → 控制流不再漂移，新增功能只改一处
- **变得容易**：单一 Dashboard Server → 路由改/加只在一处
- **变得容易**：新开发者不会困惑"该用哪个"
- **风险**：server.ps1 标注废弃但未删除——如果团队错误继续用它，功能会退化。可通过监控 server-v2 启动日志验证
