<#
.SYNOPSIS
  MAOP DAG Engine — 有向无环图编排引擎
  支持: 并行 fan-out / 同步屏障 / 条件分支 / 变量传递

.DESCRIPTION
  读取 YAML 格式的 DAG 工作流定义，按拓扑序调度节点。
  每个节点完成后，将 output 存入 $Context 供下游节点引用。
  条件节点根据上游输出做分支决策。

.PARAMETER DagFile
  DAG 工作流定义文件路径（YAML）

.PARAMETER Task
  注入到 {{ task }} 占位符的任务描述

.PARAMETER WorkDir
  工作目录

.EXAMPLE
  .\dag-engine.ps1 -DagFile "data\dags\review.yaml" -Task "审查 src/main.py"
#>

param(
  [string]$DagFile,
  [string]$Task,
  [string]$WorkDir = (Get-Location).Path,
  [string]$TraceID = [guid]::NewGuid().ToString("N")
)

$ScriptDir = Split-Path $PSCommandPath -Parent
$MAOP = Split-Path $ScriptDir -Parent

# ── SQLite checkpoint support (dot-source database.ps1 with param isolation) ──
$PEV_HasSQLite = $false
$__saved_pev_DbAction = $global:DbAction
$__saved_pev_Sql = $global:Sql
$__saved_pev_DbPath = $global:DbPath
try {
  . (Join-Path $MAOP "src\database.ps1")
  $PEV_HasSQLite = $script:PEV_HasSQLite
} catch {
  Write-Verbose "[dag] SQLite not available for checkpoints: $_"
} finally {
  $global:DbAction = $__saved_pev_DbAction
  $global:Sql = $__saved_pev_Sql
  $global:DbPath = $__saved_pev_DbPath
  Remove-Variable -Name __saved_pev_DbAction,__saved_pev_Sql,__saved_pev_DbPath -ErrorAction SilentlyContinue
}

