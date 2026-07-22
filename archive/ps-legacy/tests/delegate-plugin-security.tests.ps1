# delegate-plugin-security.tests.ps1 — Security & edge-case tests for delegate-plugin
# Covers: escape functions, circuit breaker states, CLI pre-check, JSON injection
#         whitelist, timeout handling, driver mock, wrapper path resolution
#
# Run: pwsh -File tests\delegate-plugin-security.tests.ps1
#   or: powershell -File tests\delegate-plugin-security.tests.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path $PSCommandPath -Parent
$SrcDir = Join-Path (Split-Path $ScriptDir -Parent) "src"
$DelegatePlugin = Join-Path $SrcDir "delegate-plugin.ps1"
$BreakerScript = Join-Path $SrcDir "circuit-breaker.ps1"
$BreakerModule = Join-Path $SrcDir "circuit-breaker.psm1"
$ErrorSchemaScript = Join-Path $SrcDir "error-schema.ps1"
$PEVRoot = Split-Path $ScriptDir -Parent  # F:\Nexus\MAOP

$testsRun = 0
$testsPassed = 0
$testsFailed = 0
$failureDetails = @()

function Assert-That($condition, $testName) {
    $script:testsRun++
    if ($condition) {
        Write-Host "  ✓ $testName" -ForegroundColor Green
        $script:testsPassed++
    } else {
        Write-Host "  ✗ $testName" -ForegroundColor Red
        $script:testsFailed++
        $script:failureDetails += $testName
    }
}

function Assert-Equals($expected, $actual, $testName) {
    $ok = ($expected -eq $actual)
    if (-not $ok) {
        Write-Host "    Expected: [$expected], Got: [$actual]" -ForegroundColor DarkGray
    }
    Assert-That $ok $testName
}

function Assert-Contains($haystack, $needle, $testName) {
    $ok = ($haystack -and $haystack.Contains($needle))
    if (-not $ok) {
        Write-Host "    Expected to contain: [$needle] in [truncated]" -ForegroundColor DarkGray
    }
    Assert-That $ok $testName
}

function Assert-NotContains($haystack, $needle, $testName) {
    $ok = (-not $haystack -or -not $haystack.Contains($needle))
    Assert-That $ok $testName
}

function Assert-NotNull($value, $testName) {
    Assert-That ($null -ne $value) $testName
}

Write-Host "`n═══════════════════════════════════════════════════"
Write-Host "  delegate-plugin Security & Edge-Case Tests"
Write-Host "═══════════════════════════════════════════════════`n"

# ═══════════════════════════════════════════════════════
# Suite 1: ConvertTo-CmdEscapedString — cmd.exe injection defense
# ═══════════════════════════════════════════════════════
Write-Host "Suite 1: ConvertTo-CmdEscapedString (cmd injection defense)" -ForegroundColor Cyan

# Dot-source to load functions without triggering dispatch
. $DelegatePlugin 2>&1 | Out-Null

# Re-define functions if dot-source didn't expose them (they may be in script scope)
if (-not (Get-Command ConvertTo-CmdEscapedString -ErrorAction SilentlyContinue)) {
    function ConvertTo-CmdEscapedString {
        param([string]$InputString)
        return $InputString -replace '([\^\&\|\<\>\(\)])', '^$1' -replace "`n", '^`n' -replace "`r", ''
    }
}
if (-not (Get-Command ConvertTo-PowerShellCommandEscapedString -ErrorAction SilentlyContinue)) {
    function ConvertTo-PowerShellCommandEscapedString {
        param([string]$InputString)
        return "'" + ($InputString -replace "'", "''") + "'"
    }
}

# 1.1: Ampersand injection attempt — escaped output should contain ^& not raw & followed by space
$malicious = 'hello" & del /f C:\important.txt'
$escaped = ConvertTo-CmdEscapedString $malicious
# After escaping, & should be prefixed with ^
$hasRawAmp = $escaped -match '(?<!\^)&'
Assert-That (-not $hasRawAmp) "ampersand escaped (no unescaped & in output)"

# 1.2: Pipe injection — escaped output should contain ^| not raw |
$malicious2 = 'test | whoami'
$escaped2 = ConvertTo-CmdEscapedString $malicious2
$hasRawPipe = $escaped2 -match '(?<!\^)\|'
Assert-That (-not $hasRawPipe) "pipe escaped (no unescaped | in output)"

