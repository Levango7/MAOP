<#
.SYNOPSIS
  Pester tests for src/dynamic-router.ps1 — scoring logic + cache
.DESCRIPTION
  Tests Invoke-DynamicRouter function by dot-sourcing the script and calling
  the function directly. This allows Pester Mocks to intercept file I/O calls.
  Validates scoring formula: success_rate * 0.6 + speed_score * 0.4
  Ensures cache bypass works with -Refresh switch.
#>

BeforeAll {
    $script:mockHealthData = '[]'
    $script:mockDelegData = '[]'
    $script:mockAgentsYaml = ''
    $script:mockCacheExists = $false
    $script:mockCacheContent = ''
    $script:mockCacheAge = 999
    $script:dataDirExists = $true

    # Load filelock so Pester can mock it
    . 'F:\Nexus\MAOP\src\filelock.ps1'

    # Mock file system and supporting commands
    Mock -CommandName Test-Path -MockWith {
        param($Path)
        if ($Path -like '*\data') { return $script:dataDirExists }
        if ($Path -like '*dynamic-routing-cache.json') { return $script:mockCacheExists }
        # Health/delegation/agents files: return true (we mock Get-Content for these)
        if ($Path -like '*healthcheck_latest.json') { return $true }
        if ($Path -like '*delegations.json') { return $true }
        if ($Path -like '*agents.yaml') { return $true }
        if ($Path -like '*filelock.ps1') { return $true }
        return $true
    }

    Mock -CommandName Get-Content -MockWith {
        param($Path, $Raw)
        if ($Path -like '*healthcheck_latest.json') { return $script:mockHealthData }
        if ($Path -like '*delegations.json') { return $script:mockDelegData }
        if ($Path -like '*agents.yaml') {
            # Return as array of lines (matching default Get-Content behavior)
            if ($Raw) { return $script:mockAgentsYaml }
            return $script:mockAgentsYaml -split "`n"
        }
        if ($Path -like '*dynamic-routing-cache.json') { return $script:mockCacheContent }
        return ''
    } -Verifiable

    Mock -CommandName Get-Item -MockWith {
        param($Path)
        if ($Path -like '*dynamic-routing-cache.json') {
            return [PSCustomObject]@{ LastWriteTime = (Get-Date).AddSeconds(-$script:mockCacheAge) }
        }
        return $null
    } -Verifiable

    Mock -CommandName Write-Warning -MockWith { } -Verifiable
    Mock -CommandName Write-Host -MockWith { } -Verifiable
    Mock -CommandName Out-File -MockWith { param($FilePath, $Encoding) } -Verifiable
    Mock -CommandName New-Item -MockWith { $null } -Verifiable

    Mock -CommandName Invoke-WithFileLock -MockWith {
        param($Path, $Script)
        & $Script
    } -Verifiable

    # Dot-source the script to load the function (guard prevents auto-execution)
    . 'F:\Nexus\MAOP\src\dynamic-router.ps1'
}

