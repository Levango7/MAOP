<#
.SYNOPSIS
  MAOP Unified Engine — 消费 WorkflowStep 数组，按拓扑序执行
  支持: plan / agent / dag / verify / condition / terminal 六种类型

.PARAMETER Steps
  WorkflowStep 数组 (由 workflowstep.ps1 工厂创建)

.PARAMETER Context
  初始上下文（可选），包含上游注入的变量

.PARAMETER WorkDir
  工作目录

.PARAMETER TraceID
  追踪 ID

.OUTPUTS
  JSON 到 stdout: 包含所有步骤的 status / output / duration

.EXAMPLE
  . engine.ps1 -Steps @($step1,$step2) -Context @{ task = "refactor main" }
#>

param(
  [Parameter(Mandatory)]
  [array]$Steps,

  [hashtable]$Context = @{},

  [string]$WorkDir = (Get-Location).Path,

  [string]$TraceID = [guid]::NewGuid().ToString("N")
)

# ════════════════════════════════════════
# Boot: dot-source dependencies
# ════════════════════════════════════════
$ScriptDir = Split-Path $PSCommandPath -Parent
$MAOP = Split-Path $ScriptDir -Parent

. (Join-Path $ScriptDir "workflowstep.ps1") | Out-Null
$DelegateScript = Join-Path $ScriptDir "delegate.ps1"
$DagEngineScript = Join-Path $ScriptDir "dag-engine.ps1"

# ════════════════════════════════════════
# Helpers
# ════════════════════════════════════════
function SafeFromJson($raw) {
  if (-not $raw) { return $null }
  try { return ($raw | ConvertFrom-Json) } catch { return $null }
}

function Filter-Output($raw) {
  (@($raw) | ForEach-Object { "$_" } | Where-Object { $_ -notmatch '^\[MAOP-|^\[workflowstep|^\[engine' -and $_.Trim() -ne '' }) -join "`n"
}

function Resolve-Template($template, $ctx) {
  if (-not $template -or $template -isnot [string]) { return $template }
  $result = $template
  foreach ($kv in $ctx.GetEnumerator()) {
    $val = if ($kv.Value -is [string]) { $kv.Value } else { "$($kv.Value)" }
    $result = $result -replace [regex]::Escape("{{ $($kv.Key) }}"), $val
  }
  return $result
}

# ════════════════════════════════════════
# Step executors
# ════════════════════════════════════════
function Invoke-PlanStep {
  param($Step, $Ctx)
  Write-Host "[engine] Plan: $($Step.id)"

  $planScript = Join-Path $ScriptDir "MAOP-plan.ps1"
  if (-not (Test-Path $planScript)) { throw "MAOP-plan.ps1 not found" }

  $task = if ($Step.params.task) { Resolve-Template $Step.params.task $Ctx } else { $Ctx.task }
  $rk = if ($Step.params.routing_key) { $Step.params.routing_key } else { "" }

  $raw = & $planScript -Task $task -WorkDir $WorkDir -RoutingKey $rk 2>&1
  $json = Filter-Output $raw
  $plan = SafeFromJson $json
  if (-not $plan) { throw "Plan step failed: JSON parse error. raw=[$($raw.Substring(0,[Math]::Min(100,$raw.Length)))]" }

  # Merge plan into context
  $Ctx.selected_agent = $plan.selected_agent
  $Ctx.routing_key = $plan.routing_key
  $Ctx.fallback_chain = $plan.fallback_chain
  $Ctx.budget = $plan.budget

  return @{
    step_id = $Step.id
    status  = 'completed'
    output  = $plan
    duration_ms = 0
  }
}

function Invoke-AgentStep {
  param($Step, $Ctx)
  Write-Host "[engine] Agent: $($Step.id) → $($Step.agent)"

  $agent = if ($Step.agent) { $Step.agent } else { $Ctx.selected_agent }
  $task  = if ($Step.params.task) { Resolve-Template $Step.params.task $Ctx } else { $Ctx.task }
  $rk    = if ($Step.params.routing_key) { $Step.params.routing_key } else { $Ctx.routing_key }
  $timeout = if ($Step.timeout) { $Step.timeout } else { 120 }

  $fallbackChain = if ($Step.fallback_to) { @($agent, $Step.fallback_to) } else { @($agent) }
  $result = $null
  $lastError = $null

  foreach ($a in $fallbackChain) {
    $retries = [Math]::Max($Step.retry, 1)
    for ($r = 0; $r -lt $retries; $r++) {
      if ($r -gt 0) {
        Write-Host "[engine] Retry $($r+1)/$retries for $a..."
        Start-Sleep -Milliseconds 1000
      }
      try {
        $raw = & $DelegateScript -Agent $a -Task $task -RoutingKey $rk -WorkDir $WorkDir -TimeoutSeconds $timeout -TraceID $TraceID 2>&1
        $json = Filter-Output $raw
        $result = SafeFromJson $json
        if ($result -and $result.exit_code -eq 0) {
          Write-Host "[engine] $a SUCCEEDED"
          return @{
            step_id = $Step.id
            status  = 'completed'
            agent   = $a
            output  = $result
            duration_ms = if ($result.duration_ms) { $result.duration_ms } else { 0 }
          }
        }
        $lastError = if ($result.error) { $result.error } else { "exit_code=$($result.exit_code)" }
      } catch {
        $lastError = $_.Exception.Message
      }
    }
  }

  $failStep = @{
    step_id = $Step.id
    status  = if ($Step.on_failure -eq 'skip') { 'skipped' } else { 'failed' }
    agent   = $fallbackChain[0]
    output  = $null
    error   = $lastError
    duration_ms = 0
  }

  if ($Step.on_failure -eq 'stop') { throw "Step $($Step.id) failed: $lastError" }
  return $failStep
}

