# ADR-007: Dashboard 缓存持久化 & Warm-Cache 修复

## Status
Accepted (2026-07-10)

## Context
`server-v2.ps1` 的两个基础功能存在实质性 bug：

**缓存持久化（Set-Cache）**：`Set-Cache` 只把单个端点 payload 写入 `cache.json`——`$data | ConvertTo-Json | Out-File $CacheFile`。注释声称"进程重启不丢缓存"，但重启后 `ConvertFrom-Json -AsHashtable` 读到的格式不匹配（单对象 vs 期待哈希表），导致实际不可用。

**启动预热（Warm-Cache）**：用 `Start-Job` 将 `$function:Warm-Cache` 的函数体注入子进程——但 `$HarnessDir`、`$Routes`、`$Cache` 等变量在子 runspace 中是空的。预热实际为 no-op。
此外，Warm-Cache 内部用自动生成的 job name（如 "Job1"）作为 cache key——`Set-Cache ("/api/" + $j.Name)`——而非实际 API 路径，即使子进程成功也会写到错误 key。

## Decision
1. **Set-Cache**：改为写全量 `$Cache` 哈希表：`$Cache | ConvertTo-Json`。重启加载时遍历 key，所有条目标记为 expired（`(Get-Date).AddSeconds(-1)`），首次访问自然刷新。
2. **Warm-Cache**：改为 key↔job 的 hashtable 映射——
   ```powershell
   $jobs = @{}
   foreach ($p in $priority) { ... $jobs[$p] = Start-Job ... }
   foreach ($key in $jobs.Keys) { ... Set-Cache $key $data }
   ```
   启动改为 inline 调用（废弃原 `Start-Job` 函数体注入方式），虽增加 10-20 秒启动时间但正确。

## Consequences
- **变得容易**：Dashboard 重启后缓存确实可用
- **变得容易**：预热真实填充首批 API 数据，首屏更快
- **风险**：inline warmup 延长启动时间，但本地仪表板可接受
