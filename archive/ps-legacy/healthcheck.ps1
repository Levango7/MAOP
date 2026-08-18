param(
  [string]$Agent = "all",
  [string]$HarnessDir = (Split-Path $PSCommandPath -Parent)
)

# MAOP Loop — Agent Health Check
# Probes each agent's availability with a minimal prompt.

$ProjectRoot = Split-Path $HarnessDir -Parent
$delegateScript = Join-Path $ProjectRoot "delegate.ps1"

$allAgents = @(
  @{ name = "claude";    testTask = "Say OK" }
  @{ name = "kimi";      testTask = "OK" }
  @{ name = "codex";     testTask = "OK" }
  @{ name = "autoclaw";  testTask = "OK" }
  @{ name = "qwenpaw";   testTask = "reply OK" }
  @{ name = "qoder";     testTask = "OK" }
  @{ name = "mimo";      testTask = "OK" }
  @{ name = "openclaw";  testTask = "Say OK" }
  @{ name = "hermes";    testTask = "OK" }
  @{ name = "mavis";     testTask = "say OK" }
  @{ name = "codewhale"; testTask = "say OK" }
  @{ name = "kilo";      testTask = "say OK" }
)

# ── Load last state for adaptive timeout ──
$lastReportFile = Join-Path (Join-Path $HarnessDir "logs") "healthcheck_latest.json"
$lastState = @{}
if (Test-Path $lastReportFile) {
  $lastData = Get-Content $lastReportFile -Raw | ConvertFrom-Json
  foreach ($entry in $lastData) { $lastState[$entry.agent] = $entry.status }
}

function Get-AgentTimeout($agentName) {
  $lastStatus = $lastState[$agentName]
  if ($lastStatus -eq "alive") { return 6 }   # alive recently → normal probe
  return 3                                       # dead/unknown → quick probe
}

function Test-Agent {
  param($Name, $TestTask, [int]$TimeoutSeconds = 6)
  if ($lastState[$Name] -eq "alive") { $TimeoutSeconds = 6 } else { $TimeoutSeconds = 3 }
  if (-not (Test-Path $delegateScript)) {
    return @{ agent = $Name; status = "error"; detail = "delegate.ps1 not found" }
  }

  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  try {
    $json = & $delegateScript -Agent $Name -Task $TestTask -TimeoutSeconds $TimeoutSeconds
    $result = $json | ConvertFrom-Json
    $sw.Stop()
    $alive = $result.exit_code -eq 0 -and -not [string]::IsNullOrWhiteSpace($result.stdout)
    return @{
      agent  = $Name
      status = if ($alive) { "alive" } else { "dead" }
      code   = $result.exit_code
      ms     = $sw.ElapsedMilliseconds
      detail = if ($alive) { "Responded in $($sw.ElapsedMilliseconds)ms" } else { "Exit code: $($result.exit_code)" }
    }
  } catch {
    $sw.Stop()
    return @{ agent = $Name; status = "error"; detail = $_.Exception.Message; ms = $sw.ElapsedMilliseconds }
  }
}

