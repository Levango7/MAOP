param(
  [ValidateSet("store","search","delete","list","stats","reindex")]
  [string]$Action = "search",
  [string]$Text = "",
  [string]$Id = "",
  [string]$Metadata = "{}",
  [int]$TopK = 10,
  [double]$Threshold = 0.5,
  [string]$Model = "nvidia/nv-embedqa-e5-v5",
  [string]$VectorFile = ""
)

$ScriptDir = Split-Path $PSCommandPath -Parent
if (-not $VectorFile) { $VectorFile = Join-Path (Split-Path $ScriptDir -Parent) "data\vectors.json" }
$ApiKey = [Environment]::GetEnvironmentVariable("NVIDIA_API_KEY", "User")
$EmbedUrl = "https://integrate.api.nvidia.com/v1/embeddings"

# Load SQLite database module
. (Join-Path $ScriptDir 'database.ps1')

# Ensure database tables exist (silent no-op if SQLite unavailable)
Init-Database | Out-Null

# ── helpers ──
function Get-Embedding($text) {
  if ([string]::IsNullOrWhiteSpace($text)) { return @() }
  $body = @{ model = $Model; input = @($text); input_type = "query" } | ConvertTo-Json
  $headers = @{ Authorization = "Bearer $ApiKey" }
  try {
    $resp = Invoke-RestMethod -Uri $EmbedUrl -Method Post -Headers $headers -Body $body -ContentType "application/json" -TimeoutSec 30
    return $resp.data[0].embedding
  } catch { throw "Embedding failed: $($_.Exception.Message)" }
}

function Load-Vectors {
  # Try SQLite first
  $sqlRows = Query-Database -Sql "SELECT id, text, embedding, metadata, created FROM vectors"
  if ($null -ne $sqlRows) {
    return @($sqlRows | ForEach-Object {
      @{
        id        = $_.id
        text      = $_.text
        embedding = if ($_.embedding) { $_.embedding | ConvertFrom-Json } else { @() }
        metadata  = if ($_.metadata) { $_.metadata | ConvertFrom-Json } else { @{} }
        created   = $_.created
      }
    })
  }
  # Fallback to JSON
  if (Test-Path $VectorFile) {
    try { return @(Get-Content $VectorFile -Raw | ConvertFrom-Json) } catch { return @() }
  }
  return @()
}

function Save-Vectors($vecs) {
  # Try SQLite first (replace all rows)
  $deleteOk = Execute-Database -Sql "DELETE FROM vectors"
  if ($deleteOk) {
    $allOk = $true
    foreach ($v in $vecs) {
      $ok = Execute-Database -Sql "INSERT INTO vectors (id, text, embedding, metadata, created) VALUES (@id, @text, @embed, @meta, @created)" -Parameters @{
        "@id"      = $v.id
        "@text"    = $v.text
        "@embed"   = ($v.embedding | ConvertTo-Json -Compress)
        "@meta"    = ($v.metadata | ConvertTo-Json -Compress)
        "@created" = $v.created
      }
      if (-not $ok) { $allOk = $false; break }
    }
    if ($allOk) { return }  # SQLite succeeded, skip JSON
  }
  # Fallback to JSON
  $vecs | ConvertTo-Json -Depth 3 -Compress | Set-Content $VectorFile
}

function Cosine-Similarity($a, $b) {
  if ($a.Count -ne $b.Count -or $a.Count -eq 0) { return 0 }
  $dot = 0.0; $na = 0.0; $nb = 0.0
  for ($i = 0; $i -lt $a.Count; $i++) {
    $dot += $a[$i] * $b[$i]; $na += $a[$i] * $a[$i]; $nb += $b[$i] * $b[$i]
  }
  $denom = [Math]::Sqrt($na) * [Math]::Sqrt($nb)
  if ($denom -eq 0) { return 0 }
  return $dot / $denom
}

# ── actions ──
switch ($Action) {
  "store" {
    if ([string]::IsNullOrWhiteSpace($Text)) { Write-Error "store requires -Text"; exit 1 }
    $embedding = Get-Embedding $Text
    $entry = @{
      id = if ($Id) { $Id } else { [guid]::NewGuid().ToString() }
      text = $Text
      embedding = $embedding
      metadata = ($Metadata | ConvertFrom-Json)
      created = (Get-Date -Format "o")
    }
    $vecs = @(Load-Vectors)
    $vecs = @($vecs | Where-Object { $_.id -ne $Id }) + @($entry)
    Save-Vectors $vecs
    Write-Output "stored id=$($entry.id) dim=$($embedding.Count)"
  }

  "search" {
    if ([string]::IsNullOrWhiteSpace($Text)) { Write-Error "search requires -Text"; exit 1 }
    $vecs = Load-Vectors
    if ($vecs.Count -eq 0) { Write-Output "[]"; exit 0 }
    $queryEmbed = Get-Embedding $Text
    $scored = foreach ($v in $vecs) {
      $sim = Cosine-Similarity $queryEmbed $v.embedding
      if ($sim -ge $Threshold) {
        [PSCustomObject]@{
          id = $v.id
          text = $v.text
          score = [Math]::Round($sim, 4)
          metadata = $v.metadata
          created = $v.created
        }
      }
    }
    $results = $scored | Sort-Object score -Descending | Select-Object -First $TopK
    Write-Output ($results | ConvertTo-Json -Depth 3)
  }

  "delete" {
    if (-not $Id) { Write-Error "delete requires -Id"; exit 1 }
    $vecs = Load-Vectors
    $newVecs = $vecs | Where-Object { $_.id -ne $Id }
    $removed = $vecs.Count - $newVecs.Count
    Save-Vectors $newVecs
    Write-Output "deleted $removed entries"
  }

  "list" {
    $vecs = Load-Vectors
    $list = $vecs | Select-Object id, text, created, @{N="metadata";E={$_.metadata}} | Sort-Object created -Descending
    Write-Output ($list | ConvertTo-Json -Depth 3)
  }

  "stats" {
    $vecs = Load-Vectors
    $stats = @{ total = $vecs.Count; model = $Model; file = $VectorFile }
    Write-Output ($stats | ConvertTo-Json -Compress)
  }

  "reindex" {
    $vecs = Load-Vectors
    $count = $vecs.Count
    foreach ($v in $vecs) {
      try {
        $v.embedding = Get-Embedding $v.text
      } catch { Write-Warning "reindex failed for $($v.id): $_" }
    }
    Save-Vectors $vecs
    Write-Output "reindexed $count entries"
  }
}
