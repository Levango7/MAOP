<#
.SYNOPSIS
  MAOP Plugin Agent Loader — 配置驱动的 Agent 调度器
  替代 delegate.ps1 中巨大的 switch-case

.DESCRIPTION
  从 agents.yaml 中读取 agent 配置，根据 agent name 动态路由到对应的 driver。
  支持的 driver 类型:
    - cli:       直接调用 CLI 工具
    - wrapper:   通过 PowerShell wrapper 脚本调用
    - powershell: 嵌入 PowerShell命令
    - cmd:       cmd.exe 调用

  新增 agent 只需在 agents.yaml 中添加配置，无需修改代码。
#>

param(
  [string]$Agent,
  [string]$Task,
  [string]$TaskFile = "",
  [string]$RoutingKey,
  [string]$WorkDir,
  [int]$TimeoutSeconds = 180,
  [string]$TraceID,
  [switch]$Direct,
  [switch]$JobMode,
  [switch]$StartDashboard
)

# Load Task from file if TaskFile provided (to avoid command-line length limits)
if ($TaskFile -and -not $Task) {
  $Task = Get-Content $TaskFile -Raw -ErrorAction Stop
}

# Guard: dot-source only — if both Agent+Task are empty, define functions only
if ((-not $Agent -and -not $Task)) { return }

# Validate required params for direct execution
if (-not $Agent) { Write-Error "-Agent is required"; exit 1 }
if (-not $Task)  { Write-Error "-Task is required"; exit 1 }

# ════════════════════════════════════════════════════════════
# T2.7: 统一 Error Schema
# ════════════════════════════════════════════════════════════
. (Join-Path (Split-Path $PSCommandPath -Parent) "error-schema.ps1")

$Result = New-ResultObject -Agent $Agent -Task $Task -RoutingKey $RoutingKey -TraceID $TraceID
$Result.exit_code = $null  # Will be set during execution

# ── Auto-start dashboard (skipped with -NoDashboard) ──
function Start-PevDashboard {
  <#
  .SYNOPSIS
    Starts the MAOP dashboard server if not already running.
    Also launches a watchdog Job to restart on crash.
  .DESCRIPTION
    Extracted from inline code to allow callers to skip dashboard startup
    via -NoDashboard flag (useful for testing and headless operation).
  #>
  $dashRunning = $false
  $psProcesses = Get-Process -Name powershell -ErrorAction SilentlyContinue
  foreach ($px in $psProcesses) {
    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($px.Id)" -ErrorAction SilentlyContinue).CommandLine
    if ($cmd -match "dashboard\\server-v2") { $dashRunning = $true; break }
  }
  if ($dashRunning) { return }

  # Dedup: skip if a watchdog Job already exists
  $existingWatchdog = Get-Job -Name "dashboard-watchdog" -ErrorAction SilentlyContinue
  if ($existingWatchdog) { return }

  $pevRoot = Split-Path (Split-Path $PSCommandPath -Parent)
  $dashLog = Join-Path $pevRoot "logs\\dashboard.log"

  # Start dashboard server
  $dashProcess = Start-Process -WindowStyle Hidden -FilePath "powershell" -ArgumentList "-NoProfile -File `"$pevRoot\\dashboard\\server-v2.ps1`" -Port 8080" -RedirectStandardOutput $dashLog -PassThru

  # Watchdog: restart dashboard on crash, polling every 30s
  $watchdogScript = {
    param($WatchPid, $PevRoot, $DashLog)
    $restartCount = 0
    while ($true) {
      Start-Sleep -Seconds 30
      $proc = Get-Process -Id $WatchPid -ErrorAction SilentlyContinue
      if (-not $proc -or $proc.HasExited) {
        $restartCount++
        $restartLog = "[watchdog] Dashboard crashed, restart #${restartCount} at $(Get-Date -Format 'o')"
        Add-Content -Path $DashLog -Value $restartLog
        $newProc = Start-Process -WindowStyle Hidden -FilePath "powershell" -ArgumentList "-NoProfile -File `"$PevRoot\\dashboard\\server-v2.ps1`" -Port 8080" -RedirectStandardOutput $DashLog -PassThru
        $WatchPid = $newProc.Id
      }
    }
  }
  Start-Job -Name "dashboard-watchdog" -ScriptBlock $watchdogScript -ArgumentList @($dashProcess.Id, $pevRoot, $dashLog) | Out-Null
}

