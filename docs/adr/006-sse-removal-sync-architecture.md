# ADR-006: Dashboard SSE 删除 & 同步架构保持

## Status
**Superseded** (original: Accepted)

## Date
2026-07-10 (superseded 2026-07-21)

## Decider
MAOP Core Team

## Context
`dashboard/server-v2.ps1` 有一整套 SSE（Server-Sent Events）实现——`/api/stream` 端点（46行），用于推送 ActiveJobs 状态。但前端从未创建 `EventSource` 连接，只用 `setInterval` 轮询。

更严重的是：SSE 端点持连 15 秒在同步单线程 `Handle-Request` 内——如果真有客户端连上，会冻结整个服务器 15 秒。

同时评估是否将 server 改为多线程/异步。分析后发现：
- 重的控制端点（`/api/control/run|validate|doctor`）已用 `Start-Process` 异步执行，立即返回 `job_id`
- 基础 API 全部有 Mutex 护卫的 TTL 缓存，命中时在微秒级返回
- 单用户 localhost 场景下，同步阻塞不是真实瓶颈

## Decision
1. 删除 `/api/stream` SSE 端点（46 行代码）
2. **不改为多线程**——收益远小于复杂度（`$Cache`、`$ActiveJobs`、`$Listener` 的线程安全需全量重新审查）
3. 前端保持 30-60s 轮询即可满足单用户场景

## Consequences
- **变得容易**：消除了一个潜在的全服冻结陷阱
- **变得容易**：不需要为多线程重写全部状态管理
- **风险**：未来如果多用户或需要真正实时推送，需重新设计——届时建议迁 FastAPI 而非硬改 PS

## Supersession (2026-07-21, t21)

**This ADR is superseded.** The PowerShell-era concerns that motivated the
original decision no longer apply:

1. **同步单线程阻塞问题已不存在** — MAOP v4.0.0 已完成从 PowerShell
   `server-v2.ps1` 到 FastAPI 的迁移。FastAPI 是原生异步框架，SSE
   端点通过 `async def` + `asyncio.Queue` 实现，不会阻塞事件循环。
   `/api/stream` 现在的真实实现见 `py/maop/dashboard/provider.py:306-325`
   （推送全局 state 事件）和 `py/maop/dashboard/routers/stream.py:22`
   （推送 per-execution trace 输出）。

2. **前端已接入 SSE** — t21 (2026-07-21) 新增了
   `dashboard-enterprise/src/composables/useSSE.js`，并改造 Monitor.vue
   订阅 `/api/stream` 的 `state` 事件实时刷新 Active Agents metric 与
   Event Stream 面板。EventSource 自动重连（指数退避到 30s），
   polling 作为 fallback 保留。

3. **JWT 鉴权兼容** — EventSource 不支持自定义 header，useSSE 通过
   查询参数 `?token=<maop_token>` 注入 JWT，后端 SSE 端点已支持。

**新决策**：保留并扩展 SSE 端点，前端通过 `useSSE` composable 接入，
polling 作为降级方案。原 ADR-006 的"删除 SSE"决策撤销。

**遗留**：`py/maop/dashboard/provider.py` 的 `_render_html` 函数
（行 330-365）仍含 v3.x 时代内嵌的 EventSource 脚本，已标注为
deprecated，待未来清理（与 Stack B MCP 重复实现同模式）。

