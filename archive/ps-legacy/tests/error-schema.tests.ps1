<#
.SYNOPSIS
  Pester tests for src/error-schema.ps1 — New-ResultObject, Test-ResultSuccess, Format-ResultError
.DESCRIPTION
  Tests the standard result object creation, success checking, and error formatting.
  No mocking needed — all functions are pure.
#>

BeforeAll {
    . 'F:\Nexus\MAOP\src\error-schema.ps1'
}

Describe -Name 'New-ResultObject' -Tag 'error-schema' {

    It 'creates a hashtable with all required keys' {
        $result = New-ResultObject -Agent 'test-agent' -Task 'test-task'

        $result | Should -BeOfType [System.Collections.Hashtable]
        $result.ContainsKey('ok')          | Should -Be $true
        $result.ContainsKey('exit_code')   | Should -Be $true
        $result.ContainsKey('stdout')      | Should -Be $true
        $result.ContainsKey('stderr')      | Should -Be $true
        $result.ContainsKey('error')       | Should -Be $true
        $result.ContainsKey('duration_ms') | Should -Be $true
        $result.ContainsKey('agent')       | Should -Be $true
        $result.ContainsKey('task')        | Should -Be $true
        $result.ContainsKey('trace_id')    | Should -Be $true
        $result.ContainsKey('routing_key') | Should -Be $true
        $result.ContainsKey('driver')      | Should -Be $true
        $result.ContainsKey('start_time')  | Should -Be $true
        $result.ContainsKey('model')       | Should -Be $true
    }

    It 'sets ok=$true when ExitCode=0 and no ErrMsg' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode 0
        $result.ok | Should -Be $true
    }

    It 'sets ok=$false when ExitCode=-1 (timeout)' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode -1
        $result.ok | Should -Be $false
    }

    It 'sets ok=$false when ExitCode=-2 (guardrail)' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode -2
        $result.ok | Should -Be $false
    }

    It 'sets ok=$false when ExitCode=-3 (breaker)' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode -3
        $result.ok | Should -Be $false
    }

    It 'sets ok=$false when ExitCode=1 (runtime error)' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode 1 -ErrMsg 'failure'
        $result.ok | Should -Be $false
    }

    It 'sets ok=$false when ErrMsg is provided even with ExitCode=0' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode 0 -ErrMsg 'Unexpected issue'
        $result.ok | Should -Be $false
    }

    It 'sets exit_code to the provided value' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode 42
        $result.exit_code | Should -Be 42
    }

    It 'defaults exit_code to 0' {
        $result = New-ResultObject -Agent 'a' -Task 't'
        $result.exit_code | Should -Be 0
    }

    It 'includes start_time as an ISO 8601 string' {
        $result = New-ResultObject -Agent 'a' -Task 't'
        $result.start_time | Should -Not -BeNullOrEmpty
        # Should look like ISO 8601: 2026-01-15T10:00:00...
        $result.start_time | Should -Match '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'
    }

    It 'sets agent and task correctly' {
        $result = New-ResultObject -Agent 'claude' -Task 'refactor main.ps1'
        $result.agent | Should -Be 'claude'
        $result.task  | Should -Be 'refactor main.ps1'
    }

    It 'sets optional fields: TraceID, RoutingKey, Driver, Model' {
        $result = New-ResultObject -Agent 'a' -Task 't' `
            -TraceID 'abc123' -RoutingKey 'codegen' -Driver 'cli' -Model 'gpt-4'

        $result.trace_id    | Should -Be 'abc123'
        $result.routing_key | Should -Be 'codegen'
        $result.driver      | Should -Be 'cli'
        $result.model       | Should -Be 'gpt-4'
    }
}

Describe -Name 'Test-ResultSuccess' -Tag 'error-schema' {

    It 'returns $true for a result with exit_code=0 and $null error' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode 0
        Test-ResultSuccess $result | Should -Be $true
    }

    It 'returns $true for a result with exit_code=0 and empty-string error' {
        # When ErrMsg is $null, there's no error field in the hashtable that's non-null
        # Actually New-ResultObject with ExitCode 0 and no ErrMsg sets error=$null
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode 0
        # Explicitly set error to empty string
        $result.error = ''
        Test-ResultSuccess $result | Should -Be $true
    }

    It 'returns $false for exit_code=-1 (timeout)' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode -1
        Test-ResultSuccess $result | Should -Be $false
    }

    It 'returns $false for exit_code=-2 (guardrail)' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode -2
        Test-ResultSuccess $result | Should -Be $false
    }

    It 'returns $false for exit_code=1' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode 1
        Test-ResultSuccess $result | Should -Be $false
    }

    It 'returns $false when error message is present' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode 0 -ErrMsg 'Something went wrong'
        Test-ResultSuccess $result | Should -Be $false
    }

    It 'returns $false when both exit_code non-zero and error present' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode 1 -ErrMsg 'failure'
        Test-ResultSuccess $result | Should -Be $false
    }
}

Describe -Name 'Format-ResultError' -Tag 'error-schema' {

    It 'formats basic error message correctly' {
        $result = New-ResultObject -Agent 'claude' -Task 'refactor' -ExitCode 1 -ErrMsg 'Syntax error' -DurationMs 500
        $msg = Format-ResultError $result

        $msg | Should -Match '\[MAOP-1\]'
        $msg | Should -Match "Agent='claude'"
        $msg | Should -Match "Task='refactor'"
        $msg | Should -Match 'Syntax error'
        $msg | Should -Match '500ms'
    }

    It 'formats timeout error correctly' {
        $result = New-ResultObject -Agent 'codex' -Task 'test-run' -ExitCode -1 -ErrMsg 'Timeout' -DurationMs 30000
        $msg = Format-ResultError $result

        $msg | Should -Match '\[MAOP--1\]'
        $msg | Should -Match "Agent='codex'"
        $msg | Should -Match 'Timeout'
    }

    It 'handles exit_code of 0 (no error) gracefully' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode 0 -DurationMs 100
        $msg = Format-ResultError $result

        $msg | Should -Match '\[MAOP-0\]'
        $msg | Should -Not -Match ' — '
    }

    It 'includes stderr when -IncludeDetails is specified' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode 1 -ErrMsg 'fail' -Stderr 'error log'
        $msg = Format-ResultError $result -IncludeDetails

        $msg | Should -Match 'stderr: error log'
    }

    It 'includes stdout when -IncludeDetails is specified' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode 1 -ErrMsg 'fail' -Stdout 'output log'
        $msg = Format-ResultError $result -IncludeDetails

        $msg | Should -Match 'stdout: output log'
    }

    It 'does NOT include stderr/stdout without -IncludeDetails' {
        $result = New-ResultObject -Agent 'a' -Task 't' -ExitCode 1 -ErrMsg 'fail' -Stderr 'error log' -Stdout 'output log'
        $msg = Format-ResultError $result

        $msg | Should -Not -Match 'stderr:'
        $msg | Should -Not -Match 'stdout:'
    }

    It 'handles result with missing keys gracefully' {
        $badResult = @{ agent = 'test'; task = 'test' }
        $msg = Format-ResultError $badResult

        $msg | Should -Not -BeNullOrEmpty
        # Should still contain agent and task info
        $msg | Should -Match "Agent='test'"
        $msg | Should -Match "Task='test'"
    }
}
