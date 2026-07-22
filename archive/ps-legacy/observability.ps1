param(
  [string]$Action = "log",
  [string]$LogDir = (Join-Path (Split-Path (Split-Path $PSCommandPath -Parent)) "logs"),
  [string]$Agent,
  [string]$Task,
  [string]$TaskFile = "",
  [string]$ResultJson,
  [string]$RoutingKey,
  [string]$PromptVersion
)

# Load Task from file if TaskFile provided (to avoid command-line length limits)
if ($TaskFile -and -not $Task) {
  $Task = Get-Content $TaskFile -Raw -ErrorAction Stop
}

# MAOP Loop — Observability & Metrics

# Load utilities
. (Join-Path (Split-Path $PSCommandPath -Parent) 'filelock.ps1')
. (Join-Path (Split-Path $PSCommandPath -Parent) 'database.ps1')

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }

switch ($Action) {
  "log" {
    $resultObj = if ($ResultJson) { $ResultJson | ConvertFrom-Json } else { @{} }
    $timestamp = (Get-Date -Format "o")

    # ── Try SQLite first ──
    $sqlOk = Execute-Database -Sql @"
INSERT INTO delegations (timestamp, agent, task, routing_key, exit_code, stdout, stderr, duration_ms, trace_id)
VALUES (@ts, @agent, @task, @rk, @ec, @so, @se, @dur, @trace)
"@ -Parameters @{
      "@ts"    = $timestamp
      "@agent" = $Agent
      "@task"  = $Task
      "@rk"    = $RoutingKey
      "@ec"    = if ($null -ne $resultObj.exit_code) { [int]$resultObj.exit_code } else { $null }
      "@so"    = if ($resultObj.stdout) { $resultObj.stdout } else { $null }
      "@se"    = if ($resultObj.stderr) { $resultObj.stderr } else { ($resultObj.error) }
      "@dur"   = if ($resultObj.duration_ms) { [int]$resultObj.duration_ms } else { $null }
      "@trace" = $null
    }

    if (-not $sqlOk) {
      # ── Fallback to JSON ──
      $logFile = Join-Path $LogDir "delegations.json"
      $entry = @{
        timestamp = $timestamp
        agent     = $Agent
        task      = $Task
        routing_key = $RoutingKey
        prompt_version = $PromptVersion
        result    = $resultObj
      }
      $existing = New-Object System.Collections.ArrayList
      if (Test-Path $logFile) {
        $data = Get-Content $logFile | ConvertFrom-Json
        if ($data -is [array]) { $existing.AddRange($data) }
        elseif ($data) { $existing.Add($data) | Out-Null }
      }
      $existing.Add($entry) | Out-Null
      Invoke-WithFileLock -Path $logFile -Script {
        $existing | ConvertTo-Json -Depth 5 | Set-Content $logFile
      }
    }

    Write-Host "[obs] Logged delegation: $Agent → $Task"
  }

  "summary" {
    $logFile = Join-Path $LogDir "delegations.json"
    if (-not (Test-Path $logFile)) {
      Write-Host "[obs] No delegation history yet"
      return
    }
    $data = Get-Content $logFile | ConvertFrom-Json
    $total = $data.Count
    $success = ($data | Where-Object { $_.result.exit_code -eq 0 }).Count
    $failed = $total - $success
    $durations = $data | ForEach-Object { if ($_.result.duration_ms) { $_.result.duration_ms } }
    $avgDuration = if ($durations.Count -gt 0) { [math]::Round(($durations | Measure-Object -Average).Average) } else { 0 }

    Write-Host "=== MAOP Observability Summary ==="
    Write-Host "Total delegations: $total"
    Write-Host "Successful: $success"
    Write-Host "Failed: $failed"
    Write-Host "Avg duration: ${avgDuration}ms"

    $byAgent = $data | Group-Object { $_.agent } | ForEach-Object {
      [PSCustomObject]@{
        Agent = $_.Name
        Count = $_.Count
        Success = ($_.Group | Where-Object { $_.result.exit_code -eq 0 }).Count
      }
    }
    Write-Host "`nBy agent:"
    $byAgent | Format-Table -AutoSize | Out-String | Write-Host
  }

  "metrics" {
    $timestamp = (Get-Date -Format "o")

    # Compute metrics from delegations
    $delegationsFile = Join-Path $LogDir "delegations.json"
    $total = 0; $successCount = 0; $avgDur = 0
    if (Test-Path $delegationsFile) {
      $data = Get-Content $delegationsFile | ConvertFrom-Json
      $total = $data.Count
      if ($total -gt 0) {
        $successCount = ($data | Where-Object { $_.result.exit_code -eq 0 }).Count
        $durations = $data | ForEach-Object { if ($_.result.duration_ms) { $_.result.duration_ms } }
        $avgDur = if ($durations.Count -gt 0) { [math]::Round(($durations | Measure-Object -Average).Average) } else { 0 }
      }
    }
    $successRate = if ($total -gt 0) { [math]::Round(($successCount / $total) * 100, 1) } else { 0 }

    # ── Try SQLite first (store individual metric rows) ──
    $metricRows = @(
      @{name="total_delegations"; value=[double]$total}
      @{name="success_rate";      value=[double]$successRate}
      @{name="avg_duration_ms";   value=[double]$avgDur}
    )
    $allSqlOk = $true
    foreach ($m in $metricRows) {
      $ok = Execute-Database -Sql @"
INSERT INTO metrics (timestamp, agent, metric_name, metric_value, tags)
VALUES (@ts, @agent, @name, @val, @tags)
"@ -Parameters @{
        "@ts"    = $timestamp
        "@agent" = if ($Agent) { $Agent } else { "MAOP" }
        "@name"  = $m.name
        "@val"   = $m.value
        "@tags"  = "{}"
      }
      if (-not $ok) { $allSqlOk = $false; break }
    }

    if (-not $allSqlOk) {
      # ── Fallback to JSON ──
      $logFile = Join-Path $LogDir "metrics.json"
      $metrics = @{
        last_updated       = $timestamp
        total_delegations  = $total
        success_rate       = $successRate
        avg_duration_ms    = $avgDur
      }
      Invoke-WithFileLock -Path $logFile -Script {
        $metrics | ConvertTo-Json | Set-Content $logFile
      }
    }

    Write-Host "[obs] Metrics saved"
  }

  default {
    Write-Error "[obs] Unknown action: $Action"
  }
}

