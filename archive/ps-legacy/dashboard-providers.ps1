<#
.SYNOPSIS
  MAOP Dashboard provider data — reads agents.yaml and checks CLI availability.
  Outputs JSON array to stdout. Used by MAOP Dashboard /api/providers endpoint
  and also reusable by MAOP doctor.

.PARAMETER ConfigPath
  Path to agents.yaml. Defaults to <script_dir>\..\config\agents.yaml.
#>

param([string]$ConfigPath = "")

if (-not $ConfigPath) {
  $ScriptDir = Split-Path $PSCommandPath -Parent
  $ConfigPath = Join-Path (Split-Path $ScriptDir -Parent) "config\agents.yaml"
}

if (-not (Test-Path $ConfigPath)) {
  Write-Output "[]"
  exit 0
}

try {
  $raw = Get-Content $ConfigPath -Raw -Encoding utf8
} catch {
  Write-Output "[]"
  exit 0
}

$lines = $raw -split "`r?`n"

$agents = @{}
$currentName = $null
$current = $null
$inAgents = $false
$inCaps = $false

foreach ($line in $lines) {
  $trimmed = $line.Trim()
  if ($trimmed -eq "" -or $trimmed -match "^#") { continue }

  # Detect agents: section start
  if ($trimmed -eq "agents:") {
    $inAgents = $true
    continue
  }

  # Top-level key at column 0 → end of agents section
  if ($inAgents -and $line.TrimStart() -eq $line -and $trimmed -match "^[a-z][\w-]*:") {
    $inAgents = $false
    continue
  }

  if (-not $inAgents) { continue }

  # Agent name (2-space indent, key: with no value on the same line)
  if ($line -match "^\s{2}([\w-]+):\s*$") {
    $currentName = $matches[1]
    $current = @{ name=$currentName; cli=""; driver=""; model=""; description=""; capabilities=@() }
    $agents[$currentName] = $current
    $inCaps = $false
    continue
  }

  # Agent property (4-space indent, key: value)
  if ($line -match "^\s{4}([\w-]+):\s*(.*)") {
    $k = $matches[1]; $v = $matches[2].Trim().Trim('"')
    $inCaps = $false
    if ($current) {
      switch ($k) {
        "cli"         { $current.cli = $v }
        "driver"      { $current.driver = $v }
        "model"       { $current.model = $v }
        "description" { $current.description = $v }
        "capabilities" { $inCaps = $true }
      }
    }
    continue
  }

  # Capability list item (6-space indent with -)
  if ($line -match "^\s{6}-\s+(.*)") {
    if ($current -and $inCaps) {
      $current.capabilities += $matches[1].Trim()
    }
    continue
  }
}

# Build result array
$result = @()
foreach ($name in $agents.Keys) {
  $a = $agents[$name]
  $cli = $a.cli

  $available = $false
  if ($cli -and ($a.driver -eq "cli" -or $a.driver -eq "cmd" -or $a.driver -eq "powershell")) {
    $firstToken = ($cli -split '\s+')[0]
    if ($firstToken) {
      $available = [bool](Get-Command $firstToken -ErrorAction SilentlyContinue)
    }
  }

  $result += [PSCustomObject]@{
    name         = $name
    cli          = $cli
    driver       = $a.driver
    model        = $a.model
    available    = $available
    capabilities = $a.capabilities
    description  = $a.description
  }
}

$result | ConvertTo-Json -Depth 3 -Compress
