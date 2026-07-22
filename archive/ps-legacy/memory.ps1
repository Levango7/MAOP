param(
  [ValidateSet("store","search","trajectory","inject","trace","stats","prune")]
  [string]$Action = "search",
  [string]$Agent,
  [string]$Task,
  [string]$Content,
  [string]$Tags,
  [string]$Query,
  [string]$Topic,
  [string]$TraceID,
  [string]$ParentTraceID,
  [string]$SessionID,
  [string]$ToolName,
  [string]$ToolInput,
  [string]$ToolOutput,
  [int]$ToolDurationMs = 0,
  [int]$ToolExitCode = 0,
  [int]$Top = 10,
  [string]$ID,
  [string]$Since,
  [string]$Until,
  [int]$TtlDays = 0,
  [switch]$DryRun
)

$BaseDir = Split-Path $PSCommandPath -Parent
$DataDir = Join-Path (Split-Path $BaseDir -Parent) "data"

# Load file lock utility
. (Join-Path $BaseDir 'filelock.ps1')

$MemDir = Join-Path $BaseDir "memory"
$EntriesDir = Join-Path $MemDir "entries"
$TracesDir = Join-Path $MemDir "traces"
$TrajectoryDir = Join-Path $MemDir "trajectory"
foreach ($d in @($MemDir, $EntriesDir, $TracesDir, $TrajectoryDir)) {
  if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

function New-ID {
  $rand = -join ((65..90) + (97..122) | Get-Random -Count 6 | ForEach-Object { [char]$_ })
  return "$(Get-Date -Format 'yyyyMMdd-HHmmss')-$rand"
}

function New-TraceID {
  return [guid]::NewGuid().ToString("N")
}

function Parse-Tags($tagStr) {
  if (-not $tagStr) { return @() }
  return $tagStr -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }
}

# 同义词表：提升关键词搜索命中率
$SynonymMap = @{
  "登录"    = @("login", "signin", "认证", "auth")
  "超时"    = @("timeout", "超时", "hang", "卡住")
  "错误"    = @("error", "异常", "exception", "bug", "故障")
  "慢"      = @("slow", "延迟", "latency", "性能", "performance")
  "配置"    = @("config", "设置", "setting", "setup")
  "安装"    = @("install", "setup", "部署", "deploy")
  "更新"    = @("update", "upgrade", "升级", "版本")
  "删除"    = @("delete", "remove", "清理", "clean")
  "搜索"    = @("search", "查找", "find", "query", "查询")
  "认证"    = @("auth", "login", "token", "凭据", "credential", "keyring")
}

function Expand-Keywords($text) {
  $results = @($text)
  foreach ($kv in $SynonymMap.GetEnumerator()) {
    if ($text -match $kv.Key) {
      $results += $kv.Value
    }
    foreach ($syn in $kv.Value) {
      if ($text -match [regex]::Escape($syn)) {
        $results += $kv.Key
        break
      }
    }
  }
  return ($results | Select-Object -Unique)
}

