param(
  [ValidateSet("sync","status")]
  [string]$Action = "sync"
)

<#
.SYNOPSIS
  连接 MAOP Harness evolve.ps1 ↔ F:\memory\evolve\ 状态同步
.DESCRIPTION
  sync: 将 evolve.ps1 的分析结果同步到 F:\memory\evolve\ 进化框架
  status: 显示两端状态对比
#>

$EvolveScript = "C:\Users\winge\.config\opencode\node_modules\MAOP-harness\harness\evolve.ps1"
$MemEvolveDir = "F:\memory\evolve"
$StateFile = Join-Path $MemEvolveDir "state.json"
$CandidatesFile = Join-Path $MemEvolveDir "candidates.md"
$LogFile = Join-Path $MemEvolveDir "evolution-log.md"

switch ($Action) {
  "sync" {
    Write-Host "[evolve-sync] Syncing MAOP Harness → F:\memory\evolve\"

    # 1. 获取 evolve.ps1 建议
    $suggestions = & $EvolveScript -Action suggest 2>&1 | Select-Object -Last 1
    if (-not $suggestions) { Write-Host "[evolve-sync] No suggestions from evolve.ps1"; return }
    $sugs = $suggestions | ConvertFrom-Json
    if (-not $sugs -or $sugs.Count -eq 0) { Write-Host "[evolve-sync] System healthy, no suggestions"; return }

    Write-Host "[evolve-sync] $($sugs.Count) suggestion(s) from evolve.ps1"

    # 2. 更新 state.json
    $state = @{ active_experiments = @(); max_concurrent = 10; created = "2026-07-01T04:30:00+08:00"; last_updated = (Get-Date -Format "o") }
    if (Test-Path $StateFile) { $state = Get-Content $StateFile -Raw | ConvertFrom-Json }
    if (-not $state.active_experiments) { $state.active_experiments = @() }

    foreach ($s in $sugs) {
      $expId = "evolve-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
      $existing = $state.active_experiments | Where-Object { $_.name -eq $s.detail }
      if (-not $existing) {
        $state.active_experiments += @{
          id = $expId
          name = $s.detail
          status = if ($s.auto_applicable) { "READY" } else { "PENDING" }
          created = (Get-Date -Format "o")
          severity = $s.severity
          auto_applicable = $s.auto_applicable
          suggestion = $s.suggestion
        }
        Write-Host "[evolve-sync] New experiment: $expId — $($s.detail)"
      }
    }
    $state | ConvertTo-Json -Depth 3 | Set-Content $StateFile
    Write-Host "[evolve-sync] state.json updated"
  }

  "status" {
    Write-Host "=== Evolution Status (Bridge) ==="
    
    # MAOP side
    Write-Host "`n--- MAOP Harness (evolve.ps1) ---"
    & $EvolveScript -Action status 2>&1

    # Memory side
    Write-Host "`n--- F:\memory\evolve\ ---"
    $state = @{}
    if (Test-Path $StateFile) { $state = Get-Content $StateFile -Raw | ConvertFrom-Json }
    Write-Host "Active experiments: $($state.active_experiments.Count)"
    foreach ($e in $state.active_experiments) {
      $icon = switch ($e.status) { "OBSERVING" { "🔍" } "READY" { "⚡" } "PENDING" { "⏳" } "COMPLETED" { "✅" } "FAILED" { "❌" } default { "❓" } }
      Write-Host "  $icon $($e.id): $($e.name) [$($e.status)]"
    }
  }
}