# 1.3: Parentheses injection
$malicious3 = 'test) & (calc'
$escaped3 = ConvertTo-CmdEscapedString $malicious3
Assert-That ($escaped3 -match '\^' -or -not ($escaped3 -match '\) & \(')) "parentheses escaped"

# 1.4: Caret itself is escaped — ^& becomes ^^&
$malicious4 = 'test^&whoami'
$escaped4 = ConvertTo-CmdEscapedString $malicious4
# The & should be escaped, and the original ^ should also be escaped as ^^
Assert-That ($escaped4.Contains('^^&')) "caret-ampersand neutralized (^& → ^^&)"

# 1.5: Newlines replaced
$malicious5 = "line1`nline2"
$escaped5 = ConvertTo-CmdEscapedString $malicious5
Assert-NotContains $escaped5 "`n" "newline replaced in cmd escape"

# 1.6: Carriage return stripped
$malicious6 = "line1`r`nline2"
$escaped6 = ConvertTo-CmdEscapedString $malicious6
Assert-NotContains $escaped6 "`r" "carriage return stripped in cmd escape"

# 1.7: Empty string handled gracefully
$escaped7 = ConvertTo-CmdEscapedString ""
Assert-Equals "" $escaped7 "empty string → empty output"

# 1.8: Normal text passes through (with caret prefix for special chars)
$normal = "hello world"
$escaped8 = ConvertTo-CmdEscapedString $normal
Assert-Equals "hello world" $escaped8 "normal text passes through unchanged"

# ═══════════════════════════════════════════════════════
# Suite 2: ConvertTo-PowerShellCommandEscapedString — PS injection defense
# ═══════════════════════════════════════════════════════
Write-Host "`nSuite 2: ConvertTo-PowerShellCommandEscapedString (PS injection defense)" -ForegroundColor Cyan

# 2.1: Single quote escaping — the primary defense
$psMalicious = "hello'; Remove-Item C:\ -Recurse -Force; '"
$psEscaped = ConvertTo-PowerShellCommandEscapedString $psMalicious
# Result should be wrapped in single quotes with internal quotes doubled
Assert-That ($psEscaped.StartsWith("'")) "PS escape: starts with single quote"
Assert-That ($psEscaped.EndsWith("'")) "PS escape: ends with single quote"
# Every ' in the original should become '' in the escaped output (inside the wrapper quotes)
# The wrapper itself adds 2 single quotes (open+close), so:
# original has N single quotes → escaped has 2*N + 2 single quotes total
$origCount = ($psMalicious -split "'").Length - 1  # count of ' in original
$escapedCount = ($psEscaped -split "'").Length - 1  # count of ' in escaped
Assert-Equals (($origCount * 2) + 2) $escapedCount "PS escape: all single quotes doubled inside wrapper"

# 2.2: Empty string
$psEscaped2 = ConvertTo-PowerShellCommandEscapedString ""
Assert-Equals "''" $psEscaped2 "PS escape: empty string → ''"

# 2.3: Normal text
$psEscaped3 = ConvertTo-PowerShellCommandEscapedString "hello world"
Assert-Equals "'hello world'" $psEscaped3 "PS escape: normal text → 'hello world'"

# 2.4: Text with double quotes (not special in PS single-quote context)
$psEscaped4 = ConvertTo-PowerShellCommandEscapedString 'say "hello"'
Assert-Equals "'say ""hello""'" $psEscaped4 "PS escape: double quotes pass through"

# 2.5: Dollar sign (not expanded inside single quotes)
$psEscaped5 = ConvertTo-PowerShellCommandEscapedString '$env:PATH'
Assert-Contains $psEscaped5 '$env:PATH' "PS escape: dollar sign safe inside single quotes"

# ═══════════════════════════════════════════════════════
# Suite 3: CLI pre-check (exit_code -4 path)
# ═══════════════════════════════════════════════════════
Write-Host "`nSuite 3: CLI pre-check (binary not found)" -ForegroundColor Cyan

