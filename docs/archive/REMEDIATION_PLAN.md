# MAOP 修复计划 — 执行状态

## Phase 0: 安全红线 (P0) ✅ 全部完成
### 0.1 delegate-plugin.ps1 命令注入修复
  - [x] 0.1.1 Invoke-CliDriver: ✅ 已用 Start-Process 直接调用（无 shell 层），prompt 已转义
  - [x] 0.1.2 Invoke-WrapperDriver: ✅ 使用 Start-Process powershell 直接调用，prompt 已单引号转义
  - [x] 0.1.3 Invoke-PowerShellDriver: ✅ 使用 ConvertTo-PowerShellCommandEscapedString 转义
  - [x] 0.1.4 Invoke-CmdDriver: ✅ cmd 转义补全了 `[` `]` `!` 字符
### 0.2 路径穿越修复
  - [x] 0.2.1 server-v2.ps1: ✅ 已用 Resolve-Path + 边界检查
  - [x] 0.2.2 maop-verify.ps1: ✅ 已用 `^[a-zA-Z0-9_-]+$` 正则校验 gate name
  - [x] 0.2.3 memory.ps1: ✅ id 校验已有，TraceID 校验新增
  - [x] 0.2.4 dag-engine.ps1: ✅ 已用 `^[A-Za-z0-9_-]+$` 校验 dag.id
  - [x] 0.2.5 pipeline-orchestrator.ps1: ✅ 用 GetFileName() 过滤 outputHint
  - [x] 0.2.6 sandbox.ps1: ✅ 外部输入的 SandboxId 校验
### 0.3 JSON 反序列化加固
  - [x] 0.3.1 delegate-plugin.ps1: ✅ 白名单字段提取 + exit_code 类型强制转换
  - [x] 0.3.2 maop-loop.ps1: ✅ 已用 SafeFromJson + Filter-Output 组合
  - [x] 0.3.3 dag-engine.ps1: ✅ 结果字段已有类型校验

## Phase 1: 代码清理 (P1) ✅ 全部完成
### 1.1 移除废弃文件
  - [x] 1.1.1 删除 orchestrator.ps1 (已标记 DEPRECATED)
  - [x] 1.1.2 删除 dashboard/server.ps1 (已标记 DEPRECATED)
  - [x] 1.1.3 清理 29 个备份文件 → dashboard/.backup/
  - [x] 1.1.4 清理 data/ 测试文件 → test/data-artifacts/

## Phase 2: 架构整合 (P2) ✅ 核心完成
### 2.1 YAML 解析统一化
  - [ ] 2.1.1 dag-engine.ps1 → Python bridge 替换 (标注技术债，待后续)
  - [ ] 2.1.2 validate-config.ps1 → Python bridge 替换 (标注技术债，待后续)
### 2.2 硬编码路径消除
  - [x] maop.ps1: $MAOP → Split-Path $PSCommandPath -Parent
  - [x] pipeline-orchestrator.ps1: $maopOutput → 相对路径
  - [x] pipeline-wrapper.ps1: $maopOutput → 相对路径
  - [x] _syntax_check.ps1 / _test_integration.ps1: 测试脚本改用 $PSScriptRoot
  - [x] maop-verify.ps1: 仅剩示例注释中的路径（安全）
  - [x] pipeline-wrapper.ps1: $PIPELINE_ROOT 指向外部项目（设计如此）

### 2.3 Dashboard 模块化
  - [ ] 待后续：拆分为 HTML/CSS/JS 文件（2个style块 + 2个script块）

## Phase 3: 测试补全 (P3) — 待后续
### 3.1 关键路径测试
  - [ ] 3.1.1 delegate-plugin 测试
  - [ ] 3.1.2 memory 测试
  - [ ] 3.1.3 server-v2 集成测试
