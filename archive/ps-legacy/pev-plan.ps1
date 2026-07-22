<#
.SYNOPSIS
  MAOP Planner — Routing & Budget resolver
.DESCRIPTION
  Reads agents.yaml routing table, matches Task keywords to routing_key,
  resolves primary agent + fallback chain, loads retry/timeout config,
  and outputs a Plan JSON for MAOP-loop.ps1 or engine.ps1.

  When -GenerateDag is specified, also generates a WorkflowStep array
  for multi-step DAG execution (e.g., review → verify for 'review' key).

.PARAMETER Task
  Task description — keyword-matched to routing_key.

.PARAMETER WorkDir
  Working directory (default: current dir).

.PARAMETER RoutingKey
  Explicit routing key override. When set, skips keyword matching.

.PARAMETER GenerateDag
  When set, generates WorkflowStep array for multi-step execution.
  Steps are output as 'dag_steps' in the Plan JSON.

.OUTPUTS
  JSON string via ConvertTo-Json -Depth 5 to stdout.
  Fields: phase, task, workdir, routing_key, selected_agent,
          fallback_chain, budget, gates, timestamp, dag_steps (if -GenerateDag).

.EXAMPLE
  .\MAOP-plan.ps1 -Task "write a function"
  .\MAOP-plan.ps1 -Task "review code" -GenerateDag
#>

param(
  [Parameter(Mandatory = $true)]
  [string]$Task,

  [string]$WorkDir = (Get-Location).Path,

  [string]$RoutingKey = "",

  [switch]$GenerateDag
)

# ══════════════════════════════════════════════════
# Resolve paths
# ══════════════════════════════════════════════════
$ScriptDir = Split-Path $PSCommandPath -Parent
$ProjectRoot = Split-Path $ScriptDir -Parent
$ConfigDir = Join-Path $ProjectRoot "config"
$AgentsFile = Join-Path $ConfigDir "agents.yaml"
$RulesFile  = Join-Path $ConfigDir "rules.yaml"

# ══════════════════════════════════════════════════
# YAML bridge: dot-source 共享脚本（代替内联定义）
# ══════════════════════════════════════════════════
. (Join-Path $ProjectRoot "tools\MAOP-bridge.ps1")

# ══════════════════════════════════════════════════
# 1. Parse routing table from agents.yaml (Python bridge)
# ══════════════════════════════════════════════════
$routingData = Invoke-ConfigBridge "--section routing" -Critical
$routingTable = @{}
foreach ($prop in $routingData.PSObject.Properties) {
  $rk = $prop.Name
  $entry = $prop.Value
  $routingTable[$rk] = @{
    primary   = $entry.primary
    fallback  = $entry.fallback
    tertiary  = $entry.tertiary
  }
}

# ══════════════════════════════════════════════════
# 2. Resolve routing_key
# ══════════════════════════════════════════════════
$allRoutingKeys = $routingTable.Keys | Sort-Object

if (-not [string]::IsNullOrWhiteSpace($RoutingKey)) {
  # Explicit routing key override
  if (-not $routingTable.ContainsKey($RoutingKey)) {
    Write-Warning "MAOP-plan: Unknown routing key '$RoutingKey', falling back to keyword matching"
    $RoutingKey = ""
  }
}

