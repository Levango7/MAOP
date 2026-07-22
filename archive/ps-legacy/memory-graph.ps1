param(
  [ValidateSet("add-node","add-edge","neighbors","path","query","stats","list-nodes","list-edges","delete-node","delete-edge","clear")]
  [string]$Action = "stats",
  [string]$NodeId = "",
  [string]$NodeType = "concept",
  [string]$Label = "",
  [string]$Properties = "{}",
  [string]$Source = "",
  [string]$Target = "",
  [string]$Relation = "related_to",
  [double]$Weight = 1.0,
  [string]$Query = "",
  [int]$MaxDepth = 3,
  [string]$NodesFile = "",
  [string]$EdgesFile = ""
)

$ScriptDir = Split-Path $PSCommandPath -Parent
if (-not $NodesFile) { $NodesFile = Join-Path (Split-Path $ScriptDir -Parent) "data\graph-nodes.json" }
if (-not $EdgesFile) { $EdgesFile = Join-Path (Split-Path $ScriptDir -Parent) "data\graph-edges.json" }

# Load SQLite database module
. (Join-Path $ScriptDir 'database.ps1')

# Ensure database tables exist (silent no-op if SQLite unavailable)
Init-Database | Out-Null

function Load-Nodes {
  # Try SQLite first
  $sqlRows = Query-Database -Sql "SELECT id, type, label, properties, created FROM graph_nodes"
  if ($null -ne $sqlRows) {
    return @($sqlRows | ForEach-Object {
      @{
        id         = $_.id
        type       = $_.type
        label      = $_.label
        properties = if ($_.properties) { $_.properties | ConvertFrom-Json } else { @{} }
        created    = $_.created
      }
    })
  }
  # Fallback to JSON
  if (Test-Path $NodesFile) {
    try {
      $raw = Get-Content $NodesFile -Raw -ErrorAction Stop
      $obj = ConvertFrom-Json $raw
      return @($obj.nodes)
    } catch { return @() }
  }
  return @()
}
function Save-Nodes($n) {
  # Try SQLite first (replace all rows)
  $deleteOk = Execute-Database -Sql "DELETE FROM graph_nodes"
  if ($deleteOk) {
    $allOk = $true
    foreach ($node in $n) {
      $ok = Execute-Database -Sql "INSERT INTO graph_nodes (id, type, label, properties, created) VALUES (@id, @type, @label, @props, @created)" -Parameters @{
        "@id"      = $node.id
        "@type"    = $node.type
        "@label"   = $node.label
        "@props"   = ($node.properties | ConvertTo-Json -Compress)
        "@created" = $node.created
      }
      if (-not $ok) { $allOk = $false; break }
    }
    if ($allOk) { return }  # SQLite succeeded, skip JSON
  }
  # Fallback to JSON
  $obj = @{ nodes = @($n) }
  $obj | ConvertTo-Json -Depth 3 -Compress | Set-Content $NodesFile -Encoding utf8
}

function Load-Edges {
  # Try SQLite first
  $sqlRows = Query-Database -Sql "SELECT id, source, target, relation, weight, created FROM graph_edges"
  if ($null -ne $sqlRows) {
    return @($sqlRows | ForEach-Object {
      @{
        id       = $_.id
        source   = $_.source
        target   = $_.target
        relation = $_.relation
        weight   = $_.weight
        created  = $_.created
      }
    })
  }
  # Fallback to JSON
  if (Test-Path $EdgesFile) {
    try {
      $raw = Get-Content $EdgesFile -Raw -ErrorAction Stop
      $obj = ConvertFrom-Json $raw
      return @($obj.edges)
    } catch { return @() }
  }
  return @()
}
function Save-Edges($e) {
  # Try SQLite first (replace all rows)
  $deleteOk = Execute-Database -Sql "DELETE FROM graph_edges"
  if ($deleteOk) {
    $allOk = $true
    foreach ($edge in $e) {
      $ok = Execute-Database -Sql "INSERT INTO graph_edges (id, source, target, relation, weight, created) VALUES (@id, @src, @tgt, @rel, @w, @created)" -Parameters @{
        "@id"      = $edge.id
        "@src"     = $edge.source
        "@tgt"     = $edge.target
        "@rel"     = $edge.relation
        "@w"       = $edge.weight
        "@created" = $edge.created
      }
      if (-not $ok) { $allOk = $false; break }
    }
    if ($allOk) { return }  # SQLite succeeded, skip JSON
  }
  # Fallback to JSON
  $obj = @{ edges = @($e) }
  $obj | ConvertTo-Json -Depth 3 -Compress | Set-Content $EdgesFile -Encoding utf8
}

