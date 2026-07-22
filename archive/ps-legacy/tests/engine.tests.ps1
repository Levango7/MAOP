BeforeAll {
  $ScriptDir = Split-Path $PSCommandPath -Parent
  $RootDir   = Split-Path $ScriptDir -Parent
  $SrcDir    = Join-Path $RootDir "src"

  . (Join-Path $SrcDir "workflowstep.ps1") | Out-Null

  # ── Mock external scripts ──
  $script:mockPlanResult = '{ "selected_agent":"claude","routing_key":"codegen","fallback_chain":["claude","cursor"],"budget":{"timeout_s":120},"gates":["lint"] }'
  $script:mockDelegateResult = '{ "exit_code":0,"stdout":"done","agent":"claude","duration_ms":500,"error":null }'

  # Mock the engine's external script calls by creating a mock delegate.ps1
  $script:stubDir = Join-Path $env:TEMP "engine-stubs-$(Get-Random)"
  $null = New-Item -ItemType Directory -Path $script:stubDir -Force

  # Mock delegate.ps1
  @"
param(`$Agent, `$Task, `$RoutingKey, `$WorkDir, `$TimeoutSeconds, `$TraceID)
Write-Output "`$env:mockDelegateResult"
"@ | Set-Content -Path (Join-Path $script:stubDir 'delegate.ps1') -Force

  # Mock MAOP-plan.ps1
  @"
param(`$Task, `$WorkDir, `$RoutingKey)
Write-Output "`$env:mockPlanResult"
"@ | Set-Content -Path (Join-Path $script:stubDir 'MAOP-plan.ps1') -Force
}

Describe "Engine — WorkflowStep tests (dot-source only, no real scripts)" {
  It "New-WorkflowStep with agent defaults" {
    $s = New-WorkflowStep -Id "test" -Type agent -Agent "claude"
    $s.id | Should -Be "test"
    $s.type | Should -Be "agent"
    $s.agent | Should -Be "claude"
    $s.status | Should -Be "pending"
    $s.on_failure | Should -Be "stop"
  }

  It "Get-StepExecutionOrder sorts simple linear chain" {
    $steps = @(
      New-WorkflowStep -Id "a" -Type agent -Agent "c1"
      New-WorkflowStep -Id "b" -Type agent -Agent "c2" -DependsOn @("a")
    )
    $order = Get-StepExecutionOrder $steps
    $order[0].id | Should -Be "a"
    $order[1].id | Should -Be "b"
  }
}

Describe "Engine — Topological sort" {
  It "sorts parallel + merge" {
    $steps = @(
      New-AgentStep -Id "a" -Agent "c1" -Task "t"
      New-AgentStep -Id "b" -Agent "c2" -Task "t" -DependsOn @("a")
      New-AgentStep -Id "c" -Agent "c3" -Task "t" -DependsOn @("a")
      New-AgentStep -Id "d" -Agent "c4" -Task "t" -DependsOn @("b","c")
    )
    $order = Get-StepExecutionOrder $steps
    $order.Count | Should -Be 4
    $order[0].id | Should -Be "a"
    $order[3].id | Should -Be "d"
  }
}

AfterAll {
  if (Test-Path $script:stubDir) {
    Remove-Item -Path $script:stubDir -Recurse -Force -ErrorAction SilentlyContinue
  }
}
