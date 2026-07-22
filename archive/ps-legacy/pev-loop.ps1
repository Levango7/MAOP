<#
.SYNOPSIS
  MAOP Loop — Master Orchestrator
  Plan → Execute → Verify cycle with guardrails, healthcheck, retry, and memory.

.DESCRIPTION
  Single source of truth for agent routing = MAOP-plan.ps1 (reads agents.yaml).
  MAOP-loop.ps1 does NOT duplicate routing logic.

.PARAMETER Task
  Task description to delegate.

.PARAMETER WorkDir
  Working directory (default: current dir).

.PARAMETER Retry
  Enable fallback retry on failure (up to max_attempts from rules.yaml).

.PARAMETER SkipVerify
  Skip verification gates.

.EXAMPLE
  .\MAOP-loop.ps1 -Task "Add input validation" -Retry
#>

param(
  [string]$Task,
  [string]$WorkDir = (Get-Location).Path,
  [switch]$Retry,
  [switch]$SkipVerify,
  [string]$ParentTraceID
)

$ScriptDir = Split-Path $PSCommandPath -Parent
$HarnessDir = $ScriptDir  # src/ 目录（memory.ps1 等在此）
$ProjectRoot = Split-Path $ScriptDir -Parent
$cycleStart = Get-Date

# ── 结构化日志: JSON-append 到 MAOP-loop.jsonl ──
$PevLogFile = Join-Path $ProjectRoot "data\MAOP-loop.jsonl"
function Write-PevLog {
  param(
    [Parameter(Mandatory=$true)][string]$Phase,
    [Parameter(Mandatory=$true)][string]$Level,   # INFO | WARN | ERROR
    [string]$Message = "",
    [hashtable]$Data = @{}
  )
  $entry = [ordered]@{
    timestamp = (Get-Date -Format "o")
    source    = "MAOP-loop"
    phase     = $Phase
    level     = $Level
    message   = $Message
  }
  foreach ($k in $Data.Keys) { $entry[$k] = $Data[$k] }
  try {
    Add-Content -Path $PevLogFile -Value ($entry | ConvertTo-Json -Compress -Depth 4) -Encoding utf8
  } catch {
    Write-Verbose "[MAOP-loop] Write-PevLog failed: $_"
  }
}

# ── Log rotation: prevent unbounded growth ──
$logRotateScript = Join-Path $ProjectRoot "tools\log-rotate.ps1"
if (Test-Path $logRotateScript) {
  try {
    & $logRotateScript -MaxSizeKB 512 -RetainCount 5 -Quiet
  } catch {
    Write-Verbose "[MAOP-loop] log-rotate failed: $_"
  }
}

# ── YAML bridge: dot-source 共享脚本 ──
. (Join-Path $ProjectRoot "tools\MAOP-bridge.ps1")

# ── SQLite checkpoint support ──
$PEV_HasSQLite = $false
$dbScript = Join-Path $ScriptDir "database.ps1"
if (Test-Path $dbScript) {
  try {
    # & { ... } child scope 调用 database.ps1，变量不泄漏到主作用域
    $PEV_HasSQLite = & { . $dbScript; $script:PEV_HasSQLite }
  } catch {
    Write-Verbose "[MAOP-loop] SQLite not available: $_"
  }
}

# ── 生成 TraceID 用于会话关联 ──
$TraceID = [guid]::NewGuid().ToString("N")

Write-Host "╔══════════════════════════════════════════╗"
Write-Host "║        MAOP Loop — Master Orchestrator   ║"
Write-Host "╚══════════════════════════════════════════╝"
Write-Host "Task: $Task"
Write-Host "Trace: $TraceID"
Write-Host "WorkDir: $WorkDir"

# ── 记忆注入：查相关经验拼进 Task ──
$memScript = Join-Path $HarnessDir "memory.ps1"
if (Test-Path $memScript) {
  $injectedContext = & $memScript -Action inject -Query $Task -Top 3 2>&1 | Select-Object -Last 1
  if ($injectedContext -and $injectedContext.Trim() -ne "") {
    $originalTask = $Task
    $Task = "$Task`n`n$injectedContext"
    Write-Host "[inject] Memory context appended to task"
  } else {
    Write-Host "[inject] No relevant memories found"
  }
}

