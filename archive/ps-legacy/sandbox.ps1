param(
  [ValidateSet("create","run","cleanup","list","info","config")]
  [string]$Action = "create",
  [string]$SandboxId = "",
  [string]$Command = "",
  [int]$TimeoutSeconds = 30,
  [int]$MaxOutputLines = 500,
  [string]$WorkDir = "",
  [string]$SandboxDir = ""
)

$ScriptDir = Split-Path $PSCommandPath -Parent
if (-not $SandboxDir) { $SandboxDir = Join-Path (Split-Path $ScriptDir -Parent) "data\sandboxes" }
if (-not (Test-Path $SandboxDir)) { New-Item -ItemType Directory -Force -Path $SandboxDir | Out-Null }

function Load-Index {
  $idxFile = Join-Path $SandboxDir "index.json"
  if (Test-Path $idxFile) { try { return @((Get-Content $idxFile -Raw | ConvertFrom-Json).sandboxes) } catch { return @() } }
  return @()
}
function Save-Index($s) {
  $idxFile = Join-Path $SandboxDir "index.json"
  @{ sandboxes = @($s) } | ConvertTo-Json -Depth 3 -Compress | Set-Content $idxFile -Encoding utf8
}

switch ($Action) {
  "create" {
    $id = if ($SandboxId) { $SandboxId } else { "sb-$(Get-Date -Format 'yyyyMMdd-HHmmss')-$([guid]::NewGuid().ToString().Substring(0,8))" }
    # 路径安全：校验外部输入的 SandboxId
    if ($SandboxId -and $SandboxId -notmatch '^[A-Za-z0-9_-]+$') { Write-Error "Invalid SandboxId: $SandboxId"; exit 1 }
    $sbDir = Join-Path $SandboxDir $id
    New-Item -ItemType Directory -Force -Path $sbDir | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $sbDir "input") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $sbDir "output") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $sbDir "temp") | Out-Null
    $entry = @{ id = $id; created = (Get-Date -Format "o"); status = "active"; path = $sbDir }
    $index = New-Object System.Collections.ArrayList
    foreach ($s in @(Load-Index)) { $null = $index.Add($s) }
    $null = $index.Add($entry)
    Save-Index $index
    Write-Output (@{ id = $id; path = $sbDir; status = "active" } | ConvertTo-Json)
  }

  "run" {
    if (-not $Command) { Write-Error "run requires -Command"; exit 1 }
    $dir = if ($WorkDir) { $WorkDir } else { (Join-Path $SandboxDir (Get-Date -Format "yyyyMMdd")) }
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $logFile = Join-Path $dir "sandbox-run-$(Get-Date -Format 'HHmmss').log"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
      $output = & powershell -NoProfile -Command $Command 2>&1 | Out-String
      $sw.Stop()
      $lines = $output -split "`n"
      if ($lines.Count -gt $MaxOutputLines) { $output = ($lines[0..$MaxOutputLines] -join "`n") + "`n... [truncated $($lines.Count - $MaxOutputLines) lines]" }
      $output | Out-File $logFile -Encoding utf8
      Write-Output (@{ ok = $true; exit_code = $LASTEXITCODE; duration_ms = $sw.ElapsedMilliseconds; output_lines = $lines.Count; log = $logFile } | ConvertTo-Json)
    } catch {
      $sw.Stop()
      Write-Output (@{ ok = $false; error = $_.Exception.Message; duration_ms = $sw.ElapsedMilliseconds; log = $logFile } | ConvertTo-Json)
    }
  }

  "cleanup" {
    $index = New-Object System.Collections.ArrayList
    $removed = 0
    $cutoff = (Get-Date).AddHours(-24)
    foreach ($s in @(Load-Index)) {
      $created = if ($s.created -is [string]) { [datetime]::Parse($s.created) } else { $s.created }
      if ($created -lt $cutoff) {
        if (Test-Path $s.path) { Remove-Item $s.path -Recurse -Force -ErrorAction SilentlyContinue }
        $removed++
      } else { $null = $index.Add($s) }
    }
    Save-Index $index
    Write-Output "cleaned $removed sandboxes older than 24h"
  }

  "list" {
    $index = @(Load-Index)
    $active = @($index | Where-Object { $_.status -eq "active" })
    Write-Output (@{ total = $index.Count; active = $active.Count; list = @($index | Select-Object id, status, created) } | ConvertTo-Json -Depth 2)
  }

  "info" {
    if (-not $SandboxId) { Write-Error "info requires -SandboxId"; exit 1 }
    $sb = @(Load-Index) | Where-Object { $_.id -eq $SandboxId }
    if (-not $sb) { Write-Error "sandbox not found: $SandboxId"; exit 1 }
    $size = 0
    if (Test-Path $sb.path) { $size = (Get-ChildItem $sb.path -Recurse -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum }
    Write-Output (@{ id = $sb.id; status = $sb.status; created = $sb.created; path = $sb.path; size_bytes = $size } | ConvertTo-Json)
  }

  "config" {
    Write-Output (@{
      sandbox_dir = $SandboxDir; default_timeout = $TimeoutSeconds; max_output_lines = $MaxOutputLines
      auto_cleanup_hours = 24; max_sandboxes = 50
    } | ConvertTo-Json)
  }
}
