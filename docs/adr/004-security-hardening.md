# ADR-004: 安全加固（CmdDriver 全转义 + 标识符白名单）

## Status
Accepted

## Date
2026-07-10

## Decider
MAOP Core Team

## Context
2026-07-06 安全审计发现 19 项问题。到 2026-07-09 时，严重项已基本处置（硬编码 API Key 删除、Dashboard 路径穿越修复、CmdDriver 已有基础转义）。但残留两个缺口：

1. **CmdDriver 转义不完整**：`Invoke-CmdDriver` 使用 `cmd.exe /c` 执行外部 agent CLI，当前转义仅覆盖 `^&|;<>()`，未覆盖 `%`（环境变量展开）和 `"`（引号闭合攻击）。含 `%TEMP%` 或 `"` 的恶意 prompt 仍可命令注入。

2. **路径型标识符无校验**：`memory.ps1`（id/TraceID）、`dag-engine.ps1`（dag.id）、`maop-verify.ps1`（gate name）中的标识符直接拼入 `Join-Path`，无白名单限制。这些值部分源于 LLM 输出或外部参数，属"间接可控输入"。

## Decision
1. **CmdDriver**：补 `-replace '%', '%%' -replace '"', '""'`（cmd.exe 中 `%%` 阻止环境变量展开，`""` 转义双引号）
2. **标识符白名单**：
   - `memory.ps1`：`$id` 和 `$TraceID` 加 `^[A-Za-z0-9_-]+$` 校验
   - `dag-engine.ps1`：`$dag.id` 加同上格式校验
   - `maop-verify.ps1`：gate name 加 `^[a-zA-Z0-9_-]+$` 校验

## Consequences
- **变得容易**：剩余注入面关闭，标识符路径安全
- **风险**：如果运行时有 agent 生成非标准 id 格式（如含 `.` 或中文），会被拒——但当前所有标识符均为 GUID hex 或 YAML key（字母数字+连字符），无影响
- **注意**：`cmd.exe /c` 本质上不安全，长期建议将 cmd driver 改造为 temp-file/stdin 传参方式