switch ($Action) {
  "add-node" {
    if (-not $NodeId -or -not $Label) { Write-Error "add-node requires -NodeId and -Label"; exit 1 }
    $nodes = New-Object System.Collections.ArrayList
    foreach ($n in @(Load-Nodes)) { if ($n.id -ne $NodeId) { $null = $nodes.Add($n) } }
    $null = $nodes.Add(@{
      id = $NodeId; type = $NodeType; label = $Label
      properties = ($Properties | ConvertFrom-Json)
      created = (Get-Date -Format "o")
    })
    Save-Nodes $nodes
    Write-Output "node added: $NodeId ($NodeType)"
  }

  "add-edge" {
    if (-not $Source -or -not $Target) { Write-Error "add-edge requires -Source and -Target"; exit 1 }
    $edges = New-Object System.Collections.ArrayList
    $eid = "$Source--$Target--$Relation"
    foreach ($e in @(Load-Edges)) { if ($e.id -ne $eid) { $null = $edges.Add($e) } }
    $null = $edges.Add(@{
      id = $eid; source = $Source; target = $Target; relation = $Relation
      weight = $Weight; properties = ($Properties | ConvertFrom-Json)
      created = (Get-Date -Format "o")
    })
    Save-Edges $edges
    Write-Output "edge added: $Source --[$Relation]--> $Target"
  }

  "neighbors" {
    if (-not $NodeId) { Write-Error "neighbors requires -NodeId"; exit 1 }
    $edges = @(Load-Edges)
    $nodes = @(Load-Nodes)
    $connected = $edges | Where-Object { $_.source -eq $NodeId -or $_.target -eq $NodeId }
    $neighborIds = @($connected | ForEach-Object {
      if ($_.source -eq $NodeId) { $_.target } else { $_.source }
    }) | Select-Object -Unique
    $nodeMap = @{}; foreach ($n in $nodes) { $nodeMap[$n.id] = $n }
    $edgeMap = @{}; foreach ($e in $connected) { $edgeMap[$e.id] = $e }
    $neighborList = @($neighborIds | ForEach-Object {
      $nid = $_
      $n = $nodeMap[$nid]
      $e = $connected | Where-Object { ($_.source -eq $NodeId -and $_.target -eq $nid) -or ($_.target -eq $NodeId -and $_.source -eq $nid) } | Select-Object -First 1
      if ($n) { @{ node = $n; edge = $e } }
    })
    $result = @{ center = $nodeMap[$NodeId]; neighbors = $neighborList }
    Write-Output ($result | ConvertTo-Json -Depth 3)
  }

  "path" {
    if (-not $Source -or -not $Target) { Write-Error "path requires -Source and -Target"; exit 1 }
    $edges = @(Load-Edges)
    $nodes = @(Load-Nodes)
    $nodeMap = @{}; foreach ($n in $nodes) { $nodeMap[$n.id] = $n }
    $visited = @{}; $queue = @(@($Source)); $parent = @{}
    while ($queue.Count -gt 0) {
      $cur = $queue[0]; $queue = $queue[1..($queue.Count-1)]
      if ($cur -eq $Target) {
        $path = @(); $node = $Target
        while ($node) { $path = ,$node + $path; $node = $parent[$node] }
        $result = @($path | ForEach-Object {
          $n = $nodeMap[$_]
          @{ id = $_; label = if ($n) { $n.label } else { $_ } }
        })
        Write-Output ($result | ConvertTo-Json -Depth 2)
        exit 0
      }
      if ($visited[$cur]) { continue }
      $visited[$cur] = $true
      $conn = $edges | Where-Object { $_.source -eq $cur -or $_.target -eq $cur }
      foreach ($e in $conn) {
        $next = if ($e.source -eq $cur) { $e.target } else { $e.source }
        if (-not $visited[$next]) { $parent[$next] = $cur; $queue += $next }
      }
    }
    Write-Output "[]"
  }

  "query" {
    $nodes = @(Load-Nodes); $edges = @(Load-Edges)
    $edgeMap = @{}; foreach ($e in $edges) { $edgeMap[$e.id] = $e }
    $filtered = $nodes
    if ($Query) { $filtered = $nodes | Where-Object { $_.id -match $Query -or $_.label -match $Query -or $_.type -match $Query } }
    $result = @($filtered | ForEach-Object {
      $nid = $_.id
      $conn = $edges | Where-Object { $_.source -eq $nid -or $_.target -eq $nid }
      $rel = @($conn | ForEach-Object { "$($_.source) --[$($_.relation)]--> $($_.target)" })
      @{ node = $_; relations = $rel }
    })
    Write-Output ($result | ConvertTo-Json -Depth 3)
  }

  "stats" {
    $nodes = @(Load-Nodes); $edges = @(Load-Edges)
    $typeCount = $nodes | Group-Object type | ForEach-Object { @{ type = $_.Name; count = $_.Count } }
    $relCount = $edges | Group-Object relation | ForEach-Object { @{ relation = $_.Name; count = $_.Count } }
    $result = @{ nodes = $nodes.Count; edges = $edges.Count; types = @($typeCount); relations = @($relCount) }
    Write-Output ($result | ConvertTo-Json -Depth 2)
  }

  "list-nodes" {
    $nodes = @(Load-Nodes)
    Write-Output ($nodes | Sort-Object created -Descending | ConvertTo-Json -Depth 3)
  }

  "list-edges" {
    $edges = @(Load-Edges)
    Write-Output ($edges | Sort-Object created -Descending | ConvertTo-Json -Depth 3)
  }

  "delete-node" {
    if (-not $NodeId) { Write-Error "delete-node requires -NodeId"; exit 1 }
    $nodes = New-Object System.Collections.ArrayList
    $nc = 0; foreach ($n in @(Load-Nodes)) { if ($n.id -ne $NodeId) { $null = $nodes.Add($n) } else { $nc++ } }
    $edges = New-Object System.Collections.ArrayList
    $ec = 0; foreach ($e in @(Load-Edges)) { if ($e.source -ne $NodeId -and $e.target -ne $NodeId) { $null = $edges.Add($e) } else { $ec++ } }
    Save-Nodes $nodes; Save-Edges $edges
    Write-Output "deleted node $NodeId ($nc nodes, $ec edges)"
  }

  "delete-edge" {
    if (-not $Source -or -not $Target) { Write-Error "delete-edge requires -Source and -Target"; exit 1 }
    $eid = "$Source--$Target--$Relation"
    $edges = New-Object System.Collections.ArrayList
    $ec = 0; foreach ($e in @(Load-Edges)) { if ($e.id -ne $eid) { $null = $edges.Add($e) } else { $ec++ } }
    Save-Edges $edges
    Write-Output "deleted edge $eid ($ec removed)"
  }

  "clear" {
    Save-Nodes @(); Save-Edges @()
    Write-Output "graph cleared"
  }
}
