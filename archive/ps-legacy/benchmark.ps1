param(
  [ValidateSet("benchmark","ab-test","list")]
  [string]$Action = "list",
  [string]$TestSuite,
  [string]$AgentA,
  [string]$AgentB,
  [string]$RoutingKey = "codegen",
  [int]$RunsPerAgent = 3,
  [int]$TimeoutSeconds = 60
)

<#
.SYNOPSIS
  回归测试基准集 + A/B 实验框架
.DESCRIPTION
  benchmark: 跑基准测试用例，输出各 agent 分数
  ab-test:   两个 agent 对比测试，统计成功率和耗时
  list:      列出可用基准用例
#>

$HarnessDir = Split-Path $PSCommandPath -Parent
$DelegateScript = Join-Path (Split-Path $HarnessDir -Parent) "delegate.ps1"
$BenchDir = Join-Path $HarnessDir "benchmarks"
if (-not (Test-Path $BenchDir)) { New-Item -ItemType Directory -Force -Path $BenchDir | Out-Null }

# ── 基准测试用例库 ──
$TestSuites = @{
  "quick" = @(
    @{ task = "say OK"; routing = "chat" }
    @{ task = "reply: hello"; routing = "chat" }
  )
  "codegen" = @(
    @{ task = "write a PowerShell function that adds two numbers"; routing = "codegen" }
    @{ task = "write a hello world in Python"; routing = "codegen" }
  )
  "search" = @(
    @{ task = "find all .ps1 files in current directory"; routing = "search" }
  )
  "fileops" = @(
    @{ task = "create a file called test.txt with content hello"; routing = "fileops" }
  )
  "full" = @()  # 自动组合所有套件
}

# 构建 full 套件
$fullCases = @()
foreach ($k in $TestSuites.Keys) {
  if ($k -ne "full") { $fullCases += $TestSuites[$k] }
}
$TestSuites["full"] = $fullCases

# ── 列出可用套件 ──
function Invoke-List {
  Write-Host "=== Benchmark Test Suites ==="
  foreach ($k in $TestSuites.Keys) {
    Write-Host "  $k : $($TestSuites[$k].Count) cases"
  }
  Write-Host "`nUsage: benchmark.ps1 -Action benchmark -TestSuite quick"
  Write-Host "       benchmark.ps1 -Action ab-test -AgentA claude -AgentB kilo -RoutingKey codegen -RunsPerAgent 3"
}

# ── 跑基准测试 ──
function Invoke-Benchmark {
  $suite = $TestSuites[$TestSuite]
  if (-not $suite) { Write-Host "[bench] Unknown suite: $TestSuite"; return }
  Write-Host "=== Benchmark: $TestSuite ($($suite.Count) cases) ==="

  $results = @()
  $totalPass = 0
  $totalTime = 0

  foreach ($case in $suite) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $r = & $DelegateScript -Agent "claude" -Task $case.task -RoutingKey $case.routing -TimeoutSeconds $TimeoutSeconds 2>&1
    $sw.Stop()
    $parsed = $r | ConvertFrom-Json
    $passed = $parsed.exit_code -eq 0
    if ($passed) { $totalPass++ }
    $totalTime += $sw.ElapsedMilliseconds
    $icon = if ($passed) { "✅" } else { "❌" }
    Write-Host "  $icon $($case.task) — $($sw.ElapsedMilliseconds)ms"
    $results += @{
      task = $case.task; routing = $case.routing
      passed = $passed; duration_ms = $sw.ElapsedMilliseconds
    }
  }

  Write-Host "`n--- Benchmark Result ---"
  Write-Host "Pass: $totalPass/$($suite.Count) | Avg: $([math]::Round($totalTime/$suite.Count))ms"

  # 存储基准结果
  $ts = Get-Date -Format "yyyyMMdd-HHmmss"
  $report = @{
    suite = $TestSuite; date = $ts
    total = $suite.Count; passed = $totalPass
    avg_duration_ms = [math]::Round($totalTime/$suite.Count)
    by_agent = @{ claude = $results }
  }
  $reportFile = Join-Path $BenchDir "benchmark-$ts.json"
  $report | ConvertTo-Json -Depth 5 | Set-Content $reportFile
  Write-Host "[bench] Report saved: $reportFile"
}