if ($StartDashboard) {
  Start-PevDashboard
}

# ════════════════════════════════════════
# 配置驱动的 agent 注册表
# ════════════════════════════════════════

$ScriptDir = Split-Path $PSCommandPath -Parent
$MAOP = Split-Path $ScriptDir -Parent

# ── 文件锁 ──
. (Join-Path $MAOP "src\filelock.ps1")

# ── Circuit Breaker 数据文件 ──
$BreakerFile = Join-Path $MAOP "data\circuit-breaker.json"

# ── Log rotation: prevent unbounded growth ──
$logRotateScript = Join-Path $MAOP "tools\log-rotate.ps1"
if (Test-Path $logRotateScript) {
  try { & $logRotateScript -MaxSizeKB 512 -RetainCount 5 -Quiet } catch {}
}

# ── YAML bridge: dot-source 共享脚本 ──
. (Join-Path $MAOP "tools\MAOP-bridge.ps1")

function Get-AgentConfig($agentName) {
  $cfg = Invoke-ConfigBridge "--agent $agentName" -Critical
  if ($cfg.error) { throw "[FAIL-FAST] Agent '$agentName' config error: $($cfg.error)" }
  return @{
    name     = $cfg.name
    cli      = $cfg.cli
    driver   = $cfg.driver
    cli_args = $cfg.cli_args
    capabilities = $cfg.capabilities
    timeout_s= [int]$cfg.timeout_s
    model    = $cfg.model
    wrapper  = $cfg.wrapper
    command  = $cfg.command
    env      = @{}
  }
}

function Resolve-AgentConfig($rawName) {
  # 先精确匹配（critical — agent 配置是执行前提）
  $cfg = Invoke-ConfigBridge "--agent $rawName" -Critical
  if ($cfg -and -not $cfg.error) {
    return @{ name=$cfg.name; cli=$cfg.cli; driver=$cfg.driver; cli_args=$cfg.cli_args; capabilities=$cfg.capabilities; timeout_s=[int]$cfg.timeout_s; model=$cfg.model; wrapper=$cfg.wrapper; command=$cfg.command }
  }
  if ($cfg -and $cfg.error) {
    throw "[FAIL-FAST] Agent '$rawName' config error: $($cfg.error)"
  }
  # 通配符匹配（非关键 — 降级到列表搜索）
  $all = Invoke-ConfigBridge "--section agents"
  if ($all) {
    foreach ($a in $all) {
      if ($rawName -ne $a.name -and $rawName -like $a.name) {
        return @{ name=$a.name; cli=$a.cli; driver=$a.driver; cli_args=$a.cli_args; capabilities=$a.capabilities; timeout_s=[int]$a.timeout_s; model=$a.model; wrapper=$a.wrapper; command=$a.command }
      }
    }
  }
  $wf = Invoke-ConfigBridge "--section workflows"
  if ($wf) {
    foreach ($w in $wf.PSObject.Properties) {
      if ($rawName -ne $w.Name -and $rawName -like $w.Name) {
        $c = $w.Value
        return @{ name=$w.Name; cli=$c.cli; driver=$c.driver; cli_args=''; capabilities=@(); timeout_s=[int]$c.timeout_s; model=$c.model; wrapper=$c.wrapper; command='' }
      }
    }
  }
  return $null
}

# ════════════════════════════════════════
# Security: Escape string for cmd.exe /c context
# Escapes: & | ( ) < > ^ newline
# ════════════════════════════════════════
function ConvertTo-CmdEscapedString {
  param([string]$InputString)
  return $InputString -replace '([\^\&\|\<\>\(\)])', '^$1' -replace "`n", '^`n' -replace "`r", ''
}

# ════════════════════════════════════════
# Security: Escape string for PowerShell -Command context
# ════════════════════════════════════════
function ConvertTo-PowerShellCommandEscapedString {
  param([string]$InputString)
  return "'" + ($InputString -replace "'", "''") + "'"
}

