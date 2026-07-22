param(
  [ValidateSet("report","agent-stats","chain","failures","timeseries","live")]
  [string]$Action = "report",
  [string]$Agent = "",
  [int]$Hours = 24,
  [int]$TopN = 10,
  [string]$LogFile = "",
  [string]$OutputFile = ""
)

$ScriptDir = Split-Path $PSCommandPath -Parent
if (-not $LogFile) { $LogFile = Join-Path (Split-Path $ScriptDir -Parent) "logs\delegations.json" }
$LogDir = Split-Path $LogFile -Parent

function Load-Logs {
  if (Test-Path $LogFile) {
    try { return @(Get-Content $LogFile -Raw -ErrorAction Stop | ConvertFrom-Json) } catch { return @() }
  }
  return @()
}

function Time-Filter($logs) {
  $cutoff = (Get-Date).AddHours(-$Hours)
  return $logs | Where-Object {
    if ($_.timestamp) { $t = if ($_.timestamp -is [string]) { [datetime]::Parse($_.timestamp) } else { $_.timestamp }; $t -ge $cutoff }
    else { $true }
  }
}

function Parse-Result($log) {
  $r = $log.result
  if (-not $r) { return @{ exit_code = -1; stdout = ""; duration_ms = 0 } }
  if ($r -is [string]) {
    try { $r = $r | ConvertFrom-Json } catch { return @{ exit_code = -1; stdout = $r; duration_ms = 0 } }
  }
  return @{ exit_code = if ($r.exit_code -ne $null) { $r.exit_code } else { -1 }; stdout = $r.stdout; duration_ms = if ($r.duration_ms) { $r.duration_ms } else { 0 } }
}