# ════════════════════════════════════════
# Action: STORE — 存储记忆条目
# ════════════════════════════════════════
function Invoke-Store {
  $id = New-ID
  $ts = Get-Date -Format "o"
  $tagList = Parse-Tags $Tags
  $topicVal = if ($Topic) { $Topic } else { "general" }
  $traceVal = if ($TraceID) { $TraceID } else { "" }

  $entry = @{
    id = $id
    agent = $Agent
    task = $Task
    content = $Content
    tags = $tagList
    topic = $topicVal
    trace_id = $traceVal
    session_id = $SessionID
    exit_code = $ToolExitCode
    duration_ms = $ToolDurationMs
    timestamp = $ts
  }

  # 路径安全：校验 id 不包含路径穿越字符
  if ($id -notmatch '^[A-Za-z0-9_-]+$') { Write-Warning "[mem] Invalid id rejected: $id"; return $null }

  $entry | ConvertTo-Json -Depth 3 | Set-Content (Join-Path $EntriesDir "$id.json")
  Write-Host "[mem] Stored: $id ($Agent, $($tagList -join ','))"

  # ── File-locked write to data/wiki.json ──
  $wikiFile = Join-Path $DataDir "wiki.json"
  Invoke-WithFileLock -Path $wikiFile -Script {
    $wiki = @{ entries = @() }
    if (Test-Path $wikiFile) {
      try { $wiki = Get-Content $wikiFile -Raw | ConvertFrom-Json } catch { $wiki = @{ entries = @() } }
    }
    if (-not $wiki.entries) { $wiki.entries = @() }
    # 避免重复
    $wiki.entries = @($wiki.entries | Where-Object { $_.id -ne $entry.id })
    $wikiEntry = @{
      id       = $entry.id
      title    = $entry.task
      content  = $entry.content
      category = $entry.topic
      tags     = $entry.tags
      source   = "memory:$($entry.agent)"
      added    = $entry.timestamp
    }
    $wiki.entries += $wikiEntry
    $wiki | ConvertTo-Json -Depth 3 -Compress | Set-Content $wikiFile -Encoding utf8
  }
  Write-Host "[mem] Synced to wiki: $id"

  # ── File-locked write to data/memory.json (aggregated index) ──
  $memIndexFile = Join-Path $DataDir "memory.json"
  Invoke-WithFileLock -Path $memIndexFile -Script {
    $index = @()
    if (Test-Path $memIndexFile) {
      try { $index = @(Get-Content $memIndexFile -Raw | ConvertFrom-Json) } catch { $index = @() }
    }
    # 避免重复
    $index = @($index | Where-Object { $_.id -ne $entry.id })
    $index += @{
      id        = $entry.id
      agent     = $entry.agent
      task      = $entry.task
      tags      = $entry.tags
      topic     = $entry.topic
      trace_id  = $entry.trace_id
      timestamp = $entry.timestamp
    }
    $index | ConvertTo-Json -Depth 3 | Set-Content $memIndexFile
  }
  Write-Host "[mem] Indexed to memory.json"

  # ── Auto-trigger evolve (async — with error capture and retry) ──
  # B3 fix: fire-and-forget replaced with error-aware async dispatch
  # (evolve analyze+apply was taking 2-5s per call, and MAOP-loop already
  # triggers evolve in Phase 5)
  $evolveScript = Join-Path $BaseDir "evolve.ps1"
  if (Test-Path $evolveScript) {
    $evolveJobName = "MAOP-evolve-$(Get-Date -Format 'yyyyMMddHHmmss')"
    Start-Job -Name $evolveJobName -ScriptBlock {
      param($evScript, $maxRetries)
      $retryCount = 0
      $success = $false
      while (-not $success -and $retryCount -lt $maxRetries) {
        try {
          $analyzeResult = & $evScript -Action "analyze" 2>&1
          $errors = $analyzeResult | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] }
          if ($errors) {
            throw "[evolve-async] analyze failed: $($errors | ForEach-Object { $_.Exception.Message } | Out-String)"
          }
          $applyResult = & $evScript -Action "apply" -AutoApply 2>&1
          $applyErrors = $applyResult | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] }
          if ($applyErrors) {
            throw "[evolve-async] apply failed: $($applyErrors | ForEach-Object { $_.Exception.Message } | Out-String)"
          }
          $success = $true
        } catch {
          $retryCount++
          Write-Warning "[evolve-async] Attempt $retryCount/$maxRetries failed: $($_.Exception.Message)"
          if ($retryCount -lt $maxRetries) { Start-Sleep -Seconds 2 }
        }
      }
      if (-not $success) {
        Write-Error "[evolve-async] All $maxRetries attempts failed. Evolve job will be discarded."
      }
    } -ArgumentList @($evolveScript, 2) | Out-Null
    Write-Host "[mem] Auto-evolve dispatched (async, max 2 retries)"
  }

  return $id
}

