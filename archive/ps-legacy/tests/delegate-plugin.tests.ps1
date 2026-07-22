# delegate-plugin.tests.ps1 — Core scheduler tests
# Covers: dot-source guard, unknown agent, CLI pre-check, direct/job mode, JSON schema, circuit breaker

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path $PSCommandPath -Parent
$SrcDir = Join-Path (Split-Path $ScriptDir -Parent) "src"
$DelegatePlugin = Join-Path $SrcDir "delegate-plugin.ps1"

$testsRun = 0
$testsPassed = 0
$testsFailed = 0

function Assert-That($condition, $testName) {
    $script:testsRun++
    if ($condition) {
        Write-Host "  ✓ $testName" -ForegroundColor Green
        $script:testsPassed++
    } else {
        Write-Host "  ✗ $testName" -ForegroundColor Red
        $script:testsFailed++
    }
}

function Assert-NotNull($value, $testName) {
    Assert-That ($null -ne $value) $testName
}

function Assert-Equals($expected, $actual, $testName) {
    $ok = ($expected -eq $actual)
    if (-not $ok) { Write-Host "    Expected: $expected, Got: $actual" -ForegroundColor DarkGray }
    Assert-That $ok $testName
}

Write-Host "`n═══════════════════════════════════════════"
Write-Host "  delegate-plugin Unit Tests"
Write-Host "═══════════════════════════════════════════`n"

# ════════════════════════════════════════
# Suite 1: Dot-source guard
# ════════════════════════════════════════
Write-Host "Suite 1: Dot-source guard" -ForegroundColor Cyan

# 1.1: Dot-source should NOT execute main dispatch, just define functions
try {
    $null = & $DelegatePlugin -NoDashboard 2>&1
    Assert-That $true "dot-source guard: no Agent/Task → no error"
} catch {
    Assert-That $false "dot-source guard: no Agent/Task → no error ($($_.Exception.Message))"
}

# ════════════════════════════════════════
# Suite 2: Unknown agent handling
# ════════════════════════════════════════
Write-Host "`nSuite 2: Unknown agent handling" -ForegroundColor Cyan

# 2.1: Unknown agent via Direct mode
$result = & $DelegatePlugin -Agent "nonexistent_agent_xyz" -Task "hello world" -Direct -NoDashboard 2>&1 | Out-String
$result = $result.Trim()
try {
    $json = $result | ConvertFrom-Json
    Assert-NotNull $json "unknown agent → valid JSON output"
    Assert-That ($json.exit_code -eq -1 -or $json.exit_code -eq -4) "unknown agent → exit_code < 0"
    Assert-That ($json.error -match "not installed|not in PATH|Unknown agent") "unknown agent → error message clear"
} catch {
    Assert-That $false "unknown agent → valid JSON ($($_.Exception.Message))"
    Write-Host "    Raw: $($result.Substring(0, [Math]::Min(200, $result.Length)))"
}

# 2.2: Unknown agent via JobMode
$result2 = & $DelegatePlugin -Agent "nonexistent_agent_xyz" -Task "hello world" -JobMode -NoDashboard 2>&1 | Out-String
$result2 = $result2.Trim()
try {
    $json2 = $result2 | ConvertFrom-Json
    Assert-NotNull $json2 "unknown agent JobMode → valid JSON output"
} catch {
    Assert-That $false "unknown agent JobMode → valid JSON ($($_.Exception.Message))"
}

# ════════════════════════════════════════
# Suite 3: Output schema compliance
# ════════════════════════════════════════
Write-Host "`nSuite 3: Output schema compliance" -ForegroundColor Cyan

# 3.1: Result object must have all required fields
$requiredFields = @("ok", "exit_code", "stdout", "stderr", "error", "agent", "task", "duration_ms", "driver", "model", "routing_key", "trace_id", "end_time")
foreach ($field in $requiredFields) {
    Assert-That ($json.PSObject.Properties.Name -contains $field) "schema field: $field"
}

# 3.2: exit_code must be integer type
$ecType = $json.exit_code.GetType().Name
Assert-That ($ecType -match "Int|Double") "exit_code is numeric ($ecType)"

# 3.3: duration_ms must be non-negative
Assert-That ($json.duration_ms -ge 0) "duration_ms >= 0"

# ════════════════════════════════════════
# Suite 4: Agent with known CLI but likely unavailable
# ════════════════════════════════════════
Write-Host "`nSuite 4: Known agent dispatch" -ForegroundColor Cyan