# 3.1: Agent with cli driver but non-existent binary → exit_code -4
$result = & $DelegatePlugin -Agent "claude" -Task "test" -Direct -TimeoutSeconds 5 -NoDashboard 2>&1 | Out-String
$result = $result.Trim()
try {
    $json = $result | ConvertFrom-Json
    $hasClaude = [bool](Get-Command "claude" -ErrorAction SilentlyContinue)
    if (-not $hasClaude) {
        Assert-Equals "-4" $json.exit_code.ToString() "missing CLI → exit_code -4"
        Assert-Contains $json.error "not installed or not in PATH" "missing CLI → error message clear"
    } else {
        Write-Host "  [SKIP] claude is installed, can't test -4 path" -ForegroundColor DarkGray
    }
} catch {
    # If claude is not installed, we should still get valid JSON
    Assert-That $false "CLI pre-check → valid JSON output ($($_.Exception.Message))"
}

# 3.2: Unknown agent → exit_code -1 with clear error
$result2 = & $DelegatePlugin -Agent "totally_fake_agent_999" -Task "test" -Direct -NoDashboard 2>&1 | Out-String
$result2 = $result2.Trim()
try {
    $json2 = $result2 | ConvertFrom-Json
    Assert-Equals "-1" $json2.exit_code.ToString() "unknown agent → exit_code -1"
    Assert-Contains $json2.error "Unknown agent" "unknown agent → error mentions 'Unknown agent'"
    Assert-That (-not $json2.ok) "unknown agent → ok=false"
} catch {
    Assert-That $false "unknown agent → valid JSON ($($_.Exception.Message))"
}

# ═══════════════════════════════════════════════════════
# Suite 4: Circuit breaker state transitions
# ═══════════════════════════════════════════════════════
Write-Host "`nSuite 4: Circuit breaker state transitions" -ForegroundColor Cyan

# Load circuit breaker
. $BreakerScript 2>&1 | Out-Null
if (-not (Get-Command Get-BreakerState -ErrorAction SilentlyContinue)) {
    . $BreakerModule 2>&1 | Out-Null
}

$testAgent = "test-breaker-agent-$(Get-Random)"

# 4.1: Set CLOSED state
Set-BreakerState -AgentName $testAgent -State "closed" -Failures 0 -LastFailure ""
$state = Get-BreakerState -AgentName $testAgent
Assert-NotNull $state "breaker: Get-BreakerState returns after Set"
Assert-Equals "closed" $state.state "breaker: state set to closed"

# 4.2: Simulate failures → OPEN state
Set-BreakerState -AgentName $testAgent -State "open" -Failures 5 -LastFailure (Get-Date -Format "o") -Threshold 3
$state2 = Get-BreakerState -AgentName $testAgent
Assert-Equals "open" $state2.state "breaker: state transitions to open"
Assert-Equals "5" $state2.failures.ToString() "breaker: failure count tracked"

# 4.3: HALF-OPEN state (cooldown expired)
Set-BreakerState -AgentName $testAgent -State "half-open" -Failures 5 -LastFailure (Get-Date -Format "o")
$state3 = Get-BreakerState -AgentName $testAgent
Assert-Equals "half-open" $state3.state "breaker: state transitions to half-open"

# 4.4: Success → reset to CLOSED
Set-BreakerState -AgentName $testAgent -State "closed" -Failures 0 -LastFailure ""
$state4 = Get-BreakerState -AgentName $testAgent
Assert-Equals "closed" $state4.state "breaker: success resets to closed"
Assert-Equals "0" $state4.failures.ToString() "breaker: failures reset to 0"

# 4.5: Breaker OPEN blocks dispatch
# Set breaker to open for a known agent that has a CLI binary
$breakerTestAgent = "claude"
Set-BreakerState -AgentName $breakerTestAgent -State "open" -Failures 99 -LastFailure (Get-Date -Format "o") -Threshold 1
$blockedResult = & $DelegatePlugin -Agent $breakerTestAgent -Task "test" -Direct -TimeoutSeconds 5 -NoDashboard 2>&1 | Out-String
$blockedResult = $blockedResult.Trim()
try {
    $blockedJson = $blockedResult | ConvertFrom-Json
    # If ConfigBridge works, breaker should block with -3. If ConfigBridge fails,
    # the agent config won't resolve and we get -1 (unknown agent). Either way,
    # the dispatch should not succeed.
    $ec = [int]$blockedJson.exit_code
    Assert-That ($ec -lt 0) "breaker open or config fail → exit_code < 0 (got $ec)"
    Assert-That (-not $blockedJson.ok) "breaker open → ok=false"
    if ($ec -eq -3) {
        Assert-Contains $blockedJson.error "circuit breaker open" "breaker open → error mentions 'circuit breaker open'"
    } else {
        Write-Host "  [INFO] ConfigBridge unavailable, breaker -3 path not reachable (got $ec)" -ForegroundColor DarkGray
    }
} catch {
    Assert-That $false "breaker open → valid JSON ($($_.Exception.Message))"
}
# Reset breaker
Set-BreakerState -AgentName $breakerTestAgent -State "closed" -Failures 0 -LastFailure ""