# ── 读取 DAG 定义 (Python bridge with PS fallback) ──
function Read-DagYaml($path) {
  # Try Python bridge first
  $python = $null
  if (Get-Command python -ErrorAction SilentlyContinue) { $python = "python" }
  elseif (Get-Command py -ErrorAction SilentlyContinue) { $python = @("py","-3") }
  elseif (Get-Command python3 -ErrorAction SilentlyContinue) { $python = "python3" }
  
  if ($python) {
    $parser = Join-Path $MAOP "tools\parse-config.py"
    try {
      $raw = & $python $parser --dag $path 2>&1 | Out-String
      $jsonStart = $raw.IndexOfAny(@([char]'{', [char]'['))
      $jsonText = if ($jsonStart -ge 0) { $raw.Substring($jsonStart) } else { $raw }
      $dag = $jsonText | ConvertFrom-Json
      if ($dag -and -not $dag.error) {
        $dagHT = @{ id = "$($dag.id)"; name = "$($dag.name)"; version = "$($dag.version)"; defaults = @{}; nodes = @() }
        if ($dag.defaults) { foreach ($p in $dag.defaults.PSObject.Properties) { $dagHT.defaults[$p.Name] = "$($p.Value)" } }
        foreach ($n in $dag.nodes) {
          $nodeHT = @{
            id = "$($n.id)"; type = "$($n.type)"
            agent = if ($n.agent) { "$($n.agent)" } else { $null }
            agent_slot = if ($n.agent_slot) { "$($n.agent_slot)" } else { $null }
            depends_on = @(); params = @{}; branches = @{}
            condition = if ($n.condition) { "$($n.condition)" } else { $null }
            output = if ($n.output) { "$($n.output)" } else { $null }
          }
          foreach ($d in $n.depends_on) { $nodeHT.depends_on += "$d" }
          if ($n.params) { foreach ($p in $n.params.PSObject.Properties) { $nodeHT.params[$p.Name] = "$($p.Value)" } }
          if ($n.branches) { foreach ($b in $n.branches.PSObject.Properties) { $nodeHT.branches[$b.Name] = "$($b.Value)" } }
          $dagHT.nodes += $nodeHT
        }
        return $dagHT
      }
    } catch {
      Write-Warning "[dag] Python bridge failed, falling back to PS parser (non-critical): $_"
    }
  }
  
  # ── PowerShell fallback parser (simple YAML) ──
  if (-not (Test-Path $path)) { throw "Failed to parse DAG file: $path (not found)" }
  $yamlLines = Get-Content $path -Encoding UTF8
  $dagHT = @{ id = ""; name = ""; version = "1.0"; defaults = @{}; nodes = @() }
  $section = $null
  $currentNode = $null
  $inParams = $false
  $inBranches = $false
  
  foreach ($line in $yamlLines) {
    $trimmed = $line.Trim()
    if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
    
    if ($line -match "^workflow:") { $section = "workflow"; continue }
    if ($line -match "^defaults:") { $section = "defaults"; continue }
    
    if ($section -eq "workflow") {
      if ($line -match '^\s+id:\s+(.+)$') { $dagHT.id = $Matches[1].Trim().Trim('"') }
      elseif ($line -match '^\s+name:\s+(.+)$') { $dagHT.name = $Matches[1].Trim().Trim('"') }
      elseif ($line -match "^\s+version:\s+(.+)$") { $dagHT.version = $Matches[1].Trim().Trim('"') }
      elseif ($line -match '^\s+defaults:') { $section = "defaults"; continue }
      elseif ($line -match '^\s+nodes:') { $section = "nodes"; continue }
    }
    
    if ($section -eq "defaults") {
      if ($line -match '^\s+nodes:') { $section = "nodes"; continue }
      if ($line -match '^\s+(\w+):\s+(.+)$') {
        $dagHT.defaults[$Matches[1]] = $Matches[2].Trim().Trim('"')
      }
    }
    
    if ($section -eq "nodes") {
      if ($line -match '^\s+-\s+id:\s+(\S+)') {
        if ($currentNode) { $dagHT.nodes += $currentNode }
        $currentNode = @{ id = $Matches[1]; type = "execute"; agent = $null; agent_slot = $null; depends_on = @(); params = @{}; branches = @{}; condition = $null; output = $null }
        $inParams = $false; $inBranches = $false
      }
      elseif ($currentNode -and $line -match '^\s+type:\s+(\S+)') { $currentNode.type = $Matches[1].Trim() }
      elseif ($currentNode -and $line -match '^\s+agent:\s+(.+)$') { $currentNode.agent = $Matches[1].Trim() }
      elseif ($currentNode -and $line -match '^\s+agent_slot:\s+(.+)$') { $currentNode.agent_slot = $Matches[1].Trim() }
      elseif ($currentNode -and $line -match '^\s+condition:\s+(.+)$') { $currentNode.condition = $Matches[1].Trim().Trim('"') }
      elseif ($currentNode -and $line -match '^\s+output:\s+(.+)$') { $currentNode.output = $Matches[1].Trim().Trim('"') }
      elseif ($currentNode -and $line -match '^\s+depends_on:') { continue }
      elseif ($currentNode -and $line -match '^\s+-\s+(\S+)') { $currentNode.depends_on += $Matches[1].Trim() }
      elseif ($currentNode -and $line -match '^\s+params:') { $inParams = $true; $inBranches = $false; continue }
      elseif ($currentNode -and $line -match '^\s+branches:') { $inBranches = $true; $inParams = $false; continue }
      elseif ($currentNode -and $line -match '^\s+(\w+):\s+(.+)$') {
        $key = $Matches[1]; $val = $Matches[2].Trim().Trim('"')
        if ($inBranches) { $currentNode.branches[$key] = $val }
        else { $currentNode.params[$key] = $val }
      }
    }
  }
  if ($currentNode) { $dagHT.nodes += $currentNode }
  if (-not $dagHT.id) { throw "Failed to parse DAG file: $path (no id found)" }
  return $dagHT
}

# ── 拓扑排序（Kahn 算法） ──
function Get-TopologicalOrder($nodes) {
  $inDegree = @{}
  $adj = @{}
  $nodeMap = @{}
  
  foreach ($n in $nodes) {
    $nodeMap[$n.id] = $n
    if (-not $inDegree.ContainsKey($n.id)) { $inDegree[$n.id] = 0 }
    if (-not $adj.ContainsKey($n.id)) { $adj[$n.id] = @() }
  }
  
  foreach ($n in $nodes) {
    foreach ($dep in $n.depends_on) {
      if (-not $adj.ContainsKey($dep)) { $adj[$dep] = @() }
      $adj[$dep] += $n.id
      $inDegree[$n.id]++
    }
  }
  
  $queue = [System.Collections.Queue]::new()
  foreach ($kv in $inDegree.GetEnumerator()) {
    if ($kv.Value -eq 0) { $queue.Enqueue($kv.Key) }
  }
  
  $result = @()
  while ($queue.Count -gt 0) {
    $nodeId = $queue.Dequeue()
    $result += $nodeId
    
    foreach ($neighbor in $adj[$nodeId]) {
      $inDegree[$neighbor]--
      if ($inDegree[$neighbor] -eq 0) { $queue.Enqueue($neighbor) }
    }
  }
  
  if ($result.Count -ne $nodes.Count) {
    Write-Warning "DAG has cycles or disconnected nodes. Expected $($nodes.Count), got $($result.Count)"
  }
  
  # Ensure we return an array (PowerShell unwraps single-element arrays on return)
  return ,@($result)
}

