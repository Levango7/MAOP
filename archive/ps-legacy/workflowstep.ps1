<#
.SYNOPSIS
  WorkflowStep — 统一调度单元抽象
  定义所有 step 类型和工厂函数，供 Unified Engine 消费

.STEP TYPES
  plan      → 调用 MAOP-plan.ps1 做 routing 决策
  agent     → 调用 delegate.ps1 / MAOP-execute.ps1 执行 agent
  dag       → 调用 dag-engine.ps1 执行子 DAG
  verify    → 调用 MAOP-verify.ps1 验证结果
  condition → 表达式求值 + 分支路由
  terminal  → 终点步骤，聚合输出
  feedback  → 反馈循环，条件性重试
  noop      → 空操作（占位符）

  Copyright (c) 2026 MAOP. All rights reserved.
#>

function New-WorkflowStep {
  <#
  .SYNOPSIS
    WorkflowStep 工厂函数
  .PARAMETER Id        步骤唯一标识
  .PARAMETER Type      步骤类型
  .PARAMETER Agent     目标 agent 名 (agent/verify 类型)
  .PARAMETER DagFile   DAG 定义文件路径 (dag 类型)
  .PARAMETER Params    步骤参数字典
  .PARAMETER DependsOn 依赖的上游步骤 ID 数组
  .PARAMETER Condition 条件表达式 (condition 类型)
  .PARAMETER Branches  条件分支字典 @{ $true = "next-step-id"; $false = "else-step-id" }
  .PARAMETER Retry     重试次数
  .PARAMETER Timeout   超时秒数
  .PARAMETER Description 人类可读描述
  .PARAMETER OnFailure 失败时的行为: stop | skip | fallback (默认: stop)
  .PARAMETER FallbackTo 失败时回退的 agent 名
  .OUTPUTS
    返回一个有序的 hashtable (WorkflowStep)
  #>
  param(
    [Parameter(Mandatory)]
    [string]$Id,
    [Parameter(Mandatory)]
    [ValidateSet('plan','agent','dag','verify','condition','terminal','feedback','noop')]
    [string]$Type,
    [string]$Agent = '',
    [string]$DagFile = '',
    [hashtable]$Params = @{},
    [string[]]$DependsOn = @(),
    [string]$Condition = '',
    [hashtable]$Branches = @{},
    [int]$Retry = 0,
    [int]$Timeout = 120,
    [string]$Description = '',
    [ValidateSet('stop','skip','fallback')]
    [string]$OnFailure = 'stop',
    [string]$FallbackTo = ''
  )

  if ($Type -in @('agent','verify') -and -not $Agent -and -not $Params.Agent) {
    throw "WorkflowStep [$Id] type=$Type requires -Agent or Params.Agent"
  }
  if ($Type -eq 'dag' -and -not $DagFile -and -not $Params.DagFile) {
    throw "WorkflowStep [$Id] type=dag requires -DagFile or Params.DagFile"
  }
  if ($Type -eq 'condition' -and -not $Condition) {
    throw "WorkflowStep [$Id] type=condition requires -Condition"
  }

  $step = [ordered]@{
    id          = $Id
    type        = $Type
    agent       = if ($Agent) { $Agent } elseif ($Params.Agent) { $Params.Agent } else { '' }
    dag_file    = if ($DagFile) { $DagFile } elseif ($Params.DagFile) { $Params.DagFile } else { '' }
    params      = $Params
    depends_on  = $DependsOn
    condition   = $Condition
    branches    = $Branches
    retry       = $Retry
    timeout     = $Timeout
    description = $Description
    on_failure  = $OnFailure
    fallback_to = $FallbackTo
    # Runtime (filled by engine)
    status      = 'pending'
    output      = $null
    error       = $null
    duration_ms = 0
    retry_count = 0
  }
  return $step
}

# ── 便捷工厂函数 ──

function New-PlanStep {
  param(
    [string]$Id = 'plan',
    [string]$Task = '',
    [string]$RoutingKey = '',
    [int]$Timeout = 120,
    [string[]]$DependsOn = @(),
    [string]$Description = 'Routing plan step'
  )
  New-WorkflowStep -Id $Id -Type plan -Params @{ task = $Task; routing_key = $RoutingKey } -Timeout $Timeout -DependsOn $DependsOn -Description $Description
}