$results = @()
if ($Agent -eq "all") {
  Write-Host "=== Agent Health Check ==="
  Write-Host "Probing $($allAgents.Count) agents in parallel...`n"

  # Use Runspace pool for reliable in-process parallelism
  $pool = [RunspaceFactory]::CreateRunspacePool(1, $allAgents.Count)
  $pool.Open()
  $psInstances = @()
  $handles = @()

  foreach ($a in $allAgents) {
    $ps = [PowerShell]::Create()
    $ps.RunspacePool = $pool
    $null = $ps.AddScript({
      param($Name, $Task, $Timeout, $HarnessDir)
      $sw = [System.Diagnostics.Stopwatch]::StartNew()
      try {
        $output = ""
        switch -Wildcard ($Name) {
          "hermes*"  { $output = & hermes -z "$Task" chat 2>&1 | Out-String }
          "openclaw*" { $output = & openclaw agent --local --message "$Task" --json --agent main 2>&1 | Out-String }
          "claude*" { $output = & claude -p "$Task" --model step-3.7-flash 2>&1 | Out-String }
          "codewhale*" { $output = & codewhale exec -p "$Task" --yolo --model stepfun/step-3.7-flash 2>&1 | Out-String }
          "kilo*" { $output = & kilo run "$Task" --auto --format json -m stepfun/step-3.7-flash 2>&1 | Out-String }
          "kimi*" { $output = & powershell -NoProfile -Command "echo '$($Task -replace "'","''")' | kimi --print" 2>&1 | Out-String }
          "codex*" { $output = & powershell -NoProfile -Command "`$env:CODEX_BASE_URL='http://127.0.0.1:18080/v1'; echo '$($Task -replace "'","''")' | codex exec --skip-git-repo-check --model step-3.7-flash" 2>&1 | Out-String }
          "autoclaw*" { $output = & powershell -NoProfile -Command "echo '$($Task -replace "'","''")' | autoclaw -n -y" 2>&1 | Out-String }
          "qoder*" {
            # SECURITY: never hardcode the QoderCN Personal Access Token. The token
            # must be supplied via the MAOP_QODERCN_PAT environment variable, sourced
            # from a secret manager. The previously hardcoded token
            # (pt-zDIDIXoMabdSC3DlbiDcEK0f_019f1025-...) is now considered leaked and
            # MUST be rotated in the QoderCN account.
            $qoderToken = $env:MAOP_QODERCN_PAT
            if (-not $qoderToken) {
              throw "MAOP_QODERCN_PAT environment variable (QoderCN PAT) is not set — refusing to run with a hardcoded token."
            }
            $output = & powershell -NoProfile -Command "`$env:QODERCN_PERSONAL_ACCESS_TOKEN = '$qoderToken'; qoderclicn -q -p '$($Task -replace "'","''")' --output-format json --yolo" 2>&1 | Out-String
          }
          "mimo*" { $output = & powershell -NoProfile -Command "echo '$($Task -replace "'","''")' | mimo run -" 2>&1 | Out-String }
          "mavis*" { 
            $m = "C:\Users\winge\.mavis\bin\mavis.cmd"
            $raw = & $m session new coder --from root --prompt $Task 2>&1 | Out-String
            $sid = if ($raw -match '(mvs_[a-z0-9]+)') { $Matches[1] } else { $null }
            if ($sid) { Start-Sleep -Seconds ([math]::Min([int]$Timeout-5,10)); $msgs = & $m session messages $sid 2>&1 | Out-String; & $m session close $sid 2>&1 | Out-Null; $output = $msgs }
            else { $output = $raw }
          }
          "qwenpaw*" {
            $qpInstr = Join-Path ([System.IO.Path]::GetTempPath()) "qp-instr.md"
            $Task | Out-File -FilePath $qpInstr -Encoding utf8
            try { $output = & "F:\Program Files (x86)\QwenPaw\qwenpaw.cmd" task --instruction $qpInstr --timeout=10 --no-guard 2>&1 | Out-String }
            finally { Remove-Item $qpInstr -Force -ErrorAction SilentlyContinue }
          }
          default {
            # Fallback: delegate.ps1 -Direct
            $script = Join-Path (Split-Path $PSCommandPath -Parent) "..\delegate.ps1"
            $output = & powershell -NoProfile -File $script -Agent $Name -Task $Task -TimeoutSeconds $Timeout -Direct 2>&1 | Out-String
          }
        }
        $clean = ($output -replace '\x1b\[[0-9;]*m', '').Trim()
        $alive = -not [string]::IsNullOrWhiteSpace($clean)
        return @{ agent = $Name; status = if ($alive) { "alive" } else { "dead" }; code = 0; ms = $sw.ElapsedMilliseconds; detail = if ($alive) { "Responded in $($sw.ElapsedMilliseconds)ms" } else { "No output" } }
      } catch {
        return @{ agent = $Name; status = "error"; detail = $_.Exception.Message; ms = $sw.ElapsedMilliseconds }
      }
    })
    $timeoutSec = if ($a.name -eq "mavis") { 20 } else { 8 }
$null = $ps.AddParameters(@{ Name = $a.name; Task = $a.testTask; Timeout = $timeoutSec; HarnessDir = $HarnessDir })
    $psInstances += $ps
    $handles += $ps.BeginInvoke()
  }

  # Wait for all with 60s timeout
  $timeout = 60000
  $swAll = [Diagnostics.Stopwatch]::StartNew()
  $remaining = @($handles)
  while ($remaining.Count -gt 0 -and $swAll.ElapsedMilliseconds -lt $timeout) {
    $remaining = $handles | Where-Object { -not $_.IsCompleted }
    if ($remaining.Count -gt 0) { Start-Sleep -Milliseconds 200 }
  }

  $results = @()
  for ($i = 0; $i -lt $psInstances.Count; $i++) {
    try {
      if ($handles[$i].IsCompleted) {
        $results += $psInstances[$i].EndInvoke($handles[$i])
      } else {
        $handles[$i].AsyncWaitHandle.Close()
        $results += @{ agent = $allAgents[$i].name; status = "error"; detail = "timeout"; ms = $swAll.ElapsedMilliseconds }
      }
    } catch {
      $results += @{ agent = $allAgents[$i].name; status = "error"; detail = $_.Exception.Message; ms = $swAll.ElapsedMilliseconds }
    } finally {
      $psInstances[$i].Dispose()
    }
  }
  $pool.Close()
  $pool.Dispose()

  Write-Host ""
  foreach ($r in ($results | Sort-Object agent)) {
    $icon = switch ($r.status) { "alive" { "[OK]" } "dead" { "[DOWN]" } default { "[ERR]" } }
    Write-Host "$icon $($r.agent.PadRight(12)) $($r.detail)"
  }

  $timedOut = $allAgents.Count - $results.Count
  if ($timedOut -gt 0) {
    Write-Host "[TIM] (timed-out: $timedOut)"
  }

  $aliveCount = ($results | Where-Object { $_.status -eq "alive" }).Count
  $deadCount  = ($results | Where-Object { $_.status -eq "dead" }).Count
  $errCount   = ($results | Where-Object { $_.status -eq "error" }).Count
  Write-Host "`n--- Summary ---"
  Write-Host "Alive: $aliveCount / Dead: $deadCount / Error: $errCount / Total: $($allAgents.Count)"

  # ── Healthcheck + Evolve 联动：DOWN agent 自动建实验 ──
  $deadAgents = $results | Where-Object { $_.status -eq "dead" -or $_.status -eq "error" }
  if ($deadAgents.Count -gt 0) {
    $stateFile = "F:\memory\evolve\state.json"
    if (Test-Path $stateFile) {
      $state = Get-Content $stateFile -Raw | ConvertFrom-Json
      if (-not $state.active_experiments) { $state.active_experiments = @() }
      foreach ($da in $deadAgents) {
        $existing = $state.active_experiments | Where-Object { $_.name -match $da.agent -and $_.status -ne "COMPLETED" }
        if (-not $existing) {
          $state.active_experiments += @{
            id = "hc-$(Get-Date -Format 'yyyyMMdd-HHmmss')-$($da.agent)"
            name = "healthcheck: $($da.agent) 状态异常"
            status = "PENDING"
            created = (Get-Date -Format "o")
            severity = "high"
            detail = "$($da.agent): $($da.detail)"
            suggestion = "检查 $($da.agent) CLI 状态，确认是否需要更新 delegate.ps1 或从 agents.yaml 移除"
          }
          Write-Host "[evolve] Created experiment for down agent: $($da.agent)"
        }
      }
      $state | ConvertTo-Json -Depth 3 | Set-Content $stateFile
    }
  }
} else {
  $target = @($allAgents | Where-Object { $_.name -eq $Agent })
  if ($target.Count -eq 0) { $target = @(@{ name = $Agent; testTask = "Say OK" }) }
  $r = Test-Agent $target[0].name $target[0].testTask
  $results += $r
}

$reportFile = Join-Path (Join-Path $HarnessDir "logs") "healthcheck_latest.json"
$reportDir = Split-Path $reportFile -Parent
if (-not (Test-Path $reportDir)) { New-Item -ItemType Directory -Force -Path $reportDir | Out-Null }
$results | ConvertTo-Json -Depth 3 | Set-Content $reportFile

# ── Write delegation records for evolve analysis ──
$obsScript = Join-Path (Split-Path $HarnessDir -Parent) "src\observability.ps1"
if (Test-Path $obsScript) {
  foreach ($r in $results) {
    $resultObj = @{
      exit_code = if ($r.status -eq "alive") { 0 } elseif ($r.status -eq "dead") { 1 } else { -1 }
      duration_ms = if ($r.ms) { $r.ms } else { 0 }
      stdout = if ($r.detail) { $r.detail } else { "" }
    }
    & $obsScript -Action "log" -Agent $r.agent -Task "healthcheck-probe" -ResultJson ($resultObj | ConvertTo-Json) -RoutingKey "healthcheck" 2>&1 | Out-Null
  }
  Write-Host "[healthcheck] Wrote $($results.Count) delegation records for evolve"
}

return ($results | ConvertTo-Json -Depth 3)

