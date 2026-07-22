<#
.SYNOPSIS
  MAOP Log Rotation — prevent unbounded log growth

.DESCRIPTION
  Rotates log files (.log, .jsonl, .json) when they exceed a size threshold.
  Rotated files are renamed with a timestamp suffix; old rotations beyond
  retention count are deleted.  Designed to be called from MAOP-loop at the
  start of each cycle or ad-hoc from CLI.

.PARAMETER LogDir
  Directory containing log files (default: <project>/logs).

.PARAMETER DataDir
  Directory containing data/log files (default: <project>/data).

.PARAMETER MaxSizeKB
  File size threshold in KB to trigger rotation (default: 512 KB).

.PARAMETER RetainCount
  Number of rotated backups to keep per file (default: 5).

.PARAMETER Compress
  Gzip-compress rotated files (requires .NET GZipStream; default: false).

.PARAMETER DryRun
  Report what would happen without modifying files.

.PARAMETER Quiet
  Suppress informational output; only emit warnings/errors.

.EXAMPLE
  .\log-rotate.ps1 -MaxSizeKB 256 -RetainCount 3
  .\log-rotate.ps1 -DryRun
#>
param(
  [string]$LogDir,
  [string]$DataDir,
  [int]$MaxSizeKB = 512,
  [int]$RetainCount = 5,
  [switch]$Compress,
  [switch]$DryRun,
  [switch]$Quiet
)

$ErrorActionPreference = "Continue"

# ── Resolve directories ──
$ToolDir  = Split-Path $PSCommandPath -Parent
$ProjRoot = Split-Path $ToolDir -Parent
if (-not $LogDir)  { $LogDir  = Join-Path $ProjRoot "logs" }
if (-not $DataDir) { $DataDir = Join-Path $ProjRoot "data" }

$MaxSizeBytes = $MaxSizeKB * 1024

# ── Extensions to rotate ──
$RotateExtensions = @(".log", ".jsonl", ".json")

function Write-Status($msg) {
  if (-not $Quiet) { Write-Host $msg }
}

# ════════════════════════════════════════
# Compress a file using GZipStream
# ════════════════════════════════════════
function Compress-LogFile($sourcePath) {
  $destPath = "$sourcePath.gz"
  try {
    $sourceStream = [System.IO.File]::OpenRead($sourcePath)
    $destStream   = [System.IO.File]::Create($destPath)
    $gzipStream   = New-Object System.IO.Compression.GZipStream($destStream, [System.IO.Compression.CompressionMode]::Compress)
    $sourceStream.CopyTo($gzipStream)
    $gzipStream.Close(); $destStream.Close(); $sourceStream.Close()
    return $true
  } catch {
    Write-Warning "[log-rotate] Compress failed for $sourcePath : $_"
    if ($sourceStream) { $sourceStream.Close() }
    if ($destStream)   { $destStream.Close() }
    return $false
  }
}

# ════════════════════════════════════════
# Rotate a single file
# ════════════════════════════════════════
function Invoke-RotateFile($filePath) {
  if (-not (Test-Path $filePath)) { return }

  $file = Get-Item $filePath
  if ($file.Length -lt $MaxSizeBytes) { return }

  $baseName  = $file.BaseName
  $ext       = $file.Extension
  $dir       = $file.DirectoryName
  $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $rotatedName = "${baseName}_${timestamp}${ext}"
  $rotatedPath = Join-Path $dir $rotatedName

  if ($DryRun) {
    Write-Status "[log-rotate] DRY: Would rotate $($file.Name) ($([math]::Round($file.Length/1KB,1)) KB) -> $rotatedName"
    return
  }

  try {
    # Rename current file to timestamped backup
    Move-Item -Path $filePath -Destination $rotatedPath -Force -ErrorAction Stop
    Write-Status "[log-rotate] Rotated $($file.Name) -> $rotatedName"

    # Optionally compress the rotated file
    if ($Compress) {
      if (Compress-LogFile $rotatedPath) {
        Remove-Item $rotatedPath -Force -ErrorAction SilentlyContinue
        Write-Status "[log-rotate] Compressed -> ${rotatedName}.gz"
      }
    }

    # Recreate empty file so append operations don't fail
    # For .jsonl and .log, empty file is fine.
    # For .json, we need to be careful — only recreate if it was a log-style JSON (array/lines)
    # delegations.json is a JSON array; MAOP-loop.jsonl is line-delimited.
    # We recreate as empty which is safe for append-style logs.
    if ($ext -eq ".jsonl" -or $ext -eq ".log") {
      New-Item -ItemType File -Path $filePath -Force | Out-Null
    }
    # For .json files that are log-style (delegations.json), recreate as empty array
    if ($ext -eq ".json") {
      "[]" | Set-Content $filePath -Encoding utf8
    }
  } catch {
    Write-Warning "[log-rotate] Failed to rotate $($file.Name): $_"
  }
}

# ════════════════════════════════════════
# Clean up old rotations beyond retention count
# ════════════════════════════════════════
function Remove-OldRotations($dir) {
  if (-not (Test-Path $dir)) { return }

  # Group rotated files by their base name (before the _timestamp suffix)
  # Pattern: basename_YYYYMMDD-HHMMSS.ext  or  basename_YYYYMMDD-HHMMSS.ext.gz
  $rotatedFiles = Get-ChildItem $dir -File -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -match '^(.+)_\d{8}-\d{6}\.(log|jsonl|json)(\.gz)?$'
  }

  # Group by original base name + extension
  $groups = @{}
  foreach ($f in $rotatedFiles) {
    if ($f.Name -match '^(.+)_\d{8}-\d{6}(\.(log|jsonl|json)(\.gz)?)$') {
      $key = "$($Matches[1])$($Matches[2])"
      if (-not $groups[$key]) { $groups[$key] = @() }
      $groups[$key] += $f
    }
  }

  foreach ($key in $groups.Keys) {
    $sorted = $groups[$key] | Sort-Object LastWriteTime -Descending
    if ($sorted.Count -le $RetainCount) { continue }
    $toDelete = $sorted | Select-Object -Skip $RetainCount
    foreach ($f in $toDelete) {
      if ($DryRun) {
        Write-Status "[log-rotate] DRY: Would delete old rotation $($f.Name)"
      } else {
        Remove-Item $f.FullName -Force -ErrorAction SilentlyContinue
        Write-Status "[log-rotate] Deleted old rotation: $($f.Name)"
      }
    }
  }
}

# ════════════════════════════════════════
# Main: scan directories and rotate
# ════════════════════════════════════════
$dirs = @()
if (Test-Path $LogDir)  { $dirs += $LogDir }
if (Test-Path $DataDir) { $dirs += $DataDir }

$totalRotated = 0

foreach ($d in $dirs) {
  $candidates = Get-ChildItem $d -File -ErrorAction SilentlyContinue | Where-Object {
    $RotateExtensions -contains $_.Extension.ToLower()
  }

  foreach ($f in $candidates) {
    $sizeBefore = $f.Length
    Invoke-RotateFile $f.FullName
    # Check if rotation happened (file is now smaller or gone)
    if (Test-Path $f.FullName) {
      $sizeAfter = (Get-Item $f.FullName).Length
      if ($sizeAfter -lt $sizeBefore) { $totalRotated++ }
    } else {
      $totalRotated++
    }
  }

  # Clean old rotations
  Remove-OldRotations $d
}

if ($totalRotated -gt 0) {
  Write-Status "[log-rotate] Complete: $totalRotated file(s) rotated"
} elseif (-not $Quiet) {
  Write-Status "[log-rotate] No files exceeded ${MaxSizeKB}KB threshold"
}