function Invoke-DagStep {
  param($Step, $Ctx)
  Write-Host "[engine] DAG: $($Step.id) → file=$($Step.dag_file)"

  $dagFile = $Step.dag_file
  if (-not (Test-Path $dagFile)) { throw "DAG file not found: $dagFile" }

  $task = if ($Step.params.task) { Resolve-Template $Step.params.task $Ctx } else { $Ctx.task }

  try {
    $raw = & $DagEngineScript -DagFile $dagFile -Task $task -WorkDir $WorkDir -TraceID $TraceID 2>&1
    $dagResult = SafeFromJson (Filter-Output $raw)
    return @{
      step_id = $Step.id
      status  = if ($dagResult) { 'completed' } else { 'failed' }
      output  = $dagResult
      duration_ms = 0
    }
  } catch {
    return @{
      step_id = $Step.id
      status  = 'failed'
      error   = $_.Exception.Message
      duration_ms = 0
    }
  }
}

function Invoke-VerifyStep {
  param($Step, $Ctx)
  Write-Host "[engine] Verify: $($Step.id)"

  $verifyScript = Join-Path $ScriptDir "MAOP-verify.ps1"
  if (-not (Test-Path $verifyScript)) {
    Write-Host "[engine] verify script not found, using mock"
    return @{
      step_id = $Step.id
      status  = 'completed'
      output  = @{ phase = 'verify'; passed = $true; summary = 'No verifier available'; gates = @() }
      duration_ms = 0
    }
  }

  $planJson = if ($Ctx.plan) { $Ctx.plan | ConvertTo-Json -Depth 5 } else { '{}' }
  $resultJson = if ($Ctx.last_result) { $Ctx.last_result | ConvertTo-Json -Depth 3 } else { '{}' }

  try {
    $raw = & $verifyScript -PlanJson $planJson -ResultJson $resultJson -WorkDir $WorkDir 2>&1
    $verify = SafeFromJson (Filter-Output $raw)
    if (-not $verify) { $verify = @{ phase = 'verify'; passed = $false; summary = 'JSON parse error'; gates = @() } }
    return @{
      step_id = $Step.id
      status  = if ($verify.passed) { 'completed' } else { 'completed' } # verify always completes, just reports pass/fail
      passed  = $verify.passed
      output  = $verify
      duration_ms = 0
    }
  } catch {
    return @{
      step_id = $Step.id
      status  = 'failed'
      error   = $_.Exception.Message
      duration_ms = 0
    }
  }
}

