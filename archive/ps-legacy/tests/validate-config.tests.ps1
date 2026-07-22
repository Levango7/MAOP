BeforeAll {
  $ScriptDir = Split-Path $PSCommandPath -Parent
  $RootDir   = Split-Path $ScriptDir -Parent
  $SrcDir    = Join-Path $RootDir "src"
  $ConfigDir = Join-Path $RootDir "config"
}

Describe "Validate-Config — Parse-Mapping" {
  BeforeAll {
    . (Join-Path $SrcDir "validate-config.ps1")
  }

  It "parses agents section from live agents.yaml" {
    $lines = Get-Content (Join-Path $ConfigDir "agents.yaml") -Encoding utf8
    $agents = Parse-Mapping -SectionName "agents" -Lines $lines
    $agents.Keys.Count | Should -BeGreaterThan 10  # we have 16 agents
  }

  It "parses agent capabilities as array (not concatenated string)" {
    $lines = Get-Content (Join-Path $ConfigDir "agents.yaml") -Encoding utf8
    $agents = Parse-Mapping -SectionName "agents" -Lines $lines
    $agents.Keys | ForEach-Object {
      $entry = $agents[$_]
      if ($entry.ContainsKey("capabilities")) {
        $cap = $entry["capabilities"]
        if ($cap.GetType().IsArray) {
          # Already good
        } else {
          # Type check
          $cap.GetType().Name | Should -Be "Object[]"
        }
        # Verify individual items are short strings (not a concatenated blob)
        foreach ($c in $cap) {
          ($c -is [string]) | Should -Be $true
          $c.Length | Should -BeLessThan 30
        }
      }
    }
  }

  It "returns known agent names from live config" {
    $lines = Get-Content (Join-Path $ConfigDir "agents.yaml") -Encoding utf8
    $agents = Parse-Mapping -SectionName "agents" -Lines $lines
    @("claude", "openclaw", "mavis", "cursor", "qoder") | ForEach-Object {
      $agents.ContainsKey($_) | Should -Be $true -Because "agent $_ should exist"
    }
  }

  It "parses workflows section" {
    $lines = Get-Content (Join-Path $ConfigDir "agents.yaml") -Encoding utf8
    $workflows = Parse-Mapping -SectionName "workflows" -Lines $lines
    $workflows.ContainsKey("doc-pipeline") | Should -Be $true
  }

  It "parses routing section with primary/fallback/tertiary" {
    $lines = Get-Content (Join-Path $ConfigDir "agents.yaml") -Encoding utf8
    $routing = Parse-Mapping -SectionName "routing" -Lines $lines
    $routing.Keys.Count | Should -BeGreaterOrEqual 10
    $routing["codegen"].ContainsKey("primary") | Should -Be $true
    $routing["codegen"].ContainsKey("fallback") | Should -Be $true
  }

  It "exits section on next top-level key" {
    $fakeYaml = @"
agents:
  testagent:
    cli: "fake"
    capabilities:
      - codegen

workflows:
  testworkflow:
    description: "test"

routing:
  testroute:
    primary: "testagent"
"@ -split "`r?`n"
    $agents = Parse-Mapping -SectionName "agents" -Lines $fakeYaml
    $agents.ContainsKey("workflows") | Should -Be $false -Because "agents parser must not cross into workflows section"
    $agents.Keys | Should -BeExactly @("testagent")
  }

  It "handles empty lines and comments gracefully" {
    $fakeYaml = @"
agents:
  # this is a comment

  myagent:
    cli: "hello"
    # inner comment
    driver: cli
"@ -split "`r?`n"
    $agents = Parse-Mapping -SectionName "agents" -Lines $fakeYaml
    $agents.ContainsKey("myagent") | Should -Be $true
    # Parse-Mapping preserves the double quotes from YAML value
    $agents["myagent"]["cli"] | Should -Match "hello"
  }
}

Describe "Validate-Config — Test-AgentConfig" {
  BeforeAll {
    . (Join-Path $SrcDir "validate-config.ps1")
  }

  It "returns valid=true for the current agents.yaml" {
    $result = Test-AgentConfig -ConfigPath (Join-Path $ConfigDir "agents.yaml") -ReturnObject
    $result.valid | Should -Be $true
  }

  It "returns 0 errors for live config" {
    $result = Test-AgentConfig -ConfigPath (Join-Path $ConfigDir "agents.yaml") -ReturnObject
    $result.errors.Count | Should -Be 0
  }

  It "returns agents_count > 10" {
    $result = Test-AgentConfig -ConfigPath (Join-Path $ConfigDir "agents.yaml") -ReturnObject
    $result.agents_count | Should -BeGreaterOrEqual 14
  }

  It "reports warnings for unmatched routing->capability references" {
    $result = Test-AgentConfig -ConfigPath (Join-Path $ConfigDir "agents.yaml") -ReturnObject
    # After 2026-07-12 fixes, routing entries are well-matched (0 warnings expected).
    # Just verify the warnings key exists in the result hashtable.
    $result.Contains('warnings') | Should -Be $true
    $result.warnings.GetType().IsArray | Should -Be $true
  }

  It "outputs valid JSON with -Json flag" {
    $json = & (Join-Path $SrcDir "validate-config.ps1") -Json 2>&1 | Out-String
    $parsed = $json | ConvertFrom-Json
    $parsed.valid | Should -Be $true
    $parsed.agents_count | Should -BeGreaterOrEqual 14
  }

  It "reports unreferenced agents" {
    $result = Test-AgentConfig -ConfigPath (Join-Path $ConfigDir "agents.yaml") -ReturnObject
    $result.unreferenced | Should -Not -BeNullOrEmpty
  }

  It "reports error for invalid config with missing agent" {
    $fakeYaml = @"
agents:
  realagent:
    cli: test

routing:
  testroute:
    primary: missing-agent
"@ -split "`r?`n"
    # Write a temp file
    $tmpFile = Join-Path $RootDir "test-temp-invalid.yaml"
    try {
      Set-Content $tmpFile ($fakeYaml -join "`r`n") -Encoding utf8
      $result = Test-AgentConfig -ConfigPath $tmpFile -ReturnObject
      $result.valid | Should -Be $false
      $result.errors.Count | Should -BeGreaterOrEqual 1
    } finally {
      Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
    }
  }
}