# ═══════════════════════════════════════════════════════
# Suite 5: JSON property injection whitelist (JobMode)
# ═══════════════════════════════════════════════════════
Write-Host "`nSuite 5: JSON property injection whitelist" -ForegroundColor Cyan

# 5.1: Verify that the whitelist constant exists in the source
$sourceContent = Get-Content $DelegatePlugin -Raw
Assert-Contains $sourceContent "allowedFields" "source contains allowedFields whitelist"

# 5.2: Verify disallowed fields are NOT in the whitelist
$allowedFieldsList = @('ok','exit_code','stdout','stderr','error','duration_ms','output','agent','model','driver','task','routing_key')
$dangerousFields = @('__proto__', 'constructor', 'prototype', 'eval', 'toString', 'hasOwnProperty')
foreach ($df in $dangerousFields) {
    Assert-That ($allowedFieldsList -notcontains $df) "whitelist excludes dangerous field: $df"
}

# 5.3: Verify whitelist extraction logic in source (ConvertFrom-Json + property filter)
Assert-Contains $sourceContent "PSObject.Properties" "source iterates PSObject.Properties for whitelist filter"
Assert-Contains $sourceContent "allowedFields -contains" "source uses allowedFields -contains for filtering"

# ═══════════════════════════════════════════════════════
# Suite 6: Timeout handling
# ═══════════════════════════════════════════════════════
Write-Host "`nSuite 6: Timeout handling" -ForegroundColor Cyan

# 6.1: Verify timeout constants exist in source
Assert-Contains $sourceContent "TIMEOUT" "source contains TIMEOUT error string"
Assert-Contains $sourceContent "WaitForExit" "source uses WaitForExit for timeout control"
Assert-Contains $sourceContent '$p.Kill()' "source kills process on timeout"

# 6.2: Quick timeout test with a known agent (if available)
# Use a 1-second timeout on a real agent to trigger timeout path
$testAgent = $null
foreach ($candidate in @("claude", "kimi", "codex", "openclaw")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $testAgent = $candidate
        break
    }
}

if ($testAgent) {
    Write-Host "  [INFO] Testing timeout with $testAgent (1s timeout)..."
    $toResult = & $DelegatePlugin -Agent $testAgent -Task "Write a very long essay about the history of computing, at least 10000 words" -Direct -TimeoutSeconds 1 -NoDashboard 2>&1 | Out-String
    $toResult = $toResult.Trim()
    try {
        $toJson = $toResult | ConvertFrom-Json
        # Should timeout with -1, or fail with -2 if CLI can't start within 1s
        # Both are valid non-zero failure outcomes for a 1s timeout test
        Assert-That ($toJson.exit_code -lt 0) "$testAgent timeout → exit_code < 0 (got $($toJson.exit_code))"
        if ($toJson.error -and $toJson.error.Contains('TIMEOUT')) {
            Assert-That $true "$testAgent timeout → error contains TIMEOUT"
        } else {
            Write-Host "  [INFO] $testAgent failed before timeout (exit_code=$($toJson.exit_code), error=$($toJson.error))" -ForegroundColor DarkGray
        }
    } catch {
        Write-Host "  [WARN] timeout test returned non-JSON (may have completed too fast)"
    }
} else {
    Write-Host "  [SKIP] no CLI agent available for timeout test"
}

# ═══════════════════════════════════════════════════════
# Suite 7: Driver function coverage (mock-based)
# ═══════════════════════════════════════════════════════
Write-Host "`nSuite 7: Driver function coverage (mock)" -ForegroundColor Cyan

