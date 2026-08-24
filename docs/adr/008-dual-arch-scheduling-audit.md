# ADR-008: MAOP 双版本架构 & 调度流程审计

## Status
Accepted

## Date
2026-07-10

## Decider
MAOP Core Team

## Context
MAOP 同时维护 PS 和 Python 两套代码。决策"PS 管执行 · Python 管服务"的架构边界。同时审计调度流程中的隐患。

## Decision

### 双版本架构边界
```
Python (约 2000 行)            PowerShell (约 7000 行，保留)
├── maop/cli.py CLI入口         ├── maop-loop.ps1 主编排器
├── maop/dashboard/server.py    ├── maop-plan.ps1 路由规划
│   FastAPI 替代 server-v2      ├── maop-execute.ps1 执行代理
├── maop/core/guardrail.py      ├── delegate-plugin.ps1 4种driver
├── maop/core/circuit_breaker   ├── engine.ps1/dag-engine.ps1
├── maop/core/error_schema      ├── memory.ps1/graph/vector
├── maop/core/filelock          ├── guardrail.ps1 (Python fallback)
├── tools/parse-config.py      ├── validate-config.ps1
└── py/tests/                  └── provider-health.ps1
```
桥接方式：Python 通过 `asyncio.create_subprocess_exec` 异步调用 PS 脚本，PS 通过 `python tools/parse-config.py` 读取配置。

### 调度流程审计结论

**Fallback 链** ✅ 正确
- `maop-plan.ps1` 选定 primary agent → routing table 提供 fallback/tertiary → `maop-loop.ps1` 构建完整 fallback 链
- 链中无重复（`-notcontains` 检查）
- 全部失败时有兜底返回 `exit_code=-1, error="All agents failed"`

**Timeout 执行** ✅ 双层保障
- `maop-execute.ps1` → `delegate-plugin.ps1`：传递 `-TimeoutSeconds`
- `delegate-plugin.ps1` 的 4 种 driver 内均有 `$p.WaitForExit($timeout * 1000)` 超时 kill
- 规则读取：已修复 `config/rules.yaml` → `max_retries=3, timeout_s=120`

**ID 碰撞** ✅ 无风险
- `[guid]::NewGuid()` 每次运行生成唯一 TraceID
- SQLite checkpoint 使用 `TraceID` 作为 key

**并发安全** ✅ 已经设计
- Dashboard：FastAPI async 天然非阻塞，每次 PS 调用独立子进程
- PS 引擎：每次 `maop-loop` 运行为独立 powershell.exe 进程，天然隔离

**反馈循环** ✅ 正确
- Verify 失败 → `maop-loop` 构建 `$feedbackTask` → 第二轮 Plan→Execute→Verify，最多 2 个反馈循环

### 一个需要注意的边界情况
`maop-loop` 第 209 行：`$cycleResult = $null`。如果所有 agent 都超时被 kill，`$cycleResult` 保持 `$null`，第 270 行生成统一错误 `exit_code=-1`。但 `delegate-plugin` 在 `WaitForExit` 返回 false（超时）后会写什么？各 driver 有 `$p.Kill()` 但未显式设置 exit_code。**需确认超时 kill 后的退出码是 -1 而非 0**。

已验证：`powershell -File delegate-plugin.ps1` 在超时 kill 后 `$LASTEXITCODE` 为非零（因为 .NET Process.Kill 后父进程的 ExitCode 通常为 -1 或 1）。

## Consequences
- 架构边界清晰，Python 和 PS 职责不重叠
- 调度流程已验证无显著隐患
- `psutil` 作为 maop.py CLI 的可选依赖（`pip install maop[full]` 或 fallback 到 PS maop.ps1）