# ── 记录 trace 会话 ──
if (Test-Path $memScript) {
  & $memScript -Action trace -TraceID $TraceID -ParentTraceID $ParentTraceID -Task $Task -Agent "MAOP-loop" 2>&1 | Out-Null
}

# ── Load rules + timeouts (Python bridge — unified) ──
$rules = @{}
$rulesData = Invoke-ConfigBridge "--section rules"
if ($rulesData) {
  $rules.retry_attempts  = if ($rulesData.max_retries) { [int]$rulesData.max_retries } else { 1 }
  $rules.retry_backoff   = if ($rulesData.retry_backoff_ms) { [int]$rulesData.retry_backoff_ms } else { 2000 }
  $rules.default_timeout = if ($rulesData.timeout_s) { [int]$rulesData.timeout_s } else { 120 }
}

$maxAttempts = if ($rules.retry_attempts) { $rules.retry_attempts } else { 1 }
$retryBackoff = if ($rules.retry_backoff) { $rules.retry_backoff } else { 2000 }
$defaultTimeout = if ($rules.default_timeout) { $rules.default_timeout } else { 120 }

# ── Agent timeout map (Python bridge) ──
$agentTimeoutMap = @{}
$agentsData = Invoke-ConfigBridge "--section agents"
if ($agentsData) {
  foreach ($a in $agentsData) {
    if ($a.timeout_s) { $agentTimeoutMap[$a.name] = [int]$a.timeout_s }
  }
}

# ════════════════════════════════════════
# Helper: try-parse JSON safely
# ════════════════════════════════════════
function SafeFromJson($raw) {
  if (-not $raw) { return $null }
  try { return ($raw | ConvertFrom-Json) } catch { return $null }
}

# 过滤 & powershell 2>&1 的输出，将 ErrorRecord 转为字符串后只保留非日志行
function Filter-Output($raw) {
  (@($raw) | ForEach-Object { "$_" } | Where-Object { $_ -notmatch '^\[MAOP-' -and $_.Trim() -ne '' }) -join "`n"
}

# ════════════════════════════════════════
# Helper: agent health check (light)
# ════════════════════════════════════════
function Test-AgentAlive($agentName) {
  # Fast check: verify the agent CLI binary exists in PATH.
  # This catches "agent not installed" without a full healthcheck probe.
  # Use ./harness/healthcheck.ps1 separately for manual deep checks.
  $agentsData = Invoke-ConfigBridge "--agent $agentName"
  if (-not $agentsData -or $agentsData.error) { return $false }
  $driver = "$($agentsData.driver)"
  if ($driver -eq "wrapper") { return $true }
  $cli = "$($agentsData.cli)"
  if (-not $cli) { return $false }
  $cliName = ($cli -split '\s+')[0]
  $found = Get-Command $cliName -ErrorAction SilentlyContinue
  return [bool]$found
}

# ════════════════════════════════════════
# Routing table (Python bridge — config-driven)
# ════════════════════════════════════════
$routingData = Invoke-ConfigBridge "--section routing" -Critical
$routingTable = @{}
foreach ($prop in $routingData.PSObject.Properties) {
  $entry = $prop.Value
  $routingTable[$prop.Name] = @{
    primary  = $entry.primary
    fallback = $entry.fallback
    tertiary = $entry.tertiary
  }
}

# ════════════════════════════════════════
# Phase 1: PLAN
# ════════════════════════════════════════
Write-Host "`n--- Phase 1: Plan ---"
$planResult = $null
$planError = $null
$routingKey = ""
try {
  $planScript = Join-Path $ScriptDir "MAOP-plan.ps1"
  if (-not (Test-Path $planScript)) { throw "MAOP-plan.ps1 not found" }
  $planAll = & $planScript -Task $Task -WorkDir $WorkDir -RoutingKey $routingKey 2>&1
  $planJsonLines = Filter-Output($planAll)
  try {
    $planResult = ($planJsonLines | ConvertFrom-Json)
  } catch {
    throw "Plan JSON parse failed: $($_.Exception.Message). Filtered=[$($planJsonLines.Substring(0,[math]::Min(100,$planJsonLines.Length)))]"
  }
} catch {
  $planError = $_.Exception.Message
  Write-Warning "Plan phase failed: $planError"
  $planResult = @{
    phase = "plan"; task = $Task; workdir = $WorkDir
    selected_agent = "claude"; routing_key = "chat"; gates = @("content-safety")
    budget = @{ timeout_s = $defaultTimeout; max_retries = 1 }
  }
}

