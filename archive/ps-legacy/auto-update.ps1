param(
  [ValidateSet("check","update-all","list","config")]
  [string]$Action = "check",
  [string]$Agent = "",
  [string]$VersionFile = "",
  [switch]$Force
)

$ScriptDir = Split-Path $PSCommandPath -Parent
if (-not $VersionFile) { $VersionFile = Join-Path (Split-Path $ScriptDir -Parent) "data\agent-versions.json" }
$DataDir = Split-Path $VersionFile -Parent; if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Force -Path $DataDir | Out-Null }

$Agents = @(
  @{name="claude";    type="cli";     cmd="claude";         check="--version";      regex="(\d+\.\d+\.\d+)"; pkg="anthropic/claude-code"}
  @{name="codewhale"; type="cli";     cmd="codewhale";      check="--version";      regex="(\d+\.\d+\.\d+)"; pkg="codewhale"}
  @{name="codex";     type="cli";     cmd="codex";          check="--version";      regex="(\d+\.\d+\.\d+)"}
  @{name="kimi";      type="cli";     cmd="kimi";           check="--version";      regex="(\d+\.\d+\.\d+)"}
  @{name="nvidia";    type="api";     cmd="";                                                      pkg="nvidia-nim"}
  @{name="openclaw";  type="exe";     cmd="openclaw";       check="--version";      regex="(\d+\.\d+\.\d+)"}
  @{name="qwenpaw";   type="exe";     cmd="qwenpaw";        check="--version";      regex="(\d+\.\d+\.\d+)"}
  @{name="mavis";     type="cli";     cmd="mavis";          check="--version";      regex="(\d+\.\d+\.\d+)"}
  @{name="kilo";      type="cli";     cmd="kilo";           check="--version";      regex="(\d+\.\d+\.\d+)"}
  @{name="hermes";    type="cli";     cmd="hermes";         check="--version";      regex="(\d+\.\d+\.\d+)"}
  @{name="qoder";     type="cli";     cmd="qoderclicn";     check="--version";      regex="(\d+\.\d+\.\d+)"}
  @{name="mimo";      type="cli";     cmd="mimo";           check="--version";      regex="(\d+\.\d+\.\d+)"}
  @{name="autoclaw";  type="cli";     cmd="autoclaw";       check="--version";      regex="(\d+\.\d+\.\d+)"}
)

function Load-Versions {
  if (Test-Path $VersionFile) { try { return @((Get-Content $VersionFile -Raw | ConvertFrom-Json).agents) } catch { return @() } }
  return @()
}
function Save-Versions($v) {
  @{ agents = @($v); last_check = (Get-Date -Format "o") } | ConvertTo-Json -Depth 3 -Compress | Set-Content $VersionFile -Encoding utf8
}

switch ($Action) {
  "check" {
    $result = @()
    foreach ($a in $Agents) {
      if ($Agent -and $a.name -ne $Agent) { continue }
      $current = ""
      if ($a.type -eq "cli" -or $a.type -eq "exe") {
        try {
          $out = & $a.cmd $a.check 2>&1 | Out-String
          if ($out -match $a.regex) { $current = $Matches[1] }
        } catch { $current = "error" }
      } elseif ($a.type -eq "api") {
        $current = "api"
      }
      $entry = @{ agent = $a.name; type = $a.type; current_version = $current; checked = (Get-Date -Format "o") }
      $result += $entry
    }
    $existing = @(Load-Versions)
    $merged = New-Object System.Collections.ArrayList
    foreach ($e in $existing) { $null = $merged.Add($e) }
    foreach ($r in $result) {
      $dup = $existing | Where-Object { $_.agent -eq $r.agent }
      if (-not $dup) { $null = $merged.Add($r) } else {
        $merged2 = New-Object System.Collections.ArrayList
        foreach ($m in $merged) { if ($m.agent -eq $r.agent) { $null = $merged2.Add($r) } else { $null = $merged2.Add($m) } }
        $merged = $merged2
      }
    }
    Save-Versions $merged
    $summary = $result | Select-Object agent, type, current_version
    Write-Output ($summary | ConvertTo-Json -Depth 2)
  }

  "update-all" {
    $versions = @(Load-Versions)
    $outdated = @()
    foreach ($a in $Agents) {
      if ($a.type -eq "api") { continue }
      $v = $versions | Where-Object { $_.agent -eq $a.name }
      if ($v -and $v.current_version -and $v.current_version -ne "api" -and $v.current_version -ne "error") {
        $outdated += @{ agent = $a.name; version = $v.current_version; cmd = $a.cmd }
      }
    }
    if ($Force) {
      foreach ($o in $outdated) {
        try {
          if ($o.cmd -eq "npm") { & npm update -g $o.agent 2>&1 | Out-Null }
          else { & npm update -g $o.agent 2>&1 | Out-Null }
          Write-Output ("updated: " + $o.agent)
        } catch { Write-Output ("update failed: " + $o.agent) }
      }
    } else {
      Write-Output ($outdated | ConvertTo-Json -Depth 2)
    }
  }

  "list" {
    $versions = @(Load-Versions)
    Write-Output ($versions | ConvertTo-Json -Depth 3)
  }

  "config" {
    Write-Output (@{
      version_file = $VersionFile
      agents_count = $Agents.Count
      check_interval = "每次调用"
      auto_update = $false
    } | ConvertTo-Json)
  }
}