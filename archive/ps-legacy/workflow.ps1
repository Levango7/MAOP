param(
  [ValidateSet("create","run","list","get","cancel")]
  [string]$Action = "run",
  [string]$WorkflowId = "",
  [string]$Name = "",
  [string]$Nodes = "[]",
  [string]$Edges = "[]",
  [int]$TimeoutSeconds = 300,
  [string]$WorkflowFile = ""
)

$ScriptDir = Split-Path $PSCommandPath -Parent
$Delegate = Join-Path $ScriptDir "delegate.ps1"
if (-not $WorkflowFile) { $WorkflowFile = Join-Path (Split-Path $ScriptDir -Parent) "data\workflows.json" }

function Load-Workflows {
  if (Test-Path $WorkflowFile) { try { return @((Get-Content $WorkflowFile -Raw | ConvertFrom-Json).workflows) } catch { return @() } }
  return @()
}
function Save-Workflows($w) { @{ workflows = @($w) } | ConvertTo-Json -Depth 4 -Compress | Set-Content $WorkflowFile -Encoding utf8 }

function Invoke-Agent($agent, $task, $timeout) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  try {
    $json = & powershell -NoProfile -File $Delegate -Agent $agent -Task $task -TimeoutSeconds $timeout 2>&1 | Out-String
    $result = $json | ConvertFrom-Json
    $sw.Stop()
    return @{ ok = ($result.exit_code -eq 0); output = $result.stdout; ms = $sw.ElapsedMilliseconds }
  } catch { return @{ ok = $false; output = $_.Exception.Message; ms = $sw.ElapsedMilliseconds } }
}

function Topological-Sort($nodes, $edges) {
  $inDegree = @{}; $adj = @{}; foreach ($n in $nodes) { $inDegree[$n] = 0; $adj[$n] = @() }
  foreach ($e in $edges) { $adj[$e.from] += $e.to; $inDegree[$e.to]++ }
  $queue = @($nodes | Where-Object { $inDegree[$_] -eq 0 })
  $order = @()
  while ($queue.Count -gt 0) {
    $n = $queue[0]; $queue = $queue[1..($queue.Count-1)]; $order += $n
    foreach ($next in $adj[$n]) { $inDegree[$next]--; if ($inDegree[$next] -eq 0) { $queue += $next } }
  }
  return $order
}

switch ($Action) {
  "create" {
    if (-not $WorkflowId) { Write-Error "create requires -WorkflowId"; exit 1 }
    $parsedNodes = $Nodes | ConvertFrom-Json
    $parsedEdges = $Edges | ConvertFrom-Json
    $entry = @{
      id = $WorkflowId; name = $Name; nodes = @($parsedNodes); edges = @($parsedEdges)
      status = "created"; created = (Get-Date -Format "o")
    }
    $wf = New-Object System.Collections.ArrayList
    foreach ($w in @(Load-Workflows)) { if ($w.id -ne $WorkflowId) { $null = $wf.Add($w) } }
    $null = $wf.Add($entry); Save-Workflows $wf
    Write-Output ("workflow created: " + $WorkflowId + " (" + @($parsedNodes).Count + " nodes)")
  }

  "run" {
    if ($WorkflowId) {
      $wf = @(Load-Workflows) | Where-Object { $_.id -eq $WorkflowId }
      if (-not $wf) { Write-Error "workflow not found: $WorkflowId"; exit 1 }
      $nodes = $wf.nodes; $edges = $wf.edges
    } else {
      $nodes = $Nodes | ConvertFrom-Json
      $edges = $Edges | ConvertFrom-Json
    }
    $nodeNames = @($nodes | ForEach-Object { if ($_ -is [string]) { $_ } else { $_.id } })
    $edgeList = @($edges | ForEach-Object { @{ from = if ($_ -is [string]) { ($_ -split '->')[0].Trim() } else { $_.from }; to = if ($_ -is [string]) { ($_ -split '->')[1].Trim() } else { $_.to } } })
    $order = Topological-Sort $nodeNames $edgeList
    if ($order.Count -ne $nodeNames.Count) { Write-Error "DAG has cycles or unreachable nodes"; exit 1 }
    $results = @{}; $totalMs = 0
    foreach ($n in $order) {
      $nodeDef = @($nodes | Where-Object { if ($_ -is [string]) { $_ -eq $n } else { $_.id -eq $n } })[0]
      $agent = if ($nodeDef -is [string]) { "nvidia" } elseif ($nodeDef.agent) { $nodeDef.agent } else { "nvidia" }
      $task = if ($nodeDef -is [string]) { "Execute task" } elseif ($nodeDef.task) { $nodeDef.task } else { "Execute: $n" }
      # Inject upstream results
      $upstream = $edgeList | Where-Object { $_.to -eq $n }
      $ctx = $task; $ctxIdx = 1
      foreach ($u in $upstream) {
        if ($results.ContainsKey($u.from)) {
          $ctx += "`n--- Input from upstream $($u.from) ---`n$($results[$u.from].output.Substring(0, [Math]::Min(200, $results[$u.from].output.Length)))"
          $ctxIdx++
        }
      }
      $r = Invoke-Agent $agent $ctx $TimeoutSeconds
      $results[$n] = $r; $totalMs += $r.ms
    }
    Write-Output (@{
      workflow = if ($WorkflowId) { $WorkflowId } else { "adhoc" }
      order = @($order); total_ms = $totalMs
      results = @($order | ForEach-Object { @{ node = $_; ok = $results[$_].ok; ms = $results[$_].ms } })
    } | ConvertTo-Json -Depth 3)
  }

  "list" { Write-Output (@(Load-Workflows) | Select-Object id, name, status, created | ConvertTo-Json -Depth 2) }
  "get" {
    if (-not $WorkflowId) { Write-Error "get requires -WorkflowId"; exit 1 }
    $wf = @(Load-Workflows) | Where-Object { $_.id -eq $WorkflowId }
    Write-Output ($wf | ConvertTo-Json -Depth 4)
  }
  "cancel" { Write-Output "cancel not implemented yet" }
}