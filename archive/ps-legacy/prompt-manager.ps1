param(
  [ValidateSet("create","get","list","delete","render","test","search","export","import")]
  [string]$Action = "list",
  [string]$TemplateId = "",
  [string]$Name = "",
  [string]$Category = "general",
  [string]$Content = "",
  [string]$Variables = "{}",
  [string]$Tags = "",
  [string]$Query = "",
  [string]$RenderVars = "{}",
  [string]$Version = "",
  [string]$PromptFile = ""
)

$ScriptDir = Split-Path $PSCommandPath -Parent
if (-not $PromptFile) { $PromptFile = Join-Path (Split-Path $ScriptDir -Parent) "data\prompts.json" }
$PromptDir = Split-Path $PromptFile -Parent; if (-not (Test-Path $PromptDir)) { New-Item -ItemType Directory -Force -Path $PromptDir | Out-Null }

function Load-Prompts {
  if (Test-Path $PromptFile) { try { return @((Get-Content $PromptFile -Raw | ConvertFrom-Json).prompts) } catch { return @() } }
  return @()
}
function Save-Prompts($p) {
  @{ prompts = @($p) } | ConvertTo-Json -Depth 3 -Compress | Set-Content $PromptFile -Encoding utf8
}

switch ($Action) {
  "create" {
    if (-not $TemplateId -or -not $Content) { Write-Error "create requires -TemplateId and -Content"; exit 1 }
    $tags = if ($Tags) { $Tags -split ',' | ForEach-Object { $_.Trim() } } else { @() }
    $prompts = New-Object System.Collections.ArrayList
    $now = (Get-Date -Format "o")
    $entry = @{
      id = $TemplateId; name = if ($Name) { $Name } else { $TemplateId }
      category = $Category; tags = @($tags)
      versions = @(@{ version = "1.0"; content = $Content; variables = ($Variables | ConvertFrom-Json); created = $now })
      current_version = "1.0"
    }
    foreach ($p in @(Load-Prompts)) { if ($p.id -ne $TemplateId) { $null = $prompts.Add($p) } }
    $null = $prompts.Add($entry)
    Save-Prompts $prompts
    Write-Output ("created: " + $TemplateId + " v1.0")
  }

  "get" {
    if (-not $TemplateId) { Write-Error "get requires -TemplateId"; exit 1 }
    $prompts = @(Load-Prompts)
    $p = $prompts | Where-Object { $_.id -eq $TemplateId }
    if (-not $p) { Write-Error "not found: $TemplateId"; exit 1 }
    $ver = $p.versions | Where-Object { $_.version -eq ($Version -or $p.current_version) }
    Write-Output ($ver | ConvertTo-Json -Depth 3)
  }

  "render" {
    if (-not $TemplateId) { Write-Error "render requires -TemplateId"; exit 1 }
    $prompts = @(Load-Prompts)
    $p = $prompts | Where-Object { $_.id -eq $TemplateId }
    if (-not $p) { Write-Error "not found: $TemplateId"; exit 1 }
    $ver = $p.versions | Where-Object { $_.version -eq ($Version -or $p.current_version) }
    $content = $ver.content
    $vars = $RenderVars | ConvertFrom-Json
    foreach ($kv in $vars.PSObject.Properties) {
      $content = $content -replace ('{{' + $kv.Name + '}}', "$($kv.Value)")
    }
    Write-Output $content
  }

  "list" {
    $prompts = @(Load-Prompts)
    $list = $prompts | Select-Object id, name, category, @{N="tags";E={$_.tags -join ','}}, current_version, @{N="updated";E={$_.versions[-1].created}} | Sort-Object updated -Descending
    Write-Output ($list | ConvertTo-Json -Depth 2)
  }

  "delete" {
    if (-not $TemplateId) { Write-Error "delete requires -TemplateId"; exit 1 }
    $prompts = New-Object System.Collections.ArrayList
    foreach ($p in @(Load-Prompts)) { if ($p.id -ne $TemplateId) { $null = $prompts.Add($p) } }
    Save-Prompts $prompts
    Write-Output ("deleted: " + $TemplateId)
  }

  "test" {
    if (-not $TemplateId) { Write-Error "test requires -TemplateId"; exit 1 }
    $rendered = & $MyInvocation.MyCommand.Path -Action render -TemplateId $TemplateId -Version $Version -RenderVars $RenderVars
    Write-Output ("=== " + $TemplateId + " ===")
    Write-Output $rendered
  }

  "search" {
    if (-not $Query) { Write-Error "search requires -Query"; exit 1 }
    $prompts = @(Load-Prompts)
    $matches = $prompts | Where-Object {
      $_.id -match $Query -or $_.name -match $Query -or $_.category -match $Query -or ($_.tags -join ' ') -match $Query
    }
    Write-Output ($matches | Select-Object id, name, category, tags, current_version | ConvertTo-Json -Depth 2)
  }

  "export" {
    $prompts = @(Load-Prompts)
    Write-Output ($prompts | ConvertTo-Json -Depth 3)
  }

  "import" {
    $json = Get-Content $PromptFile -Raw
    $imported = $json | ConvertFrom-Json
    $existing = @(Load-Prompts)
    $merged = New-Object System.Collections.ArrayList
    foreach ($e in $existing) { $null = $merged.Add($e) }
    foreach ($i in $imported.prompts) {
      $dup = $existing | Where-Object { $_.id -eq $i.id }
      if (-not $dup) { $null = $merged.Add($i) }
    }
    Save-Prompts $merged
    Write-Output ("imported: " + $merged.Count + " prompts")
  }
}