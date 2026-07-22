<#
.SYNOPSIS
  迁移指南: delegate.ps1 (switch-case) → delegate-plugin.ps1 (配置驱动)

  迁移步骤:
  1. agents.yaml 中每个 agent 添加 driver 字段
  2. 用 delegate-plugin.ps1 取代 delegate.ps1
  3. 测试所有 agent 类型

  driver 类型说明:
    cli       — 直接调用命令行工具 (claude, openclaw, hermes, codex, cursor ...)
    wrapper   — 通过 PowerShell wrapper 脚本调用 (nvidia*, qianfan*, tencent*, freellmapi*, mavis* ...)
    powershell— 内联 PowerShell 命令 (kimi, codewhale, kilo, qoder, autoclaw ...)
    cmd       — cmd.exe 原生调用 (qwenpaw ...)

  agents.yaml 新增字段示例:
    hermes:
      driver: cli              # <-- 新增
      cli: "hermes"
      cli_args: "-z '{task}' chat"  # <-- 新增模板化参数
      timeout_s: 120

    nvidia:
      driver: wrapper           # <-- 新增
      cli: "nvidia-wrapper"     # 统一 CLI 入口
      wrapper: "nvidia-wrapper.ps1"
      cli_args: "-Model 'z-ai/glm-5.2'"
      timeout_s: 60

    kimi:
      driver: powershell        # <-- 新增
      command: "echo '{task}' | kimi --print 2>$null"
      timeout_s: 120

功能对比:
  delegate.ps1 (旧)                    delegate-plugin.ps1 (新)
  ─────────────────────────           ─────────────────────────
  200+ 行 switch-case                 5 行配置驱动路由
  新增 agent 需要改代码               新增 agent 只需改 YAML
  没有 driver 抽象                    4 种 driver 统一
  参数硬编码在代码里                  参数模板化
  wrapper 路径散落                    统一在 config 中管理
#>

Write-Host "Migration: delegate.ps1 → delegate-plugin.ps1"
Write-Host ""
Write-Host "Step 1: Add 'driver' field to all agents in agents.yaml"
Write-Host "Step 2: Replace delegate.ps1 with delegate-plugin.ps1"
Write-Host "Step 3: Test all agent types"
Write-Host ""
Write-Host "New agent registration (no code change):"
Write-Host "  agents:"
Write-Host "    my-new-agent:"
Write-Host "      driver: cli"
Write-Host "      cli: 'my-tool'"
Write-Host "      cli_args: '-p \"{task}\"'"
Write-Host "      timeout_s: 60"
Write-Host "      capabilities: [codegen, chat]"
