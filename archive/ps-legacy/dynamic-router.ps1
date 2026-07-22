<#
.SYNOPSIS
  MAOP Dynamic Router — Score agents by health + delegation history
.DESCRIPTION
  Reads healthcheck_latest.json (agent health), delegations.json (last 100 records),
  and config/agents.yaml (routing table). Calculates total_score for each agent per
  routing key as success_rate * 0.6 + speed_score * 0.4, and outputs a ranked JSON.

  Caches results for 30 seconds. Use -Refresh to bypass cache.

.PARAMETER Refresh
  Force re-read all inputs and bypass the 30s cache.

.OUTPUTS
  JSON object to stdout keyed by routing_key. Each value is an array of
  { agent, score, success_rate, speed } sorted by score descending.
#>

param(
  [switch]$Refresh
)

# ── Path resolution (module-level, available to function) ──
$ScriptDir = Split-Path $PSCommandPath -Parent
$ProjectRoot = Split-Path $ScriptDir -Parent

# ══════════════════════════════════════════════════
# Core function — can be called directly when dot-sourced
# ══════════════════════════════════════════════════
function Invoke-DynamicRouter {
  param([switch]$Refresh)

  $DataDir    = Join-Path $ProjectRoot "data"
  $CacheFile  = Join-Path $DataDir "dynamic-routing-cache.json"
  $CacheTTLSec = 30

  # ═══ Cache check — return cached JSON if still fresh ═══
  if (-not $Refresh -and (Test-Path $CacheFile)) {
    $cacheAge = [int]((Get-Date) - (Get-Item $CacheFile).LastWriteTime).TotalSeconds
    if ($cacheAge -lt $CacheTTLSec) {
      Write-Output (Get-Content $CacheFile -Raw -Encoding utf8)
      return
    }
  }

  # ═══ 1. Read agent health ═══
  $HealthFile = Join-Path $ProjectRoot "src\logs\healthcheck_latest.json"
  $healthMap = @{}

  if (Test-Path $HealthFile) {
    try {
      $rawHealth = Get-Content $HealthFile -Raw -Encoding utf8
      if ($rawHealth -and $rawHealth.Trim()) {
        $healthData = $rawHealth | ConvertFrom-Json
        foreach ($h in $healthData) {
          $healthMap[$h.agent] = @{
            alive = ($h.status -eq "alive")
            ms    = [int]$h.ms
          }
        }
      }
    } catch {
      Write-Warning "dynamic-router: Failed to parse $HealthFile — $($_.Exception.Message)"
    }
  } else {
    Write-Warning "dynamic-router: Health file not found at $HealthFile"
  }

  # ═══ 2. Read delegation history ═══
  $DelegFile = Join-Path $ProjectRoot "logs\delegations.json"
  $delegStats = @{}

  if (Test-Path $DelegFile) {
    try {
      $rawDeleg = Get-Content $DelegFile -Raw -Encoding utf8
      if ($rawDeleg -and $rawDeleg.Trim()) {
        $delegData = $rawDeleg | ConvertFrom-Json
        $recent = $delegData | Select-Object -Last 100

        $groups = $recent | Group-Object agent
        foreach ($g in $groups) {
          $agent = $g.Name
          $entries = $g.Group
          $total = $entries.Count

          $successCount = 0
          $totalDuration = 0
          $durationCount = 0

          foreach ($entry in $entries) {
            $exitCode   = $entry.result.exit_code
            $durationMs = $entry.result.duration_ms

            if ($exitCode -eq 0) { $successCount++ }

            if ($null -ne $durationMs -and $durationMs -gt 0) {
              $totalDuration += $durationMs
              $durationCount++
            }
          }

          $successRate = if ($total -gt 0) { [math]::Round($successCount / $total, 4) } else { 0.5 }
          $avgDuration = if ($durationCount -gt 0) { [math]::Round($totalDuration / $durationCount, 0) } else { $null }

          $delegStats[$agent] = @{
            success_rate    = $successRate
            avg_duration_ms = $avgDuration
          }
        }
      }
    } catch {
      Write-Warning "dynamic-router: Failed to parse $DelegFile — $($_.Exception.Message)"
    }
  } else {
    Write-Warning "dynamic-router: Delegations file not found at $DelegFile"
  }

  # ═══ 3. Parse routing table from agents.yaml (via Python bridge) ═══
$routing = @{}

# Use Python bridge instead of hand-rolled YAML parser
. (Join-Path $ProjectRoot "tools\MAOP-bridge.ps1")
$routingData = Invoke-ConfigBridge "--section routing" -Critical
foreach ($prop in $routingData.PSObject.Properties) {
    $rk = $prop.Name
    $entry = $prop.Value
    $agents = @()
    if ($entry.primary) { $agents += $entry.primary }
    if ($entry.fallback) { $agents += $entry.fallback }
    if ($entry.tertiary) { $agents += $entry.tertiary }
    $routing[$rk] = $agents | Select-Object -Unique
}
# ═══ 4. Calculate scores per routing key ═══
  $result = @{}
  $speedNormalizationMs = 30000

  foreach ($rk in $routing.Keys) {
    $agents = $routing[$rk]
    $scoredAgents = @()

    foreach ($agent in $agents) {
      $successRate = 0.5
      $speedScore  = 0.5

      $healthInfo = $healthMap[$agent]
      if ($healthInfo) {
        if (-not $healthInfo.alive) {
          $successRate = 0.05
          $speedScore  = 0.05
        } else {
          $healthMs = $healthInfo.ms
          if ($healthMs -gt 0) {
            $healthSpeed = [math]::Max(0.0, [math]::Min(1.0, 1.0 - ($healthMs / $speedNormalizationMs)))
            $speedScore = $healthSpeed
          }
        }
      }

      $stats = $delegStats[$agent]
      if ($stats) {
        $successRate = $stats.success_rate

        if ($null -ne $stats.avg_duration_ms -and $stats.avg_duration_ms -gt 0) {
          $delegSpeed = [math]::Max(0.0, [math]::Min(1.0, 1.0 - ($stats.avg_duration_ms / $speedNormalizationMs)))
          if ($healthMap[$agent] -and $healthMap[$agent].alive -and $healthMap[$agent].ms -gt 0) {
            $healthMs = $healthMap[$agent].ms
            $healthSpeed = [math]::Max(0.0, [math]::Min(1.0, 1.0 - ($healthMs / $speedNormalizationMs)))
            $speedScore = ($delegSpeed * 0.7) + ($healthSpeed * 0.3)
          } else {
            $speedScore = $delegSpeed
          }
        }
      }

      $totalScore = [math]::Round(($successRate * 0.6) + ($speedScore * 0.4), 4)

      $scoredAgents += [PSCustomObject]@{
        agent        = $agent
        score        = $totalScore
        success_rate = [math]::Round($successRate, 4)
        speed        = [math]::Round($speedScore, 4)
      }
    }

    $result[$rk] = $scoredAgents | Sort-Object -Property score -Descending
  }

  # ═══ 5. Output JSON + cache ═══
  $json = $result | ConvertTo-Json -Depth 5
  $json

  if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
  }

  $filelockPath = Join-Path $ScriptDir 'filelock.ps1'
  if (Test-Path $filelockPath) {
    . $filelockPath
    Invoke-WithFileLock -Path $CacheFile -Script {
      $json | Out-File -FilePath $CacheFile -Encoding utf8
    }
  } else {
    $json | Out-File -FilePath $CacheFile -Encoding utf8
  }
}

# ══════════════════════════════════════════════════
# Main execution — only when run directly, not dot-sourced
# ══════════════════════════════════════════════════
# Guard: when dot-sourced for testing, $MyInvocation.InvocationName is "."
if ($MyInvocation.InvocationName -ne ".") {
  Invoke-DynamicRouter -Refresh:$Refresh
}