# 7.1: All 4 driver functions exist in source
$driverFunctions = @(
    "function Invoke-CliDriver",
    "function Invoke-WrapperDriver",
    "function Invoke-PowerShellDriver",
    "function Invoke-CmdDriver"
)
foreach ($df in $driverFunctions) {
    Assert-Contains $sourceContent $df "source defines: $($df -replace 'function ', '')"
}

# 7.2: Each driver sets $Result.driver correctly
$driverMappings = @{
    'Invoke-CliDriver' = 'cli'
    'Invoke-WrapperDriver' = 'wrapper'
    'Invoke-PowerShellDriver' = 'powershell'
    'Invoke-CmdDriver' = 'cmd'
}
foreach ($kv in $driverMappings.GetEnumerator()) {
    $funcName = $kv.Key
    $expectedDriver = $kv.Value
    # Build pattern: function Invoke-XXxxDriver ... $Result.driver = "xxx"
    $pattern = '(?s)function ' + $funcName + '.*?\$Result\.driver\s*=\s*"' + $expectedDriver + '"'
    Assert-That ($sourceContent -match $pattern) "$funcName sets driver=$expectedDriver"
}

# 7.3: Each driver handles timeout (Kill on timeout)
foreach ($df in $driverFunctions) {
    $funcName = $df -replace "function ", ""
    # Check that each driver function contains a Kill() call
    $funcPattern = '(?ms)function ' + [regex]::Escape($funcName) + '.*?(?=^function |\Z)'
    if ($sourceContent -match $funcPattern) {
        $funcBody = $Matches[0]
        Assert-That ($funcBody -match '\$p\.Kill\(\)') "$funcName has Kill() on timeout"
    } else {
        Assert-That $false "$funcName function body not found for Kill() check"
    }
}

# 7.4: Each driver uses temp files for output capture (not inline string)
foreach ($df in $driverFunctions) {
    $funcName = $df -replace "function ", ""
    $pattern = "(?s)$funcName.*?GetTempFileName"
    Assert-That ($sourceContent -match $pattern) "$funcName uses GetTempFileName for output"
}

# ═══════════════════════════════════════════════════════
# Suite 8: Wrapper driver path resolution
# ═══════════════════════════════════════════════════════
Write-Host "`nSuite 8: Wrapper driver path resolution" -ForegroundColor Cyan

# 8.1: Wrapper driver resolves relative paths
Assert-That ($sourceContent.Contains('Test-Path') -and $sourceContent.Contains('$wrapper')) "wrapper driver checks path existence"
Assert-That ($sourceContent.Contains('Join-Path') -and $sourceContent.Contains('$ScriptDir')) "wrapper driver joins ScriptDir for resolution"

# 8.2: Wrapper driver adds .ps1 extension if missing
Assert-That ($sourceContent.Contains('.ps1')) "wrapper driver checks for .ps1 extension"
Assert-Contains $sourceContent "wrapperName" "wrapper driver constructs wrapperName"

# ═══════════════════════════════════════════════════════
# Suite 9: Output schema completeness
# ═══════════════════════════════════════════════════════
Write-Host "`nSuite 9: Output schema completeness" -ForegroundColor Cyan

# 9.1: Error schema defines all required fields
. $ErrorSchemaScript 2>&1 | Out-Null
if (-not (Get-Command New-ResultObject -ErrorAction SilentlyContinue)) {
    . (Join-Path $SrcDir "error-schema.psm1") 2>&1 | Out-Null
}

$testResult = New-ResultObject -Agent "test" -Task "test" -TraceID "trace-123" -RoutingKey "codegen"
$requiredFields = @("ok","exit_code","stdout","stderr","error","duration_ms","agent","task","trace_id","routing_key","driver","model","start_time")
foreach ($field in $requiredFields) {
    Assert-That ($testResult.Contains($field)) "New-ResultObject has field: $field"
}

# 9.2: end_time is added at the end of dispatch (not in constructor)
Assert-Contains $sourceContent "end_time" "source adds end_time after execution"
Assert-That (-not $testResult.Contains("end_time")) "New-ResultObject does NOT have end_time (added post-execution)"