function New-AgentStep {
  param(
    [Parameter(Mandatory)]
    [string]$Id,
    [string]$Agent,
    [string]$Task = '',
    [string]$RoutingKey = '',
    [int]$Retry = 1,
    [int]$Timeout = 120,
    [string[]]$DependsOn = @(),
    [string]$OnFailure = 'fallback',
    [string]$FallbackTo = '',
    [string]$Description = 'Agent execution step'
  )
  New-WorkflowStep -Id $Id -Type agent -Agent $Agent -Params @{ task = $Task; routing_key = $RoutingKey } `
    -Retry $Retry -Timeout $Timeout -DependsOn $DependsOn -OnFailure $OnFailure -FallbackTo $FallbackTo -Description $Description
}

function New-DagStep {
  param(
    [Parameter(Mandatory)]
    [string]$Id,
    [Parameter(Mandatory)]
    [string]$DagFile,
    [string]$Task = '',
    [int]$Timeout = 300,
    [string[]]$DependsOn = @(),
    [string]$Description = 'DAG sub-workflow step'
  )
  New-WorkflowStep -Id $Id -Type dag -DagFile $DagFile -Params @{ task = $Task } -Timeout $Timeout -DependsOn $DependsOn -Description $Description
}

function New-VerifyStep {
  param(
    [string]$Id = 'verify',
    [string]$Agent = 'mavis-verifier',
    [int]$Timeout = 120,
    [string[]]$DependsOn = @(),
    [string]$Description = 'Verification step'
  )
  New-WorkflowStep -Id $Id -Type verify -Agent $Agent -Timeout $Timeout -DependsOn $DependsOn -Description $Description
}

function New-ConditionStep {
  param(
    [Parameter(Mandatory)]
    [string]$Id,
    [Parameter(Mandatory)]
    [string]$Condition,
    [hashtable]$Branches = @{},
    [string[]]$DependsOn = @(),
    [string]$Description = 'Conditional branch step'
  )
  New-WorkflowStep -Id $Id -Type condition -Condition $Condition -Branches $Branches -DependsOn $DependsOn -Description $Description
}

function New-TerminalStep {
  param(
    [string]$Id = 'done',
    [string]$Description = 'Terminal / aggregation step',
    [string[]]$DependsOn = @()
  )
  New-WorkflowStep -Id $Id -Type terminal -DependsOn $DependsOn -Description $Description
}

# ── 验证函数 ──

function Assert-ValidSteps {
  <#
  .SYNOPSIS
    验证一组 WorkflowStep 的合法性（循环引用、缺失依赖、类型约束）
  #>
  param(
    [Parameter(Mandatory)]
    $AssertSteps
    )

    $ids = @{}

    # Normalize to array
    if ($AssertSteps -is [hashtable] -or $AssertSteps -is [System.Collections.Specialized.OrderedDictionary]) {
      $StepList = @($AssertSteps)
    } elseif ($AssertSteps -is [array]) {
      # Filter to step-like objects in case [array] expanded a hashtable
      $StepList = @($AssertSteps | Where-Object { $_ -is [hashtable] -or $_ -is [System.Collections.Specialized.OrderedDictionary] })
      if ($StepList.Count -eq 0) { $StepList = @($AssertSteps) }
    } else {
      $StepList = @($AssertSteps)
    }

    foreach ($s in $StepList) {
    if ($ids.ContainsKey($s.id)) { throw "Duplicate step id: $($s.id)" }
    $ids[$s.id] = $true
  }

  # 检查依赖是否存在
  foreach ($s in $Steps) {
    foreach ($dep in $s.depends_on) {
      if (-not $ids.ContainsKey($dep)) {
        throw "Step '$($s.id)' depends on '$dep' which is not in the step list"
      }
    }
  }

  # 检查 condition 类型必有 condition 表达式
  foreach ($s in $Steps) {
    if ($s.type -eq 'condition' -and -not $s.condition) {
      throw "Condition step '$($s.id)' has no condition expression"
    }
  }

  return $true
}

# ── 拓扑排序 —─

function Get-StepExecutionOrder {
  <#
  .SYNOPSIS
    对 WorkflowStep 数组进行拓扑排序
  #>
  param(
    [Parameter(Mandatory)]
    $Steps
    )

    $null = Assert-ValidSteps $Steps

    # Normalize to array: if a single hashtable/ordered-dict was passed, wrap it
    if ($Steps -is [hashtable] -or $Steps -is [System.Collections.Specialized.OrderedDictionary]) { $Steps = @($Steps) }
    # If the caller passed an array but [array] expanded a single OrderedDict's values,
    # reconstruct by checking if elements look like step objects
    if ($Steps -is [array] -and $Steps.Count -gt 1 -and $Steps[0] -isnot [hashtable] -and $Steps[0] -isnot [System.Collections.Specialized.OrderedDictionary]) {
      # Check if first element has an 'id' property — if not, we got expanded values
      $stepLike = @($Steps | Where-Object { $_ -is [hashtable] -or $_ -is [System.Collections.Specialized.OrderedDictionary] })
      if ($stepLike.Count -gt 0) { $Steps = $stepLike }
    }
  $inDegree = @{}
  $adj = @{}
  $stepMap = @{}

  foreach ($s in $Steps) {
    $inDegree[$s.id] = 0
    $adj[$s.id] = @()
    $stepMap[$s.id] = $s
  }

  foreach ($s in $Steps) {
    foreach ($dep in $s.depends_on) {
      $adj[$dep] += $s.id
      $inDegree[$s.id]++
    }
  }

  $queue = [System.Collections.Queue]::new()
  foreach ($id in $inDegree.Keys) {
    if ($inDegree[$id] -eq 0) { $queue.Enqueue($id) }
  }

  $order = @()
  while ($queue.Count -gt 0) {
    $n = $queue.Dequeue()
    $order += $stepMap[$n]
    foreach ($next in $adj[$n]) {
      $inDegree[$next]--
      if ($inDegree[$next] -eq 0) { $queue.Enqueue($next) }
    }
  }

  if ($order.Count -ne $Steps.Count) {
    throw "DAG has cycles or unreachable steps: $($Steps.Count) steps, but only $($order.Count) can be ordered"
  }

  return ,$order
}

Write-Host "[workflowstep] Module loaded: New-WorkflowStep, New-PlanStep, New-AgentStep, New-DagStep, New-VerifyStep, New-ConditionStep, New-TerminalStep, Assert-ValidSteps, Get-StepExecutionOrder"