$selectedAgent = $planResult.selected_agent
if (-not $selectedAgent) { $selectedAgent = "claude" }
$routingKey = if ($planResult.routing_key) { $planResult.routing_key } else { $routingKey }
$timeout = if ($planResult.budget -and $planResult.budget.timeout_s) { $planResult.budget.timeout_s } else { $defaultTimeout }

# Build fallback chain from routing table
$fallbackChain = @($selectedAgent)
$route = $routingTable[$routingKey]
if ($route) {
  foreach ($level in @("primary","fallback","tertiary")) {
    if ($route[$level] -and $route[$level] -ne $selectedAgent -and $fallbackChain -notcontains $route[$level]) {
      $fallbackChain += $route[$level]
    }
  }
}

Write-Host "Selected agent: $selectedAgent"
Write-Host "Routing key: $routingKey"
Write-Host "Fallback chain: $($fallbackChain -join ' → ')"

# ── Save Plan checkpoint ──
if ($PEV_HasSQLite) {
  $planCheckpoint = @{
    phase = "plan-done"
    task = $Task
    routing_key = $routingKey
    selected_agent = $selectedAgent
    fallback_chain = @($fallbackChain)
    budget = $planResult.budget
    timestamp = (Get-Date -Format "o")
  }
  try { Save-DbCheckpoint -Agent "MAOP-loop" -Task $TraceID -Phase "plan-done" -State $planCheckpoint | Out-Null }
  catch { Write-Warning "SQLite plan checkpoint save failed: $_" }
}

# ════════════════════════════════════════
# Phase 2: EXECUTE (with iterative loop + routing fallback)
# ════════════════════════════════════════
Write-Host "`n--- Phase 2: Execute ---"
$execScript = Join-Path $ScriptDir "MAOP-execute.ps1"
$cycleResult = $null
$cycleAgent = $null
$aliveAgents = @()
foreach ($a in $fallbackChain) {
  if (Test-AgentAlive $a) { $aliveAgents += $a }
}

if ($aliveAgents.Count -eq 0) {
  Write-Warning "No alive agents in fallback chain, falling back to default: $selectedAgent"
  $aliveAgents = @($selectedAgent)
}
if ($Retry) { $attemptOrder = $aliveAgents }
else { $attemptOrder = @($aliveAgents[0]) }

# ── 迭代循环配置 ──
$iterativeMaxAttempts = 3
$iterativeBackoffMs = 2000

for ($attempt = 0; $attempt -lt $attemptOrder.Count; $attempt++) {
  $agent = $attemptOrder[$attempt]
  Write-Host "Attempt $($attempt+1)/$($attemptOrder.Count): $agent (timeout: ${timeout}s)"

  if ($attempt -gt 0) {
    Write-Host "  Waiting ${retryBackoff}ms before retry..."
    Start-Sleep -Milliseconds $retryBackoff
  }

  # ── 同 agent 迭代重试 ──
  for ($iter = 0; $iter -lt $iterativeMaxAttempts; $iter++) {
    if ($iter -gt 0) {
      Write-Host "  Iterative retry $($iter+1)/$iterativeMaxAttempts for $agent..."
      Start-Sleep -Milliseconds $iterativeBackoffMs
    }

    try {
      $execAll = & $execScript -Agent $agent -Task $Task -RoutingKey $routingKey -WorkDir $WorkDir -TimeoutSeconds $timeout -TraceID $TraceID 2>&1
      $result = SafeFromJson((Filter-Output($execAll)))
      if (-not $result) {
        Write-Warning "  $agent returned invalid JSON, treating as failure"
        continue
      }
      $cycleResult = $result
      $cycleAgent = $agent

      if ($result.exit_code -eq 0) {
        Write-Host "  $agent SUCCEEDED (exit: 0, duration: $($result.duration_ms)ms)"
        break
      } else {
        Write-Host "  $agent FAILED (exit: $($result.exit_code))"
        if ($result.error) { Write-Host "  Error: $($result.error)" }
      }
    } catch {
      Write-Warning "  $agent threw exception: $($_.Exception.Message)"
    }

    if ($cycleResult -and $cycleResult.exit_code -eq 0) { break }
  }

  if ($cycleResult -and $cycleResult.exit_code -eq 0) { break }
}