# ── Driver: CLI ──
function Invoke-CliDriver($config, $prompt, $timeout) {
  $Result.driver = "cli"
  $Result.model = $config.model
  
  $cli = $config.cli
  $argsTemplate = if ($config.cli_args) { $config.cli_args } else { "-p '{task}'" }
  # Secure: escape prompt for argument passing (no cmd.exe shell layer)
  $escapedPrompt = $prompt -replace '"', '\"'
  $argLine = $argsTemplate -replace "'\{task\}'", "'$escapedPrompt'"
  $argLine = $argLine -replace "\{task\}", "`"$escapedPrompt`""
  
  $outFile = [System.IO.Path]::GetTempFileName()
  $errFile = [System.IO.Path]::GetTempFileName()
  try {
    # Direct Start-Process — no cmd.exe intermediary
    $spArgs = @{
      FilePath               = $cli
      ArgumentList           = $argLine
      NoNewWindow            = $true
      RedirectStandardOutput = $outFile
      RedirectStandardError  = $errFile
      PassThru               = $true
    }
    if ($WorkDir) { $spArgs.WorkingDirectory = $WorkDir }
    
    $p = Start-Process @spArgs
    if ($p.WaitForExit($timeout * 1000)) {
      Start-Sleep -Milliseconds 300
      $Result.stdout = ([System.IO.File]::ReadAllText($outFile) -replace '\x1b\[[0-9;]*m', '').TrimEnd()
      $Result.stderr = ([System.IO.File]::ReadAllText($errFile) -replace '\x1b\[[0-9;]*m', '').TrimEnd()
      $ec = try { $p.ExitCode } catch { 0 }
      $Result.exit_code = if ($null -eq $ec) { 0 } else { $ec }
    } else {
      $p.Kill()
      $Result.error = "TIMEOUT after ${timeout}s"
      $Result.exit_code = -1
    }
  } finally {
    Remove-Item $outFile -Force -ErrorAction SilentlyContinue
    Remove-Item $errFile -Force -ErrorAction SilentlyContinue
  }
}

# ── Driver: Wrapper ──
function Invoke-WrapperDriver($config, $prompt, $timeout) {
  $Result.driver = "wrapper"
  $Result.model = $config.model
  
  $wrapper = $config.wrapper
  if (-not (Test-Path $wrapper)) {
    $wrapperName = if ($wrapper -notmatch '\.ps1$') { "$wrapper.ps1" } else { $wrapper }
    $wrapper = Join-Path $ScriptDir $wrapperName
    if (-not (Test-Path $wrapper)) {
      $wrapper = Join-Path $ScriptDir $wrapper
    }
  }
  
  $safePrompt = $prompt -replace "'", "''"
  $modelArgs = if ($config.cli_args) { $config.cli_args } else { "" }
  
  $outFile = [System.IO.Path]::GetTempFileName()
  try {
    # Direct Start-Process powershell — no cmd.exe intermediary
    $spArgs = @{
      FilePath               = "powershell"
      ArgumentList           = "-NoProfile -File `"$wrapper`" -Prompt '$safePrompt' -TimeoutSeconds $timeout -AgentName '$($config.name)' -TraceID '$TraceID' $modelArgs 2>&1"
      NoNewWindow            = $true
      RedirectStandardOutput = $outFile
      PassThru               = $true
    }
    
    $p = Start-Process @spArgs
    if ($p.WaitForExit($timeout * 1000)) {
      Start-Sleep -Milliseconds 300
      $rawOutput = ([System.IO.File]::ReadAllText($outFile) -replace '\\x1b\\[[0-9;]*m', '').TrimEnd()
      # 尝试解析 wrapper 输出的统一 JSON schema
      try {
        $wrapperResult = $rawOutput | ConvertFrom-Json -ErrorAction Stop
        if ($wrapperResult.PSObject.Properties.Name -contains "ok") {
          $Result.ok = $wrapperResult.ok
          $Result.exit_code = if ($null -ne $wrapperResult.exit_code) { [int]$wrapperResult.exit_code } else { 0 }
          $Result.stdout = if ($wrapperResult.stdout) { $wrapperResult.stdout } else { "" }
          $Result.stderr = if ($wrapperResult.stderr) { $wrapperResult.stderr } else { "" }
          $Result.error = if ($wrapperResult.error -and $wrapperResult.error -ne "null") { $wrapperResult.error } else { $null }
          if ($null -ne $wrapperResult.duration_ms) { $Result.duration_ms = [int]$wrapperResult.duration_ms }
        } else {
          # JSON 但不含 ok 字段，保留原始输出
          $Result.stdout = $rawOutput
          $Result.exit_code = try { $p.ExitCode } catch { 0 }
        }
      } catch {
        # 不是 JSON，保留原始输出（向后兼容）
        $Result.stdout = $rawOutput
        $Result.exit_code = try { $p.ExitCode } catch { 0 }
      }
    } else {
      $p.Kill()
      $Result.error = "TIMEOUT after ${timeout}s"
      $Result.exit_code = -1
    }
  } finally {
    Remove-Item $outFile -Force -ErrorAction SilentlyContinue
  }
}