if ([string]::IsNullOrWhiteSpace($RoutingKey)) {
  # ── Keyword matching ──
  $taskLower = $Task.ToLowerInvariant()

  # Define keyword sets for each routing key (most specific first)
  $keywordMap = @{
    codegen  = @("write", "create", "implement", "refactor", "fix", "add", "build",
                 "develop", "写", "创建", "实现", "重构", "修复", "编写",
                 "code", "function", "class", "method", "feature", "generate",
                 "开发", "修改", "添加", "构造")
    planning = @("plan", "design", "architecture", "strategy", "roadmap",
                 "规划", "计划", "设计", "方案", "流程", "架构",
                 "outline", "diagram", "spec", "specification", "蓝图")
    search   = @("search", "find", "lookup", "research", "information", "query",
                 "搜索", "查找", "查询", "调研", "研究", "检索",
                 "documentation", "docs", "wiki", "what is", "how to")
    review   = @("review", "audit", "inspect", "verify", "validate",
                 "审查", "审核", "检查", "评审", "审计",
                 "code review", "check", "test", "安全")
    chat     = @("chat", "talk", "discuss", "ask", "question",
                 "聊天", "讨论", "问答", "咨询", "对话",
                 "help", "what do you think", "opinion")
  }

  # Score each routing key by number of matching keywords
  $scores = @{}
  foreach ($rk in $keywordMap.Keys) {
    $score = 0
    foreach ($kw in $keywordMap[$rk]) {
      if ($taskLower -match "\b$([regex]::Escape($kw))\b" -or $taskLower.Contains($kw)) {
        $score++
      }
    }
    $scores[$rk] = $score
  }

  # Pick the highest-scoring routing key (tie → first in sort order)
  $bestScore = 0
  $bestKey = "codegen"  # default
  foreach ($rk in ($allRoutingKeys | Where-Object { $scores.ContainsKey($_) })) {
    if ($scores[$rk] -gt $bestScore) {
      $bestScore = $scores[$rk]
      $bestKey = $rk
    }
  }
  $RoutingKey = $bestKey
}

Write-Host "[MAOP-plan] routing_key = $RoutingKey" -ForegroundColor DarkGray

# ══════════════════════════════════════════════════
# 3. Resolve selected_agent + fallback_chain
# ══════════════════════════════════════════════════
$route = $routingTable[$RoutingKey]
if (-not $route) {
  Write-Warning "MAOP-plan: No routing entry for '$RoutingKey', using defaults"
  $route = @{ primary = "claude"; fallback = "kimi"; tertiary = "qoder" }
}

$selectedAgent = $route["primary"]
if ([string]::IsNullOrWhiteSpace($selectedAgent)) {
  $selectedAgent = "claude"
}

# Build fallback chain: primary → fallback → tertiary (deduplicated)
$fallbackChain = @($selectedAgent)
foreach ($level in @("fallback", "tertiary")) {
  if ($route.ContainsKey($level) -and (-not [string]::IsNullOrWhiteSpace($route[$level]))) {
    $agent = $route[$level]
    if ($fallbackChain -notcontains $agent) {
      $fallbackChain += $agent
    }
  }
}

Write-Host "[MAOP-plan] selected_agent = $selectedAgent" -ForegroundColor DarkGray
Write-Host "[MAOP-plan] fallback_chain = $($fallbackChain -join ' → ')" -ForegroundColor DarkGray

# ══════════════════════════════════════════════════
# 3.5 Dynamic routing (optional — fallback to static)
# ══════════════════════════════════════════════════
$dynamicAgents = $null

function Get-DynamicRouting {
  <#
  .SYNOPSIS
    Invoke dynamic-router.ps1 to get score-ranked agents for a routing key.
    Falls back silently if the script is missing or fails.
  #>
  param([string]$RouteKey)

  $dynRouter = Join-Path (Split-Path $PSCommandPath -Parent) "dynamic-router.ps1"
  if (-not (Test-Path $dynRouter)) {
    Write-Host "[MAOP-plan] dynamic-router.ps1 not found, skipping" -ForegroundColor DarkGray
    return $null
  }

  try {
    # Call the dynamic router and capture JSON output
    $rawJson = & $dynRouter -Refresh:$false 2>&1 | Out-String
    $dynamicData = $rawJson | ConvertFrom-Json

    if ($null -eq $dynamicData) {
      Write-Host "[MAOP-plan] dynamic-router returned null" -ForegroundColor DarkGray
      return $null
    }
    if ($null -eq $dynamicData.$RouteKey) {
      Write-Host "[MAOP-plan] no dynamic data for routing key '$RouteKey'" -ForegroundColor DarkGray
      return $null
    }

    return $dynamicData.$RouteKey
  } catch {
    Write-Warning "MAOP-plan: Dynamic routing failed — $($_.Exception.Message)"
    return $null
  }
}