if (-not $cycleResult) {
  Write-Error "All agents failed. No result."
  $cycleResult = @{ agent = "none"; task = $Task; exit_code = -1; error = "All agents failed"; stdout = $null; stderr = $null }
  $cycleAgent = "none"
}

# ── Save Execute checkpoint ──
if ($PEV_HasSQLite) {
  $execCheckpoint = @{
    phase = "execute-done"
    task = $Task
    routing_key = $routingKey
    selected_agent = $cycleAgent
    exit_code = $cycleResult.exit_code
    error = $cycleResult.error
    duration_ms = $cycleResult.duration_ms
    timestamp = (Get-Date -Format "o")
  }
  try { Save-DbCheckpoint -Agent "MAOP-loop" -Task $TraceID -Phase "execute-done" -State $execCheckpoint | Out-Null }
  catch { Write-Warning "SQLite execute checkpoint save failed: $_" }
}

# ════════════════════════════════════════
# Phase 3: VERIFY (with feedback loop)
# ════════════════════════════════════════
Write-Host "`n--- Phase 3: Verify ---"
if ($SkipVerify) {
  Write-Warning "[MAOP-loop] Verify phase SKIPPED via -SkipVerify flag. Result marked as 'skipped', NOT 'passed'."
  Write-PevLog -Phase "verify" -Level "WARN" -Message "Verify skipped by -SkipVerify flag" -Data @{ skipped = $true; passed = "skipped" }
  $verifyResult = @{ phase = "verify"; passed = "skipped"; summary = "Skipped by -SkipVerify flag (not verified)"; gates = @() }
  $verifyJson = $verifyResult | ConvertTo-Json
} else {
  $verifyScript = Join-Path $ScriptDir "MAOP-verify.ps1"
  try {
    $verifyAll = & $verifyScript -PlanJson ($planResult | ConvertTo-Json -Depth 5) -ResultJson ($cycleResult | ConvertTo-Json -Depth 3) -WorkDir $WorkDir 2>&1
    $verifyJson = Filter-Output($verifyAll)
  } catch {
    Write-Warning "[MAOP-loop] Verify phase EXCEPTION: $($_.Exception.Message)"
    Write-PevLog -Phase "verify" -Level "ERROR" -Message "Verify script threw exception" -Data @{ exception = $_.Exception.Message; type = $_.Exception.GetType().Name }
    $verifyResult = @{ phase = "verify"; passed = $false; summary = "Verify error: $($_.Exception.Message)"; gates = @() }
    $verifyJson = $verifyResult | ConvertTo-Json
  }
}
$verify = SafeFromJson($verifyJson)
if (-not $verify) {
  Write-Warning "[MAOP-loop] Verify output JSON parse FAILED. Raw output (first 200 chars): $($verifyJson.Substring(0,[math]::Min(200,$verifyJson.Length)))"
  Write-PevLog -Phase "verify" -Level "ERROR" -Message "Verify JSON parse error — treating as verification failure" -Data @{ raw_preview = $verifyJson.Substring(0,[math]::Min(200,$verifyJson.Length)) }
  $verify = @{ phase = "verify"; passed = $false; summary = "Verify JSON parse error — output unparseable, treated as failure"; gates = @() }
}

