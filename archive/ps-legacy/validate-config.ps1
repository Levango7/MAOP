<#
.SYNOPSIS
  MAOP Config Validator — agents.yaml 的 routing/capability 一致性校验
.DESCRIPTION
  验证 agents.yaml 中 routing 段引用的 agent 存在于 agents: 或 workflows: 段，
  且引用 agent 具备对应能力标签。可作为独立 CLI 调用（含 -Json 输出），
  也可被 MAOP-plan.ps1 dot-source 后调用 Test-AgentConfig 函数。
.PARAMETER ConfigPath
  agents.yaml 路径（默认自动检测）
.PARAMETER Json
  输出 JSON 而非彩色文本（供 Dashboard/doctor 使用）
.EXAMPLE
  .\src\validate-config.ps1
  .\src\validate-config.ps1 -Json
#>

param(
  [string]$ConfigPath = "",
  [switch]$Json
)

# ── Auto-detect paths ──
$ScriptDir = Split-Path $PSCommandPath -Parent
if (-not $ConfigPath) {
  $ConfigPath = Join-Path (Split-Path $ScriptDir -Parent) "config\agents.yaml"
}
if (-not (Test-Path $ConfigPath)) {
  if ($Json) { return '{ "error": "config not found", "valid": false }' }
  Write-Host "❌ config/agents.yaml not found at $ConfigPath" -ForegroundColor Red
  exit 1
}

# ── Parse a YAML mapping section into a hashtable ──
function Parse-Mapping {
  param(
    [string]$SectionName,
    [array]$Lines
  )
  $result = @{}
  $inSection = $false
  $currentKey = $null
  $currentEntry = $null

  foreach ($line in $Lines) {
    $trimmed = $line.Trim()
    if ($trimmed -eq "" -or $trimmed -match "^#") { continue }

    # Section header
    if ($line -match "^$($SectionName):") { $inSection = $true; continue }

    # Exit on next top-level key (no indent)
    if ($inSection -and $line -match "^\w[\w-]*:" -and -not ($line -match "^\s")) {
      if ($currentEntry -and $currentKey) {
        $result[$currentKey] = $currentEntry
      }
      $currentKey = $null; $currentEntry = $null
      break
    }

    if (-not $inSection) { continue }

    # 2-space indent: agent/workflow name
    # IMPORTANT: save $Matches BEFORE the second -match, which overwrites it
    $nameCond1 = $trimmed -match "^([\w-]+):\s*$"
    $nameCond2 = $line -match "^\s{2}\S"
    if ($nameCond1 -and $nameCond2) {
      $matchedName = $Matches[1]  # from the first -match that set $Matches (actually, wait, this is from nameCond1)
    }
    # Actually, in PowerShell -match sets $Matches per statement.
    # $nameCond1 = $trimmed -match "^([\w-]+):" sets $Matches[1] = name
    # Then $nameCond2 = $line -match "^\s{2}\S" overwrites $Matches with no capture group
    # So $Matches[1] is $null after nameCond2.
    # Fix: extract the capture before the second -match.

    if ($trimmed -match "^([\w-]+):\s*$") {
      $parsedName = $Matches[1]
      if ($line -match "^\s{2}\S") {
      # This is an agent name line
      if ($currentEntry -and $currentKey) { $result[$currentKey] = $currentEntry }
      $currentKey = $parsedName
      $currentEntry = @{ capabilities = @() }  # init capabilities as array
      }
    }
    # 4-space indent: properties (cli, driver, model, description, etc.)
    elseif ($currentKey) {
      if ($trimmed -match "^([\w-]+):\s+(.+)$") {
        $propName = $Matches[1]
        $propVal = $Matches[2].Trim()
        if ($line -match "^\s{4}\S") {
          $currentEntry[$propName] = $propVal
        }
      }
      # capabilities list items (6-space indent)
      elseif ($trimmed -match "^-\s+([\w-]+)$") {
        $capName = $Matches[1]
        if ($line -match "^\s{6}\S") {
          $currentEntry.capabilities += $capName
        }
      }
    }
  }

  # Flush last entry
  if ($currentEntry -and $currentKey) {
    $result[$currentKey] = $currentEntry
  }

  return $result
}