# ── 解析模板变量 ──
function Expand-Template($template, $context) {
  if (-not $template) { return "" }
  $result = $template
  
  # {{ task }} 替换 — 支持子字段 task.workdir / task.id 等
  if ($context.ContainsKey("task_extended")) {
    foreach ($kv in $context.task_extended.GetEnumerator()) {
      $result = $result -replace [regex]::Escape("{{ task.$($kv.Key) }}"), "$($kv.Value)"
    }
  }
  $result = $result -replace '\{\{\s*task\s*\}\}', $context.task
  # {{ trace_id }} 替换
  if ($context.ContainsKey("trace_id")) {
    $result = $result -replace '\{\{\s*trace_id\s*\}\}', $context.trace_id
  }
  
  # {{ nodeId.output | truncate(N) }} 替换 (must come before plain .output)
  $truncateMatches = [regex]::Matches($result, '\{\{\s*([\w-]+)\.output\s*\|\s*truncate\s*\(\s*(\d+)\s*\)\s*\}\}')
  foreach ($m in $truncateMatches) {
    $nid = $m.Groups[1].Value; $maxLen = [int]$m.Groups[2].Value
    $replacement = ""
    if ($context.node_results.ContainsKey($nid) -and $context.node_results[$nid].output) {
      $o = $context.node_results[$nid].output
      if ($o.Length -gt $maxLen) { $replacement = $o.Substring(0, $maxLen) + "..." } else { $replacement = $o }
    }
    $result = $result -replace [regex]::Escape($m.Value), $replacement
  }
  
  # {{ nodeId.output }} 替换
  $outputMatches = [regex]::Matches($result, '\{\{\s*([\w-]+)\.output\s*\}\}')
  foreach ($m in $outputMatches) {
    $nid = $m.Groups[1].Value
    $replacement = ""
    if ($context.node_results.ContainsKey($nid) -and $context.node_results[$nid].output) {
      $replacement = $context.node_results[$nid].output
    }
    $result = $result -replace [regex]::Escape($m.Value), $replacement
  }
  
  # {{ nodeId.attribute }} 替换
  $attrMatches = [regex]::Matches($result, '\{\{\s*([\w-]+)\.(\w+)\s*\}\}')
  foreach ($m in $attrMatches) {
    $nid = $m.Groups[1].Value; $attr = $m.Groups[2].Value
    $replacement = ""
    if ($context.node_results.ContainsKey($nid) -and $context.node_results[$nid].ContainsKey($attr)) {
      $replacement = $context.node_results[$nid][$attr].ToString()
    }
    $result = $result -replace [regex]::Escape($m.Value), $replacement
  }
  
  return $result
}

# ── 解析条件表达式 ──
function Test-Condition($expr, $context) {
  $resolved = Expand-Template $expr $context
  
  # 支持 == false / == true
  if ($resolved -match "^(.+?)\s*==\s*(true|false)$") {
    $val = $Matches[1].Trim()
    $expected = $Matches[2].Trim()
    $boolVal = ($val -eq "true" -or $val -eq $true -or $val -eq 1)
    return ($boolVal -eq ($expected -eq "true"))
  }
  
  # 支持 contains (引号可选)
  if ($resolved -match '^(.+?)\.contains\([''""]?(.+?)[''""]?\)$') {
    $source = $Matches[1].Trim()
    $substr = $Matches[2]
    return $source -match [regex]::Escape($substr)
  }
  
  return $false
}

