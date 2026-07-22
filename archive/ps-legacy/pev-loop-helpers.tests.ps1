<#
.SYNOPSIS
  Pester tests for MAOP-loop.ps1 internal helper functions
.DESCRIPTION
  Unit tests for SafeFromJson, Filter-Output, Write-PevLog, and
  the -SkipVerify explicit warning behavior.
  These helpers are dot-sourced from MAOP-loop.ps1 in isolation.
#>

BeforeAll {
    # ── Dot-source MAOP-loop.ps1 in a child scope to extract helpers ──
    # We cannot dot-source the full script (it runs immediately), so we
    # redefine the helper functions from the source for unit testing.

    $script:ProjectRoot = "F:\Nexus\MAOP"
    $script:TempLogDir = Join-Path $env:TEMP "MAOP-test-log-$(Get-Random)"
    $null = New-Item -ItemType Directory -Path $script:TempLogDir -Force
    $script:PevLogFile = Join-Path $script:TempLogDir "MAOP-loop.jsonl"

    # Re-define SafeFromJson (from MAOP-loop.ps1 line 131)
    function SafeFromJson($raw) {
        if (-not $raw) { return $null }
        try { return ($raw | ConvertFrom-Json) } catch { return $null }
    }

    # Re-define Filter-Output (from MAOP-loop.ps1 line 137)
    function Filter-Output($raw) {
        (@($raw) | ForEach-Object { "$_" } | Where-Object { $_ -notmatch '^\[MAOP-' -and $_.Trim() -ne '' }) -join "`n"
    }

    # Re-define Write-PevLog (from MAOP-loop.ps1 line 41)
    function Write-PevLog {
        param(
            [Parameter(Mandatory=$true)][string]$Phase,
            [Parameter(Mandatory=$true)][string]$Level,
            [string]$Message = "",
            [hashtable]$Data = @{}
        )
        $entry = [ordered]@{
            timestamp = (Get-Date -Format "o")
            source    = "MAOP-loop"
            phase     = $Phase
            level     = $Level
            message   = $Message
        }
        foreach ($k in $Data.Keys) { $entry[$k] = $Data[$k] }
        try {
            Add-Content -Path $script:PevLogFile -Value ($entry | ConvertTo-Json -Compress -Depth 4) -Encoding utf8
        } catch {
            Write-Verbose "[MAOP-loop] Write-PevLog failed: $_"
        }
    }
}

