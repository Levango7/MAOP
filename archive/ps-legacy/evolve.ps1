param(
  [ValidateSet("analyze", "suggest", "apply", "promote", "status")]
  [string]$Action = "analyze",
  [string]$SuggestionId,
  [switch]$AutoApply,
  [string]$HarnessDir = (Split-Path $PSCommandPath -Parent)
)

# MAOP Harness — Self-Evolution Engine v2
# Reads execution history, computes stats, suggests improvements, auto-applies.

function Get-ObservabilityData {
  $logFile = Join-Path (Join-Path $HarnessDir "logs") "delegations.json"
  if (-not (Test-Path $logFile)) { return @() }
  $data = Get-Content $logFile -Raw
  if (-not $data) { return @() }
  $raw = $data | ConvertFrom-Json
  if (-not $raw) { return @() }
  if ($raw -is [array]) { return $raw }
  return @($raw)
}

function Compute-Stats($data) {
  if ($data.Count -eq 0) { return $null }

  $perAgent = $data | Group-Object agent | ForEach-Object {
    $total = $_.Count
    $success = 0; $avgDurationSum = 0; $avgCount = 0
    foreach ($item in $_.Group) {
      if ($item.result.exit_code -eq 0) { $success++ }
      if ($item.result.duration_ms) { $avgDurationSum += $item.result.duration_ms; $avgCount++ }
    }
    $avgDuration = if ($avgCount -gt 0) { [math]::Round($avgDurationSum / $avgCount) } else { 0 }
    $fail = $total - $success

    [PSCustomObject]@{
      agent = $_.Name
      total = $total
      success = $success
      fail = $fail
      rate = if ($total -gt 0) { [math]::Round(($success / $total) * 100, 1) } else { 0 }
      avg_duration_ms = $avgDuration
    }
  }

  $perKey = $data | Group-Object { $_.routing_key } | ForEach-Object {
    $total = $_.Count
    $success = 0
    foreach ($item in $_.Group) { if ($item.result.exit_code -eq 0) { $success++ } }
    [PSCustomObject]@{
      routing_key = $_.Name
      total = $total
      success = $success
      rate = if ($total -gt 0) { [math]::Round(($success / $total) * 100, 1) } else { 0 }
    }
  }

  $perAgentKey = $data | Group-Object { "$($_.agent):$($_.routing_key)" } | ForEach-Object {
    $total = $_.Count
    $success = 0; $avgDurationSum = 0; $avgCount = 0
    foreach ($item in $_.Group) {
      if ($item.result.exit_code -eq 0) { $success++ }
      if ($item.result.duration_ms) { $avgDurationSum += $item.result.duration_ms; $avgCount++ }
    }
    $avgDuration = if ($avgCount -gt 0) { [math]::Round($avgDurationSum / $avgCount) } else { 0 }

    $parts = $_.Name -split ':', 2
    [PSCustomObject]@{
      agent = $parts[0]
      routing_key = $parts[1]
      total = $total
      success = $success
      rate = if ($total -gt 0) { [math]::Round(($success / $total) * 100, 1) } else { 0 }
      avg_duration_ms = $avgDuration
    }
  }

  return @{ by_agent = $perAgent; by_key = $perKey; by_agent_key = $perAgentKey }
}

