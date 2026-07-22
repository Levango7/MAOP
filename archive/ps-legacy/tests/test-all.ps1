<#
.SYNOPSIS
  MAOP Unified Test Runner — runs all Pester tests and integration smoke tests.
.DESCRIPTION
  Discovers all *.tests.ps1 in tests/ and runs them via Invoke-Pester.
  Also runs a quick smoke-test suite on select src/ modules (existing pattern).
  Outputs a combined pass/fail summary at the end.

.PARAMETER PesterOnly
  Skip the legacy smoke tests, run only Pester unit tests.
.PARAMETER Category
  Optional filter: only run Pester tests with specific tag. E.g. -Tag "circuit-breaker"
.PARAMeter Quiet
  Minimal output (summary only).
.EXAMPLE
  .\test-all.ps1
  .\test-all.ps1 -PesterOnly
  .\test-all.ps1 -Category "validate-config,MAOP-plan"
#>

param(
  [switch]$PesterOnly,
  [string]$Category = "",
  [switch]$Quiet
)

$RootDir = Split-Path $PSCommandPath -Parent
$SrcDir  = Join-Path $RootDir "src"
$TestDir = Join-Path $RootDir "tests"
$TotalPassed = 0
$TotalFailed = 0
$TotalSkipped = 0

# ═══════════════════════════════════════════════
# 1. Pester Unit Tests
# ═══════════════════════════════════════════════
if (-not $Quiet) {
  Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Cyan
  Write-Host "║     MAOP Test Suite — Running             ║" -ForegroundColor Cyan
  Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Cyan
  Write-Host ""
}

# Discovery
$testFiles = @(Get-ChildItem -Path $TestDir -Filter "*.tests.ps1" -ErrorAction SilentlyContinue | Sort-Object Name)

if ($testFiles.Count -eq 0) {
  Write-Warning "No test files found in $TestDir"
} elseif (-not $Quiet) {
  Write-Host "Found $($testFiles.Count) Pester test file(s):" -ForegroundColor Gray
  foreach ($f in $testFiles) { Write-Host "  • $($f.Name)" -ForegroundColor DarkGray }
  Write-Host ""
}

if ($Category) {
  $categories = $Category.Split(",", [StringSplitOptions]::TrimEntries)
  $testFiles = @($testFiles | Where-Object {
    $base = $_.BaseName -replace "\.tests", ""
    $base -in $categories
  })
  if (-not $Quiet) {
    Write-Host "Filtered to $($testFiles.Count) file(s) for category: $Category" -ForegroundColor Yellow
  }
}

$pesterPassed = 0
$pesterFailed = 0
$pesterSkipped = 0
$pesterTotal = 0

foreach ($tf in $testFiles) {
  $name = $tf.BaseName
  if (-not $Quiet) { Write-Host "Running $name..." -NoNewline }

  $config = New-PesterContainer -Path $tf.FullName
  $result = Invoke-Pester -Container $config -PassThru -Output Minimal -ErrorAction SilentlyContinue
  if (-not $result) {
    Write-Host " FAILED (Invoke-Pester error)" -ForegroundColor Red
    continue
  }

  $passed = $result.PassedCount
  $failed = $result.FailedCount
  $skipped = $result.SkippedCount
  $total = $result.TotalCount

  $pesterPassed += $passed
  $pesterFailed += $failed
  $pesterSkipped += $skipped
  $pesterTotal += $total

  if ($Quiet) { continue }

  if ($failed -gt 0) {
    Write-Host " $passed/$total passed, $failed FAILED" -ForegroundColor Red
    # Show failure details
    foreach ($fb in $result.Failures) {
      Write-Host "  ❌ $($fb.Describe) / $($fb.FullName)" -ForegroundColor Red
      $errmsg = $fb.ErrorRecord.Exception.Message
      if ($errmsg) { Write-Host "     $errmsg" -ForegroundColor DarkRed }
    }
  } elseif ($skipped -gt 0) {
    Write-Host " $passed/$total passed, $skipped SKIPPED" -ForegroundColor Yellow
  } else {
    Write-Host " ✅ $passed/$total passed" -ForegroundColor Green
  }
}

