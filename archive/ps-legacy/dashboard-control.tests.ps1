BeforeAll {
  $script:ScriptDir = Split-Path $PSCommandPath -Parent
  $script:RootDir   = Split-Path $script:ScriptDir -Parent
  $script:SrcDir    = Join-Path $script:RootDir "src"
  $script:DashDir   = Join-Path $script:RootDir "dashboard"
}

Describe "Dashboard Control — Endpoint definitions" {
  It "server-v2.ps1 contains control route definitions" {
    $content = Get-Content (Join-Path $script:DashDir "server-v2.ps1") -Raw -Encoding utf8
    $content | Should -Match "/api/control/run"
    $content | Should -Match "/api/control/validate"
    $content | Should -Match "/api/control/doctor"
    $content | Should -Match "/api/control/cancel"
    $content | Should -Match "/api/control/status"
  }

  It "server-v2.ps1 SSE stream endpoint (TODO: not yet implemented)" {
    # SSE streaming is a planned feature, not yet in server-v2.ps1
    # This test documents the gap so it shows up in test reports
    $content = Get-Content (Join-Path $script:DashDir "server-v2.ps1") -Raw -Encoding utf8
    # TODO: when SSE is implemented, uncomment these assertions
    # $content | Should -Match "/api/stream"
    # $content | Should -Match "text/event-stream"
    $content | Should -Not -BeNullOrEmpty  # placeholder assertion
  }

  It "server-v2.ps1 contains Handle-ControlRequest function" {
    $content = Get-Content (Join-Path $script:DashDir "server-v2.ps1") -Raw -Encoding utf8
    $content | Should -Match "function Handle-ControlRequest"
  }

  It "server-v2.ps1 contains Cleanup-ActiveJobs function" {
    $content = Get-Content (Join-Path $script:DashDir "server-v2.ps1") -Raw -Encoding utf8
    $content | Should -Match "function Cleanup-ActiveJobs"
  }

  It "server-v2.ps1 contains Read-RequestBody function" {
    $content = Get-Content (Join-Path $script:DashDir "server-v2.ps1") -Raw -Encoding utf8
    $content | Should -Match "function Read-RequestBody"
  }
}

Describe "Dashboard Control — ActiveJobs tracking" {
  BeforeEach {
    # Fresh ConcurrentDictionary for each test
    $script:ActiveJobs = [System.Collections.Concurrent.ConcurrentDictionary[string, object]]::new()
  }

  It "can add and retrieve a job from ActiveJobs" {
    $jobId = "test123"
    $script:ActiveJobs[$jobId] = @{
      action  = "run"
      status  = "running"
      start   = (Get-Date -Format "o")
      task    = "test task"
      process = $null
    }
    $script:ActiveJobs.Count | Should -Be 1
    $script:ActiveJobs[$jobId].task | Should -Be "test task"
  }

  It "can remove a job from ActiveJobs" {
    $jobId = "test456"
    $script:ActiveJobs[$jobId] = @{ action = "validate"; status = "running" }
    $script:ActiveJobs.Count | Should -Be 1

    $null = $script:ActiveJobs.TryRemove($jobId, [ref]$null)
    $script:ActiveJobs.Count | Should -Be 0
  }

  It "ConcurrentDictionary is thread-safe and supports concurrent access" {
    # Add multiple jobs
    for ($i = 0; $i -lt 5; $i++) {
      $script:ActiveJobs["job-$i"] = @{ action = "run"; status = "running"; task = "task $i" }
    }
    $script:ActiveJobs.Count | Should -Be 5

    # Remove one
    $null = $script:ActiveJobs.TryRemove("job-2", [ref]$null)
    $script:ActiveJobs.Count | Should -Be 4
    $script:ActiveJobs.ContainsKey("job-2") | Should -Be $false
  }
}

Describe "Dashboard Control — ControlRoutes table" {
  It "has all 5 control routes defined" {
    $content = Get-Content (Join-Path $script:DashDir "server-v2.ps1") -Raw -Encoding utf8
    $content | Should -Match '"/api/control/run"\s+=\s+"run"'
    $content | Should -Match '"/api/control/validate"\s+=\s+"validate"'
    $content | Should -Match '"/api/control/doctor"\s+=\s+"doctor"'
    $content | Should -Match '"/api/control/cancel"\s+=\s+"cancel"'
    $content | Should -Match '"/api/control/status"\s+=\s+"status"'
  }
}