# ── 反馈循环: verify 失败后自动重走 Plan→Execute→Verify ──
$feedbackMaxCycles = 2
$feedbackCycle = 0
while (-not $verify.passed -and $feedbackCycle -lt $feedbackMaxCycles -and -not $SkipVerify) {
  $feedbackCycle++
  Write-Host "`n--- Feedback Loop ${feedbackCycle}/${feedbackMaxCycles}: Re-planning... ---"
  $feedbackTask = "修正: $($verify.summary) | 原始任务: $Task"

  try {
    $planAll2 = & $planScript -Task $feedbackTask -WorkDir $WorkDir -RoutingKey $routingKey 2>&1
    $planResult = (Filter-Output($planAll2) | ConvertFrom-Json)
    if (-not $planResult) { $planResult = @{ selected_agent = "claude"; routing_key = "chat"; budget = @{ timeout_s = $defaultTimeout } } }
  } catch {
    $planResult = @{ selected_agent = "claude"; routing_key = "chat"; budget = @{ timeout_s = $defaultTimeout } }
  }

  $selectedAgent = $planResult.selected_agent
  $routingKey = if ($planResult.routing_key) { $planResult.routing_key } else { "chat" }

  try {
    $execAll2 = & $execScript -Agent $selectedAgent -Task $feedbackTask -RoutingKey $routingKey -WorkDir $WorkDir -TimeoutSeconds $timeout -TraceID $TraceID 2>&1
    $cycleResult = SafeFromJson((Filter-Output($execAll2)))
    $cycleAgent = $selectedAgent
  } catch {
    $cycleResult = @{ agent = $selectedAgent; exit_code = -1; error = $_.Exception.Message }
  }

  try {
    $verifyAll2 = & $verifyScript -PlanJson ($planResult | ConvertTo-Json -Depth 5) -ResultJson ($cycleResult | ConvertTo-Json -Depth 3) -WorkDir $WorkDir 2>&1
    $verifyJson = Filter-Output($verifyAll2)
  } catch {
    Write-Warning "[MAOP-loop] Feedback loop verify EXCEPTION (cycle $feedbackCycle): $($_.Exception.Message)"
    Write-PevLog -Phase "verify-feedback" -Level "ERROR" -Message "Verify script threw exception in feedback loop" -Data @{ cycle = $feedbackCycle; exception = $_.Exception.Message }
    $verifyJson = "{ `"phase`": `"verify`", `"passed`": false, `"summary`": `"Feedback loop verify error: $($_.Exception.Message)`" }"
  }
  $verify = SafeFromJson($verifyJson)
  if (-not $verify) {
    Write-Warning "[MAOP-loop] Feedback loop verify JSON parse FAILED (cycle $feedbackCycle). Raw: $($verifyJson.Substring(0,[math]::Min(200,$verifyJson.Length)))"
    Write-PevLog -Phase "verify-feedback" -Level "ERROR" -Message "Verify JSON parse error in feedback loop" -Data @{ cycle = $feedbackCycle; raw_preview = $verifyJson.Substring(0,[math]::Min(200,$verifyJson.Length)) }
    $verify = @{ phase = "verify"; passed = $false; summary = "Feedback loop verify JSON parse error (cycle $feedbackCycle)" }
  }
  Write-Host "  Feedback cycle ${feedbackCycle}: verify passed=$($verify.passed)"
}
if ($feedbackCycle -gt 0) { Write-Host "--- Feedback Loop Complete ($feedbackCycle cycle(s)) ---" }

# ════════════════════════════════════════
# Phase 4: STORE → MEMORY + METRICS
# ════════════════════════════════════════
$memScript = Join-Path $HarnessDir "memory.ps1"
if (Test-Path $memScript) {
  $tags = $routingKey
  $exitInfo = if ($cycleResult.exit_code -eq 0) { "OK" } else { "FAIL:$($cycleResult.exit_code)" }
  $contentPreview = if ($cycleResult.stdout) { ($cycleResult.stdout -replace "`n"," ").Substring(0,[math]::Min(200,($cycleResult.stdout -replace "`n"," ").Length)) } else { "(no output)" }
  $content = "[$exitInfo] $contentPreview"
  & $memScript -Action store -Agent $cycleAgent -Task $Task -Tags $tags -Content $content -TraceID $TraceID -ToolExitCode $cycleResult.exit_code -ToolDurationMs $cycleResult.duration_ms *>&1 | Out-Null

  # Auto-prune: TTL=30d, keep=50/agent
  try {
    & $memScript -Action prune -TtlDays 30 -Top 50 -Quiet *>&1 | Out-Null
  } catch {
    Write-Verbose "[MAOP-loop] Memory auto-prune failed: $_"
  }
}