# ── 执行单个节点 ──
function Invoke-DagNode($node, $context) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  $result = @{
    node_id = $node.id
    type = $node.type
    status = "pending"
    output = $null
    error = $null
    duration_ms = 0
    agent = $null
  }
  
  # 解析模板
  $expandedParams = @{}
  foreach ($kv in $node.params.GetEnumerator()) {
    $expandedParams[$kv.Key] = Expand-Template $kv.Value $context
  }
  
  switch ($node.type) {
    "plan" {
      $agent = if ($node.agent) { $node.agent } else { "claude" }
      $result.agent = $agent
      $prompt = "You are a planner. Task: $($expandedParams.task)`nOutput only numbered steps."
      $agentResult = & (Join-Path $MAOP "src\delegate.ps1") -Agent $agent -Task $prompt -TimeoutSeconds 60
      $parsed = $agentResult | ConvertFrom-Json
      $result.output = $parsed.stdout
      $result.status = if ($parsed.exit_code -eq 0) { "completed" } else { "failed" }
      break
    }
    
    "execute" {
      $agent = if ($node.agent) {
        $node.agent
      } elseif ($node.agent_slot) {
        # Use Python bridge to resolve agent from routing table (non-critical: falls back to "claude")
        . (Join-Path $MAOP "tools\MAOP-bridge.ps1")
        $slotData = Invoke-ConfigBridge "--section routing"
        $slotAgent = $null
        if ($slotData) {
          $routeEntry = $slotData.$($node.agent_slot)
          if ($routeEntry -and $routeEntry.primary) { $slotAgent = $routeEntry.primary }
        }
        if ($slotAgent) { $slotAgent } else { "claude" }
      
      $result.agent = $agent
      $prompt = $expandedParams.task
      if (-not $prompt) { $prompt = $node.id }
      
      $timeout = if ($node.params.ContainsKey("timeout_s")) { [int]$node.params.timeout_s } else { 60 }
      $agentResult = & (Join-Path $MAOP "src\delegate.ps1") -Agent $agent -Task $prompt -TimeoutSeconds $timeout
      $parsed = $agentResult | ConvertFrom-Json
      $result.output = $parsed.stdout
      $result.status = if ($parsed.exit_code -eq 0) { "completed" } else { "failed" }
      break
    }
    
    "verify" {
      $agent = if ($node.agent) { $node.agent } else { "kimi" }
      $result.agent = $agent
      $prompt = "Verify this output is correct. Output: $($context.node_results[$node.depends_on[0]].output)`nReply ONLY 'PASS' or 'FAIL' with reason."
      $agentResult = & (Join-Path $MAOP "src\delegate.ps1") -Agent $agent -Task $prompt -TimeoutSeconds 30
      $parsed = $agentResult | ConvertFrom-Json
      $result.output = $parsed.stdout
      $result.status = "completed"
      $result.passed = ($parsed.stdout -match 'PASS')
      break
    }
    
    "condition" {
      $result.agent = "__condition__"
      $result.passed = Test-Condition $node.condition $context
      $branchKey = if ($result.passed) { "true" } else { "false" }
      if ($node.branches.ContainsKey($branchKey)) {
        $result.branch_target = $node.branches[$branchKey]
      }
      $result.output = if ($result.passed) { "true" } else { "false" }
      $result.status = "completed"
      break
    }
    
    "terminal" {
      $result.agent = "__terminal__"
      $result.output = Expand-Template $node.output $context
      $result.status = "completed"
      break
    }
    
    default {
      $result.error = "Unknown node type: $($node.type)"
      $result.status = "failed"
    }
  }
  
  $sw.Stop()
  $result.duration_ms = $sw.ElapsedMilliseconds
  return $result
}

# ════════════════════════════════════════
# MAIN
# ════════════════════════════════════════

# Guard: dot-source only — skip main execution
if (-not $DagFile -and -not $Task) { return }

# Validate required params
if (-not $DagFile) { throw "DagFile is required" }
if (-not $Task) { throw "Task is required" }

Write-Host "╔══════════════════════════════════════════╗"
Write-Host "║        MAOP DAG Engine v0.1               ║"
Write-Host "╚══════════════════════════════════════════╝"
Write-Host "DAG file: $DagFile"
Write-Host "Task:     $Task"
Write-Host "Trace:    $TraceID"

# Step 1: 读取 DAG
$dag = Read-DagYaml $DagFile
Write-Host "`n[1/4] Loaded DAG: $($dag.name) (v$($dag.version))"
Write-Host "  Nodes: $($dag.nodes.Count)"