# ── Driver: PowerShell 内联 ──
function Invoke-PowerShellDriver($config, $prompt, $timeout) {
  $Result.driver = "powershell"
  $Result.model = $config.model

  $command = $config.command
  # Secure: use single-quote escaping for PowerShell -Command context
  $escapedPrompt = ConvertTo-PowerShellCommandEscapedString $prompt
  $resolved = $command -replace "'{task}'", $escapedPrompt
  
  $outFile = [System.IO.Path]::GetTempFileName()
  try {
    # Direct Start-Process powershell — no cmd.exe intermediary
    $spArgs = @{
      FilePath               = "powershell"
      ArgumentList           = "-NoProfile -Command `"$resolved`""
      NoNewWindow            = $true
      RedirectStandardOutput = $outFile
      PassThru               = $true
    }
    
    $p = Start-Process @spArgs
    if ($p.WaitForExit($timeout * 1000)) {
      Start-Sleep -Milliseconds 300
      $Result.stdout = ([System.IO.File]::ReadAllText($outFile) -replace '\x1b\[[0-9;]*m', '').TrimEnd()
      $Result.exit_code = try { $p.ExitCode } catch { 0 }
    } else {
      $p.Kill()
      $Result.error = "TIMEOUT after ${timeout}s"
      $Result.exit_code = -1
    }
  } finally {
    Remove-Item $outFile -Force -ErrorAction SilentlyContinue
  }
}

# ── Driver: cmd 原生 (for .cmd/.bat agents like qwenpaw) ──
function Invoke-CmdDriver($config, $prompt, $timeout) {
  $Result.driver = "cmd"
  $Result.model = $config.model
  
  $cli = $config.cli
  # Escape special chars for cmd argument context
  # cmd.exe /c 完整转义: ^ 覆盖 &|<>() (unescaped 区域), %% 阻止环境变量展开, "" 转义引号
  $safePrompt = $prompt -replace '%', '%%' -replace '"', '""' -replace '([\^&|;<>\t\(\)\[\]!])', '^$1'
  $argLine = $config.cli_args -replace "'{task}'", $safePrompt
  
  $outFile = [System.IO.Path]::GetTempFileName()
  try {
    # Use cmd.exe only for .cmd/.bat agents that require it
    $spArgs = @{
      FilePath               = "cmd.exe"
      ArgumentList           = "/c `"$cli`" $argLine"
      RedirectStandardOutput = $outFile
      PassThru               = $true
      WindowStyle            = "Hidden"
    }
    
    $p = Start-Process @spArgs
    if ($p.WaitForExit($timeout * 1000)) {
      Start-Sleep -Milliseconds 500
      $Result.stdout = ([System.IO.File]::ReadAllText($outFile) -replace '\x1b\[[0-9;]*m', '').TrimEnd()
      $Result.exit_code = try { $p.ExitCode } catch { 0 }
    } else {
      $p.Kill()
      $Result.error = "TIMEOUT after ${timeout}s"
      $Result.exit_code = -1
    }
  } finally {
    Remove-Item $outFile -Force -ErrorAction SilentlyContinue
  }
}