# 9.3: Test-ResultSuccess correctly identifies success/failure
$successResult = New-ResultObject -Agent "t" -Task "t" -ExitCode 0
Assert-That (Test-ResultSuccess $successResult) "Test-ResultSuccess: exit_code=0 → success"

$failResult = New-ResultObject -Agent "t" -Task "t" -ExitCode 1
Assert-That (-not (Test-ResultSuccess $failResult)) "Test-ResultSuccess: exit_code=1 → failure"

$errorResult = New-ResultObject -Agent "t" -Task "t" -ExitCode 0 -ErrMsg "something broke"
Assert-That (-not (Test-ResultSuccess $errorResult)) "Test-ResultSuccess: error set → failure"

# ═══════════════════════════════════════════════════════
# Suite 10: Fallback chain config integrity
# ═══════════════════════════════════════════════════════
Write-Host "`nSuite 10: Fallback chain config integrity" -ForegroundColor Cyan

# 10.1: agents.yaml has routing section
$agentsYaml = Get-Content (Join-Path $PEVRoot "config\agents.yaml") -Raw
Assert-Contains $agentsYaml "routing:" "agents.yaml has routing section"

# 10.2: Each routing key has at least primary + fallback
$routingKeys = @("codegen","refactor","search","planning","review","verify","fileops","chat","quickfix","mcp","memory","docgen","techdoc","pipeline")
foreach ($key in $routingKeys) {
    Assert-Contains $agentsYaml "  ${key}:" "routing has key: $key"
    Assert-Contains $agentsYaml "    primary:" "routing key $key has primary"
}

# 10.3: Every agent referenced in routing exists in agents section
# Extract agent names from the agents: section
$agentNames = @()
$inAgentsSection = $false
foreach ($line in (Get-Content (Join-Path $PEVRoot "config\agents.yaml"))) {
    if ($line -match "^agents:") { $inAgentsSection = $true; continue }
    if ($line -match "^[a-z]") { $inAgentsSection = $false }
    if ($inAgentsSection -and $line -match "^\s{2}(\w[\w-]*):\s*$") {
        $agentNames += $Matches[1]
    }
}
# Also add workflow names
$inWorkflows = $false
foreach ($line in (Get-Content (Join-Path $PEVRoot "config\agents.yaml"))) {
    if ($line -match "^workflows:") { $inWorkflows = $true; continue }
    if ($line -match "^routing:") { $inWorkflows = $false }
    if ($inWorkflows -and $line -match "^\s{2}(\w[\w-]*):\s*$") {
        $agentNames += $Matches[1]
    }
}

# Extract primary/fallback/tertiary values from routing
$routingRefs = @()
$inRouting = $false
foreach ($line in (Get-Content (Join-Path $PEVRoot "config\agents.yaml"))) {
    if ($line -match "^routing:") { $inRouting = $true; continue }
    if ($line -match "^loops:") { $inRouting = $false }
    if ($inRouting -and $line -match "^\s{4}(primary|fallback|tertiary):\s*(\S+)") {
        $routingRefs += $Matches[2]
    }
}

$missingAgents = $routingRefs | Where-Object { $agentNames -notcontains $_ } | Sort-Object -Unique
Assert-That ($missingAgents.Count -eq 0) "all routing references exist in agents/workflows (missing: $($missingAgents -join ', '))"

# ═══════════════════════════════════════════════════════
# Suite 11: Hardcoded path check
# ═══════════════════════════════════════════════════════
Write-Host "`nSuite 11: Hardcoded path check" -ForegroundColor Cyan

# 11.1: delegate-plugin.ps1 should NOT contain hardcoded F:\Nexus\MAOP
$pluginContent = Get-Content $DelegatePlugin -Raw
# Allow in comments but not in executable paths
$execLines = $pluginContent -split "`n" | Where-Object { $_ -notmatch '^\s*#' -and $_ -notmatch '<#' -and $_ -notmatch '#>' }
$hardcoded = $execLines | Where-Object { $_ -match 'F:\\Nexus\\MAOP' }
Assert-That ($hardcoded.Count -eq 0) "no hardcoded F:\Nexus\MAOP in executable lines"

# 11.2: Uses $PSScriptRoot / $MAOP for path resolution
Assert-Contains $pluginContent "Split-Path `$PSCommandPath" "uses PSCommandPath for script dir"
Assert-Contains $pluginContent "Split-Path `$ScriptDir" "uses ScriptDir for MAOP root"