function Generate-Suggestions($stats, $data) {
  $suggestions = @()
  if (-not $stats) { return $suggestions }

  $sid = 0

  # 1. Low success rate per agent
  foreach ($a in $stats.by_agent) {
    if ($a.total -ge 3 -and $a.rate -lt 60) {
      $suggestions += [PSCustomObject]@{
        id = "S$( '{0:D3}' -f $sid )"
        type = "agent_low_success"
        severity = "high"
        agent = $a.agent
        detail = "$($a.agent): $($a.rate)% success ($($a.success)/$($a.total))"
        suggestion = "Check if $($a.agent) CLI is working. Consider updating delegate.ps1 or switching primary agent in agents.yaml"
        auto_applicable = $false
      }
      $sid++
    }
  }

  # 2. Per-agent-key optimization
  foreach ($ak in $stats.by_agent_key) {
    if ($ak.total -ge 3 -and $ak.rate -lt 50) {
      $suggestions += [PSCustomObject]@{
        id = "S$( '{0:D3}' -f $sid )"
        type = "routing_mismatch"
        severity = "high"
        agent = $ak.agent
        routing_key = $ak.routing_key
        detail = "$($ak.agent)/$($ak.routing_key): $($ak.rate)% ($($ak.success)/$($ak.total))"
        suggestion = "Agent $($ak.agent) is underperforming for routing_key=$($ak.routing_key). Change routing in agents.yaml"
        auto_applicable = $true
      }
      $sid++
    }
  }

  # 3. Slow agents
  foreach ($a in $stats.by_agent) {
    if ($a.total -ge 2 -and $a.avg_duration_ms -gt 60000) {
      $suggestions += [PSCustomObject]@{
        id = "S$( '{0:D3}' -f $sid )"
        type = "slow_agent"
        severity = "medium"
        agent = $a.agent
        detail = "$($a.agent): avg $($a.avg_duration_ms)ms"
        suggestion = "Reduce timeout_s for $($a.agent) in agents.yaml or try a faster model"
        auto_applicable = $true
      }
      $sid++
    }
  }

  # 4. Prompt version drift
  $byPrompt = @(); $pvGroups = @{}
  foreach ($item in $data) { if ($item.prompt_version) { if (-not $pvGroups.ContainsKey($item.prompt_version)) { $pvGroups[$item.prompt_version] = @() }; $pvGroups[$item.prompt_version] += $item } }
  foreach ($k in $pvGroups.Keys) { $byPrompt += [PSCustomObject]@{ Name = $k; Group = $pvGroups[$k]; Count = $pvGroups[$k].Count } }
  foreach ($g in $byPrompt) {
    $total = $g.Count
    $success = 0; foreach ($item in $g.Group) { if ($item.result.exit_code -eq 0) { $success++ } }
    $rate = if ($total -gt 0) { [math]::Round(($success / $total) * 100, 1) } else { 0 }
    if ($total -ge 3 -and $rate -lt 50) {
      $suggestions += [PSCustomObject]@{
        id = "S$( '{0:D3}' -f $sid )"
        type = "prompt_regression"
        severity = "medium"
        detail = "Prompt v$($g.Name): $rate% success ($($success)/$($total))"
        suggestion = "Prompt version $($g.Name) has low success rate. Consider reverting in registry.yaml"
        auto_applicable = $false
      }
      $sid++
    }
  }

  # 5. Empty routing key
  $noKey = $data | Where-Object { -not $_.routing_key -or $_.routing_key -eq "" }
  if ($noKey.Count -gt 0) {
    $suggestions += [PSCustomObject]@{
      id = "S$( '{0:D3}' -f $sid )"
      type = "missing_routing_key"
      severity = "medium"
      detail = "$($noKey.Count) delegations without routing_key"
      suggestion = "Ensure MAOP-plan.ps1 always sets routing_key. Check observability logging."
      auto_applicable = $false
    }
    $sid++
  }

  # 6. Unused agents (configured but never called)
  $usedAgents = ($data | Where-Object { $_.agent } | ForEach-Object { $_.agent }) + ($stats.by_agent | ForEach-Object { $_.agent })
  $usedAgents = $usedAgents | Select-Object -Unique

  # Read agents.yaml to find all configured agents
  $yamlPath = Join-Path (Split-Path $HarnessDir -Parent) "config\agents.yaml"
  if (Test-Path $yamlPath) {
    $yamlLines = Get-Content $yamlPath
    $inAgents = $false
    $configuredAgents = @()
    $skipKeys = @("routing", "capabilities", "model", "timeout_s", "description", "cli", "primary", "fallback", "tertiary", "codegen", "refactor", "search", "planning", "review", "verify", "fileops", "chat", "quickfix", "mcp")
    foreach ($line in $yamlLines) {
      if ($line -match "^agents:") { $inAgents = $true; continue }
      if ($inAgents -and $line -match "^routing:") { break }
      if ($inAgents -and $line -match "^\s{2}(\w+):$") {
        $name = $Matches[1]
        if ($skipKeys -notcontains $name) { $configuredAgents += $name }
      }
    }
    $unused = $configuredAgents | Where-Object { $usedAgents -notcontains $_ }
    if ($unused.Count -gt 0) {
      $suggestions += [PSCustomObject]@{
        id = "S$( '{0:D3}' -f $sid )"
        type = "unused_agent"
        severity = "low"
        detail = "Unused agents: $($unused -join ', ')"
        suggestion = "These agents are configured in agents.yaml but never used. Consider removing or updating routing."
        auto_applicable = $false
      }
      $sid++
    }
  }

  # 7. Regression detection: agent getting worse over time
  $timeline = $data | Where-Object { $_.result -and $_.result.duration_ms } | Sort-Object { $_.timestamp }
  $byAgentTimeline = $timeline | Group-Object agent
  foreach ($g in $byAgentTimeline) {
    if ($g.Count -ge 4) {
      $sorted = $g.Group | Sort-Object timestamp
      $half = [math]::Floor($sorted.Count / 2)
      $firstHalf = $sorted[0..($half-1)]
      $secondHalf = $sorted[$half..($sorted.Count-1)]
      $firstSuccess = ($firstHalf | Where-Object { $_.result.exit_code -eq 0 }).Count
      $secondSuccess = ($secondHalf | Where-Object { $_.result.exit_code -eq 0 }).Count
      $firstRate = ($firstSuccess / $firstHalf.Count) * 100
      $secondRate = ($secondSuccess / $secondHalf.Count) * 100
      if ($firstRate - $secondRate -gt 20) {
        $suggestions += [PSCustomObject]@{
          id = "S$( '{0:D3}' -f $sid )"
          type = "regression"
          severity = "high"
          agent = $g.Name
          detail = "$($g.Name) regression: $('{0:N1}' -f $firstRate)% → $('{0:N1}' -f $secondRate)% ($($g.Count) total runs)"
          suggestion = "$($g.Name) is degrading. Check for recent changes to delegate.ps1, model, or prompt."
          auto_applicable = $false
        }
        $sid++
      }
    }
  }

  return $suggestions
}