# ── Checkpoint setup ──
$CheckpointDir = Join-Path $MAOP "data\dag-checkpoints"
# 路径安全：校验 dag.id 不包含穿越字符
if ($dag.id -notmatch '^[A-Za-z0-9_-]+$') { Write-Error "Invalid dag.id: $($dag.id)"; exit 1 }
$CheckpointFile = Join-Path $CheckpointDir "$($dag.id)-$TraceID.json"
if (-not (Test-Path $CheckpointDir)) {
  New-Item -ItemType Directory -Path $CheckpointDir -Force | Out-Null
}
$completedNodes = @()
$failedNodes = @()
$checkpointStartedAt = $null

# 尝试从已有 checkpoint 恢复 (JSON file first, then SQLite fallback)
if (Test-Path $CheckpointFile) {
  try {
    $checkpointData = Get-Content $CheckpointFile -Raw | ConvertFrom-Json
    $checkpointStartedAt = $checkpointData.started_at
    $completedNodes = @($checkpointData.completed_nodes)
    $failedNodes = @($checkpointData.failed_nodes)
    Write-Host "  [RESUME] Found JSON checkpoint from $checkpointStartedAt"
    Write-Host "  [RESUME] Completed: $($completedNodes -join ', ')"
    if ($failedNodes.Count -gt 0) { Write-Host "  [RESUME] Failed: $($failedNodes -join ', ')" }
  } catch {
    Write-Warning "Failed to load JSON checkpoint: $_"
    $checkpointData = $null
  }
}
# SQLite fallback if JSON checkpoint not found
if (-not $checkpointData -and $PEV_HasSQLite) {
  try {
    $dbCp = Get-DbCheckpoint -Agent "dag" -Task $dag.id
    if ($dbCp) {
      $checkpointData = $dbCp
      $checkpointStartedAt = $dbCp.started_at
      $completedNodes = @($dbCp.completed_nodes)
      $failedNodes = @($dbCp.failed_nodes)
      Write-Host "  [RESUME] Found SQLite checkpoint from $checkpointStartedAt"
      Write-Host "  [RESUME] Completed: $($completedNodes -join ', ')"
      if ($failedNodes.Count -gt 0) { Write-Host "  [RESUME] Failed: $($failedNodes -join ', ')" }
    }
  } catch {
    Write-Warning "SQLite checkpoint load failed: $_"
  }
}
if (-not $checkpointStartedAt) {
  $checkpointStartedAt = [DateTime]::UtcNow.ToString("o")
}

# Step 2: 拓扑排序
$order = Get-TopologicalOrder $dag.nodes
Write-Host "[2/4] Topological order: $($order -join ' → ')"

# Step 3: 执行 DAG
$context = @{
  task = $Task
  task_extended = @{
    workdir = $WorkDir
    id = $TraceID
  }
  node_results = @{}
  trace_id = $TraceID
}

$allResults = @()
$aborted = $false

# 从 checkpoint 恢复已有结果
if ($checkpointData -and $checkpointData.results) {
  foreach ($resultEntry in $checkpointData.results.PSObject.Properties) {
    $context.node_results[$resultEntry.Name] = $resultEntry.Value
  }
  # 恢复 allResults 列表
  foreach ($nid in $completedNodes + $failedNodes) {
    if ($context.node_results.ContainsKey($nid)) {
      $allResults += $context.node_results[$nid]
    }
  }
}

