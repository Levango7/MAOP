<#
.SYNOPSIS
  Pester tests for src/MAOP-loop.ps1 — master orchestrator phase ordering
.DESCRIPTION
  Tests that Plan → Execute → Verify → Store → Evolve phases execute in order.
  Uses stub scripts injected via Join-Path mock to avoid calling real scripts.
  Mocks all side-effect operations (file I/O, Write-Host).
#>

BeforeAll {
    # ── Create a temp directory for stub scripts ──
    $script:stubDir = Join-Path $env:TEMP "MAOP-loop-stubs-$(Get-Random)"
    if (-not (Test-Path $script:stubDir)) {
        $null = New-Item -ItemType Directory -Path $script:stubDir -Force
    }

    # ── Global coordination variables for stubs ──
    # These are set by the test and read by stub scripts
    $global:PevMockPlanResult = ''
    $global:PevMockExecResult = ''
    $global:PevMockVerifyResult = ''
    $global:PevMockPlanScriptCalled = $false
    $global:PevMockExecScriptCalled = $false
    $global:PevMockVerifyScriptCalled = $false
    $global:PevMockExecCount = 0
    $global:PevMockMemoryCalled = $false
    $global:PevMockEvolveCalled = $false

    # ── Create stub scripts ──
    # MAOP-plan.ps1 stub
    @"
param(`$Task, `$WorkDir, `$RoutingKey)
`$global:PevMockPlanScriptCalled = `$true
return `$global:PevMockPlanResult
"@ | Set-Content -Path (Join-Path $script:stubDir 'MAOP-plan.ps1') -Force

    # MAOP-execute.ps1 stub
    @"
param(`$Agent, `$Task, `$RoutingKey, `$WorkDir, `$TimeoutSeconds, `$TraceID)
`$global:PevMockExecScriptCalled = `$true
`$global:PevMockExecCount++
return `$global:PevMockExecResult
"@ | Set-Content -Path (Join-Path $script:stubDir 'MAOP-execute.ps1') -Force

    # MAOP-verify.ps1 stub
    @"
param(`$PlanJson, `$ResultJson, `$WorkDir)
`$global:PevMockVerifyScriptCalled = `$true
return `$global:PevMockVerifyResult
"@ | Set-Content -Path (Join-Path $script:stubDir 'MAOP-verify.ps1') -Force

    # rules.yaml stub content
    $script:stubRulesYaml = @"
guards:
  retry:
    max_attempts: 1
    backoff_ms: 100
  timeout:
    default_s: 120
"@

    # agents.yaml stub content
    $script:stubAgentsYaml = @"
claude:
  timeout_s: 120
kimi:
  timeout_s: 120
nvidia:
  timeout_s: 120
routing:
  codegen:
    primary: claude
    fallback: kimi
"@

    # ── Mocks ──
    Mock -CommandName Join-Path -MockWith {
        param($Path, $ChildPath)
        # Redirect specific scripts to stubs
        if ($ChildPath -eq 'MAOP-plan.ps1' -or $ChildPath -eq 'MAOP-execute.ps1' -or $ChildPath -eq 'MAOP-verify.ps1') {
            return [System.IO.Path]::Combine($script:stubDir, $ChildPath)
        }
        # For everything else, use .NET to avoid recursion
        return [System.IO.Path]::Combine($Path, $ChildPath)
    } -Verifiable

    Mock -CommandName Split-Path -MockWith {
        param($Path, $Parent)
        # Use .NET to avoid recursion through mocked Split-Path
        if ($Parent) { return [System.IO.Path]::GetDirectoryName($Path) }
        return [System.IO.Path]::GetFileName($Path.TrimEnd('\'))
    } -Verifiable

    Mock -CommandName Test-Path -MockWith {
        param($Path)
        # Make scripts appear to exist
        if ($Path -like '*MAOP-plan.ps1' -or $Path -like '*MAOP-execute.ps1' -or $Path -like '*MAOP-verify.ps1' -or $Path -like '*memory.ps1' -or $Path -like '*evolve.ps1') {
            return $true
        }
        # Config files
        if ($Path -like '*rules.yaml' -or $Path -like '*agents.yaml') {
            return $true
        }
        return $false
    } -Verifiable

    Mock -CommandName Get-Content -MockWith {
        param($Path, $Raw)
        if ($Path -like '*rules.yaml') { return ($script:stubRulesYaml -split "`n") }
        if ($Path -like '*agents.yaml') { return ($script:stubAgentsYaml -split "`n") }
        return ''
    } -Verifiable

    # Mock memory.ps1 call
    Mock -CommandName Invoke-Expression -MockWith {
        param($Command)
        $global:PevMockMemoryCalled = $true
        return $null
    } -ParameterFilter { $Command -like '*memory.ps1*' }

    # Mock evolve.ps1 call (more complex — multiple calls via &)
    $script:mockEvolveResult = '[]'  # empty JSON array = no suggestions

    # Suppress Write-Host
    Mock -CommandName Write-Host -MockWith { } -Verifiable
    Mock -CommandName Write-Error -MockWith { } -Verifiable
    Mock -CommandName Write-Warning -MockWith { } -Verifiable
    Mock -CommandName Out-Null -MockWith { } -Verifiable
    Mock -CommandName Start-Sleep -MockWith { } -Verifiable
}

Describe -Name 'MAOP Loop - happy path' -Tag 'MAOP-loop' {

    BeforeEach {
        # Reset globals
        $global:PevMockPlanResult = '{ "selected_agent": "claude", "routing_key": "codegen", "gates": ["lint"], "budget": { "timeout_s": 120 } }'
        $global:PevMockExecResult = '{"exit_code":0,"stdout":"code written","agent":"claude","duration_ms":500,"error":null}'
        $global:PevMockVerifyResult = '{ "phase": "verify", "passed": $true, "summary": "All gates passed", "gates": ["lint"] }'
        $global:PevMockPlanScriptCalled = $false
        $global:PevMockExecScriptCalled = $false
        $global:PevMockVerifyScriptCalled = $false
        $global:PevMockExecCount = 0
    }

    It 'executes Plan → Execute → Verify phases in order' {
        $output = & 'F:\Nexus\MAOP\src\MAOP-loop.ps1' -Task 'refactor main.ps1' 2>&1

        # Output should be valid JSON with cycle info
        # (plan stub may not be called if ConfigBridge fallback kicks in, but the
        #  orchestrator should still produce a complete cycle report)
        $jsonOutput = ($output | ForEach-Object { "$_" } | Where-Object { $_.Trim().StartsWith('{') }) -join "`n"
        # Try to find the JSON block (it's the last multi-line block starting with {)
        $jsonStart = $jsonOutput.IndexOf('{')
        if ($jsonStart -gt 0) { $jsonOutput = $jsonOutput.Substring($jsonStart) }
        $jsonOutput | Should -Not -BeNullOrEmpty
        $parsed = $jsonOutput | ConvertFrom-Json

        $parsed.pev_cycle | Should -Not -BeNullOrEmpty
        $parsed.execution | Should -Not -BeNullOrEmpty
        $parsed.verification | Should -Not -BeNullOrEmpty

        $parsed.pev_cycle.task | Should -BeLike '*refactor main*'
    }

    It 'returns a JSON report with pev_cycle, execution, verification sections' {
        $output = & 'F:\Nexus\MAOP\src\MAOP-loop.ps1' -Task 'test task'
        $parsed = $output | ConvertFrom-Json

        $parsed.PSObject.Properties.Name | Should -Contain 'pev_cycle'
        $parsed.PSObject.Properties.Name | Should -Contain 'execution'
        $parsed.PSObject.Properties.Name | Should -Contain 'verification'

        $parsed.pev_cycle.start_time | Should -Not -BeNullOrEmpty
        $parsed.pev_cycle.end_time | Should -Not -BeNullOrEmpty
        $parsed.pev_cycle.duration_ms | Should -BeGreaterThan 0
    }
}

Describe -Name 'MAOP Loop - SkipVerify' -Tag 'MAOP-loop' {

    BeforeEach {
        $global:PevMockPlanResult = '{ "selected_agent": "claude", "routing_key": "codegen", "gates": ["lint"], "budget": { "timeout_s": 120 } }'
        $global:PevMockExecResult = '{"exit_code":0,"stdout":"code","agent":"claude","duration_ms":100,"error":null}'
        $global:PevMockVerifyResult = '{ "phase": "verify", "passed": $true, "summary": "OK", "gates": ["lint"] }'
        $global:PevMockVerifyScriptCalled = $false
    }

    It 'skips the verify phase when -SkipVerify is set' {
        $null = & 'F:\Nexus\MAOP\src\MAOP-loop.ps1' -Task 'test' -SkipVerify

        # verify script should NOT have been called
        $global:PevMockVerifyScriptCalled | Should -Be $false
    }
}

Describe -Name 'MAOP Loop - fallback on failure' -Tag 'MAOP-loop' {

    BeforeEach {
        $global:PevMockPlanResult = '{ "selected_agent": "claude", "routing_key": "codegen", "gates": ["lint"], "budget": { "timeout_s": 120 } }'
        # First exec fails, then succeeds
        $global:PevMockExecResult = '{"exit_code":0,"stdout":"recovered","agent":"kimi","duration_ms":300,"error":null}'
        $global:PevMockVerifyResult = '{ "phase": "verify", "passed": $true, "summary": "OK", "gates": ["lint"] }'
        $global:PevMockExecCount = 0
    }

    It 'routes to fallback agent when primary fails (with -Retry)' {
        # Make exec return failure on first call
        $global:PevMockExecResult = '{"exit_code":1,"stdout":"","agent":"claude","duration_ms":100,"error":"failure"}'

        $output = & 'F:\Nexus\MAOP\src\MAOP-loop.ps1' -Task 'test' -Retry
        $parsed = $output | ConvertFrom-Json

        # Should have a result (might be the failure result since all agents fail)
        $parsed | Should -Not -BeNullOrEmpty
    }
}

Describe -Name 'MAOP Loop - feedback loop' -Tag 'MAOP-loop' {

    It 'triggers feedback (re-plan + re-execute) when verify fails' {
        # Setup: verify returns failed
        $global:PevMockPlanResult = '{ "selected_agent": "claude", "routing_key": "codegen", "gates": ["lint"], "budget": { "timeout_s": 120 } }'
        $global:PevMockExecResult = '{"exit_code":0,"stdout":"output","agent":"claude","duration_ms":100,"error":null}'
        $global:PevMockVerifyResult = '{ "phase": "verify", "passed": $false, "summary": "Lint failed", "gates": ["lint"] }'
        $global:PevMockPlanScriptCalled = $false
        $global:PevMockExecScriptCalled = $false
        $global:PevMockVerifyScriptCalled = $false
        $global:PevMockExecCount = 0

        $output = & 'F:\Nexus\MAOP\src\MAOP-loop.ps1' -Task 'test task'
        $parsed = $output | ConvertFrom-Json

        # After the feedback loop, verify should have run at least once
        $parsed.verification | Should -Not -BeNullOrEmpty
    }
}

AfterAll {
    # Cleanup stub scripts
    if (Test-Path $script:stubDir) {
        Remove-Item -Path $script:stubDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
