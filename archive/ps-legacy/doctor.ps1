<#
.SYNOPSIS
    MAOP Doctor — 一键诊断工具
.DESCRIPTION
    检查 Agent CLI 可用性、Routing 配置有效性和全局系统状态。
    支持彩色 console 输出和 -Json 参数供 Dashboard 消费。
.PARAMETER Json
    输出 JSON 格式（不输出彩色文本），供 Dashboard 集成。
.EXAMPLE
    powershell -File src\doctor.ps1
    powershell -File src\doctor.ps1 -Json
#>

param(
    [switch]$Json
)

# ── Paths ──
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$MAOP = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$ConfigPath = Join-Path (Join-Path $MAOP "config") "agents.yaml"
$DataDir = Join-Path $MAOP "data"
$CircuitBreakerPath = Join-Path $DataDir "circuit-breaker.json"
$DashboardUrl = "http://localhost:8080/api/health"

if (-not (Test-Path $ConfigPath)) {
    if ($Json) { Write-Output (ConvertTo-Json @{error = "Config not found: $ConfigPath"}) }
    else { Write-Host "`n[FATAL] Config not found: $ConfigPath" -ForegroundColor Red }
    exit 1
}

# ============================================================
#  Config Bridge (replaces hand-rolled YAML parser)
# ============================================================
. (Join-Path $MAOP "tools\MAOP-bridge.ps1")

# ============================================================
#  Agent Availability Check
# ============================================================
function Check-AgentAvailability {
    param([hashtable]$Agents)

    $results = @()

    foreach ($name in $Agents.Keys | Sort-Object) {
        $agt = $Agents[$name]
        $cliRaw = if ($agt.ContainsKey('cli')) { $agt['cli'] } else { '' }
        $driver = if ($agt.ContainsKey('driver')) { $agt['driver'] } else { 'cli' }
        $model = if ($agt.ContainsKey('model')) { $agt['model'] } else { '' }
        $caps = if ($agt.ContainsKey('capabilities')) { $agt['capabilities'] } else { @() }

        # Extract first word of the CLI command
        $cliCheck = ($cliRaw -split '\s+')[0]

        $available = $false
        $status = ''

        if ($driver -eq 'wrapper') {
            $status = '⚪'
            $available = $true  # wrapper is "available" by definition
        } else {
            # For cli/cmd/powershell drivers, check the command on PATH
            if ([string]::IsNullOrEmpty($cliCheck)) {
                $status = '❌'
                $available = $false
            } else {
                $cmd = Get-Command $cliCheck -ErrorAction SilentlyContinue -CommandType Application
                if ($cmd) {
                    $status = '✅'
                    $available = $true
                } else {
                    $status = '❌'
                    $available = $false
                }
            }
        }

        $results += [PSCustomObject]@{
            name         = $name
            cli          = $cliRaw
            driver       = $driver
            model        = $model
            capabilities = $caps -join ', '
            available    = $available
            status       = $status
        }
    }

    return $results
}

# ============================================================
#  Routing Validation
# ============================================================
function Test-Routing {
    param(
        [hashtable]$Routing,
        [hashtable]$Agents,
        [hashtable]$Workflows
    )

    $results = @()
    $validSlots = @('primary', 'fallback', 'tertiary')

    # Build a merged lookup of all known agent/workflow names
    $known = @{}
    if ($Agents) { foreach ($k in $Agents.Keys) { $known[$k] = $Agents[$k] } }
    if ($Workflows) { foreach ($k in $Workflows.Keys) { $known[$k] = $Workflows[$k] } }

    foreach ($routeName in ($Routing.Keys | Sort-Object)) {
        $route = $Routing[$routeName]

        foreach ($slot in $validSlots) {
            if (-not $route.ContainsKey($slot)) { continue }

            $agentName = $route[$slot]
            $status = ''
            $message = ''
            $capMatch = $false

            # Check if agent/workflow exists
            if ($known.ContainsKey($agentName)) {
                $target = $known[$agentName]

                # Check if the target has a capabilities list
                if ($target.ContainsKey('capabilities') -and $target['capabilities'] -is [array] -and $target['capabilities'].Count -gt 0) {
                    if ($target['capabilities'] -contains $routeName) {
                        $status = '✅'
                        $message = "OK"
                        $capMatch = $true
                    } else {
                        $status = '❌'
                        $message = "Missing capability '$routeName' on '$agentName' (has: $($target['capabilities'] -join ', '))"
                        $capMatch = $false
                    }
                } else {
                    # Agent exists but has no capabilities declared
                    $status = '⚠️'
                    $message = "Agent '$agentName' exists but capabilities not declared"
                    $capMatch = $false
                }
            } else {
                $status = '⚠️'
                $message = "Agent/workflow '$agentName' does not exist in config"
                $capMatch = $false
            }

            $results += [PSCustomObject]@{
                route    = $routeName
                slot     = $slot
                agent    = $agentName
                status   = $status
                message  = $message
                capMatch = $capMatch
            }
        }
    }

    return $results
}

