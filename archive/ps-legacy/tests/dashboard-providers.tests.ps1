BeforeAll {
  $ScriptDir = Split-Path $PSCommandPath -Parent
  $RootDir   = Split-Path $ScriptDir -Parent
  $SrcDir    = Join-Path $RootDir "src"
  $ConfigDir = Join-Path $RootDir "config"
}

Describe "Dashboard-Providers — Output" {
  It "outputs valid JSON when run against live config" {
    $json = & (Join-Path $SrcDir "dashboard-providers.ps1") 2>&1 | Out-String
    $parsed = $json | ConvertFrom-Json
    $parsed.Count | Should -BeGreaterOrEqual 14
  }

  It "outputs known agent names" {
    $json = & (Join-Path $SrcDir "dashboard-providers.ps1") 2>&1 | Out-String
    $parsed = $json | ConvertFrom-Json
    $names = $parsed | ForEach-Object { $_.name }
    @("claude", "openclaw", "mavis") | ForEach-Object {
      $_ -in $names | Should -Be $true -Because "agent $_ should be in providers output"
    }
  }

  It "includes capabilities for each agent" {
    $json = & (Join-Path $SrcDir "dashboard-providers.ps1") 2>&1 | Out-String
    $parsed = $json | ConvertFrom-Json
    $withCaps = $parsed | Where-Object { $_.capabilities.Count -gt 0 }
    $withCaps.Count | Should -BeGreaterOrEqual 14
  }

  It "includes driver field" {
    $json = & (Join-Path $SrcDir "dashboard-providers.ps1") 2>&1 | Out-String
    $parsed = $json | ConvertFrom-Json
    $parsed[0].driver | Should -Not -BeNullOrEmpty
  }

  It "returns empty array for missing config file" {
    $result = & (Join-Path $SrcDir "dashboard-providers.ps1") -ConfigPath "Z:\nonexistent\agents.yaml" 2>&1 | Out-String
    $result.Trim() | Should -Be "[]"
  }
}

Describe "Dashboard-Providers — Capabilities Parsing" {
  It "parses capabilities as array (not string)" {
    $json = & (Join-Path $SrcDir "dashboard-providers.ps1") 2>&1 | Out-String
    $parsed = $json | ConvertFrom-Json
    foreach ($p in $parsed) {
      if ($p.capabilities.Count -gt 0) {
        $p.capabilities[0].GetType().Name | Should -Be "String"
      }
    }
  }

  It "each capability is a short keyword, not a concatenated blob" {
    $json = & (Join-Path $SrcDir "dashboard-providers.ps1") 2>&1 | Out-String
    $parsed = $json | ConvertFrom-Json
    foreach ($p in $parsed) {
      foreach ($cap in $p.capabilities) {
        $cap.Length | Should -BeLessThan 30
        $cap -notmatch "," | Should -Be $true -Because "capabilities should not be comma-joined strings"
      }
    }
  }
}