# Try dynamic routing — reorder selected_agent and fallback_chain by score
$dynamicAgents = Get-DynamicRouting -RouteKey $RoutingKey
if ($dynamicAgents -and ($dynamicAgents.Count -gt 0)) {
  Write-Host "[MAOP-plan] dynamic routing active for '$RoutingKey'" -ForegroundColor Cyan

  # Top-ranked agent becomes selected_agent
  $topAgent = $dynamicAgents[0].agent
  if (-not [string]::IsNullOrWhiteSpace($topAgent)) {
    $selectedAgent = $topAgent
  }

  # Rebuild fallback_chain: top agent first, remaining agents by score order
  $dynamicNames = $dynamicAgents | ForEach-Object { $_.agent }
  $fallbackChain = @($topAgent) + ($dynamicNames | Where-Object { $_ -ne $topAgent })

  Write-Host "[MAOP-plan] dynamic selected_agent = $selectedAgent" -ForegroundColor Cyan
  Write-Host "[MAOP-plan] dynamic fallback_chain = $($fallbackChain -join ' → ')" -ForegroundColor Cyan
}

# ══════════════════════════════════════════════════
# 4. Load budget + loop config (Python bridge — unified)
# ══════════════════════════════════════════════════
$timeoutS   = 120
$maxRetries = 2
$backoffMs  = 2000

# Rules (guards) from rules.yaml (if exists)
$rulesData = Invoke-ConfigBridge "--section rules"
if ($rulesData -and $rulesData.max_retries) {
  $maxRetries = [int]$rulesData.max_retries
  $backoffMs  = [int]$rulesData.retry_backoff_ms
  $timeoutS   = [int]$rulesData.timeout_s
}

# Loops (iterative retry config from agents.yaml)
$loopsData = Invoke-ConfigBridge "--section loops"
if ($loopsData -and $loopsData.iterative) {
  $loops = $loopsData.iterative
  if ($loops.max_attempts) { $maxRetries = [int]$loops.max_attempts }
  if ($loops.backoff_ms)   { $backoffMs  = [int]$loops.backoff_ms }
}

# ══════════════════════════════════════════════════
# 5. Build gates list (based on routing_key)
# ══════════════════════════════════════════════════
$gates = @()
switch ($RoutingKey) {
  "codegen"  { $gates = @("lint", "security-scan", "syntax-check") }
  "refactor" { $gates = @("syntax-check", "security-scan", "benchmark-compare") }
  "search"   { $gates = @("source-verify", "freshness-check") }
  "planning" { $gates = @("consistency-check", "feasibility-check") }
  "review"   { $gates = @("consistency-check", "security-scan", "quality-gate") }
  "verify"   { $gates = @("consistency-check", "quality-gate") }
  "chat"     { $gates = @("content-safety") }
  "quickfix" { $gates = @("syntax-check", "lint") }
  "fileops"  { $gates = @("dry-run", "path-safety") }
  "mcp"      { $gates = @("connectivity-check") }
  default    { $gates = @("lint", "syntax-check") }
}

# ══════════════════════════════════════════════════
# 5.5. DAG generation (if -GenerateDag)
# ══════════════════════════════════════════════════
$dagSteps = @()

