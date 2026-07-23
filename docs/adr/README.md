# MAOP Architecture Decision Records

| ADR | 标题 | 状态 |
|-----|------|------|
| [001](001-python-yaml-bridge.md) | Python YAML 桥接替代手写正则解析 | Accepted (2026-07-10) |
| [002](002-server-merge-orchestrator-deprecation.md) | Server 合并 & Orchestrator 废弃 | Accepted (2026-07-10) |
| [003](003-mock-fallback-removal.md) | Dashboard 假数据兜底清除 | Accepted (2026-07-10) |
| [004](004-security-hardening.md) | 安全加固（CmdDriver 全转义 + 标识符白名单） | Accepted (2026-07-10) |
| [005](005-powershell-retention.md) | 保留 PowerShell 核心引擎，Dashboard 可迁 Python | Accepted (2026-07-10) |
| [006](006-sse-removal-sync-architecture.md) | Dashboard SSE 删除 & 同步架构保持 | Superseded (2026-07-21) — see "Supersession" section at the bottom. |
| [007](007-cache-warmup-fix.md) | Dashboard 缓存持久化 & Warm-Cache 修复 | Accepted (2026-07-10) |
| [008](008-dual-arch-scheduling-audit.md) | MAOP 双版本架构 & 调度流程审计 | Accepted (2026-07-10) |
| [009](009-python-primary-engine.md) | Python 主引擎 — 基于实证的架构转向 | Proposed (Supersedes ADR-005) |
| [010](010-bugfix-batch.md) | Batch Bugfix — Critical/High/Medium Priority |  |
| [011](011-state-unification.md) | P0-3 状态源真统一（队列/人工队列单一真源） | Proposed |
| [012](012-routing-refactor.md) | 配置化路由重构评估（仅设计/不执行） | Proposed（评估结论：建议做，但列为 P1/P2 后续，非当前 P0 范围） |
| [013](013-agent-llm-direct-cli-fallback.md) | Agent 机制 — LLM 直连主路径 + CLI 降级保留（双路径并存） | Accepted (2026-07-22) — Phase F 决策记录。 |