# ── A/B 测试 ──
function Invoke-ABTest {
  if (-not $AgentA -or -not $AgentB) { Write-Host "[ab] Requires -AgentA and -AgentB"; return }
  $suite = if ($TestSuite) { $TestSuites[$TestSuite] } else { $TestSuites["quick"] }
  if (-not $suite) { Write-Host "[ab] Unknown suite: $TestSuite"; return }

  Write-Host "=== A/B Test: $AgentA vs $AgentB ==="
  Write-Host "Suite: $(if ($TestSuite) { $TestSuite } else { 'quick' }) ($($suite.Count) cases × $RunsPerAgent runs)"
  Write-Host ""

  $agents = @($AgentA, $AgentB)
  $allResults = @{}

  foreach ($agent in $agents) {
    Write-Host "--- Running $agent ---"
    $agentResults = @()
    $pass = 0; $totalTime = 0
    for ($run = 0; $run -lt $RunsPerAgent; $run++) {
      foreach ($case in $suite) {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $r = & $DelegateScript -Agent $agent -Task $case.task -RoutingKey $RoutingKey -TimeoutSeconds $TimeoutSeconds 2>&1
        $sw.Stop()
        $parsed = $r | ConvertFrom-Json
        $passed = $parsed.exit_code -eq 0
        if ($passed) { $pass++ }
        $totalTime += $sw.ElapsedMilliseconds
        Write-Host "  $(if ($passed) { '✅' } else { '❌' }) run=$($run+1) $($case.task) — $($sw.ElapsedMilliseconds)ms"
        $agentResults += @{ task = $case.task; run = $run; passed = $passed; duration_ms = $sw.ElapsedMilliseconds; exit_code = $parsed.exit_code }
      }
    }
    $rate = [math]::Round(($pass / ($suite.Count * $RunsPerAgent)) * 100, 1)
    $avg = [math]::Round($totalTime / ($suite.Count * $RunsPerAgent))
    Write-Host "  → ${agent}: $pass/$($suite.Count * $RunsPerAgent) passed, $rate%, avg ${avg}ms`n"
    $allResults[$agent] = @{ pass = $pass; total = $suite.Count * $RunsPerAgent; rate = $rate; avg_duration_ms = $avg; details = $agentResults }
  }

  # 对比报告
  $winner = if ($allResults[$AgentA].rate -gt $allResults[$AgentB].rate) { $AgentA } elseif ($allResults[$AgentB].rate -gt $allResults[$AgentA].rate) { $AgentB } else { "tie" }
  Write-Host "=== A/B Result ==="
  Write-Host "$AgentA : $($allResults[$AgentA].rate)% success, avg $($allResults[$AgentA].avg_duration_ms)ms"
  Write-Host "$AgentB : $($allResults[$AgentB].rate)% success, avg $($allResults[$AgentB].avg_duration_ms)ms"
  Write-Host "Winner: $winner"

  # 存储结果
  $ts = Get-Date -Format "yyyyMMdd-HHmmss"
  $report = @{
    ab_test = @{ agent_a = $AgentA; agent_b = $AgentB; suite = $TestSuite; runs = $RunsPerAgent }
    result = @{
      winner = $winner
      a = $allResults[$AgentA]; b = $allResults[$AgentB]
    }
    timestamp = $ts
  }
  $reportFile = Join-Path $BenchDir "abtest-$AgentA-vs-$AgentB-$ts.json"
  $report | ConvertTo-Json -Depth 5 | Set-Content $reportFile
  Write-Host "[ab] Report saved: $reportFile"

  # 如果有 evolve 状态文件，记录 A/B 结果
  $stateFile = "F:\memory\evolve\state.json"
  if (Test-Path $stateFile) {
    $state = Get-Content $stateFile -Raw | ConvertFrom-Json
    if (-not $state.ab_results) { $state | Add-Member -NotePropertyName ab_results -NotePropertyValue @() }
    $state.ab_results += @{
      date = $ts
      agent_a = $AgentA; agent_b = $AgentB
      suite = $TestSuite; runs = $RunsPerAgent
      winner = $winner
      rate_a = $allResults[$AgentA].rate; rate_b = $allResults[$AgentB].rate
      avg_ms_a = $allResults[$AgentA].avg_duration_ms; avg_ms_b = $allResults[$AgentB].avg_duration_ms
    }
    $state | ConvertTo-Json -Depth 5 | Set-Content $stateFile
    Write-Host "[ab] A/B result synced to F:\memory\evolve\state.json"
  }
}

# Dispatch
switch ($Action) {
  "list"      { Invoke-List }
  "benchmark" { Invoke-Benchmark }
  "ab-test"   { Invoke-ABTest }
}