if ($GenerateDag) {
  # Dot-source workflowstep module
  $wfsScript = Join-Path $ScriptDir "workflowstep.ps1"
  if (Test-Path $wfsScript) { . $wfsScript | Out-Null }

  switch ($RoutingKey) {
    "review" {
      # review → 3-step DAG: agent → verify → terminal
      $dagSteps = @(
        (New-AgentStep -Id "agent-review" -Agent $selectedAgent -Task $Task -Retry $maxRetries -Timeout $timeoutS -Description "Review code/logic"),
        (New-VerifyStep -Id "verify-review" -Agent "mavis-verifier" -DependsOn @("agent-review") -Description "Validate review findings"),
        (New-TerminalStep -Id "done" -DependsOn @("verify-review") -Description "Aggregate review results")
      )
    }
    "verify" {
      # verify → standalone verify step
      $dagSteps = @(
        (New-AgentStep -Id "agent-verify-core" -Agent $selectedAgent -Task $Task -Retry $maxRetries -Timeout $timeoutS -Description "Run verification"),
        (New-TerminalStep -Id "done" -DependsOn @("agent-verify-core") -Description "Report verification")
      )
    }
    "codegen" {
      # codegen → 3-step: plan → execute → verify + feedback loop
      $dagSteps = @(
        (New-AgentStep -Id "agent-codegen" -Agent $selectedAgent -Task $Task -Retry $maxRetries -Timeout $timeoutS -Description "Generate/refactor code" -OnFailure "fallback" -FallbackTo $fallbackChain[1]),
        (New-VerifyStep -Id "verify-codegen" -Agent "mavis-verifier" -DependsOn @("agent-codegen") -Description "Verify generated code"),
        (New-TerminalStep -Id "done" -DependsOn @("verify-codegen") -Description "Finalize codegen")
      )
    }
    "refactor" {
      $dagSteps = @(
        (New-AgentStep -Id "agent-refactor" -Agent $selectedAgent -Task $Task -Retry $maxRetries -Timeout $timeoutS -Description "Refactor code" -OnFailure "fallback" -FallbackTo $fallbackChain[1]),
        (New-VerifyStep -Id "verify-refactor" -Agent "mavis-verifier" -DependsOn @("agent-refactor") -Description "Validate refactoring"),
        (New-TerminalStep -Id "done" -DependsOn @("verify-refactor"))
      )
    }
    "search" {
      $dagSteps = @(
        (New-AgentStep -Id "agent-search" -Agent $selectedAgent -Task $Task -Retry $maxRetries -Timeout $timeoutS -Description "Search research"),
        (New-TerminalStep -Id "done" -DependsOn @("agent-search") -Description "Compile search results")
      )
    }
    "docgen" {
      # Use DAG for multi-stage document generation
      $dagSteps = @(
        (New-AgentStep -Id "agent-doc-outline" -Agent $selectedAgent -Task "Outline: $Task" -Retry 1 -Timeout ($timeoutS * 2) -Description "Draft document outline"),
        (New-AgentStep -Id "agent-doc-write" -Agent $selectedAgent -Task "Write: $Task" -DependsOn @("agent-doc-outline") -Retry $maxRetries -Timeout ($timeoutS * 2) -Description "Write full document"),
        (New-VerifyStep -Id "verify-doc" -Agent "mavis-verifier" -DependsOn @("agent-doc-write") -Description "Verify document quality"),
        (New-TerminalStep -Id "done" -DependsOn @("verify-doc"))
      )
    }
    default {
      # For other routing keys, generate a simple single-agent DAG
      $dagSteps = @(
        (New-AgentStep -Id "agent-$RoutingKey" -Agent $selectedAgent -Task $Task -Retry $maxRetries -Timeout $timeoutS -Description "Execute: $RoutingKey" -OnFailure "fallback" -FallbackTo $fallbackChain[1]),
        (New-TerminalStep -Id "done" -DependsOn @("agent-$RoutingKey"))
      )
    }
  }
}

# ══════════════════════════════════════════════════
# 7. Config validation check (non-blocking warnings)
# ══════════════════════════════════════════════════
$validateScript = Join-Path $ScriptDir "validate-config.ps1"
if (Test-Path $validateScript) {
  $validationResult = & $validateScript -Json:$true 2>&1 | Out-String
  $validation = try { $validationResult | ConvertFrom-Json } catch { $null }
  if ($validation -and -not $validation.valid) {
    Write-Host "[MAOP-plan] ⚠️ Config validation found errors:" -ForegroundColor Yellow
    foreach ($err in $validation.errors) {
      Write-Host "[MAOP-plan]   ❌ $err" -ForegroundColor Red
    }
    foreach ($warn in $validation.warnings) {
      Write-Host "[MAOP-plan]   ⚠️ $warn" -ForegroundColor Yellow
    }
  }
}

# ══════════════════════════════════════════════════
# 6. Build & output Plan JSON
# ══════════════════════════════════════════════════
$plan = @{
  phase          = "plan"
  task           = $Task
  workdir        = $WorkDir
  routing_key    = $RoutingKey
  selected_agent = $selectedAgent
  fallback_chain = $fallbackChain
  budget         = @{
    timeout_s        = $timeoutS
    max_retries      = $maxRetries
    retry_backoff_ms = $backoffMs
  }
  gates          = $gates
  dynamic_agents = $dynamicAgents
  timestamp      = (Get-Date -Format "o")
}

# Attach DAG steps if generated
if ($dagSteps.Count -gt 0) {
  $plan.dag_steps = @($dagSteps)
}

# Output to stdout as JSON
$plan | ConvertTo-Json -Depth 5
