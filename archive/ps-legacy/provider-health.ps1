# MAOP Provider Health Check
# 自动检测 agents.yaml 中每个 CLI 是否可用，输出 JSON
# 可被 validate-config.ps1 集成

param([switch]$Json = $false)

$ScriptDir = Split-Path $PSCommandPath -Parent
$MAOP = (Join-Path (Join-Path $ScriptDir "..") "..") | Resolve-Path
$ParsePy = Join-Path $MAOP "tools\parse-config.py"
# Use MAOP bridge for Python resolution
$bridgeScript = Join-Path $MAOP "tools\MAOP-bridge.ps1"
if (Test-Path $bridgeScript) {
  . $bridgeScript
  $python = $script:PevPython
} else {
  $python = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "python3" }
}

# Get agent list via Python bridge
$agentsJson = & $python $ParsePy "--section" "agents" 2>&1 | Out-String
$data = try { $agentsJson | ConvertFrom-Json } catch { $null }
if (-not $data) {
  if ($Json) { Write-Output '{"error":"Failed to parse agents.yaml"}' }
  else { Write-Host "ERROR: Failed to parse agents.yaml" -ForegroundColor Red }
  exit 1
}

$results = @()
$total = 0
$available = 0

foreach ($agent in $data) {
  $total++
  $name = $agent.name
  $cli = $agent.cli
  $driver = $agent.driver

  # Skip non-CLI agents (wrappers, workflows)
  if ($driver -eq "wrapper" -or -not $cli) {
    $results += @{ name=$name; cli=$cli; driver=$driver; available=$false; reason="wrapper/non-cli" }
    continue
  }

  # Try to run the CLI with a quick existence/probe command
  $probeCmd = $cli.Split(' ')[0]
  try {
    $proc = Start-Process -FilePath $probeCmd -ArgumentList "--version" -NoNewWindow -PassThru -Wait -WindowStyle Hidden -ErrorAction Stop
    $ok = ($proc.ExitCode -ge 0)
  } catch {
    try {
      # Fallback: just check if Get-Command finds it
      $null = Get-Command $probeCmd -ErrorAction Stop
      $ok = $true
    } catch {
      $ok = $false
    }
  }

  if ($ok) { $available++ }
  $results += @{ name=$name; cli=$cli; driver=$driver; available=$ok; reason=(if($ok){""}else{"not found or not executable"}) }

  if (-not $Json) {
    $color = if ($ok) { "Green" } else { "Red" }
    $status = if ($ok) { "OK" } else { "MISSING" }
    Write-Host "  [$status] $name" -ForegroundColor $color
  }
}

$summary = @{
  total_agents = $total
  available = $available
  unavailable = $total - $available
  health_pct = if ($total -gt 0) { [math]::Round($available / $total * 100, 1) } else { 0 }
  agents = $results
}

if ($Json) {
  Write-Output ($summary | ConvertTo-Json -Depth 3)
} else {
  Write-Host "`nProvider Health: $available/$total available ($([math]::Round($available/$total*100,1))%)" -ForegroundColor Cyan
}

# FUTURE: Extend to SLA monitoring — add response-time benchmarking per agent,
# track availability over time (timeseries), and integrate alerts for degraded providers.
# Current scope: binary reachability check (is the CLI executable on PATH).
