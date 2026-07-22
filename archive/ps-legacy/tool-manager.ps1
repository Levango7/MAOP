param(
  [ValidateSet("register","list","find","info","enable","disable","call","delete","stats")]
  [string]$Action = "list",
  [string]$ToolId = "",
  [string]$Name = "",
  [string]$Description = "",
  [string]$Command = "",
  [string]$Category = "general",
  [string]$Params = "{}",
  [string]$Query = "",
  [string[]]$Args = @(),
  [int]$TimeoutSeconds = 30,
  [string]$ToolsFile = ""
)

$ScriptDir = Split-Path $PSCommandPath -Parent
if (-not $ToolsFile) { $ToolsFile = Join-Path (Split-Path $ScriptDir -Parent) "data\tools.json" }
$ToolsDir = Split-Path $ToolsFile -Parent; if (-not (Test-Path $ToolsDir)) { New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null }

function Load-Tools {
  if (Test-Path $ToolsFile) { try { return @((Get-Content $ToolsFile -Raw | ConvertFrom-Json).tools) } catch { return @() } }
  return @()
}
function Save-Tools($t) {
  @{ tools = @($t) } | ConvertTo-Json -Depth 3 -Compress | Set-Content $ToolsFile -Encoding utf8
}

switch ($Action) {
  "register" {
    if (-not $ToolId -or -not $Command) { Write-Error "register requires -ToolId and -Command"; exit 1 }
    $tools = New-Object System.Collections.ArrayList
    foreach ($t in @(Load-Tools)) { if ($t.id -ne $ToolId) { $null = $tools.Add($t) } }
    $null = $tools.Add(@{
      id = $ToolId; name = if ($Name) { $Name } else { $ToolId }
      description = $Description; command = $Command; category = $Category
      params = ($Params | ConvertFrom-Json); enabled = $true; created = (Get-Date -Format "o")
    })
    Save-Tools $tools
    Write-Output "registered: $ToolId"
  }

  "list" {
    $tools = @(Load-Tools)
    $cat = $tools | Group-Object category | ForEach-Object {
      @{ category = $_.Name; tools = $_.Group | Select-Object id, name, description, enabled, category }
    }
    Write-Output ($cat | ConvertTo-Json -Depth 3)
  }

  "find" {
    if (-not $Query) { Write-Error "find requires -Query"; exit 1 }
    $tools = @(Load-Tools)
    $matches = $tools | Where-Object { $_.id -match $Query -or $_.name -match $Query -or $_.description -match $Query -or $_.category -match $Query }
    Write-Output ($matches | ConvertTo-Json -Depth 2)
  }

  "info" {
    if (-not $ToolId) { Write-Error "info requires -ToolId"; exit 1 }
    $tool = @(Load-Tools) | Where-Object { $_.id -eq $ToolId }
    Write-Output ($tool | ConvertTo-Json -Depth 3)
  }

  "enable" {
    if (-not $ToolId) { Write-Error "enable requires -ToolId"; exit 1 }
    $tools = New-Object System.Collections.ArrayList
    foreach ($t in @(Load-Tools)) { $null = $tools.Add(@{id=$t.id;name=$t.name;description=$t.description;command=$t.command;category=$t.category;params=$t.params;enabled=($t.id -eq $ToolId -or $t.enabled -eq $true);created=$t.created}) }
    Save-Tools $tools
    Write-Output "enabled: $ToolId"
  }

  "disable" {
    if (-not $ToolId) { Write-Error "disable requires -ToolId"; exit 1 }
    $tools = New-Object System.Collections.ArrayList
    foreach ($t in @(Load-Tools)) { $null = $tools.Add(@{id=$t.id;name=$t.name;description=$t.description;command=$t.command;category=$t.category;params=$t.params;enabled=($t.id -ne $ToolId -and $t.enabled -eq $true);created=$t.created}) }
    Save-Tools $tools
    Write-Output "disabled: $ToolId"
  }

  "call" {
    if (-not $ToolId) { Write-Error "call requires -ToolId"; exit 1 }
    $tool = @(Load-Tools) | Where-Object { $_.id -eq $ToolId -and $_.enabled }
    if (-not $tool) { Write-Error "tool not found or disabled: $ToolId"; exit 1 }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
      $output = & powershell -NoProfile -File $tool.command @Args 2>&1 | Out-String
      $sw.Stop()
      Write-Output (@{ ok = $true; tool = $ToolId; output = $output.Trim(); duration_ms = $sw.ElapsedMilliseconds } | ConvertTo-Json)
    } catch {
      $sw.Stop()
      Write-Output (@{ ok = $false; tool = $ToolId; error = $_.Exception.Message; duration_ms = $sw.ElapsedMilliseconds } | ConvertTo-Json)
    }
  }

  "delete" {
    if (-not $ToolId) { Write-Error "delete requires -ToolId"; exit 1 }
    $tools = New-Object System.Collections.ArrayList
    $removed = 0
    foreach ($t in @(Load-Tools)) { if ($t.id -eq $ToolId) { $removed++ } else { $null = $tools.Add($t) } }
    Save-Tools $tools
    Write-Output "deleted $removed tool(s): $ToolId"
  }

  "stats" {
    $tools = @(Load-Tools)
    $total = $tools.Count; $enabled = @($tools | Where-Object { $_.enabled }).Count
    $byCat = $tools | Group-Object category | ForEach-Object { @{ category = $_.Name; count = $_.Count } }
    Write-Output (@{ total = $total; enabled = $enabled; disabled = $total - $enabled; categories = @($byCat) } | ConvertTo-Json)
  }
}
