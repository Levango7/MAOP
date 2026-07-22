BeforeAll {
  $ScriptDir = Split-Path $PSCommandPath -Parent
  $RootDir = Split-Path $ScriptDir -Parent
  $SrcDir = Join-Path $RootDir "src"
  $LockPath = Join-Path (Join-Path $RootDir "data") "test-lock-file.tmp"
}

Describe "FileLock — Invoke-WithFileLock" {
  BeforeAll {
    . (Join-Path $SrcDir "filelock.ps1")
  }
  AfterEach {
    Remove-Item "$LockPath.lock" -Force -ErrorAction SilentlyContinue
    Remove-Item $LockPath -Force -ErrorAction SilentlyContinue
  }

  It "runs the script and returns output" {
    $result = Invoke-WithFileLock -Path $LockPath -Script { "hello from lock" } -TimeoutSeconds 5
    $result | Should -Be "hello from lock"
  }

  It "creates and cleans up .lock file" {
    Invoke-WithFileLock -Path $LockPath -Script { "locked" } -TimeoutSeconds 5
    Test-Path "$LockPath.lock" | Should -Be $false
  }

  It "times out when lock is held" {
    # Create an orphan lock file (future timestamp so it's not cleaned)
    Set-Content "$LockPath.lock" "held by test"
    # Set its creation time to now so it's not treated as orphan (>30s old)
    $now = Get-Date
    (Get-Item "$LockPath.lock").CreationTime = $now

    # Test with very short timeout
    { Invoke-WithFileLock -Path $LockPath -Script { "won't run" } -TimeoutSeconds 1 } | Should -Throw
  }
}

Describe "FileLock — Orphan Lock Cleanup" {
  BeforeAll {
    . (Join-Path $SrcDir "filelock.ps1")
  }
  AfterEach {
    Remove-Item "$LockPath.lock" -Force -ErrorAction SilentlyContinue
    Remove-Item $LockPath -Force -ErrorAction SilentlyContinue
  }

  It "cleans up orphan locks older than 30 seconds" {
    # Create a very old lock file
    Set-Content "$LockPath.lock" "old stale lock"
    $oldDate = (Get-Date).AddSeconds(-60)
    (Get-Item "$LockPath.lock").CreationTime = $oldDate
    (Get-Item "$LockPath.lock").LastWriteTime = $oldDate

    # Should be able to acquire lock (orphan cleaned)
    $result = Invoke-WithFileLock -Path $LockPath -Script { "cleaned orphan" } -TimeoutSeconds 3
    $result | Should -Be "cleaned orphan"
  }
}