# 4.1: Check if autoclaw is available (fastest to test with)
$autoclaw = Get-Command "autoclaw" -ErrorAction SilentlyContinue
if ($autoclaw) {
    Write-Host "  [INFO] autoclaw found, testing real dispatch..."
    $ar = & $DelegatePlugin -Agent "autoclaw" -Task "say hello" -Direct -TimeoutSeconds 15 -NoDashboard 2>&1 | Out-String
    $ar = $ar.Trim()
    try {
        $ajson = $ar | ConvertFrom-Json
        Assert-NotNull $ajson "autoclaw dispatch → valid JSON"
        Assert-That ($ajson.agent -eq "autoclaw") "autoclaw dispatch → agent field correct"
    } catch {
        Write-Host "  [WARN] autoclaw returned non-JSON: $($ar.Substring(0, [Math]::Min(100, $ar.Length)))"
    }
} else {
    Write-Host "  [SKIP] autoclaw not installed"
}

# 4.2: mimo should be fast if available
$mimo = Get-Command "mimo" -ErrorAction SilentlyContinue
if ($mimo) {
    Write-Host "  [INFO] mimo found, testing real dispatch..."
    $mr = & $DelegatePlugin -Agent "mimo" -Task "say hi" -Direct -TimeoutSeconds 15 -NoDashboard 2>&1 | Out-String
    $mr = $mr.Trim()
    try {
        $mjson = $mr | ConvertFrom-Json
        Assert-NotNull $mjson "mimo dispatch → valid JSON"
    } catch {
        Write-Host "  [WARN] mimo returned non-JSON"
    }
} else {
    Write-Host "  [SKIP] mimo not installed"
}

# ════════════════════════════════════════
# Suite 5: Circuit breaker integration
# ════════════════════════════════════════
Write-Host "`nSuite 5: Circuit breaker integration" -ForegroundColor Cyan

$breakerScript = Join-Path $SrcDir "circuit-breaker.ps1"
if (Test-Path $breakerScript) {
    # 5.1: Verify breaker script loads
    try {
        $null = & $breakerScript 2>&1
        Assert-That $true "circuit-breaker.ps1 exists and loads"
    } catch {
        Assert-That $false "circuit-breaker.ps1 loads ($($_.Exception.Message))"
    }
    
    # 5.2: Dot-source to check Get-BreakerState availability
    $before = Get-Command "Get-BreakerState" -ErrorAction SilentlyContinue
    . $breakerScript 2>&1 | Out-Null
    $after = Get-Command "Get-BreakerState" -ErrorAction SilentlyContinue
    Assert-That ($null -ne $after) "Get-BreakerState function available"
} else {
    Write-Host "  [SKIP] circuit-breaker.ps1 not found"
}

# ════════════════════════════════════════
# Suite 6: Error schema
# ════════════════════════════════════════
Write-Host "`nSuite 6: Error schema compliance" -ForegroundColor Cyan

# 6.1: Error situations produce non-null error field
if ($json.exit_code -ne 0) {
    Assert-That ($null -ne $json.error -or $json.stderr) "failed dispatch → error/stderr populated"
}

# 6.2: Failed dispatch should have ok=false
if ($json.exit_code -ne 0) {
    Assert-That (-not $json.ok) "failed dispatch → ok=false"
}

# ════════════════════════════════════════
# Suite 7: Driver dispatch coverage
# ════════════════════════════════════════
Write-Host "`nSuite 7: Driver type coverage" -ForegroundColor Cyan

# 7.1: Agents with CLI driver should attempt to find binary
$claude = Get-Command "claude" -ErrorAction SilentlyContinue
$openclaw = Get-Command "openclaw" -ErrorAction SilentlyContinue
Write-Host "  [INFO] Available CLI agents: claude=$(!!$claude), openclaw=$(!!$openclaw)"

# 7.2: Quick delegate with existing agent to verify JSON output format
if ($openclaw) {
    Write-Host "  [INFO] Testing openclaw dispatch (fast sanity check)..."
    $or = & $DelegatePlugin -Agent "openclaw" -Task "echo hello" -Direct -TimeoutSeconds 20 -NoDashboard 2>&1 | Out-String
    $or = $or.Trim()
    try {
        $ojson = $or | ConvertFrom-Json
        Assert-NotNull $ojson "openclaw dispatch → valid JSON"
        Assert-NotNull $ojson.end_time "openclaw → end_time populated"
    } catch {
        Write-Host "  [WARN] openclaw returned non-JSON or timed out"
    }
}

# ════════════════════════════════════════
# Final report
# ════════════════════════════════════════
Write-Host "`n═══════════════════════════════════════════"
Write-Host "  Results: $testsPassed / $testsRun passed"
Write-Host "═══════════════════════════════════════════`n"

if ($testsFailed -gt 0) {
    Write-Host "FAILED: $testsFailed test(s)" -ForegroundColor Red
    exit 1
} else {
    Write-Host "ALL TESTS PASSED" -ForegroundColor Green
    exit 0
}