# ── Auto-apply routing changes in agents.yaml ─────────────────
function Set-AgentsYamlRouting($routingKey, $oldAgent, $newAgent) {
  $yamlPath = Join-Path (Split-Path $HarnessDir -Parent) "config\agents.yaml"
  if (-not (Test-Path $yamlPath)) { Write-Host "[evolve] agents.yaml not found at $yamlPath"; return $false }

  $lines = Get-Content $yamlPath
  $inRouting = $false
  $inTargetKey = $false
  $changed = $false

  for ($i = 0; $i -lt $lines.Count; $i++) {
    $line = $lines[$i]

    if ($line -match "^routing:") { $inRouting = $true; continue }
    if (-not $inRouting) { continue }

    # Detect when leaving routing section (next top-level key)
    if ($inRouting -and $line -match "^\w+:" -and $line -notmatch "^\s") { break }

    if ($line -match "^\s+${routingKey}:") { $inTargetKey = $true; continue }
    if ($inTargetKey -and $line -match "^\s+\w+:" -and $line -notmatch "^\s{6}") {
      # Next sibling routing key or end of section
      $inTargetKey = $false
      continue
    }
    if (-not $inTargetKey) { continue }

    # Inside target routing key, look for primary/fallback/tertiary
    if ($line -match "^\s+(primary|fallback|tertiary):\s+$oldAgent\s*$") {
      $lines[$i] = $line -replace ":\s+$oldAgent\s*$", ": $newAgent"
      Write-Host "[evolve] agents.yaml: $routingKey/$($Matches[1]): $oldAgent → $newAgent"
      $changed = $true
    }
  }

  if ($changed) {
    $lines | Set-Content $yamlPath
    Write-Host "[evolve] agents.yaml updated"
  } else {
    Write-Host "[evolve] No match found for $routingKey/$oldAgent in agents.yaml"
  }

  return $changed
}

