BeforeAll {
  $ScriptDir = Split-Path $PSCommandPath -Parent
  $RootDir   = Split-Path $ScriptDir -Parent
  $SrcDir    = Join-Path $RootDir "src"
  . (Join-Path $SrcDir "workflowstep.ps1") | Out-Null
}

Describe "WorkflowStep — New-WorkflowStep factory" {
  It "creates a valid agent step" {
    $s = New-WorkflowStep -Id "test" -Type agent -Agent "claude" -Params @{ task = "hello" }
    $s.id | Should -Be "test"
    $s.type | Should -Be "agent"
    $s.agent | Should -Be "claude"
    $s.params.task | Should -Be "hello"
    $s.status | Should -Be "pending"
  }

  It "throws when agent type has no agent" {
    { New-WorkflowStep -Id "bad" -Type agent } | Should -Throw
  }

  It "throws when dag type has no file" {
    { New-WorkflowStep -Id "bad" -Type dag } | Should -Throw
  }

  It "throws when condition type has no expression" {
    { New-WorkflowStep -Id "bad" -Type condition } | Should -Throw
  }

  It "accepts condition with expression" {
    $s = New-WorkflowStep -Id "c" -Type condition -Condition "{{ result }} == true"
    $s.condition | Should -Not -BeNullOrEmpty
  }

  It "accepts all valid types" {
    foreach ($t in @('plan','agent','dag','verify','condition','terminal','feedback','noop')) {
      $s = New-WorkflowStep -Id "t-$t" -Type $t -Agent "x" -DagFile "test.yaml" -Condition "true"
      $s.type | Should -Be $t
    }
  }
}

Describe "WorkflowStep — Convenience factory functions" {
  It "New-PlanStep creates plan step" {
    $s = New-PlanStep -Task "do something" -RoutingKey "codegen"
    $s.type | Should -Be "plan"
    $s.params.task | Should -Be "do something"
    $s.params.routing_key | Should -Be "codegen"
  }

  It "New-AgentStep creates agent step" {
    $s = New-AgentStep -Id "step1" -Agent "claude" -Task "write code" -Retry 2 -FallbackTo "cursor"
    $s.type | Should -Be "agent"
    $s.agent | Should -Be "claude"
    $s.retry | Should -Be 2
    $s.fallback_to | Should -Be "cursor"
  }

  It "New-DagStep creates dag step" {
    $s = New-DagStep -Id "d" -DagFile "data/review.yaml" -Task "review code"
    $s.type | Should -Be "dag"
    $s.dag_file | Should -Be "data/review.yaml"
  }

  It "New-VerifyStep creates verify step" {
    $s = New-VerifyStep -Agent "mavis-verifier"
    $s.type | Should -Be "verify"
    $s.agent | Should -Be "mavis-verifier"
  }

  It "New-ConditionStep creates condition step" {
    $s = New-ConditionStep -Id "decide" -Condition "{{ verify.passed }} == false" -Branches @{ true = "fix"; false = "done" }
    $s.type | Should -Be "condition"
    $s.condition | Should -Match "verify"
    $s.branches["true"] | Should -Be "fix"
  }

  It "New-TerminalStep creates terminal step" {
    $s = New-TerminalStep -Id "done" -DependsOn @("review")
    $s.type | Should -Be "terminal"
    $s.depends_on | Should -Contain "review"
  }
}

Describe "WorkflowStep — Assert-ValidSteps" {
  It "passes for valid step list" {
    $steps = @(
      New-AgentStep -Id "a" -Agent "claude" -Task "t"
      New-AgentStep -Id "b" -Agent "cursor" -Task "t" -DependsOn @("a")
    )
    Assert-ValidSteps $steps | Should -Be $true
  }

  It "throws on duplicate ids" {
    $steps = @(
      New-AgentStep -Id "dup" -Agent "claude" -Task "t"
      New-AgentStep -Id "dup" -Agent "cursor" -Task "t"
    )
    { Assert-ValidSteps $steps } | Should -Throw
  }

  It "throws on missing dependency" {
    $steps = @(
      New-AgentStep -Id "a" -Agent "claude" -Task "t" -DependsOn @("nonexistent")
    )
    { Assert-ValidSteps $steps } | Should -Throw
  }
}

Describe "WorkflowStep — Get-StepExecutionOrder topological sort" {
  It "orders independent steps first" {
    $steps = @(
      New-AgentStep -Id "a" -Agent "claude" -Task "t"
      New-AgentStep -Id "b" -Agent "cursor" -Task "t" -DependsOn @("a")
    )
    $order = Get-StepExecutionOrder $steps
    $order[0].id | Should -Be "a"
    $order[1].id | Should -Be "b"
  }

  It "handles diamond dependency" {
    $steps = @(
      New-AgentStep -Id "root" -Agent "claude" -Task "t"
      New-AgentStep -Id "left" -Agent "cursor" -Task "t" -DependsOn @("root")
      New-AgentStep -Id "right" -Agent "kimi" -Task "t" -DependsOn @("root")
      New-AgentStep -Id "merge" -Agent "openclaw" -Task "t" -DependsOn @("left","right")
    )
    $order = Get-StepExecutionOrder $steps
    $order.Count | Should -Be 4
    $order[0].id | Should -Be "root"
    $order[3].id | Should -Be "merge"
  }

  It "throws on cycle" {
    $steps = @(
      New-AgentStep -Id "a" -Agent "claude" -Task "t" -DependsOn @("b")
      New-AgentStep -Id "b" -Agent "cursor" -Task "t" -DependsOn @("a")
    )
    { Get-StepExecutionOrder $steps } | Should -Throw
  }

  It "handles single step" {
    $steps = @(
      New-AgentStep -Id "only" -Agent "claude" -Task "t"
    )
    $order = Get-StepExecutionOrder $steps
    $order.Count | Should -Be 1
    $order[0].id | Should -Be "only"
  }
}