$obsScript = Join-Path (Split-Path $HarnessDir -Parent) "src\observability.ps1"
if (Test-Path $obsScript) {
  & $obsScript -Action "metrics" *>&1 | Out-Null
}

# ════════════════════════════════════════
# Phase 5: EVOLVE → analyze + auto-apply + sync
# ════════════════════════════════════════
$evolveScript = Join-Path $HarnessDir "evolve.ps1"
$bridgeScript = Join-Path $HarnessDir "evolve-bridge.ps1"
if (Test-Path $evolveScript) {
  try {
    $evolveResult = & $evolveScript -Action "analyze" *>&1
    Write-Host "`n--- Evolution Analysis ---"
    $evolveSuggestions = $evolveResult | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | Select-Object -Last 1
    $suggestionCount = 0
    if ($evolveSuggestions -and $evolveSuggestions -is [string]) {
      try { $parsed = $evolveSuggestions | ConvertFrom-Json; $suggestionCount = $parsed.Count } catch {}
    }

    # 自动应用可自动修复的建议
    if ($suggestionCount -gt 0) {
      $autoApplied = & $evolveScript -Action apply -AutoApply *>&1 | Select-Object -Last 1
      Write-Host "[evolve] $suggestionCount suggestion(s), auto-applied changes (if any)"
    }

    # 同步到 F:\memory\evolve\
    if (Test-Path $bridgeScript) {
      & $bridgeScript -Action sync *>&1 | Out-Null
      Write-Host "[evolve] Synced to F:\memory\evolve\"
    }

    # Auto-promote prompts if data available
    & $evolveScript -Action "promote" *>&1 | Out-Null
  } catch {
    Write-Host "[evolve] Phase error: $($_.Exception.Message)"
  }
}

# ════════════════════════════════════════
# Final Report
# ════════════════════════════════════════
$cycleEnd = Get-Date
$totalMs = [math]::Round(($cycleEnd - $cycleStart).TotalMilliseconds)

Write-Host "`n╔══════════════════════════════════════════╗"
Write-Host "║           MAOP Cycle Complete            ║"
Write-Host "╠══════════════════════════════════════════╣"
$displayAgent = if ($cycleAgent -is [string]) { $cycleAgent } else { "$cycleAgent" }
Write-Host "║ Agent:      $($displayAgent.PadRight(26))║"
Write-Host "║ Attempts:   $(($attempt+1).ToString().PadRight(25))║"
Write-Host "║ Exit code:  $(if ($cycleResult.exit_code -eq 0) { '0 (OK)'.PadRight(23) } else { "$($cycleResult.exit_code)".PadRight(23) })║"
Write-Host ("║ Duration:   ${totalMs}ms".PadRight(42) + "║")
Write-Host "║ Gates:      $(if ($verify.passed -eq 'skipped') { 'SKIPPED'.PadRight(22) } elseif ($verify.passed) { 'ALL PASS'.PadRight(22) } else { 'SOME FAILED'.PadRight(20) })║"
if ($verify.summary) { Write-Host ("║ $($verify.summary)".PadRight(43) + "║") }
Write-Host "╚══════════════════════════════════════════╝"

$finalResult = @{
  pev_cycle = @{
    start_time = $cycleStart.ToString("o")
    end_time   = $cycleEnd.ToString("o")
    duration_ms = $totalMs
    task       = $Task
    agent      = $cycleAgent
    attempts   = $attempt + 1
    routing_key = $routingKey
  }
  execution  = $cycleResult
  verification = $verify
}

# ── Cleanup SQLite checkpoint (cycle complete) ──
if ($PEV_HasSQLite) {
  try { Remove-DbCheckpoint -Agent "MAOP-loop" -Task $TraceID | Out-Null }
  catch { Write-Warning "SQLite checkpoint cleanup failed: $_" }
}

return ($finalResult | ConvertTo-Json -Depth 5)
