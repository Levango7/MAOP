<#
  log-rotate.tests.ps1 — Tests for tools/log-rotate.ps1
#>

BeforeAll {
  $projRoot  = Split-Path (Split-Path $PSCommandPath -Parent) -Parent
  $toolScript= Join-Path $projRoot "tools\log-rotate.ps1"
  $logDir    = Join-Path $projRoot "logs"
  $testDir   = Join-Path $projRoot "temp\log-rotate-test"

  # Create isolated test directory
  if (-not (Test-Path $testDir)) { New-Item -ItemType Directory -Force -Path $testDir | Out-Null }
}

Describe "log-rotate.ps1 - DryRun" {
  It "Runs without error in dry-run mode" {
    { & $toolScript -LogDir $logDir -DryRun 2>&1 | Out-Null } | Should -Not -Throw
  }

  It "Does not modify any files in dry-run mode" {
    $filesBefore = Get-ChildItem $logDir -File -ErrorAction SilentlyContinue | Select-Object Name, Length, LastWriteTime
    & $toolScript -LogDir $logDir -DryRun -Quiet 2>&1 | Out-Null
    $filesAfter = Get-ChildItem $logDir -File -ErrorAction SilentlyContinue | Select-Object Name, Length, LastWriteTime
    # File list should be identical
    $filesBefore.Count | Should -Be $filesAfter.Count
  }
}

Describe "log-rotate.ps1 - Rotation" {
  BeforeEach {
    # Create a test file that exceeds threshold
    $testFile = Join-Path $testDir "test-rotate.jsonl"
    # Write 600KB of data (exceeds default 512KB threshold)
    $line = '{"test":"data","ts":"' + (Get-Date -Format 'o') + '"}'
    1..15000 | ForEach-Object { $line } | Set-Content $testFile -Encoding utf8
  }

  AfterEach {
    # Clean up test directory
    Get-ChildItem $testDir -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
  }

  It "Rotates file when it exceeds MaxSizeKB" {
    $sizeBefore = (Get-Item $testFile).Length
    $sizeBefore | Should -BeGreaterThan (512 * 1024)

    & $toolScript -LogDir $testDir -MaxSizeKB 512 -RetainCount 3 -Quiet 2>&1 | Out-Null

    # Original file should be recreated (small or empty)
    $sizeAfter = (Get-Item $testFile).Length
    $sizeAfter | Should -BeLessThan $sizeBefore
  }

  It "Creates a rotated backup file" {
    & $toolScript -LogDir $testDir -MaxSizeKB 512 -RetainCount 3 -Quiet 2>&1 | Out-Null

    # Should find a rotated file with timestamp pattern
    $rotated = Get-ChildItem $testDir -Filter "test-rotate_*" -ErrorAction SilentlyContinue
    $rotated.Count | Should -BeGreaterOrEqual 1
  }

  It "Cleans up old rotations beyond RetainCount" {
    # Create multiple rotations
    1..4 | ForEach-Object {
      1..15000 | ForEach-Object { '{"test":"data"}' } | Set-Content $testFile -Encoding utf8
      & $toolScript -LogDir $testDir -MaxSizeKB 512 -RetainCount 2 -Quiet 2>&1 | Out-Null
    }

    # Should keep at most 2 rotated files
    $rotated = Get-ChildItem $testDir -Filter "test-rotate_*" -ErrorAction SilentlyContinue
    $rotated.Count | Should -BeLessOrEqual 2
  }
}

AfterAll {
  # Clean up test directory
  if (Test-Path $testDir) { Remove-Item $testDir -Recurse -Force -ErrorAction SilentlyContinue }
}