# ============================================================
#  Global Checks
# ============================================================
function Invoke-GlobalChecks {
    $results = @{}

    # ── Dashboard health ──
    $dashStatus = '❌'
    $dashMsg = 'Not running / unreachable'
    try {
        $resp = Invoke-WebRequest -Uri $DashboardUrl -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        if ($resp.StatusCode -eq 200) {
            $dashStatus = '✅'
            $dashMsg = "Running (HTTP $($resp.StatusCode))"
        } else {
            $dashMsg = "HTTP $($resp.StatusCode)"
        }
    } catch {
        $dashStatus = '❌'
        $dashMsg = "Not running / unreachable"
    }
    $results['dashboard'] = @{status = $dashStatus; message = $dashMsg}

    # ── Circuit breaker ──
    $cbStatus = '❌'
    $cbMsg = 'File not found'
    if (Test-Path $CircuitBreakerPath) {
        try {
            $cb = Get-Content $CircuitBreakerPath -Raw | ConvertFrom-Json
            $cbStatus = '✅'
            $cbMsg = "Valid JSON ($([Math]::Round((Get-Item $CircuitBreakerPath).Length / 1KB, 1)) KB)"
        } catch {
            $cbStatus = '❌'
            $cbMsg = "Invalid JSON: $_"
        }
    }
    $results['circuit_breaker'] = @{status = $cbStatus; message = $cbMsg}

    # ── Data directory integrity ──
    $ddStatus = '❌'
    $ddMsg = "Directory not found"
    if (Test-Path $DataDir) {
        $files = Get-ChildItem $DataDir -File
        $jsonFiles = $files | Where-Object { $_.Extension -eq '.json' }
        $allJsonOk = $true
        $badFiles = @()

        foreach ($f in $jsonFiles) {
            try {
                $null = Get-Content $f.FullName -Raw | ConvertFrom-Json
            } catch {
                $allJsonOk = $false
                $badFiles += $f.Name
            }
        }

        if ($allJsonOk) {
            $ddStatus = '✅'
            $ddMsg = "$($files.Count) files, $($jsonFiles.Count) JSON — all valid"
        } else {
            $ddStatus = '⚠️'
            $ddMsg = "$($files.Count) files, $($jsonFiles.Count) JSON — $($badFiles.Count) corrupted: $($badFiles -join ', ')"
        }
    }
    $results['data_dir'] = @{status = $ddStatus; message = $ddMsg}

    return $results
}

# ============================================================
#  Output
# ============================================================
function Write-StatusLine {
    param([string]$Status, [string]$Label, [string]$Detail)

    $color = switch ($Status[0]) {
        '✅' { 'Green' }
        '❌' { 'Red' }
        '⚠️' { 'Yellow' }
        default { 'Gray' }
    }
    Write-Host ("{0} {1}" -f $Status, $Label) -NoNewline -ForegroundColor $color
    if ($Detail) { Write-Host ("  {0}" -f $Detail) -ForegroundColor Gray }
    else { Write-Host '' }
}

# ============================================================
#  MAIN
# ============================================================
# Use Python bridge for config sections
$agentsData = Invoke-ConfigBridge "--section agents"
$workflowsData = Invoke-ConfigBridge "--section workflows"
$routingData = Invoke-ConfigBridge "--section routing"

# Convert PSCustomObject sections to hashtables for compatibility with Check/Test functions
function ConvertTo-FlatHashtable($obj) {
    if ($null -eq $obj) { return @{} }
    $ht = @{}
    if ($obj -is [System.Management.Automation.PSCustomObject]) {
        foreach ($prop in $obj.PSObject.Properties) {
            $val = $prop.Value
            if ($val -is [System.Management.Automation.PSCustomObject]) {
                $inner = @{}
                foreach ($ip in $val.PSObject.Properties) {
                    $inner[$ip.Name] = $ip.Value
                }
                $ht[$prop.Name] = $inner
            } else {
                $ht[$prop.Name] = $val
            }
        }
    }
    return $ht
}

