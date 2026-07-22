param(
  [ValidateSet("create","list","info","run","delete","search","export","install")]
  [string]$Action = "list",
  [string]$SkillId = "",
  [string]$Name = "",
  [string]$Category = "general",
  [string]$Description = "",
  [string]$Prompt = "",
  [string]$Agent = "",
  [string]$Variables = "{}",
  [string]$Tags = "",
  [string]$Query = "",
  [string]$RenderVars = "{}",
  [string]$SkillFile = ""
)

$ScriptDir = Split-Path $PSCommandPath -Parent
if (-not $SkillFile) { $SkillFile = Join-Path (Split-Path $ScriptDir -Parent) "data\skills.json" }
$SkillDir = Split-Path $SkillFile -Parent; if (-not (Test-Path $SkillDir)) { New-Item -ItemType Directory -Force -Path $SkillDir | Out-Null }

function Load-Skills {
  if (Test-Path $SkillFile) { try { return @((Get-Content $SkillFile -Raw | ConvertFrom-Json).skills) } catch { return @() } }
  return @()
}
function Save-Skills($s) {
  @{ skills = @($s) } | ConvertTo-Json -Depth 3 -Compress | Set-Content $SkillFile -Encoding utf8
}

$DelegateScript = Join-Path $ScriptDir "delegate.ps1"

switch ($Action) {
  "create" {
    if (-not $SkillId -or -not $Prompt) { Write-Error "create requires -SkillId and -Prompt"; exit 1 }
    $tags = if ($Tags) { $Tags -split ',' | ForEach-Object { $_.Trim() } } else { @() }
    $skills = New-Object System.Collections.ArrayList
    foreach ($s in @(Load-Skills)) { if ($s.id -ne $SkillId) { $null = $skills.Add($s) } }
    $null = $skills.Add(@{
      id = $SkillId; name = if ($Name) { $Name } else { $SkillId }
      category = $Category; description = $Description; prompt = $Prompt
      agent = $Agent; variables = ($Variables | ConvertFrom-Json); tags = @($tags)
      version = "1.0"; created = (Get-Date -Format "o"); usage_count = 0
    })
    Save-Skills $skills
    Write-Output ("skill created: " + $SkillId)
  }

  "list" {
    $skills = @(Load-Skills)
    $list = $skills | Select-Object id, name, category, @{N="tags";E={$_.tags -join ','}}, version, @{N="used";E={$_.usage_count}}, @{N="agent";E={if ($_.agent) { $_.agent } else { "auto" }}}
    Write-Output ($list | ConvertTo-Json -Depth 2)
  }

  "info" {
    if (-not $SkillId) { Write-Error "info requires -SkillId"; exit 1 }
    $s = @(Load-Skills) | Where-Object { $_.id -eq $SkillId }
    Write-Output ($s | ConvertTo-Json -Depth 3)
  }

  "run" {
    if (-not $SkillId) { Write-Error "run requires -SkillId"; exit 1 }
    $skill = @(Load-Skills) | Where-Object { $_.id -eq $SkillId }
    if (-not $skill) { Write-Error "skill not found: $SkillId"; exit 1 }
    $prompt = $skill.prompt
    $vars = $RenderVars | ConvertFrom-Json
    foreach ($kv in $vars.PSObject.Properties) {
      $prompt = $prompt -replace ('{{' + $kv.Name + '}}', "$($kv.Value)")
    }
    $agent = if ($skill.agent) { $skill.agent } else { "nvidia" }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
      $json = & powershell -NoProfile -File $DelegateScript -Agent $agent -Task $prompt -TimeoutSeconds 60 2>&1 | Out-String
      $result = $json | ConvertFrom-Json
      $sw.Stop()
      # Update usage count
      $skills = New-Object System.Collections.ArrayList
      foreach ($s in @(Load-Skills)) {
        if ($s.id -eq $SkillId) { $null = $skills.Add(@{id=$s.id;name=$s.name;category=$s.category;description=$s.description;prompt=$s.prompt;agent=$s.agent;variables=$s.variables;tags=$s.tags;version=$s.version;created=$s.created;usage_count=($s.usage_count+1)}) }
        else { $null = $skills.Add($s) }
      }
      Save-Skills $skills
      Write-Output (@{ skill = $SkillId; agent = $agent; ok = ($result.exit_code -eq 0); output = $result.stdout; ms = $sw.ElapsedMilliseconds } | ConvertTo-Json)
    } catch {
      $sw.Stop()
      Write-Output (@{ skill = $SkillId; ok = $false; error = $_.Exception.Message; ms = $sw.ElapsedMilliseconds } | ConvertTo-Json)
    }
  }

  "delete" {
    if (-not $SkillId) { Write-Error "delete requires -SkillId"; exit 1 }
    $skills = New-Object System.Collections.ArrayList
    foreach ($s in @(Load-Skills)) { if ($s.id -ne $SkillId) { $null = $skills.Add($s) } }
    Save-Skills $skills
    Write-Output ("deleted: " + $SkillId)
  }

  "search" {
    if (-not $Query) { Write-Error "search requires -Query"; exit 1 }
    $skills = @(Load-Skills)
    $matches = $skills | Where-Object { $_.id -match $Query -or $_.name -match $Query -or $_.category -match $Query -or $_.description -match $Query -or ($_.tags -join ' ') -match $Query }
    Write-Output ($matches | Select-Object id, name, category, description, version | ConvertTo-Json -Depth 2)
  }

  "export" {
    $skills = @(Load-Skills)
    Write-Output ($skills | ConvertTo-Json -Depth 3)
  }

  "install" {
    if (-not $SkillId -or -not $Prompt) { Write-Error "install requires -SkillId and -Prompt"; exit 1 }
    & $MyInvocation.MyCommand.Path -Action create -SkillId $SkillId -Name $Name -Category $Category -Description $Description -Prompt $Prompt -Agent $Agent -Variables $Variables -Tags $Tags
  }
}