# ════════════════════════════════════════
# Circuit Breaker — 智能熔断
#
# 注意:
#   本文件的 dispatch 逻辑（Main Dispatch §523-566）负责执行给定的 agent，
#   但不做 agent 选择决策。Agent 路由由 MAOP-plan.ps1 作为唯一真相源
#   (single source of truth) 决定 selected_agent。本文件的 circuit breaker
#   仅在 breaker open 时阻止执行，不参与 agent 选择。
#   如需调整路由策略，请修改 MAOP-plan.ps1 或 dynamic-router.ps1。
# ════════════════════════════════════════

# PS5.1 兼容：将 ConvertFrom-Json 输出的 PSCustomObject 递归转为 Hashtable
function ConvertFrom-JsonToHashtable {
  param([Parameter(ValueFromPipeline = $true)][object]$InputObject)
  process {
    if ($null -eq $InputObject) { return $null }
    # PSCustomObject (PS5.1 ConvertFrom-Json output) — 通过 PSObject.Properties 枚举
    if ($InputObject -is [System.Management.Automation.PSCustomObject]) {
      $ht = @{}
      foreach ($prop in $InputObject.PSObject.Properties) {
        $ht[$prop.Name] = ConvertFrom-JsonToHashtable $prop.Value
      }
      return $ht
    }
    if ($InputObject -is [System.Collections.IDictionary]) {
      $ht = @{}
      foreach ($kv in $InputObject.GetEnumerator()) {
        $ht[$kv.Key] = ConvertFrom-JsonToHashtable $kv.Value
      }
      return $ht
    }
    if ($InputObject -is [System.Collections.IList]) {
      $list = @()
      foreach ($item in $InputObject) {
        $list += ConvertFrom-JsonToHashtable $item
      }
      return $list
    }
    return $InputObject
  }
}

# ── Circuit Breaker — import module ──
. (Join-Path $MAOP "src\circuit-breaker.ps1")

# ════════════════════════════════════════
# MAIN DISPATCH
# ════════════════════════════════════════

$start = Get-Date