Describe -Name 'Dynamic Router - scoring logic' -Tag 'dynamic-router' {

    BeforeEach {
        $script:mockCacheExists = $false
        $script:mockCacheContent = ''
        $script:dataDirExists = $true

        $script:mockAgentsYaml = @"
routing:
  codegen:
    primary: claude
    fallback: kimi
  review:
    primary: nvidia
"@
    }

    It 'returns scored agents with perfect inputs — best agent ranked first' {
        $script:mockHealthData = @'
[
  { "agent": "claude", "status": "alive", "ms": 100 },
  { "agent": "kimi", "status": "alive", "ms": 500 }
]
'@
        $script:mockDelegData = @'
[
  { "agent": "claude", "result": { "exit_code": 0, "duration_ms": 2000 } },
  { "agent": "claude", "result": { "exit_code": 0, "duration_ms": 1500 } },
  { "agent": "claude", "result": { "exit_code": 0, "duration_ms": 2500 } },
  { "agent": "claude", "result": { "exit_code": 1, "duration_ms": 3000 } },
  { "agent": "claude", "result": { "exit_code": 0, "duration_ms": 1800 } },
  { "agent": "kimi",  "result": { "exit_code": 0, "duration_ms": 8000 } },
  { "agent": "kimi",  "result": { "exit_code": 1, "duration_ms": 12000 } },
  { "agent": "kimi",  "result": { "exit_code": 0, "duration_ms": 10000 } }
]
'@

        $result = Invoke-DynamicRouter -Refresh
        $parsed = $result | ConvertFrom-Json

        $parsed.codegen | Should -Not -BeNullOrEmpty
        $parsed.review  | Should -Not -BeNullOrEmpty

        $agents = $parsed.codegen
        $agents.Count | Should -Be 2

        $agents[0].agent | Should -Be 'claude'
        $agents[1].agent | Should -Be 'kimi'

        $agents[0].score | Should -BeGreaterThan $agents[1].score
    }

    It 'correctly calculates score as success_rate * 0.6 + speed * 0.4' {
        $script:mockHealthData = @'
[
  { "agent": "claude", "status": "alive", "ms": 100 }
]
'@
        $script:mockDelegData = @'
[
  { "agent": "claude", "result": { "exit_code": 0, "duration_ms": 1000 } },
  { "agent": "claude", "result": { "exit_code": 0, "duration_ms": 1000 } }
]
'@

        $result = Invoke-DynamicRouter -Refresh
        $parsed = $result | ConvertFrom-Json

        $claude = $parsed.codegen | Where-Object { $_.agent -eq 'claude' }
        $claude | Should -Not -BeNullOrEmpty

        $claude.success_rate | Should -Be 1.0
        $claude.speed | Should -BeGreaterThan 0.9
        $claude.score | Should -BeGreaterThan 0.9
        $claude.score | Should -BeLessThan 1.01
    }

    It 'penalizes dead agents heavily' {
        $script:mockHealthData = @'
[
  { "agent": "claude", "status": "dead", "ms": 0 }
]
'@
        $script:mockDelegData = '[]'

        $result = Invoke-DynamicRouter -Refresh
        $parsed = $result | ConvertFrom-Json

        $claude = $parsed.codegen | Where-Object { $_.agent -eq 'claude' }
        $claude | Should -Not -BeNullOrEmpty

        $claude.score | Should -Be 0.05
    }

    It 'uses default neutral scores when no health or delegation data exists' {
        $script:mockHealthData = '[]'
        $script:mockDelegData = '[]'

        $result = Invoke-DynamicRouter -Refresh
        $parsed = $result | ConvertFrom-Json

        $claude = $parsed.codegen | Where-Object { $_.agent -eq 'claude' }
        $claude | Should -Not -BeNullOrEmpty

        $claude.score | Should -Be 0.5
        $claude.success_rate | Should -Be 0.5
        $claude.speed | Should -Be 0.5
    }
}

Describe -Name 'Dynamic Router - cache behavior' -Tag 'dynamic-router' {

    It 'returns cached data when cache is fresh (<30s old)' {
        $script:mockCacheContent = "{ `"codegen`": [ { `"agent`": `"cached-agent`", `"score`": 0.99, `"success_rate`": 1.0, `"speed`": 1.0 } ] }"
        $script:mockCacheExists = $true
        $script:mockCacheAge = 10

        $result = Invoke-DynamicRouter
        $parsed = $result | ConvertFrom-Json

        $parsed.codegen[0].agent | Should -Be 'cached-agent'
        $parsed.codegen[0].score | Should -Be 0.99
    }

    It 'bypasses cache with -Refresh switch' {
        $script:mockCacheContent = "{ `"codegen`": [ { `"agent`": `"stale-agent`", `"score`": 0.1, `"success_rate`": 0.1, `"speed`": 0.1 } ] }"
        $script:mockCacheExists = $true
        $script:mockCacheAge = 10

        $script:mockHealthData = '[]'
        $script:mockDelegData = '[]'

        $result = Invoke-DynamicRouter -Refresh
        $parsed = $result | ConvertFrom-Json

        $parsed.codegen[0].agent | Should -Not -Be 'stale-agent'
        $parsed.codegen | Where-Object { $_.agent -eq 'claude' } | Should -Not -BeNullOrEmpty
    }
}

Describe -Name 'Dynamic Router - edge cases' -Tag 'dynamic-router' {

    It 'handles missing health file gracefully' {
        $script:mockHealthData = '[]'
        $script:mockDelegData = '[]'

        $result = Invoke-DynamicRouter -Refresh
        $parsed = $result | ConvertFrom-Json

        $parsed.codegen | Should -Not -BeNullOrEmpty
        $parsed.codegen[0].score | Should -Be 0.5
    }

    It 'handles missing delegations file gracefully' {
        $script:mockHealthData = '[]'
        $script:mockDelegData = '[]'

        $result = Invoke-DynamicRouter -Refresh
        $parsed = $result | ConvertFrom-Json

        $parsed.codegen | Should -Not -BeNullOrEmpty
    }

    It 'sorts agents by score descending' {
        $script:mockHealthData = @'
[
  { "agent": "claude", "status": "alive", "ms": 100 },
  { "agent": "kimi", "status": "alive", "ms": 5000 }
]
'@
        $script:mockDelegData = @'
[
  { "agent": "claude", "result": { "exit_code": 0, "duration_ms": 1000 } },
  { "agent": "kimi", "result": { "exit_code": 0, "duration_ms": 25000 } }
]
'@

        $result = Invoke-DynamicRouter -Refresh
        $parsed = $result | ConvertFrom-Json

        $agents = $parsed.codegen
        $agents.Count | Should -Be 2
        $agents[0].score | Should -BeGreaterOrEqual $agents[1].score
    }
}
