BeforeAll {
  $ScriptDir = Split-Path $PSCommandPath -Parent
  $RootDir = Split-Path $ScriptDir -Parent
  $BreakerFile = Join-Path (Join-Path $RootDir "data") "circuit-breaker.json"
  $SrcDir = Join-Path $RootDir "src"
}

Describe "Circuit Breaker — Module Load" {
  It "should dot-source without errors" {
    { . (Join-Path $SrcDir "circuit-breaker.ps1") } | Should -Not -Throw
  }

  It "should define all three functions" {
    . (Join-Path $SrcDir "circuit-breaker.ps1")
    Test-Path "Function:\Get-BreakerState" | Should -Be $true
    Test-Path "Function:\Set-BreakerState" | Should -Be $true
    Test-Path "Function:\Initialize-BreakerFile" | Should -Be $true
  }
}

Describe "Circuit Breaker — State Machine" {
  BeforeAll {
    . (Join-Path $SrcDir "circuit-breaker.ps1")
    # 备份原始断路器文件
    if (Test-Path $BreakerFile) {
      $script:BreakerBak = Get-Content $BreakerFile -Raw
    }
  }
  AfterAll { 
    if ($script:BreakerBak) {
      Set-Content $BreakerFile $script:BreakerBak -Force
    }
  }

  It "starts in closed state for all agents" {
    $state = Get-BreakerState nvidia
    $state.state | Should -Be "closed"
  }

  It "transitions to open when failures >= threshold" {
    # Set 3 failures and open state
    Set-BreakerState -AgentName nvidia -State open -Failures 3
    $state = Get-BreakerState nvidia
    $state.state | Should -Be "open"
    $state.failures | Should -Be 3
  }

  It "resets to closed" {
    Set-BreakerState -AgentName nvidia -State closed -Failures 0
    $state = Get-BreakerState nvidia
    $state.state | Should -Be "closed"
    $state.failures | Should -Be 0
  }

  It "supports half-open state" {
    Set-BreakerState -AgentName nvidia -State "half-open"
    $state = Get-BreakerState nvidia
    $state.state | Should -Be "half-open"
  }

  It "creates unknown agents on demand" {
    Set-BreakerState -AgentName test-agent-xyz -State closed
    $state = Get-BreakerState "test-agent-xyz"
    $state | Should -Not -BeNullOrEmpty
    $state.state | Should -Be "closed"
  }
}

Describe "Circuit Breaker — File Persistence" {
  BeforeAll {
    . (Join-Path $SrcDir "circuit-breaker.ps1")
  }

  It "writes data to circuit-breaker.json" {
    Set-BreakerState -AgentName persistence-test -State open -Failures 5
    $content = Get-Content $BreakerFile -Raw | ConvertFrom-Json
    $content.'persistence-test'.state | Should -Be "open"
    $content.'persistence-test'.failures | Should -Be 5
  }

  It "reads back consistent state after JSON round-trip" {
    $state = Get-BreakerState "persistence-test"
    $state.state | Should -Be "open"
    $state.failures | Should -Be 5
  }

  It "cleans up test agents" {
    Set-BreakerState -AgentName nvidia -State closed -Failures 0
  }
}

<#
.SYNOPSIS
  备份/恢复断路器文件
#>
function Backup-Breaker {
  if (Test-Path $BreakerFile) {
    $script:BreakerBackup = Get-Content $BreakerFile -Raw
  }
}
function Restore-Breaker {
  if ($script:BreakerBackup) {
    Set-Content $BreakerFile $script:BreakerBackup -Force
  }
}