# ════════════════════════════════════════
# Action: SEARCH — 检索记忆（含同义词扩展）
# ════════════════════════════════════════
function Invoke-Search {
  $all = @()
  Get-ChildItem "$EntriesDir\*.json" -ErrorAction SilentlyContinue | ForEach-Object {
    $all += Get-Content $_.FullName -Raw | ConvertFrom-Json
  }

  if ($all.Count -eq 0) { Write-Host "[mem] No entries yet"; return }

  if ($Since) { $sinceDt = [datetime]::Parse($Since); $all = $all | Where-Object { [datetime]::Parse($_.timestamp) -ge $sinceDt } }
  if ($Until) { $untilDt = [datetime]::Parse($Until); $all = $all | Where-Object { [datetime]::Parse($_.timestamp) -le $untilDt } }
  if ($Agent) { $all = $all | Where-Object { $_.agent -eq $Agent } }
  if ($TraceID) { $all = $all | Where-Object { $_.trace_id -eq $TraceID } }
  if ($ID) {
    $match = $all | Where-Object { $_.id -eq $ID } | Select-Object -First 1
    if ($match) { return ($match | ConvertTo-Json -Depth 3) }
    Write-Host "[mem] No entry with ID: $ID"; return
  }

  # 关键词搜索 + 同义词扩展
  if ($Query) {
    $keywords = Expand-Keywords $Query
    $scored = @()
    foreach ($e in $all) {
      $score = 0
      $searchText = "$($e.task) $($e.content) $($e.agent) $($e.tags -join ' ')"
      foreach ($kw in $keywords) {
        $matches = [regex]::Matches($searchText, [regex]::Escape($kw), "IgnoreCase")
        $score += $matches.Count
      }
      if ($score -gt 0) {
        $scored += [PSCustomObject]@{
          id = $e.id; agent = $e.agent; task = $e.task
          tags = $e.tags -join ','; topic = $e.topic
          trace_id = $e.trace_id; timestamp = $e.timestamp
          score = $score; snippet = ($e.content -replace "`n"," ").Substring(0, [Math]::Min(120, $e.content.Length))
        }
      }
    }
    $results = $scored | Sort-Object score -Descending | Select-Object -First $Top
    if ($results.Count -eq 0) { Write-Host "[mem] No results for: $Query"; return }
    Write-Host "=== Memory Search: '$Query' ($($results.Count) results) ==="
    $results | ForEach-Object {
      Write-Host "[$($_.score)x] $($_.agent) | $($_.timestamp)"
      Write-Host "    $($_.task)"
      Write-Host "    >> $($_.snippet)"
    }
    return ($results | ConvertTo-Json -Depth 3)
  }

  $list = $all | Sort-Object timestamp -Descending | Select-Object -First $Top
  Write-Host "=== Recent Memory ($($list.Count) entries) ==="
  $list | ForEach-Object { Write-Host "  $($_.id) | $($_.agent) | $($_.tags) | $($_.timestamp)" }
}

# ════════════════════════════════════════
# Action: TRACE — 会话关联管理
# ════════════════════════════════════════
function Invoke-Trace {
  $traceFile = Join-Path $TracesDir "traces.json"
  $traces = @()
  if (Test-Path $traceFile) {
    try { $traces = @(Get-Content $traceFile -Raw | ConvertFrom-Json) } catch { $traces = @() }
  }
  if (-not $traces) { $traces = @() }

  if ($Action -eq "trace" -and $TraceID) {
    $ts = Get-Date -Format "o"
    $existing = $traces | Where-Object { $_.trace_id -eq $TraceID } | Select-Object -First 1
    if ($existing) {
      # 更新 trace（添加子 agent 调用）
      if ($Agent -and ($existing.agents -notcontains $Agent)) { $existing.agents += $Agent }
      $existing.last_active = $ts
    } else {
      $traces += @{
        trace_id = $TraceID
        parent_trace_id = $ParentTraceID
        session_id = $SessionID
        task = $Task
        agents = @($Agent)
        created = $ts
        last_active = $ts
        status = "active"
      }
    }
    Invoke-WithFileLock -Path $traceFile -Script {
      $traces | ConvertTo-Json -Depth 3 | Set-Content $traceFile
    }
    Write-Host "[trace] $TraceID ($Agent, parent=$ParentTraceID)"
    return $TraceID
  }

  # 搜索 trace
  if ($Query) {
    $traces | Where-Object { $_.task -match $Query -or $_.trace_id -eq $Query } | Sort-Object created -Descending | Select-Object -First $Top | ForEach-Object {
      Write-Host "  $($_.trace_id) | $($_.task) | $($_.agents -join ',') | $($_.created)"
    }
    return
  }

  # 列出最近的 trace
  $traces | Sort-Object created -Descending | Select-Object -First $Top | ForEach-Object {
    Write-Host "  $($_.trace_id) | $($_.task) | $($_.agents -join ',') | $($_.created)"
  }
}