# ── Auto-apply timeout changes in agents.yaml ─────────────────
function Set-AgentsYamlTimeout($agent, $newTimeout) {
  $yamlPath = Join-Path (Split-Path $HarnessDir -Parent) "config\agents.yaml"
  if (-not (Test-Path $yamlPath)) { return $false }

  $lines = Get-Content $yamlPath
  $changed = $false

  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match "^\s+${agent}:") {
      # Look for timeout_s line within the next few lines
      for ($j = $i + 1; $j -lt $lines.Count -and $j -le $i + 10; $j++) {
        if ($lines[$j] -match "^\s+timeout_s:\s+\d+") {
          $currentTimeout = [int]($lines[$j] -replace "^\s+timeout_s:\s+", "")
          if ($currentTimeout -gt $newTimeout) {
            $lines[$j] = "    timeout_s: $newTimeout"
            Write-Host "[evolve] agents.yaml: $agent timeout_s: $currentTimeout → $newTimeout"
            $changed = $true
          }
          break
        }
      }
      break
    }
  }

  if ($changed) { $lines | Set-Content $yamlPath }
  return $changed
}

# ── Find best replacement agent for a routing key ─────────────
function Find-ReplacementAgent($routingKey, $badAgent) {
  # Read agents.yaml to find which agents have this capability
  $yamlPath = Join-Path (Split-Path $HarnessDir -Parent) "config\agents.yaml"
  if (-not (Test-Path $yamlPath)) { return $null }

  $lines = Get-Content $yamlPath
  $inAgents = $false
  $currentAgent = $null
  $hasCapability = $false

  $candidates = @()

  foreach ($line in $lines) {
    if ($line -match "^agents:") { $inAgents = $true; continue }
    if (-not $inAgents) { continue }
    if ($inAgents -and $line -match "^routing:") { break }

    if ($line -match "^\s+(\w+):") {
      if ($currentAgent -and $hasCapability -and $currentAgent -ne $badAgent) {
        $candidates += $currentAgent
      }
      $currentAgent = $Matches[1]
      $hasCapability = $false
    }
    elseif ($currentAgent -and $line -match "^\s+-\s+$routingKey\s*$") {
      $hasCapability = $true
    }
  }

  # Check last agent
  if ($currentAgent -and $hasCapability -and $currentAgent -ne $badAgent) {
    $candidates += $currentAgent
  }

  if ($candidates.Count -eq 0) { return $null }

  # Use observability data to pick best candidate by success rate
  $data = Get-ObservabilityData
  if ($data.Count -gt 0) {
    $best = $null; $bestRate = -1
    foreach ($c in $candidates) {
      $entries = $data | Where-Object { $_.agent -eq $c -and $_.routing_key -eq $routingKey }
      if ($entries.Count -ge 1) {
        $success = ($entries | Where-Object { $_.result.exit_code -eq 0 }).Count
        $rate = ($success / $entries.Count) * 100
        if ($rate -gt $bestRate) { $bestRate = $rate; $best = $c }
      }
    }
    if ($best) { return $best }
  }

  # Fallback: return first candidate
  return $candidates[0]
}

function Apply-Suggestion($sid, $suggestions, $autoApply) {
  $sug = $suggestions | Where-Object { $_.id -eq $sid }
  if (-not $sug) { Write-Host "[evolve] Suggestion $sid not found"; return }
  if (-not $sug.auto_applicable) { Write-Host "[evolve] Suggestion $sid is not auto-applicable"; return }

  Write-Host "[evolve] Applying $($sid): $($sug.suggestion)"

  switch ($sug.type) {
    "routing_mismatch" {
      $replacement = Find-ReplacementAgent $sug.routing_key $sug.agent
      if (-not $replacement) {
        Write-Host "[evolve] No replacement agent found for $($sug.routing_key) (excluding $($sug.agent))"
        return
      }
      Write-Host "[evolve] Replacing $($sug.agent) with $replacement for routing_key=$($sug.routing_key)"
      Set-AgentsYamlRouting $sug.routing_key $sug.agent $replacement
    }
    "slow_agent" {
      $data = Get-ObservabilityData
      $agentData = $data | Where-Object { $_.agent -eq $sug.agent }
      $avgDuration = 0
      $durations = $agentData | ForEach-Object { if ($_.result.duration_ms) { $_.result.duration_ms } }
      if ($durations.Count -gt 0) { $avgDuration = [math]::Round(($durations | Measure-Object -Average).Average) }
      $newTimeout = [math]::Max(30, [math]::Ceiling($avgDuration * 2.5 / 1000) * 1000)
      Set-AgentsYamlTimeout $sug.agent $newTimeout
    }
    default {
      Write-Host "[evolve] Unknown suggestion type: $($sug.type)"
    }
  }
}

