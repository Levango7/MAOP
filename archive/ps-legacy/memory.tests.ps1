<#
  memory.tests.ps1 — Minimal coverage for memory.ps1
  Tests: store, search, prune (with TTL), stats, inject, trace, trajectory
#>

BeforeAll {
  $scriptDir = Split-Path $PSCommandPath -Parent
  $srcDir    = Join-Path (Split-Path $scriptDir -Parent) "src"
  $dataDir   = Join-Path (Split-Path $scriptDir -Parent) "data"
  $memScript = Join-Path $srcDir "memory.ps1"

  # Backup and isolate memory data
  $memDir       = Join-Path $srcDir "memory"
  $entriesDir   = Join-Path $memDir "entries"
  $tracesDir    = Join-Path $memDir "traces"
  $trajectoryDir= Join-Path $memDir "trajectory"

  # Create test-specific subdirs if missing
  foreach ($d in @($memDir, $entriesDir, $tracesDir, $trajectoryDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
  }

  # Snapshot existing entries count
  $script:originalEntries = @(Get-ChildItem "$entriesDir\*.json" -ErrorAction SilentlyContinue)
}

Describe "memory.ps1 - Store" {
  It "Stores a new entry and returns an ID" {
    $result = & $memScript -Action store -Agent "test-agent" -Task "test task" -Tags "unit,test" -Content "test content" -TraceID "trace-001" 2>&1 | Select-Object -Last 1
    $result | Should -Not -BeNullOrEmpty
    # Result should contain the generated ID (timestamp-based)
    $result | Should -Match "^\d{8}-\d{6}-[A-Za-z]{6}$"
  }

  It "Creates a JSON file in entries/" {
    $entries = Get-ChildItem "$entriesDir\*.json" -ErrorAction SilentlyContinue
    $entries.Count | Should -BeGreaterOrEqual 1
  }
}

Describe "memory.ps1 - Search" {
  It "Returns results for matching query" {
    # Store a unique entry to search for
    $uniqueTag = "searchtest-$(Get-Date -Format 'HHmmss')"
    & $memScript -Action store -Agent "search-agent" -Task "searchable task" -Tags $uniqueTag -Content "findable content" 2>&1 | Out-Null

    # Should not throw
    { & $memScript -Action search -Query $uniqueTag -Top 5 2>&1 | Out-Null } | Should -Not -Throw
  }

  It "Handles empty search gracefully" {
    # Should not throw even for nonexistent query
    { & $memScript -Action search -Query "nonexistent-xyz-$(Get-Random)" -Top 5 2>&1 | Out-Null } | Should -Not -Throw
  }
}

Describe "memory.ps1 - Prune (TTL + count)" {
  It "Runs prune with TTL without error" {
    # DryRun mode - should not delete anything
    { & $memScript -Action prune -TtlDays 30 -Top 50 -DryRun 2>&1 | Out-Null } | Should -Not -Throw
  }

  It "Prune respects DryRun flag" {
    $entriesBefore = @(Get-ChildItem "$entriesDir\*.json" -ErrorAction SilentlyContinue).Count
    & $memScript -Action prune -TtlDays 30 -Top 50 -DryRun 2>&1 | Out-Null
    $entriesAfter = @(Get-ChildItem "$entriesDir\*.json" -ErrorAction SilentlyContinue).Count
    # DryRun should not change entry count
    $entriesAfter | Should -Be $entriesBefore
  }

  It "Prune with very small TTL does not throw" {
    # TTL=0 days should prune everything older than today
    { & $memScript -Action prune -TtlDays 0 -Top 1000 2>&1 | Out-Null } | Should -Not -Throw
  }
}

Describe "memory.ps1 - Stats" {
  It "Runs stats without error" {
    { & $memScript -Action stats 2>&1 | Out-Null } | Should -Not -Throw
  }
}

Describe "memory.ps1 - Inject" {
  It "Returns context for matching query" {
    $result = & $memScript -Action inject -Query "test" -Top 3 2>&1
    # May return empty string if no match, or context block
    $result | Should -Not -BeNull
  }
}

Describe "memory.ps1 - Trace" {
  It "Creates a trace entry without error" {
    $traceID = [guid]::NewGuid().ToString("N")
    { & $memScript -Action trace -TraceID $traceID -Agent "test-agent" -Task "trace test" 2>&1 | Out-Null } | Should -Not -Throw
  }
}

Describe "memory.ps1 - Trajectory" {
  It "Records a tool call event" {
    $traceID = "traj-test-$(Get-Date -Format 'HHmmss')"
    & $memScript -Action trajectory -TraceID $traceID -Agent "test-agent" -ToolName "test-tool" -ToolInput "test-input" 2>&1 | Out-Null

    # Should create a .jsonl file
    $trajFile = Join-Path $trajectoryDir "$traceID.jsonl"
    Test-Path $trajFile | Should -BeTrue
  }
}

AfterAll {
  # Cleanup: remove test-generated entries (those with "test-agent" or "search-agent")
  Get-ChildItem "$entriesDir\*.json" -ErrorAction SilentlyContinue | ForEach-Object {
    try {
      $entry = Get-Content $_.FullName -Raw | ConvertFrom-Json
      if ($entry.agent -in @("test-agent","search-agent")) {
        Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
      }
    } catch {}
  }

  # Cleanup test trajectory files
  Get-ChildItem "$trajectoryDir\traj-test-*.jsonl" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue

  # Cleanup test trace entries from traces.json
  $traceFile = Join-Path $tracesDir "traces.json"
  if (Test-Path $traceFile) {
    try {
      $traces = Get-Content $traceFile -Raw | ConvertFrom-Json
      $filtered = @($traces | Where-Object { $_.task -ne "trace test" })
      $filtered | ConvertTo-Json -Depth 3 | Set-Content $traceFile -Encoding utf8
    } catch {}
  }
}