try {
  # 解析 agent 配置
  $config = Resolve-AgentConfig $Agent
  if (-not $config) {
    $Result.error = "Unknown agent: $Agent. Check config\agents.yaml"
    $Result.exit_code = -1
  } else {
    $timeout = if ($TimeoutSeconds) { $TimeoutSeconds } else { $config.timeout_s }
    $safeTask = $Task

    # ── T2.1: JobMode — 在 Start-Job 中执行 driver，实现进程隔离 ──
    if ($JobMode) {
      $jobScript = {
        param($sp_ScriptDir, $sp_Agent, $sp_Task, $sp_RoutingKey, $sp_WorkDir, $sp_TimeoutSeconds, $sp_TraceID)
        # 用 -Direct 调用自身，在独立 runspace 中运行全部 dispatch 逻辑
        & (Join-Path $sp_ScriptDir "delegate-plugin.ps1") -Agent $sp_Agent -Task $sp_Task -RoutingKey $sp_RoutingKey -WorkDir $sp_WorkDir -TimeoutSeconds $sp_TimeoutSeconds -TraceID $sp_TraceID -Direct
      }

      $job = Start-Job -Name "delegate-job-$Agent-$TraceID" -ScriptBlock $jobScript -ArgumentList @(
        $ScriptDir, $Agent, $Task, $RoutingKey, $WorkDir, $TimeoutSeconds, $TraceID
      )

      $completed = $job | Wait-Job -Timeout $TimeoutSeconds
      if (-not $completed) {
        Stop-Job $job
        $Result.error = "JOB_TIMEOUT after ${TimeoutSeconds}s"
        $Result.exit_code = -1
      } else {
        $jobOutput = Receive-Job $job -ErrorAction SilentlyContinue
        $rawOutput = ($jobOutput | Out-String).Trim()
        if ($rawOutput) {
          $jsonStart = $rawOutput.IndexOf('{')
          $jsonText = if ($jsonStart -ge 0) { $rawOutput.Substring($jsonStart) } else { $rawOutput }
          try {
            $parsed = $jsonText | ConvertFrom-Json -ErrorAction Stop
            # 白名单提取：只允许预期字段，防止 JSON 属性注入
            $allowedFields = @('ok','exit_code','stdout','stderr','error','duration_ms','output','agent','model','driver','task','routing_key')
            foreach ($prop in $parsed.PSObject.Properties) {
              if ($allowedFields -contains $prop.Name) {
                $Result[$prop.Name] = if ($prop.Name -eq 'exit_code') { [int]$prop.Value } else { $prop.Value }
              }
            }
          } catch {
            $Result.stdout = $rawOutput
          }
        }
      }
      Remove-Job $job -Force -ErrorAction SilentlyContinue
    } else {
      # ── Direct mode: 在当前进程执行（已有 Start-Process 隔离） ──

      # ── Circuit Breaker Check ──
      $breaker = Get-BreakerState -AgentName $Agent
      if ($breaker -and $breaker.state -eq "open") {
        $now = Get-Date
        $lastFails = if ($breaker.last_failure) { [datetime]::Parse($breaker.last_failure) } else { $null }
        $cooldownEnd = if ($lastFails) { $lastFails.AddSeconds($breaker.cooldown_s) } else { $null }

        if ($cooldownEnd -and ($now -lt $cooldownEnd)) {
          $Result.error = "circuit breaker open for agent '$Agent' — cooldown until $($cooldownEnd.ToString('o'))"
          $Result.exit_code = -3
        } else {
          # Cooldown expired → set HALF-OPEN and proceed
          Set-BreakerState -AgentName $Agent -State "half-open" -Failures $breaker.failures -LastFailure $breaker.last_failure
        }
      }

      if (-not $Result.error) {
        # ── CLI Pre-check: verify binary exists before dispatching ──
        if ($config.driver -in @("cli", "cmd") -and $config.cli) {
          $cliName = ($config.cli -split '\s+')[0]  # e.g. "kimi" or "claude.exe"
          $found = Get-Command $cliName -ErrorAction SilentlyContinue
          if (-not $found) {
            $Result.error = "Agent '$Agent' requires CLI '$cliName' which is not installed or not in PATH. Install it first or use 'MAOP config agent $Agent --remove' to remove this agent."
            $Result.exit_code = -4
          }
        }
      }

      if (-not $Result.error) {
        switch ($config.driver) {
          "cli"       { Invoke-CliDriver $config $safeTask $timeout }
          "wrapper"   { Invoke-WrapperDriver $config $safeTask $timeout }
          "powershell" { Invoke-PowerShellDriver $config $safeTask $timeout }
          "cmd"       { Invoke-CmdDriver $config $safeTask $timeout }
          default     {
            # 回退到 CLI driver
            Write-Warning "Unknown driver '$($config.driver)' for agent '$Agent', falling back to CLI"
            Invoke-CliDriver $config $safeTask $timeout
          }
        }

        # ── Update breaker state after execution ──
        $isFailure = ($null -ne $Result.error) -or ($null -ne $Result.exit_code -and $Result.exit_code -ne 0)
        if ($isFailure) {
          $breaker2 = Get-BreakerState -AgentName $Agent
          if ($breaker2) {
            $newFailures = $breaker2.failures + 1
            $nowStr = (Get-Date -Format "o")
            if ($newFailures -ge $breaker2.threshold) {
              Set-BreakerState -AgentName $Agent -State "open" -Failures $newFailures -LastFailure $nowStr
            } else {
              Set-BreakerState -AgentName $Agent -State $breaker2.state -Failures $newFailures -LastFailure $nowStr
            }
          }
        } else {
          # Success → reset to CLOSED
          Set-BreakerState -AgentName $Agent -State "closed" -Failures 0 -LastFailure ""
        }
      }
    }
  }
} catch {
  $Result.error = $_.Exception.Message
  $Result.exit_code = -2
}

$end = Get-Date
$Result.duration_ms = [math]::Round(($end - $start).TotalMilliseconds)
$Result.ok = Test-ResultSuccess $Result
$Result.end_time = (Get-Date -Format "o")

# ── 轨迹追踪（仅在非 JobMode 时记录，避免双重记录） ──
if ($TraceID -and -not $JobMode) {
  $memScript = Join-Path $MAOP "src\memory.ps1"
  if (Test-Path $memScript) {
    & $memScript -Action trajectory -TraceID $TraceID -Agent $Agent -ToolName "delegate" -ToolDurationMs $Result.duration_ms -ToolExitCode $Result.exit_code -ToolOutput $Result.stdout 2>&1 | Out-Null
  }
}

($Result | ConvertTo-Json -Depth 3)