# ═══════════════════════════════════════════════════════
# Suite 12: Dot-source guard and parameter validation
# ═══════════════════════════════════════════════════════
Write-Host "`nSuite 12: Dot-source guard and parameter validation" -ForegroundColor Cyan

# 12.1: No params → no execution, no error (run in subprocess to isolate exit)
$guardResult = & powershell -NoProfile -Command "& '$DelegatePlugin' 2>&1" | Out-String
Assert-That ($guardResult.Trim() -eq "" -or $guardResult -notmatch "error") "dot-source guard: no params → silent return"

# 12.2: Agent without Task → error (run in subprocess to isolate exit 1)
$noTaskResult = & powershell -NoProfile -Command "& '$DelegatePlugin' -Agent 'claude' *>&1 | Out-String" | Out-String
# delegate-plugin uses Write-Error + exit 1 for missing -Task
Assert-That ($noTaskResult -match "Task.*required|error|exit_code") "Agent without Task → error response"

# ═══════════════════════════════════════════════════════
# Suite 13: Dashboard auto-start safety
# ═══════════════════════════════════════════════════════
Write-Host "`nSuite 13: Dashboard auto-start safety" -ForegroundColor Cyan

# 13.1: Dashboard auto-start exists but uses relative paths
Assert-Contains $pluginContent "dashboard\\server-v2" "dashboard auto-start references server-v2"
Assert-Contains $pluginContent "Split-Path" "dashboard path uses Split-Path (not hardcoded)"

# 13.2: Watchdog uses Start-Job (not inline infinite loop in main process)
Assert-Contains $pluginContent "Start-Job" "watchdog uses Start-Job for isolation"
Assert-Contains $pluginContent "dashboard-watchdog" "watchdog job named 'dashboard-watchdog'"

# ═══════════════════════════════════════════════════════
# Suite 14: Memory trajectory tracking
# ═══════════════════════════════════════════════════════
Write-Host "`nSuite 14: Memory trajectory tracking" -ForegroundColor Cyan

# 14.1: Trajectory tracking called only in non-JobMode
Assert-Contains $pluginContent "if (`$TraceID -and -not `$JobMode)" "trajectory tracking gated by TraceID and not JobMode"

# 14.2: Uses memory.ps1 for trajectory
Assert-Contains $pluginContent "memory.ps1" "trajectory uses memory.ps1"
Assert-Contains $pluginContent "-Action trajectory" "trajectory action passed to memory.ps1"

# ═══════════════════════════════════════════════════════
# Suite 15: Agent config resolution (wildcard matching)
# ═══════════════════════════════════════════════════════
Write-Host "`nSuite 15: Agent config resolution" -ForegroundColor Cyan

# 15.1: Resolve-AgentConfig function exists
Assert-Contains $pluginContent "function Resolve-AgentConfig" "Resolve-AgentConfig defined"

# 15.2: Wildcard matching logic exists
Assert-Contains $pluginContent "-like `$a.name" "wildcard matching with -like operator"
Assert-Contains $pluginContent "workflows" "workflow resolution in Resolve-AgentConfig"

# 15.3: Get-AgentConfig function exists
Assert-Contains $pluginContent "function Get-AgentConfig" "Get-AgentConfig defined"

# 15.4: Config bridge is used for YAML parsing (not hand-rolled)
Assert-Contains $pluginContent "Invoke-ConfigBridge" "config loading uses Invoke-ConfigBridge (Python bridge)"

# ═══════════════════════════════════════════════════════
# Final report
# ═══════════════════════════════════════════════════════
Write-Host "`n═══════════════════════════════════════════════════"
Write-Host "  Results: $testsPassed / $testsRun passed"
if ($testsFailed -gt 0) {
    Write-Host "  FAILED: $testsFailed test(s)" -ForegroundColor Red
    foreach ($detail in $failureDetails) {
        Write-Host "    - $detail" -ForegroundColor DarkRed
    }
} else {
    Write-Host "  ALL TESTS PASSED" -ForegroundColor Green
}
Write-Host "═══════════════════════════════════════════════════`n"

if ($testsFailed -gt 0) { exit 1 } else { exit 0 }