# ── Main validation ──
function Test-AgentConfig {
  param(
    [string]$ConfigPath,
    [switch]$ReturnObject  # output as hashtable for programmatic use
  )

  $localLines = Get-Content $ConfigPath -Encoding utf8
  $localScriptDir = Split-Path $PSCommandPath -Parent

  # Re-parse inline (avoid dot-source race)
  $agents = Parse-Mapping -SectionName "agents" -Lines $localLines
  $workflows = Parse-Mapping -SectionName "workflows" -Lines $localLines
  $routing = Parse-Mapping -SectionName "routing" -Lines $localLines

  # Merge agents + workflows into one lookup
  $allEntities = @{}
  foreach ($k in $agents.Keys) { $allEntities[$k] = $agents[$k] }
  foreach ($k in $workflows.Keys) { $allEntities[$k] = $workflows[$k] }

  $errors = @(); $warnings = @()
  $routingKeys = $routing.Keys | Sort-Object

  foreach ($rk in $routingKeys) {
    $entry = $routing[$rk]
    $levels = @("primary", "fallback", "tertiary")

    foreach ($level in $levels) {
      if (-not $entry.ContainsKey($level)) { continue }
      $agentName = $entry[$level]
      if ([string]::IsNullOrWhiteSpace($agentName)) { continue }

      # Check agent exists
      if (-not $allEntities.ContainsKey($agentName)) {
        $errors += "routing.$rk.$level = '$agentName' → agent not found in agents: or workflows:"
        continue
      }

      # Check capability match
      $agentCaps = @()
      if ($allEntities[$agentName].ContainsKey("capabilities")) {
        $agentCaps = $allEntities[$agentName]["capabilities"]
      }
      if ($rk -notin $agentCaps -and $agentCaps -notcontains "*") {
        $warnings += "routing.$rk.$level = '$agentName' → routing key '$rk' not in agent's capabilities [$($agentCaps -join ', ')]"
      }
    }
  }

  # Check for stale agents (defined but never referenced in routing)
  $referencedAgents = @()
  foreach ($rk in $routingKeys) {
    $entry = $routing[$rk]
    foreach ($level in @("primary", "fallback", "tertiary")) {
      if ($entry.ContainsKey($level)) {
        $referencedAgents += $entry[$level]
      }
    }
  }

  $unreferenced = @()
  foreach ($agentName in $allEntities.Keys) {
    if ($agentName -notin $referencedAgents -and $agentName -notin @("doc-pipeline")) {
      $unreferenced += $agentName
    }
  }

  # Count CLI availability
  $availableCount = 0; $unavailableCount = 0; $wrapperCount = 0
  $agentAvailability = @{}
  foreach ($agentName in $agents.Keys) {
    $entry = $agents[$agentName]
    $driver = if ($entry.ContainsKey("driver")) { $entry["driver"] } else { "cli" }
    $cliCmd = if ($entry.ContainsKey("cli")) { $entry["cli"] } else { $null }

    if ($driver -eq "wrapper") {
      $wrapperCount++
      $agentAvailability[$agentName] = "wrapper"
    } else {
      # Extract first word for Get-Command check
      $binary = if ($cliCmd) { ($cliCmd -split "\s+")[0] } else { $null }
      if ($binary -and (Get-Command $binary -ErrorAction SilentlyContinue)) {
        $availableCount++
        $agentAvailability[$agentName] = "available"
      } else {
        $unavailableCount++
        $agentAvailability[$agentName] = "missing"
      }
    }
  }

  # Build result
  $result = @{
    valid           = ($errors.Count -eq 0)
    agents_count    = $agents.Keys.Count
    workflows_count = $workflows.Keys.Count
    routing_count   = $routingKeys.Count
    agents          = @($agents.Keys | Sort-Object | ForEach-Object {
      $e = $agents[$_]
      $c = if ($e.ContainsKey("cli")) { $e["cli"] } else { "" }
      $d = if ($e.ContainsKey("driver")) { $e["driver"] } else { "cli" }
      $m = if ($e.ContainsKey("model")) { $e["model"] } else { "" }
      $cap = if ($e.ContainsKey("capabilities")) { @($e["capabilities"]) } else { @() }
      @{
        name          = $_
        cli           = $c
        driver        = $d
        model         = $m
        available     = $agentAvailability[$_]
        capabilities  = $cap
      }
    })
    errors          = @($errors)
    warnings        = @($warnings)
    unreferenced    = @($unreferenced | Sort-Object)
    summary         = "agents: $($agents.Keys.Count) ($availableCount ✅, $unavailableCount ❌, $wrapperCount ⚪) | routing: $($routingKeys.Count) ($($errors.Count) ❌, $($warnings.Count) ⚠️)"
  }

  if ($ReturnObject) { return $result }

  # ── Output ──
  if ($Json.IsPresent -or $ReturnObject) {
    return $result | ConvertTo-Json -Depth 3
  }

  Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Cyan
  Write-Host "║     MAOP Config Validation Report         ║" -ForegroundColor Cyan
  Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Cyan

  Write-Host "`nAgents:" -ForegroundColor White
  foreach ($agentName in ($agents.Keys | Sort-Object)) {
    $e = $agents[$agentName]
    $avail = $agentAvailability[$agentName]
    $icon = switch ($avail) { "available" { "✅" } "missing" { "❌" } "wrapper" { "⚪" } default { "❓" } }
    $cli = if ($e.ContainsKey("cli")) { $e["cli"] } else { "-" }
    $model = if ($e.ContainsKey("model")) { $e["model"] } else { "-" }
    Write-Host "  $icon $agentName" -NoNewline
    Write-Host "  cli=$cli  model=$model" -ForegroundColor DarkGray
  }

  Write-Host "`nWorkflows:" -ForegroundColor White
  foreach ($wfName in ($workflows.Keys | Sort-Object)) {
    $e = $workflows[$wfName]
    $desc = if ($e.ContainsKey("description")) { $e["description"] } else { "-" }
    Write-Host "  🔄 $wfName — $desc" -ForegroundColor Gray
  }

  Write-Host "`nRouting:" -ForegroundColor White
  foreach ($rk in $routingKeys) {
    $entry = $routing[$rk]
    $line = "  $rk → "
    $line += "primary=$($entry['primary'])"
    if ($entry.ContainsKey("fallback")) { $line += " / fallback=$($entry['fallback'])" }
    if ($entry.ContainsKey("tertiary")) { $line += " / tertiary=$($entry['tertiary'])" }
    Write-Host $line
  }

  if ($errors.Count -gt 0) {
    Write-Host "`nErrors:" -ForegroundColor Red
    foreach ($e in $errors) { Write-Host "  ❌ $e" -ForegroundColor Red }
  }
  if ($warnings.Count -gt 0) {
    Write-Host "`nWarnings:" -ForegroundColor Yellow
    foreach ($w in $warnings) { Write-Host "  ⚠️ $w" -ForegroundColor Yellow }
  }

  Write-Host "`nSummary: $($result.summary)" -ForegroundColor White
  if ($result.valid) {
    Write-Host "✅ Config is valid." -ForegroundColor Green
  } else {
    Write-Host "❌ Config has $($errors.Count) error(s)." -ForegroundColor Red
  }

  return $result.valid
}

# ── Optional: Provider health check ──
function Test-ProviderHealth {
  $healthScript = Join-Path (Split-Path $ConfigPath -Parent) "..\src\provider-health.ps1"
  if (Test-Path $healthScript) {
    Write-Host "`nProvider Health Check:" -ForegroundColor DarkCyan
    & $healthScript -Json:$false 2>&1 | Out-Null
  }
}

# Execute
if ($Json.IsPresent) {
  $result = Test-AgentConfig -ConfigPath $ConfigPath -ReturnObject
  $result | ConvertTo-Json -Depth 3
} else {
  $valid = Test-AgentConfig -ConfigPath $ConfigPath -Json:$false
  Test-ProviderHealth
  exit $(if ($valid) { 0 } else { 1 })
}
