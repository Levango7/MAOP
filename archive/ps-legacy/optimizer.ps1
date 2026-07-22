param(
  [ValidateSet("analyze","suggest","select-agent","tune-config","report","run")]
  [string]$Action = "report",
  [string]$Task = "",
  [string]$Goal = "speed", # speed | quality | cost | balanced
  [string]$ReportFile = ""
)

$ScriptDir = Split-Path $PSCommandPath -Parent
$Correlation = Join-Path $ScriptDir "correlation.ps1"
$Delegate = Join-Path (Split-Path $ScriptDir -Parent) "delegate.ps1"
$LogFile = Join-Path (Split-Path $ScriptDir -Parent) "logs\delegations.json"

$AgentProfiles = @{
  claude    = @{ speed = 6904; success = 46.7; cost = "high";   best_for = @("reasoning", "code") }
  kimi      = @{ speed = 10917; success = 40;  cost = "med";    best_for = @("analysis", "chinese") }
  codex     = @{ speed = 16636; success = 40;  cost = "med";    best_for = @("code", "planning") }
  codewhale = @{ speed = 8957;  success = 88.9; cost = "low";   best_for = @("code", "quick") }
  kilo      = @{ speed = 12716; success = 66.7; cost = "low";   best_for = @("shell", "ops") }
  openclaw  = @{ speed = 25387; success = 53.3; cost = "high";  best_for = @("complex", "autonomous") }
  qwenpaw   = @{ speed = 16201; success = 40;   cost = "low";   best_for = @("code", "chinese") }
  qoder     = @{ speed = 9798;  success = 40;   cost = "low";   best_for = @("code") }
}

switch ($Action) {
  "analyze" {
    $report = if (Test-Path $Correlation) { & powershell -NoProfile -File $Correlation -Action report -Hours 48 2>&1 | Out-String | ConvertFrom-Json } else { $null }
    $result = @{ agent_count = 17; total_delegations = if ($report) { $report.total_delegations } else { 0 } }
    if ($report -and $report.agents) {
      $result.best_performers = @($report.agents | Where-Object { $_.success_rate -ge 75 } | Sort-Object avg_latency_ms | Select-Object agent, success_rate, avg_latency_ms)
      $result.worst_performers = @($report.agents | Where-Object { $_.success_rate -lt 50 } | Sort-Object success_rate | Select-Object agent, success_rate, avg_latency_ms)
      $result.overall_success_rate = $report.success_rate
      $result.avg_latency_ms = $report.avg_latency_ms
      $result.unique_agents = $report.unique_agents
    }
    Write-Output ($result | ConvertTo-Json -Depth 3)
  }

  "suggest" {
    $analysis = & $MyInvocation.MyCommand.Path -Action analyze 2>&1 | Out-String | ConvertFrom-Json
    $suggestions = @()
    if ($analysis.overall_success_rate -lt 60) {
      $suggestions += @{ type = "config"; priority = "high"; message = "整体成功率 $($analysis.overall_success_rate)%，建议增加高成功率 agent（codewhale/nvidia）的使用权重" }
    }
    if ($analysis.worst_performers) {
      foreach ($w in $analysis.worst_performers) {
        $profile = $AgentProfiles[$w.agent]
        if ($profile) {
          $suggestions += @{ type = "reroute"; priority = "med"; message = "$($w.agent) 成功率 $($w.success_rate)%，建议改用 $($profile.best_for[0]) 场景专用 agent" }
        }
      }
    }
    if ($analysis.best_performers -and $analysis.best_performers.Count -gt 0) {
      $suggestions += @{ type = "promote"; priority = "low"; message = "最佳 agent: $($analysis.best_performers[0].agent)（$($analysis.best_performers[0].success_rate)%, $($analysis.best_performers[0].avg_latency_ms)ms），设为默认" }
    }
    Write-Output (@{ suggestions = @($suggestions) } | ConvertTo-Json -Depth 3)
  }

  "select-agent" {
    if (-not $Task) { Write-Error "select-agent requires -Task"; exit 1 }
    $goalWeights = @{ speed = @{ speed = 0.5; success = 0.3; cost = 0.2 }; quality = @{ speed = 0.1; success = 0.6; cost = 0.3 }; cost = @{ speed = 0.2; success = 0.2; cost = 0.6 }; balanced = @{ speed = 0.33; success = 0.33; cost = 0.34 } }
    $w = $goalWeights[$Goal]
    $scores = @()
    foreach ($entry in $AgentProfiles.GetEnumerator()) {
      $name = $entry.Key; $p = $entry.Value
      $speedScore = [Math]::Max(0, 1 - ($p.speed / 30000))
      $successScore = $p.success / 100
      $costScore = @{ free = 1.0; low = 0.7; med = 0.4; high = 0.2 }[$p.cost]
      $total = $speedScore * $w.speed + $successScore * $w.success + $costScore * $w.cost
      $scores += @{ agent = $name; score = [Math]::Round($total, 3); speed = $p.speed; success_rate = $p.success; cost = $p.cost; best_for = $p.best_for }
    }
    $sorted = $scores | Sort-Object score -Descending
    $top5 = $sorted | Select-Object -First 5
    Write-Output (@{ goal = $Goal; task = $Task; recommendation = $top5[0].agent; top = @($top5) } | ConvertTo-Json -Depth 3)
  }

  "tune-config" {
    $analysis = & $MyInvocation.MyCommand.Path -Action analyze 2>&1 | Out-String | ConvertFrom-Json
    $config = @{
      suggested_timeouts = @{}
      suggested_agents = @{}
    }
    if ($analysis.best_performers) {
      foreach ($bp in $analysis.best_performers) {
        $config.suggested_timeouts[$bp.agent] = [Math]::Max(15, [Math]::Ceiling($bp.avg_latency_ms / 1000) * 2 + 5)
      }
    }
    if ($analysis.overall_success_rate -lt 50) {
      $config.recommendation = "增加重试次数或换用成功率更高的 agent"
    }
    Write-Output ($config | ConvertTo-Json)
  }

  "report" {
    $analysis = & $MyInvocation.MyCommand.Path -Action analyze 2>&1 | Out-String | ConvertFrom-Json
    $suggestions = & $MyInvocation.MyCommand.Path -Action suggest 2>&1 | Out-String | ConvertFrom-Json
    $result = @{ analysis = $analysis; suggestions = $suggestions.suggestions; generated = (Get-Date -Format "o") }
    $json = $result | ConvertTo-Json -Depth 4
    if ($ReportFile) { $json | Set-Content $ReportFile -Encoding utf8 }
    Write-Output $json
  }

  "run" {
    if (-not $Task) { Write-Error "run requires -Task"; exit 1 }
    $selected = & $MyInvocation.MyCommand.Path -Action select-agent -Task $Task -Goal $Goal 2>&1 | Out-String | ConvertFrom-Json
    $agent = $selected.recommendation
    Write-Output (@{ selected_agent = $agent; goal = $Goal; task_preview = $Task.Substring(0, [Math]::Min(50, $Task.Length)) } | ConvertTo-Json)
  }
}