foreach ($nodeId in $order) {
  if ($aborted) {
    Write-Host "  [SKIP] $nodeId (DAG aborted)"
    continue
  }
  
  $node = $dag.nodes | Where-Object { $_.id -eq $nodeId } | Select-Object -First 1
  if (-not $node) { Write-Warning "Node not found: $nodeId"; continue }
  
  # 如果节点已在之前的 checkpoint 中完成或失败，跳过
  if ($completedNodes -contains $nodeId -or $failedNodes -contains $nodeId) {
    Write-Host "  [RESTORE] $nodeId (from checkpoint: $($context.node_results[$nodeId].status))"
    continue
  }
  
  # 条件节点分支跳过：基于条件节点的 branch_target 计算可达性
  # 如果节点祖先链中的条件节点选择了另一条分支，则当前节点不可达 → 跳过
  $shouldSkip = $false
  $visited = @{}
  $queue = [System.Collections.Queue]::new()
  foreach ($dep in $node.depends_on) { $queue.Enqueue($dep) }
  while ($queue.Count -gt 0) {
    $aid = $queue.Dequeue()
    if ($visited.ContainsKey($aid)) { continue }
    $visited[$aid] = $true
    $an = $dag.nodes | Where-Object { $_.id -eq $aid } | Select-Object -First 1
    if ($an) {
      foreach ($d in $an.depends_on) { if (-not $visited.ContainsKey($d)) { $queue.Enqueue($d) } }
    }
  }
  foreach ($condResult in ($allResults | Where-Object { $_.type -eq "condition" -and $_.branch_target })) {
    if ($visited.ContainsKey($condResult.node_id)) {
      # branch_target 是条件节点选中分支的直接下游
      # 如果当前节点不是 branch_target 且 branch_target 不在当前节点的祖先链中 → 跳过
      if ($nodeId -ne $condResult.branch_target -and -not $visited.ContainsKey($condResult.branch_target)) {
        $shouldSkip = $true
        Write-Host "  [SKIP-BRANCH] $nodeId (condition $($condResult.node_id) chose $($condResult.branch_target))"
        break
      }
    }
  }

  Write-Host "`n[EXEC] $nodeId ($($node.type))"
  if ($node.agent) { Write-Host "  Agent: $($node.agent)" }
  if ($node.depends_on.Count -gt 0) { Write-Host "  Depends on: $($node.depends_on -join ', ')" }
  
  $result = Invoke-DagNode $node $context
  $context.node_results[$nodeId] = $result
  $allResults += $result
  
  # ── 保存 checkpoint ──
  $checkpointPayload = @{
    dag_id = $dag.id
    dag_name = $dag.name
    status = "running"
    completed_nodes = @(($allResults | Where-Object { $_.status -eq "completed" }) | ForEach-Object { $_.node_id })
    failed_nodes   = @(($allResults | Where-Object { $_.status -eq "failed" }) | ForEach-Object { $_.node_id })
    results = $context.node_results
    started_at = $checkpointStartedAt
    updated_at = [DateTime]::UtcNow.ToString("o")
  }
  $checkpointPayload | ConvertTo-Json -Depth 6 | Set-Content $CheckpointFile -Force
  
  # ── SQLite dual-write ──
  if ($PEV_HasSQLite) {
    try { Save-DbCheckpoint -Agent "dag" -Task $dag.id -Phase "running" -State $checkpointPayload | Out-Null }
    catch { Write-Warning "SQLite checkpoint save failed: $_" }
  }
  
  $statusIcon = if ($result.status -eq "completed") { "✓" } else { "✗" }
  Write-Host "  [$statusIcon] $($result.status) ($($result.duration_ms)ms)"
  if ($result.branch_target) { Write-Host "  → Branch: $($result.branch_target)" }
  
  # 如果是条件节点，记录分支决策
  if ($result.type -eq "condition" -and $result.branch_target) {
    $branchTarget = $result.branch_target
    # 找到所有不在所选分支上的下游节点并跳过
    $siblingNodes = @($dag.nodes | Where-Object {
      $_.depends_on -contains $result.node_id -and $_.id -ne $branchTarget
    })
    foreach ($sn in $siblingNodes) {
      Write-Host "  [SKIP] $($sn.id) (branch not taken)"
    }
  }
  
  if ($result.status -eq "failed" -and $node.type -ne "condition") {
    Write-Warning "Node $nodeId FAILED, continuing DAG execution..."
    # DAG 中部分节点失败不应中止全部
  }
}

# Step 4: 输出结果
Write-Host "`n╔══════════════════════════════════════════╗"
Write-Host "║           DAG Complete                    ║"
Write-Host "╚══════════════════════════════════════════╝"
$totalMs = ($allResults | Measure-Object -Property duration_ms -Sum).Sum
Write-Host "Total duration: ${totalMs}ms"

$finalOutput = @{
  dag_id = $dag.id
  dag_name = $dag.name
  trace_id = $TraceID
  task = $Task
  total_duration_ms = $totalMs
  nodes = @($allResults | Select-Object node_id, type, status, agent, duration_ms, output)
}

# 删除 checkpoint（DAG 已完成）
if (Test-Path $CheckpointFile) {
  Remove-Item $CheckpointFile -Force
  Write-Host "[CLEANUP] JSON checkpoint deleted: $CheckpointFile"
}
if ($PEV_HasSQLite) {
  try { Remove-DbCheckpoint -Agent "dag" -Task $dag.id | Out-Null }
  catch { Write-Warning "SQLite checkpoint cleanup failed: $_" }
}

return ($finalOutput | ConvertTo-Json -Depth 4)