# ═══════════════════════════════════════════════
# 2. Legacy Integration Smoke Tests
# ═══════════════════════════════════════════════
if (-not $PesterOnly) {
  $smokeTests = @(
    @{n="Router";      s="delegate.ps1";         a=@("-Agent","nvidia","-Task","Say OK","-TimeoutSeconds","15");   c="exit_code"}
    @{n="Tool Mgr";    s="tool-manager.ps1";      a=@("-Action","stats");                                          c="total"}
    @{n="Optimizer";   s="optimizer.ps1";         a=@("-Action","select-agent","-Task","hello","-Goal","speed");   c="recommendation"}
    @{n="Coord";       s="coordination.ps1";      a=@("-Action","report");                                         c="available_agents"}
    @{n="Monitor";     s="healthcheck.ps1";       a=@();                                                           c="Alive:"}
    @{n="Validate";    s="validate-config.ps1";   a=@("-Json");                                                    c="valid"}
    @{n="Providers";   s="dashboard-providers.ps1";a=@();                                                          c="name"}
    @{n="Plan";        s="MAOP-plan.ps1";           a=@("-Task","write test","-RoutingKey","codegen");               c="selected_agent"}
  )

  if (-not $Quiet) { Write-Host "`n── Smoke Tests ──" -ForegroundColor Magenta }

  $smokePassed = 0
  $smokeFailed = 0

  foreach ($t in $smokeTests) {
    $scriptPath = Join-Path $SrcDir $t.s
    if (-not (Test-Path $scriptPath)) {
      if (-not $Quiet) { Write-Host "  ⚠️  SKIP $($t.n) (script not found)" -ForegroundColor Yellow }
      $smokeFailed++
      continue
    }

    $out = & powershell -NoProfile -File $scriptPath @($t.a) 2>&1 | Out-String
    if ($out -match $t.c) {
      if (-not $Quiet) { Write-Host "  ✅ $($t.n) [$($t.s)]" -ForegroundColor Green }
      $smokePassed++
    } else {
      if (-not $Quiet) {
        Write-Host "  ❌ $($t.n) [$($t.s)] — expected match: $($t.c)" -ForegroundColor Red
        $outLines = $out.Trim() -split "`r?`n"
        if ($outLines.Count -gt 6) { $outLines = $outLines[0..5] + @("... ($($outLines.Count) lines)") }
        foreach ($ol in $outLines) { Write-Host "     | $ol" -ForegroundColor DarkGray }
      }
      $smokeFailed++
    }
  }
} else {
  $smokePassed = 0
  $smokeFailed = 0
}

# ═══════════════════════════════════════════════
# 3. Summary
# ═══════════════════════════════════════════════
$grandTotal = $pesterTotal + $smokePassed + $smokeFailed
$grandPassed = $pesterPassed + $smokePassed
$grandFailed = $pesterFailed + $smokeFailed
$grandSkipped = $pesterSkipped

Write-Host ""
Write-Host "═══════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Pester : $pesterPassed passed, $pesterFailed failed, $pesterSkipped skipped ($pesterTotal total)" -ForegroundColor $(if($pesterFailed -eq 0){'Green'}else{'Red'})
Write-Host "  Smoke  : $smokePassed passed, $smokeFailed failed ($($smokePassed+$smokeFailed) total)" -ForegroundColor $(if($smokeFailed -eq 0){'Green'}else{'Red'})
Write-Host "  TOTAL  : $grandPassed passed, $grandFailed failed, $grandSkipped skipped" -ForegroundColor $(if($grandFailed -eq 0){'White'}else{'Red'})
Write-Host "═══════════════════════════════════════════" -ForegroundColor Cyan

exit $(if ($grandFailed -eq 0) { 0 } else { 1 })