$agents = ConvertTo-FlatHashtable $agentsData
$workflows = ConvertTo-FlatHashtable $workflowsData
$routing = ConvertTo-FlatHashtable $routingData

# ── Run checks ──
$agentResults = Check-AgentAvailability -Agents $agents
$routeResults = Test-Routing -Routing $routing -Agents $agents -Workflows $workflows
$globalResults = Invoke-GlobalChecks

# ── Summaries ──
$agentOk   = ($agentResults | Where-Object { $_.status -eq '✅' }).Count
$agentFail = ($agentResults | Where-Object { $_.status -eq '❌' }).Count
$agentWrap = ($agentResults | Where-Object { $_.status -eq '⚪' }).Count

$routeOk    = ($routeResults | Where-Object { $_.status -eq '✅' }).Count
$routeWarn  = ($routeResults | Where-Object { $_.status -eq '⚠️' }).Count
$routeFail  = ($routeResults | Where-Object { $_.status -eq '❌' }).Count

$summaryAgent  = "$($agentResults.Count) agents: $agentOk ✅, $agentFail ❌"
if ($agentWrap -gt 0) { $summaryAgent += ", $agentWrap ⚪" }

$summaryRoute  = "$($routeResults.Count) routes: $routeOk ✅, $routeFail ❌"
if ($routeWarn -gt 0) { $summaryRoute += ", $routeWarn ⚠️" }

# ── JSON output ──
if ($Json) {
    $jsonOutput = @{
        timestamp = (Get-Date -Format 'o')
        agents    = $agentResults | ForEach-Object {
            @{
                name         = $_.name
                cli          = $_.cli
                driver       = $_.driver
                model        = $_.model
                capabilities = $_.capabilities
                available    = $_.available
                status       = $_.status
            }
        }
        routes    = $routeResults | ForEach-Object {
            @{
                route   = $_.route
                slot    = $_.slot
                agent   = $_.agent
                status  = $_.status
                message = $_.message
            }
        }
        global    = @{
            dashboard       = $globalResults['dashboard']
            circuit_breaker = $globalResults['circuit_breaker']
            data_dir        = $globalResults['data_dir']
        }
        summary   = "$summaryAgent | $summaryRoute"
    }

    Write-Output (ConvertTo-Json $jsonOutput -Depth 4)
    exit 0
}

# ── Console output ──
Write-Host "`n══════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  MAOP Doctor — Diagnostic Report" -ForegroundColor Cyan
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host "══════════════════════════════════════════════`n" -ForegroundColor Cyan

# --- Agent Availability ---
Write-Host "◆ Agent Availability" -ForegroundColor White
Write-Host "────────────────────" -ForegroundColor DarkGray

foreach ($r in $agentResults) {
    Write-StatusLine -Status $r.status -Label ("{0,-16}" -f $r.name) -Detail ("cli={0,-20} driver={1,-12} model={2}" -f $r.cli, $r.driver, $r.model)
    if ($r.capabilities) {
        Write-Host ("{0,-18}{1}" -f '', "capabilities: $($r.capabilities)") -ForegroundColor DarkGray
    }
}

# --- Routing Validation ---
Write-Host "`n◆ Routing Validation" -ForegroundColor White
Write-Host "────────────────────" -ForegroundColor DarkGray

$lastRoute = ''
foreach ($r in $routeResults) {
    if ($r.route -ne $lastRoute) {
        if ($lastRoute) { Write-Host '' }
        Write-Host ("  {0,-14} " -f "$($r.route):") -NoNewline -ForegroundColor White
        $lastRoute = $r.route
    }
    Write-Host ("{0,-10}-> {1,-20} " -f $r.slot, $r.agent) -NoNewline
    Write-StatusLine -Status $r.status -Label '' -Detail $r.message
}
Write-Host ''

# --- Global Checks ---
Write-Host "◆ Global Checks" -ForegroundColor White
Write-Host "────────────────" -ForegroundColor DarkGray

foreach ($key in $globalResults.Keys) {
    $g = $globalResults[$key]
    $label = switch ($key) {
        'dashboard'       { 'Dashboard' }
        'circuit_breaker' { 'Circuit Breaker' }
        'data_dir'        { 'Data Directory' }
        default           { $key }
    }
    Write-StatusLine -Status $g.status -Label ("{0,-20}" -f $label) -Detail $g.message
}

# --- Summary ---
Write-Host "`n══════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  SUMMARY:  $summaryAgent | $summaryRoute" -ForegroundColor Cyan
Write-Host "══════════════════════════════════════════════" -ForegroundColor Cyan