# ════════════════════════════════════════
# Action: TRAJECTORY — 轨迹追踪
# ════════════════════════════════════════
function Invoke-Trajectory {
  if (-not $TraceID) { Write-Host "[traj] Requires -TraceID"; return }

  # 路径安全：校验 TraceID 不含穿越字符
  if ($TraceID -notmatch '^[A-Za-z0-9_-]+$') { Write-Warning "[traj] Invalid TraceID rejected: $TraceID"; return }
  $trajFile = Join-Path $TrajectoryDir "$TraceID.jsonl"
  $ts = Get-Date -Format "o"

  if ($ToolName) {
    $event = @{
      type = "tool_call"
      tool = $ToolName
      input = $ToolInput
      agent = $Agent
      trace_id = $TraceID
      timestamp = $ts
    }
    Add-Content -Path $trajFile -Value ($event | ConvertTo-Json -Compress)
    Write-Host "[traj] $TraceID → $ToolName"
  } elseif ($ToolOutput -or $ToolExitCode -ne 0 -or $ToolDurationMs -gt 0) {
    $event = @{
      type = "tool_result"
      tool = $ToolName
      output = $ToolOutput
      exit_code = $ToolExitCode
      duration_ms = $ToolDurationMs
      agent = $Agent
      trace_id = $TraceID
      timestamp = $ts
    }
    Add-Content -Path $trajFile -Value ($event | ConvertTo-Json -Compress)
    Write-Host "[traj] $TraceID ← $ToolName ($($ToolDurationMs)ms)"
  } else {
    # 读取轨迹
    if (-not (Test-Path $trajFile)) { Write-Host "[traj] No trajectory for $TraceID"; return }
    Get-Content $trajFile | ForEach-Object { $_ | ConvertFrom-Json | ForEach-Object { Write-Host "  [$($_.type)] $($_.tool) | $($_.timestamp)" } }
  }
}

# ════════════════════════════════════════
# Action: INJECT — 记忆注入
# 返回格式化的上下文片段，供拼入 agent prompt
# ════════════════════════════════════════
function Invoke-Inject {
  if (-not $Query) { Write-Host "[inject] Requires -Query"; return }

  $all = @()
  Get-ChildItem "$EntriesDir\*.json" -ErrorAction SilentlyContinue | ForEach-Object {
    $all += Get-Content $_.FullName -Raw | ConvertFrom-Json
  }

  if ($all.Count -eq 0) { return "" }

  $keywords = Expand-Keywords $Query
  $scored = @()
  foreach ($e in $all) {
    $score = 0
    $searchText = "$($e.task) $($e.content) $($e.agent) $($e.tags -join ' ')"
    foreach ($kw in $keywords) {
      $matches = [regex]::Matches($searchText, [regex]::Escape($kw), "IgnoreCase")
      $score += $matches.Count
    }
    if ($score -gt 0) {
      $scored += [PSCustomObject]@{ entry = $e; score = $score }
    }
  }

  $top = $scored | Sort-Object score -Descending | Select-Object -First $Top
  if ($top.Count -eq 0) { return "" }

  $context = @("`n[来自历史记忆]")
  foreach ($t in $top) {
    $e = $t.entry
    $snippet = ($e.content -replace "`n"," ").Substring(0, [Math]::Min(150, $e.content.Length))
    $context += "  $($e.timestamp) | $($e.agent) | $($e.task)"
    $context += "  → $snippet"
  }
  $context += "[/历史记忆]`n"

  $result = $context -join "`n"
  Write-Host "[inject] $($top.Count) memories injected for '$Query'"
  return $result
}

# ════════════════════════════════════════
# Action: STATS
# ════════════════════════════════════════
function Invoke-Stats {
  $all = @()
  Get-ChildItem "$EntriesDir\*.json" -ErrorAction SilentlyContinue | ForEach-Object {
    $all += Get-Content $_.FullName -Raw | ConvertFrom-Json
  }
  if ($all.Count -eq 0) { Write-Host "[mem] No entries yet"; return }

  $total = $all.Count
  $byAgent = $all | Group-Object agent | ForEach-Object { [PSCustomObject]@{ Agent = $_.Name; Count = $_.Count } } | Sort-Object Count -Descending
  $byTag = @{}
  foreach ($e in $all) { foreach ($t in $e.tags) { $byTag[$t] = ($byTag[$t] -or 0) + 1 } }
  $tagList = $byTag.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 10

  $trajCount = (Get-ChildItem "$TrajectoryDir\*.jsonl" -ErrorAction SilentlyContinue).Count
  $traceCount = @()
  $traceFile = Join-Path $TracesDir "traces.json"
  if (Test-Path $traceFile) { $traceCount = (Get-Content $traceFile -Raw | ConvertFrom-Json).Count }

  Write-Host "=== MAOP Memory Stats ==="
  Write-Host "Entries: $total | Traces: $traceCount | Trajectories: $trajCount"
  Write-Host "`nBy agent:"
  $byAgent | Format-Table -AutoSize | Out-String | Write-Host
  Write-Host "Top tags:"
  $tagList | ForEach-Object { Write-Host "  $($_.Key): $($_.Value)" }
}