# ── Prompt promotion (A/B testing) ────────────────────────────
function Promote-Prompts($data) {
  $registryPath = Join-Path (Join-Path $HarnessDir "prompts") "registry.yaml"
  if (-not (Test-Path $registryPath)) { Write-Host "[evolve] registry.yaml not found"; return }

  $lines = Get-Content $registryPath
  $entries = @()
  $current = $null

  # Parse entries into objects
  foreach ($line in $lines) {
    if ($line -match "^\s+- id:\s+(.+)$") {
      if ($current) { $entries += $current }
      $current = @{ id = $Matches[1].Trim(); version = $null; tag = $null; template = $null; _lines = @() }
      $current._lines += $line
    }
    elseif ($current) {
      $current._lines += $line
      if ($line -match "^\s+version:\s+(\d+)") { $current.version = [int]$Matches[1] }
      elseif ($line -match "^\s+tag:\s+(\w+)") { $current.tag = $Matches[1] }
      elseif ($line -match "^\s+template:\s+(.+)$") { $current.template = $Matches[1] }
    }
  }
  if ($current) { $entries += $current }

  # Group by base ID (strip .beta suffix)
  $byBase = @{}
  foreach ($e in $entries) {
    $baseId = $e.id
    $isBeta = $baseId -match "^(.+)\.beta$"
    if ($isBeta) { $baseId = $Matches[1]; $e._isBeta = $true }
    else { $e._isBeta = $false }

    if (-not $byBase.ContainsKey($baseId)) { $byBase[$baseId] = @() }
    $byBase[$baseId] += $e
  }

  $promoted = 0
  foreach ($baseId in $byBase.Keys) {
    $group = $byBase[$baseId]
    $stable = $group | Where-Object { $_.tag -eq "stable" -and -not $_.isBeta }
    $beta = $group | Where-Object { $_.tag -eq "beta" -or $_.isBeta }

    if (-not $stable -or -not $beta) { continue }

    # Get success rate for stable version
    $stableVer = "v$($stable.version)"
    $stableEntries = $data | Where-Object { $_.prompt_version -eq $stableVer }
    if ($stableEntries.Count -lt 3) { continue }  # Not enough data

    $stableSuccess = ($stableEntries | Where-Object { $_.result.exit_code -eq 0 }).Count
    $stableRate = ($stableSuccess / $stableEntries.Count) * 100

    # Get success rate for beta version
    $betaVer = "v$($beta.version)"
    $betaEntries = $data | Where-Object { $_.prompt_version -eq $betaVer }
    if ($betaEntries.Count -lt 2) { continue }  # Need at least 2 beta samples

    $betaSuccess = ($betaEntries | Where-Object { $_.result.exit_code -eq 0 }).Count
    $betaRate = ($betaSuccess / $betaEntries.Count) * 100

    Write-Host "[evolve] A/B: $baseId — stable(v$($stable.version)): $('{0:N1}' -f $stableRate)% ($($stableEntries.Count) runs), beta(v$($beta.version)): $('{0:N1}' -f $betaRate)% ($($betaEntries.Count) runs)"

    if ($betaRate -gt $stableRate -and $betaEntries.Count -ge 3) {
      # Promote beta → stable, deprecate old stable
      Write-Host "[evolve] Promoting $baseId v$($beta.version) (beta → stable), deprecating v$($stable.version)"
      $newLines = @()
      foreach ($l in $lines) {
        if ($l -match "^\s+tag:\s+stable\s*$" -and $newLines.Count -gt 0 -and $newLines[-1] -match "^\s+version:\s+$($stable.version)\s*$") {
          $newLines += "    tag: deprecated"
        }
        elseif ($l -match "^\s+tag:\s+beta\s*$") {
          $newLines += "    tag: stable"
        }
        else { $newLines += $l }
      }
      $newLines | Set-Content $registryPath
      $promoted++
    }
  }

  if ($promoted -eq 0) {
    Write-Host "[evolve] No promotions triggered. Either insufficient data or beta is not outperforming stable."
  } else {
    Write-Host "[evolve] $promoted prompt(s) promoted to stable."
  }
}