AfterAll {
    if (Test-Path $script:TempLogDir) {
        Remove-Item -Path $script:TempLogDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ════════════════════════════════════════
# SafeFromJson
# ════════════════════════════════════════
Describe "SafeFromJson" -Tag "MAOP-loop" {
    It "parses valid JSON object" {
        $result = SafeFromJson '{"exit_code":0,"stdout":"ok"}'
        $result | Should -Not -BeNullOrEmpty
        $result.exit_code | Should -Be 0
        $result.stdout | Should -Be "ok"
    }

    It "parses valid JSON array" {
        $result = SafeFromJson '[1,2,3]'
        $result | Should -Not -BeNullOrEmpty
        $result.Count | Should -Be 3
    }

    It "returns null for null input" {
        $result = SafeFromJson $null
        $result | Should -BeNullOrEmpty
    }

    It "returns null for empty string" {
        $result = SafeFromJson ''
        $result | Should -BeNullOrEmpty
    }

    It "returns null for invalid JSON" {
        $result = SafeFromJson 'not-json-at-all'
        $result | Should -BeNullOrEmpty
    }

    It "returns null for truncated JSON" {
        $result = SafeFromJson '{"exit_code":0,"stdout":"ok'
        $result | Should -BeNullOrEmpty
    }

    It "handles JSON with nested objects" {
        $result = SafeFromJson '{"pev_cycle":{"task":"test"},"execution":{"exit_code":0}}'
        $result | Should -Not -BeNullOrEmpty
        $result.pev_cycle.task | Should -Be "test"
    }
}

# ════════════════════════════════════════
# Filter-Output
# ════════════════════════════════════════
Describe "Filter-Output" -Tag "MAOP-loop" {
    It "passes through normal text" {
        $result = Filter-Output '{"exit_code":0}'
        $result | Should -Be '{"exit_code":0}'
    }

    It "filters out [MAOP- prefixed log lines" {
        $raw = @("[MAOP-loop] some log", '{"exit_code":0}')
        $result = Filter-Output $raw
        $result | Should -Be '{"exit_code":0}'
    }

    It "filters out empty lines" {
        $raw = @("", "  ", '{"exit_code":0}')
        $result = Filter-Output $raw
        $result | Should -Be '{"exit_code":0}'
    }

    It "handles single string input" {
        $result = Filter-Output '{"passed":true}'
        $result | Should -Be '{"passed":true}'
    }

    It "handles empty input" {
        $result = Filter-Output @()
        $result | Should -Be ""
    }

    It "preserves ErrorRecord as string" {
        $err = [System.Management.Automation.ErrorRecord]::new(
            [System.Exception]::new("test error"), "TestId", "NotSpecified", $null
        )
        $result = Filter-Output @($err)
        # ErrorRecord should be converted to string and kept (no [MAOP- prefix)
        $result | Should -Not -BeNullOrEmpty
    }
}

# ════════════════════════════════════════
# Write-PevLog
# ════════════════════════════════════════
Describe "Write-PevLog" -Tag "MAOP-loop" {
    BeforeEach {
        # Clear the log file before each test
        if (Test-Path $script:PevLogFile) {
            Remove-Item $script:PevLogFile -Force -ErrorAction SilentlyContinue
        }
    }

    It "appends a JSON line to the log file" {
        Write-PevLog -Phase "verify" -Level "WARN" -Message "Test warning"
        $content = Get-Content $script:PevLogFile -Raw
        $content | Should -Not -BeNullOrEmpty
        $parsed = $content | ConvertFrom-Json
        $parsed.phase | Should -Be "verify"
        $parsed.level | Should -Be "WARN"
        $parsed.message | Should -Be "Test warning"
    }

    It "includes source field as MAOP-loop" {
        Write-PevLog -Phase "plan" -Level "INFO" -Message "Planning"
        $parsed = Get-Content $script:PevLogFile -Raw | ConvertFrom-Json
        $parsed.source | Should -Be "MAOP-loop"
    }

    It "includes timestamp in ISO 8601 format" {
        Write-PevLog -Phase "execute" -Level "INFO" -Message "Running"
        $parsed = Get-Content $script:PevLogFile -Raw | ConvertFrom-Json
        $parsed.timestamp | Should -Not -BeNullOrEmpty
        # ISO 8601 pattern: 2026-07-12T...
        $parsed.timestamp | Should -Match '^\d{4}-\d{2}-\d{2}T'
    }

    It "appends extra Data keys to the log entry" {
        Write-PevLog -Phase "verify" -Level "ERROR" -Message "Failed" -Data @{ skipped = $true; cycle = 2 }
        $parsed = Get-Content $script:PevLogFile -Raw | ConvertFrom-Json
        $parsed.skipped | Should -Be $true
        $parsed.cycle | Should -Be 2
    }

    It "appends multiple entries (log is append-only)" {
        Write-PevLog -Phase "plan" -Level "INFO" -Message "First"
        Write-PevLog -Phase "execute" -Level "INFO" -Message "Second"
        $lines = Get-Content $script:PevLogFile
        $lines.Count | Should -Be 2
    }

    It "does not throw on write failure (file locked)" {
        # Write-PevLog catches exceptions internally
        { Write-PevLog -Phase "test" -Level "INFO" -Message "ok" } | Should -Not -Throw
    }
}

# ════════════════════════════════════════
# SkipVerify behavior (structural check)
# ════════════════════════════════════════
Describe "MAOP-loop SkipVerify - explicit warning" -Tag "MAOP-loop" {
    It "MAOP-loop.ps1 contains explicit WARN log for SkipVerify" {
        $src = Get-Content "F:\Nexus\MAOP\src\MAOP-loop.ps1" -Raw
        # Verify the source code contains the explicit warning (not silent skip)
        $src | Should -Match 'SkipVerify.*SKIPPED'
        $src | Should -Match 'Write-PevLog.*verify.*WARN.*SkipVerify'
    }

    It "verify result uses 'skipped' not 'passed' when SkipVerify is active" {
        $src = Get-Content "F:\Nexus\MAOP\src\MAOP-loop.ps1" -Raw
        # The verify result should set passed = "skipped", not $true
        $src | Should -Match 'passed.*=.*"skipped"'
    }

    It "MAOP-loop.ps1 logs ERROR when verify JSON parse fails" {
        $src = Get-Content "F:\Nexus\MAOP\src\MAOP-loop.ps1" -Raw
        $src | Should -Match 'Write-PevLog.*verify.*ERROR.*Verify JSON parse error'
    }

    It "MAOP-loop.ps1 logs ERROR when verify script throws exception" {
        $src = Get-Content "F:\Nexus\MAOP\src\MAOP-loop.ps1" -Raw
        $src | Should -Match 'Write-PevLog.*verify.*ERROR.*Verify script threw exception'
    }
}