# ════════════════════════════════════════
# Action: PRUNE — TTL 过期 + 数量裁剪
# ════════════════════════════════════════
function Invoke-Prune {
  $keep = if ($Top -gt 0) { $Top } else { 50 }
  $ttl  = if ($TtlDays -gt 0) { $TtlDays } else { 30 }   # default 30 days
  $now  = Get-Date
  $ttlCutoff = $now.AddDays(-$ttl)

  $all = @()
  Get-ChildItem "$EntriesDir\*.json" -ErrorAction SilentlyContinue | ForEach-Object {
    $all += Get-Content $_.FullName -Raw | ConvertFrom-Json
  }

  if ($all.Count -eq 0) { Write-Host "[mem] Prune: no entries"; return }

  $removedTtl = 0
  $removedCount = 0

  # ── Phase 1: TTL 过期清理 ──
  foreach ($e in $all) {
    try {
      $ts = [datetime]::Parse($e.timestamp)
      if ($ts -lt $ttlCutoff) {
        if ($DryRun) {
          Write-Host "[mem] DRY: TTL expire $($e.id) (age $([math]::Round(($now - $ts).TotalDays,1))d > ${ttl}d)"
        } else {
          Remove-Item (Join-Path $EntriesDir "$($e.id).json") -Force -ErrorAction SilentlyContinue
        }
        $removedTtl++
      }
    } catch {
      # Malformed timestamp — prune the entry
      if (-not $DryRun) {
        Remove-Item (Join-Path $EntriesDir "$($e.id).json") -Force -ErrorAction SilentlyContinue
      }
      $removedTtl++
    }
  }

  # ── Phase 2: 数量裁剪（per-agent, 仅对 TTL 存活条目） ──
  # Re-read after TTL prune to get accurate list
  if (-not $DryRun) {
    $survivors = @()
    Get-ChildItem "$EntriesDir\*.json" -ErrorAction SilentlyContinue | ForEach-Object {
      $survivors += Get-Content $_.FullName -Raw | ConvertFrom-Json
    }
  } else {
    $survivors = $all | Where-Object {
      try { [datetime]::Parse($_.timestamp) -ge $ttlCutoff } catch { $false }
    }
  }

  $byAgent = $survivors | Group-Object agent
  foreach ($g in $byAgent) {
    $sorted = $g.Group | Sort-Object timestamp -Descending
    if ($sorted.Count -gt $keep) {
      $toRemove = $sorted[$keep..($sorted.Count - 1)]
      foreach ($e in $toRemove) {
        if ($DryRun) {
          Write-Host "[mem] DRY: Count prune $($e.id) (agent=$($e.agent))"
        } else {
          Remove-Item (Join-Path $EntriesDir "$($e.id).json") -Force -ErrorAction SilentlyContinue
        }
        $removedCount++
      }
    }
  }

  # ── Also prune stale trajectory files ──
  $trajPruned = 0
  Get-ChildItem "$TrajectoryDir\*.jsonl" -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.LastWriteTime -lt $ttlCutoff) {
      if ($DryRun) {
        Write-Host "[mem] DRY: TTL expire trajectory $($_.Name)"
      } else {
        Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
      }
      $trajPruned++
    }
  }

  $totalRemoved = $removedTtl + $removedCount
  Write-Host "[mem] Pruned: $removedTtl TTL-expired + $removedCount count-exceeded + $trajPruned stale-trajectories (keep=$keep/agent, ttl=${ttl}d)"
}

# Dispatch
switch ($Action) {
  "store"      { Invoke-Store }
  "search"     { Invoke-Search }
  "trace"      { Invoke-Trace }
  "trajectory" { Invoke-Trajectory }
  "inject"     { Invoke-Inject }
  "stats"      { Invoke-Stats }
  "prune"      { Invoke-Prune }
}