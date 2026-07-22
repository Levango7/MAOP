param(
  [ValidateSet("add","search","get","list","delete","rebuild","stats","add-dir")]
  [string]$Action = "search",
  [string]$WikiId = "",
  [string]$Title = "",
  [string]$Content = "",
  [string]$Category = "general",
  [string]$Tags = "",
  [string]$Source = "",
  [string]$Query = "",
  [string]$Dir = "",
  [string]$WikiFile = "",
  [string]$IndexFile = "",
  [int]$TopK = 5
)

$ScriptDir = Split-Path $PSCommandPath -Parent
$DataDir = Join-Path (Split-Path $ScriptDir -Parent) "data"
if (-not $WikiFile) { $WikiFile = Join-Path $DataDir "wiki.json" }
if (-not $IndexFile) { $IndexFile = Join-Path $DataDir "wiki-index.json" }

function Load-Wiki {
  if (Test-Path $WikiFile) { try { return @((Get-Content $WikiFile -Raw | ConvertFrom-Json).entries) } catch { return @() } }
  return @()
}
function Save-Wiki($w) {
  @{ entries = @($w) } | ConvertTo-Json -Depth 3 -Compress | Set-Content $WikiFile -Encoding utf8
}
function Save-Index($i) {
  $i | ConvertTo-Json -Depth 2 -Compress | Set-Content $IndexFile -Encoding utf8
}

switch ($Action) {
  "add" {
    if (-not $WikiId -or -not $Content) { Write-Error "add requires -WikiId and -Content"; exit 1 }
    $tags = if ($Tags) { $Tags -split ',' | ForEach-Object { $_.Trim() } } else { @() }
    $wiki = New-Object System.Collections.ArrayList
    foreach ($w in @(Load-Wiki)) { if ($w.id -ne $WikiId) { $null = $wiki.Add($w) } }
    $null = $wiki.Add(@{ id = $WikiId; title = if ($Title) { $Title } else { $WikiId }; content = $Content; category = $Category; tags = @($tags); source = $Source; added = (Get-Date -Format "o") })
    Save-Wiki $wiki
    Write-Output ("wiki entry added: " + $WikiId)
  }

  "search" {
    if (-not $Query) { Write-Error "search requires -Query"; exit 1 }
    $entries = @(Load-Wiki)
    $q = $Query.ToLower()
    $scored = foreach ($e in $entries) {
      $score = 0
      if ($e.id.ToLower() -match [regex]::Escape($q)) { $score += 10 }
      if ($e.title.ToLower() -match [regex]::Escape($q)) { $score += 8 }
      if ($e.category.ToLower() -match [regex]::Escape($q)) { $score += 5 }
      if (($e.tags -join ' ').ToLower() -match [regex]::Escape($q)) { $score += 5 }
      if ($e.content.ToLower() -match [regex]::Escape($q)) { $score += 3 }
      if ($score -gt 0) { [PSCustomObject]@{ id = $e.id; title = $e.title; category = $e.category; tags = $e.tags -join ','; score = $score; snippet = $e.content.Substring(0, [Math]::Min(80, $e.content.Length)) } }
    }
    $results = $scored | Sort-Object score -Descending | Select-Object -First $TopK
    Write-Output ($results | ConvertTo-Json -Depth 2)
  }

  "get" {
    if (-not $WikiId) { Write-Error "get requires -WikiId"; exit 1 }
    $e = @(Load-Wiki) | Where-Object { $_.id -eq $WikiId }
    Write-Output ($e | ConvertTo-Json -Depth 3)
  }

  "list" {
    $entries = @(Load-Wiki)
    $list = $entries | Select-Object id, title, category, @{N="tags";E={$_.tags -join ','}}, source, added | Sort-Object added -Descending
    Write-Output ($list | ConvertTo-Json -Depth 2)
  }

  "delete" {
    if (-not $WikiId) { Write-Error "delete requires -WikiId"; exit 1 }
    $wiki = New-Object System.Collections.ArrayList
    foreach ($w in @(Load-Wiki)) { if ($w.id -ne $WikiId) { $null = $wiki.Add($w) } }
    Save-Wiki $wiki
    Write-Output ("deleted: " + $WikiId)
  }

  "rebuild" {
    $entries = @(Load-Wiki)
    $index = @()
    foreach ($e in $entries) {
      $index += @{ id = $e.id; title = $e.title; category = $e.category; tags = $e.tags; searchable = ($e.title + " " + $e.category + " " + ($e.tags -join ' ') + " " + $e.content).ToLower() }
    }
    Save-Index $index
    Write-Output ("index rebuilt: " + $index.Count + " entries")
  }

  "stats" {
    $entries = @(Load-Wiki)
    $byCat = $entries | Group-Object category | ForEach-Object { @{ category = $_.Name; count = $_.Count } }
    Write-Output (@{ total = $entries.Count; categories = @($byCat) } | ConvertTo-Json -Depth 2)
  }

  "add-dir" {
    if (-not $Dir) { Write-Error "add-dir requires -Dir"; exit 1 }
    if (-not (Test-Path $Dir)) { Write-Error "directory not found: $Dir"; exit 1 }
    $files = Get-ChildItem $Dir -Recurse -Include "*.md" -ErrorAction SilentlyContinue
    $added = 0
    foreach ($f in $files) {
      $relativePath = $f.FullName.Substring((Resolve-Path $Dir).Path.Length + 1)
      $relId = $relativePath -replace '\\', '/' -replace '\.md$', ''
      $existing = @(Load-Wiki) | Where-Object { $_.id -eq $relId }
      if (-not $existing) {
        $content = Get-Content $f.FullName -Raw -Encoding utf8 -ErrorAction SilentlyContinue
        if ($content) {
          $tags = ($f.Directory.Name -split '\\|/')[-1]
          & $MyInvocation.MyCommand.Path -Action add -WikiId $relId -Title $f.BaseName -Content $content -Category "docs" -Tags $tags -Source $f.FullName 2>&1 | Out-Null
          $added++
        }
      }
    }
    Write-Output ("added " + $added + " wiki entries from " + $Dir)
  }
}