switch ($Action) {
  "report" {
    $logs = Time-Filter (Load-Logs)
    $total = $logs.Count
    $agents = $logs | Group-Object agent
    $success = @($logs | Where-Object { (Parse-Result $_).exit_code -eq 0 }).Count
    $failures = @($logs | Where-Object { (Parse-Result $_).exit_code -ne 0 }).Count
    $avgLatency = if ($total -gt 0) { [Math]::Round(($logs | ForEach-Object { (Parse-Result $_).duration_ms } | Measure-Object -Average).Average) } else { 0 }
    $agentStats = $agents | ForEach-Object {
      $aLogs = $_.Group
      $aSuccess = @($aLogs | Where-Object { (Parse-Result $_).exit_code -eq 0 }).Count
      $aLatency = [Math]::Round(($aLogs | ForEach-Object { (Parse-Result $_).duration_ms } | Measure-Object -Average).Average)
      $aTasks = ($aLogs | Group-Object task).Count
      @{
        agent = $_.Name; count = $_.Count; success = $aSuccess; fail = $_.Count - $aSuccess
        success_rate = if ($_.Count -gt 0) { [Math]::Round($aSuccess / $_.Count * 100, 1) } else { 0 }
        avg_latency_ms = $aLatency; unique_tasks = $aTasks
      }
    } | Sort-Object count -Descending
    $result = @{
      period_hours = $Hours; total_delegations = $total; success = $success; failures = $failures
      success_rate = if ($total -gt 0) { [Math]::Round($success / $total * 100, 1) } else { 0 }
      avg_latency_ms = $avgLatency; unique_agents = $agents.Count
      agents = $agentStats
    }
    $json = $result | ConvertTo-Json -Depth 3
    if ($OutputFile) { $json | Set-Content $OutputFile -Encoding utf8 }
    Write-Output $json
  }

  "agent-stats" {
    $logs = Time-Filter (Load-Logs)
    if ($Agent) { $logs = $logs | Where-Object { $_.agent -eq $Agent } }
    $byAgent = $logs | Group-Object agent
    $result = $byAgent | ForEach-Object {
      $aLogs = $_.Group
      $latencies = $aLogs | ForEach-Object { (Parse-Result $_).duration_ms }
      $avgLat = [Math]::Round(($latencies | Measure-Object -Average).Average)
      $maxLat = ($latencies | Measure-Object -Maximum).Maximum
      $minLat = ($latencies | Measure-Object -Minimum).Minimum
      $codes = $aLogs | Group-Object { (Parse-Result $_).exit_code }
      @{
        agent = $_.Name; count = $_.Count
        avg_latency_ms = $avgLat; max_latency_ms = $maxLat; min_latency_ms = $minLat
        exit_codes = $codes | ForEach-Object { @{ code = $_.Name; count = $_.Count } }
        top_tasks = ($aLogs | Group-Object task | Sort-Object Count -Descending | Select-Object -First 5 | ForEach-Object { @{ task = $_.Name; count = $_.Count } })
      }
    }
    Write-Output ($result | ConvertTo-Json -Depth 3)
  }

  "chain" {
    $logs = Time-Filter (Load-Logs)
    $chains = $logs | Where-Object { $_.routing_key -and $_.routing_key -ne "healthcheck" } | Group-Object routing_key
    $result = $chains | ForEach-Object {
      $cLogs = $_.Group
      @{
        chain = $_.Name; count = $_.Count
        agents = ($cLogs | Group-Object agent | ForEach-Object { @{ agent = $_.Name; count = $_.Count } })
        avg_latency_ms = [Math]::Round(($cLogs | ForEach-Object { (Parse-Result $_).duration_ms } | Measure-Object -Average).Average)
      }
    } | Sort-Object count -Descending
    Write-Output ($result | ConvertTo-Json -Depth 3)
  }

  "failures" {
    $logs = Time-Filter (Load-Logs)
    $failed = $logs | Where-Object { (Parse-Result $_).exit_code -ne 0 }
    $result = @{
      total_failures = $failed.Count
      failure_rate = if ($logs.Count -gt 0) { [Math]::Round($failed.Count / $logs.Count * 100, 1) } else { 0 }
      by_agent = ($failed | Group-Object agent | ForEach-Object { @{ agent = $_.Name; count = $_.Count } } | Sort-Object count -Descending)
      by_task = ($failed | Group-Object task | ForEach-Object { @{ task = $_.Name; count = $_.Count } } | Sort-Object count -Descending)
      recent = $failed | Sort-Object timestamp -Descending | Select-Object -First 10 | ForEach-Object {
        $r = Parse-Result $_
        @{ agent = $_.agent; task = $_.task; timestamp = $_.timestamp; exit_code = $r.exit_code; duration_ms = $r.duration_ms }
      }
    }
    Write-Output ($result | ConvertTo-Json -Depth 3)
  }

  "timeseries" {
    $logs = Time-Filter (Load-Logs)
    $byHour = $logs | Group-Object { if ($_.timestamp) { $t = if ($_.timestamp -is [string]) { [datetime]::Parse($_.timestamp) } else { $_.timestamp }; $t.ToString("yyyy-MM-dd HH:00") } else { "unknown" } }
    $result = $byHour | ForEach-Object {
      $hLogs = $_.Group
      $hSuccess = @($hLogs | Where-Object { (Parse-Result $_).exit_code -eq 0 }).Count
      @{
        hour = $_.Name; count = $_.Count; success = $hSuccess; fail = $_.Count - $hSuccess
        avg_latency_ms = [Math]::Round(($hLogs | ForEach-Object { (Parse-Result $_).duration_ms } | Measure-Object -Average).Average)
      }
    } | Sort-Object hour
    Write-Output ($result | ConvertTo-Json -Depth 2)
  }

  "live" {
    if (-not (Test-Path $LogDir)) { Write-Output "[]"; exit 0 }
    $logs = Load-Logs
    $recent = $logs | Sort-Object timestamp -Descending | Select-Object -First 20
    $result = $recent | ForEach-Object {
      $r = Parse-Result $_
      @{
        agent = $_.agent; task = $_.task; timestamp = $_.timestamp
        exit_code = $r.exit_code; duration_ms = $r.duration_ms
        status = if ($r.exit_code -eq 0) { "ok" } elseif ($r.exit_code -eq -1) { "error" } else { "fail" }
      }
    }
    Write-Output ($result | ConvertTo-Json -Depth 2)
  }
}
