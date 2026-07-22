BeforeAll {
  $ScriptDir = Split-Path $PSCommandPath -Parent
  $RootDir   = Split-Path $ScriptDir -Parent
  $SrcDir    = Join-Path $RootDir "src"
  $ConfigDir = Join-Path $RootDir "config"
}

Describe "MAOP-Plan — End-to-End Routing Resolution" {
  It "selects an agent for a codegen task" {
    $result = & (Join-Path $SrcDir "MAOP-plan.ps1") -Task "write a function" 2>&1 | Out-String
    $plan = $result | ConvertFrom-Json
    $plan.selected_agent | Should -Not -BeNullOrEmpty
    $plan.routing_key | Should -Not -BeNullOrEmpty
  }

  It "supports explicit routing key override" {
    $result = & (Join-Path $SrcDir "MAOP-plan.ps1") -Task "anything" -RoutingKey "codegen" 2>&1 | Out-String
    $plan = $result | ConvertFrom-Json
    $plan.routing_key | Should -Be "codegen"
  }

  It "includes budget with defaults" {
    $result = & (Join-Path $SrcDir "MAOP-plan.ps1") -Task "test task" 2>&1 | Out-String
    $plan = $result | ConvertFrom-Json
    $plan.budget.timeout_s | Should -BeGreaterThan 0
    $plan.budget.max_retries | Should -BeGreaterOrEqual 0
    $plan.budget.retry_backoff_ms | Should -BeGreaterThan 0
  }

  It "includes gates list" {
    $result = & (Join-Path $SrcDir "MAOP-plan.ps1") -Task "test task" 2>&1 | Out-String
    $plan = $result | ConvertFrom-Json
    $plan.gates.Count | Should -BeGreaterOrEqual 1
  }

  It "selects planning routing key for design task" {
    $result = & (Join-Path $SrcDir "MAOP-plan.ps1") -Task "design the architecture" 2>&1 | Out-String
    $plan = $result | ConvertFrom-Json
    $plan.routing_key | Should -Be "planning"
  }

  It "selects search routing key for research task" {
    $result = & (Join-Path $SrcDir "MAOP-plan.ps1") -Task "search documentation" 2>&1 | Out-String
    $plan = $result | ConvertFrom-Json
    $plan.routing_key | Should -Be "search"
  }

  It "builds fallback chain with at least primary agent" {
    $result = & (Join-Path $SrcDir "MAOP-plan.ps1") -Task "test task" 2>&1 | Out-String
    $plan = $result | ConvertFrom-Json
    $plan.fallback_chain.Count | Should -BeGreaterOrEqual 1
    $plan.fallback_chain[0] | Should -Be $plan.selected_agent
  }

  It "includes timestamp in ISO 8601 format" {
    $result = & (Join-Path $SrcDir "MAOP-plan.ps1") -Task "test task" 2>&1 | Out-String
    $plan = $result | ConvertFrom-Json
    $plan.timestamp | Should -Match "^\d{4}-\d{2}-\d{2}T"
  }
}

Describe "MAOP-Plan — DAG Generation (-GenerateDag)" {
  It "generates dag_steps when -GenerateDag is set" {
    $result = & (Join-Path $SrcDir "MAOP-plan.ps1") -Task "write a function" -RoutingKey "codegen" -GenerateDag 2>&1 | Out-String
    $plan = $result | ConvertFrom-Json
    $plan.dag_steps | Should -Not -BeNullOrEmpty
    $plan.dag_steps.Count | Should -BeGreaterOrEqual 2
  }

  It "does not generate dag_steps without -GenerateDag" {
    $result = & (Join-Path $SrcDir "MAOP-plan.ps1") -Task "write a function" -RoutingKey "codegen" 2>&1 | Out-String
    $plan = $result | ConvertFrom-Json
    $plan.dag_steps | Should -BeNullOrEmpty
  }

  It "codegen DAG has agent → verify → terminal" {
    $result = & (Join-Path $SrcDir "MAOP-plan.ps1") -Task "write code" -RoutingKey "codegen" -GenerateDag 2>&1 | Out-String
    $plan = $result | ConvertFrom-Json
    $types = @($plan.dag_steps | ForEach-Object { $_.type })
    $types | Should -Contain "agent"
    $types | Should -Contain "verify"
    $types | Should -Contain "terminal"
  }

  It "review DAG has verify step" {
    $result = & (Join-Path $SrcDir "MAOP-plan.ps1") -Task "review code" -RoutingKey "review" -GenerateDag 2>&1 | Out-String
    $plan = $result | ConvertFrom-Json
    $types = @($plan.dag_steps | ForEach-Object { $_.type })
    $types | Should -Contain "verify"
  }

  It "dag_steps have valid dependencies" {
    $result = & (Join-Path $SrcDir "MAOP-plan.ps1") -Task "write code" -RoutingKey "codegen" -GenerateDag 2>&1 | Out-String
    $plan = $result | ConvertFrom-Json
    $ids = @($plan.dag_steps | ForEach-Object { $_.id })
    foreach ($s in $plan.dag_steps) {
      foreach ($dep in $s.depends_on) {
        $ids | Should -Contain $dep
      }
    }
  }

  It "default routing key also generates valid DAG" {
    $result = & (Join-Path $SrcDir "MAOP-plan.ps1") -Task "do something" -RoutingKey "mcp" -GenerateDag 2>&1 | Out-String
    $plan = $result | ConvertFrom-Json
    $plan.dag_steps.Count | Should -BeGreaterOrEqual 2
    $plan.dag_steps[0].type | Should -Be "agent"
    $plan.dag_steps[-1].type | Should -Be "terminal"
  }
}