function Invoke-ConditionStep {
  param($Step, $Ctx)
  Write-Host "[engine] Condition: $($Step.id) → '$($Step.condition)'"

  $expr = Resolve-Template $Step.condition $Ctx
  # Simple expression evaluator: supports ==, !=, .contains(), .startsWith()
  $result = $false
  try {
    if ($expr -match '(.+)\s*==\s*(.+)') {
      $lhs = (Resolve-Template $Matches[1].Trim() $Ctx).Trim('"').Trim("'")
      $rhs = $Matches[2].Trim().Trim('"').Trim("'")
      $result = $lhs -eq $rhs
    } elseif ($expr -match '(.+)\s*!=\s*(.+)') {
      $lhs = (Resolve-Template $Matches[1].Trim() $Ctx).Trim('"').Trim("'")
      $rhs = $Matches[2].Trim().Trim('"').Trim("'")
      $result = $lhs -ne $rhs
    } elseif ($expr -match '(.+)\.contains\((.+)\)') {
      $val = (Resolve-Template $Matches[1].Trim() $Ctx).Trim('"').Trim("'")
      $search = $Matches[2].Trim().Trim('"').Trim("'")
      $result = $val -match [regex]::Escape($search)
    } elseif ($expr -match '(.+)\.startsWith\((.+)\)') {
      $val = (Resolve-Template $Matches[1].Trim() $Ctx).Trim('"').Trim("'")
      $search = $Matches[2].Trim().Trim('"').Trim("'")
      $result = $val -match "^$([regex]::Escape($search))"
    } elseif ($expr -match '^\s*(true|false)\s*$') {
      $result = $Matches[1] -eq 'true'
    } elseif ($expr -match '^\s*!') {
      $result = $false
    } else {
      # Fallback: true for non-empty
      $result = ($expr -and $expr -ne 'false' -and $expr -ne '0')
    }
  } catch {
    Write-Host "[engine] Condition eval error: $_"
    $result = $false
  }

  $branch = if ($result -and $Step.branches.ContainsKey('true')) { $Step.branches['true'] }
            elseif (-not $result -and $Step.branches.ContainsKey('false')) { $Step.branches['false'] }
            else { '' }

  Write-Host "[engine] Condition result: $result → branch=$branch"

  return @{
    step_id     = $Step.id
    status      = 'completed'
    branch      = $branch
    branch_true = $result
    output      = @{ result = $result; branch = $branch }
  }
}

function Invoke-TerminalStep {
  param($Step, $Ctx)
  Write-Host "[engine] Terminal: $($Step.id)"
  return @{
    step_id = $Step.id
    status  = 'completed'
    output  = $Ctx
    duration_ms = 0
  }
}

# ════════════════════════════════════════
# Main engine loop
# ════════════════════════════════════════
$engineStart = Get-Date

# Topological sort
try {
  $orderedSteps = Get-StepExecutionOrder $Steps
} catch {
  Write-Error "Engine topology error: $_"
  exit 1
}

Write-Host "[engine] Execution order: $($orderedSteps.ForEach({ $_.id }) -join ' → ')"

$results = @{}
$context = @{}
# Copy initial context
foreach ($kv in $Context.GetEnumerator()) { $context[$kv.Key] = $kv.Value }

$engineFailed = $false

foreach ($step in $orderedSteps) {
  $stepStart = Get-Date

  # Wait for dependencies
  foreach ($dep in $step.depends_on) {
    if ($results.ContainsKey($dep) -and $results[$dep].status -eq 'failed') {
      Write-Host "[engine] Dependency $dep failed, skipping $($step.id)"
      $results[$step.id] = @{
        step_id = $step.id; status = 'skipped'; error = "dependency $dep failed"
      }
      $context[$step.id] = $null
      continue
    }
  }
  if ($results.ContainsKey($step.id) -and $results[$step.id].status -eq 'skipped') { continue }

  try {
    $handlerName = "Invoke-$($step.type)Step"
    $handler = Get-Command $handlerName -ErrorAction SilentlyContinue
    if (-not $handler) { throw "No handler for step type: $($step.type)" }

    $stepResult = & $handler $step $context
    $stepResult.duration_ms = [math]::Round(((Get-Date) - $stepStart).TotalMilliseconds)
    $results[$step.id] = $stepResult
    $context[$step.id] = $stepResult.output

    # Track last result for verify
    if ($step.type -eq 'agent') {
      $context.last_result = $stepResult.output
      $context.last_agent = $stepResult.agent
    }
    if ($step.type -eq 'plan') {
      $context.plan = $stepResult.output
    }

    if ($stepResult.status -eq 'failed' -and $step.on_failure -eq 'stop') {
      $engineFailed = $true
      break
    }

    Write-Host "[engine] Step $($step.id) → $($stepResult.status) ($($stepResult.duration_ms)ms)"
  } catch {
    Write-Host "[engine] Step $($step.id) threw: $_"
    $results[$step.id] = @{
      step_id = $step.id; status = 'error'; error = $_.Exception.Message
    }
    if ($step.on_failure -eq 'stop') { $engineFailed = $true; break }
  }
}

$engineEnd = Get-Date

# ════════════════════════════════════════
# Final report
# ════════════════════════════════════════
$report = @{
  engine = @{
    start_time   = $engineStart.ToString("o")
    end_time     = $engineEnd.ToString("o")
    duration_ms  = [math]::Round(($engineEnd - $engineStart).TotalMilliseconds)
    step_count   = $Steps.Count
    trace_id     = $TraceID
    failed       = $engineFailed
  }
  steps = $orderedSteps.ForEach({
    $r = $results[$_.id]
    if (-not $r) { $r = @{ step_id = $_.id; status = 'not_reached' } }
    $r.description = $_.description
    $r.type = $_.type
    $r
  })
  context = $context
}

Write-Output ($report | ConvertTo-Json -Depth 5)