function Show-Status($stats, $suggestions) {
  Write-Host "=== MAOP Evolution Status ==="
  if (-not $stats) { Write-Host "No data yet. Run some cycles first."; return }

  Write-Host "`n--- Agent Performance ---"
  Write-Host ("{0,-12} {1,6} {2,6} {3,6} {4,8} {5,10}" -f "Agent", "Total", "OK", "Fail", "Rate%", "Avg(ms)")
  Write-Host ("{0,-12} {1,6} {2,6} {3,6} {4,8} {5,10}" -f "-----", "-----", "--", "----", "----", "-------")
  foreach ($a in ($stats.by_agent | Sort-Object rate)) {
    Write-Host ("{0,-12} {1,6} {2,6} {3,6} {4,7}% {5,10}" -f $a.agent, $a.total, $a.success, $a.fail, $a.rate, $a.avg_duration_ms)
  }

  Write-Host "`n--- Routing Key Performance ---"
  Write-Host ("{0,-12} {1,6} {2,6} {3,7}" -f "Key", "Total", "OK", "Rate%")
  Write-Host ("{0,-12} {1,6} {2,6} {3,7}" -f "---", "-----", "--", "----")
  foreach ($k in ($stats.by_key | Sort-Object rate)) {
    Write-Host ("{0,-12} {1,6} {2,6} {3,6}%" -f $k.routing_key, $k.total, $k.success, $k.rate)
  }

  if ($suggestions.Count -gt 0) {
    Write-Host "`n--- Suggestions ($($suggestions.Count)) ---"
    foreach ($s in $suggestions) {
      $icon = if ($s.severity -eq "high") { "!!" } elseif ($s.severity -eq "medium") { "! " } else { "  " }
      Write-Host "  [$icon] $($s.id) [$($s.severity)] $($s.detail)"
      Write-Host "        → $($s.suggestion)"
      if (-not $s.auto_applicable) { Write-Host "        (manual only)" }
      else { Write-Host "        (auto-applicable: evolve.ps1 -Action apply -SuggestionId $($s.id) -AutoApply)" }
    }
  } else {
    Write-Host "`nNo suggestions. System is healthy."
  }
}

# ════════════════════════════════════════
# Main
# ════════════════════════════════════════
$data = Get-ObservabilityData
$stats = Compute-Stats $data
$suggestions = Generate-Suggestions $stats $data

switch ($Action) {
  "analyze" {
    Show-Status $stats $suggestions
    return ($suggestions | ConvertTo-Json -Depth 3)
  }

  "suggest" {
    return ($suggestions | ConvertTo-Json -Depth 3)
  }

  "apply" {
    if ($AutoApply) {
      $autoApplied = 0
      foreach ($s in $suggestions) {
        if ($s.auto_applicable) {
          Apply-Suggestion $s.id $suggestions $true
          $autoApplied++
        }
      }
      Write-Host "[evolve] Auto-applied $autoApplied suggestion(s)"
    }
    elseif ($SuggestionId) {
      Apply-Suggestion $SuggestionId $suggestions $false
    }
    else {
      Write-Host "[evolve] Usage: -Action apply -SuggestionId S001  OR  -Action apply -AutoApply"
    }
  }

  "promote" {
    Promote-Prompts $data
  }

  "status" {
    Show-Status $stats $suggestions
  }
}


