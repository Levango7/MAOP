# MAOP filelock — thin wrapper for backward compatibility
# Now uses filelock.psm1 as the canonical module
$modulePath = Join-Path $PSScriptRoot "filelock.psm1"
if (Test-Path $modulePath) {
  Import-Module $modulePath -Force
} else {
  # Fallback: inline definition (for environments where .psm1 is unavailable)
  function Invoke-WithFileLock {
    param(
      [Parameter(Mandatory = $true)]
      [string]$Path,
      [Parameter(Mandatory = $true)]
      [scriptblock]$Script,
      [int]$TimeoutSeconds = 5
    )
    $lockFile = $Path + ".lock"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $orphanThresholdSeconds = 30
    while ((Get-Date) -lt $deadline) {
      if (Test-Path $lockFile) {
        $lockAge = (Get-Date) - (Get-Item $lockFile).LastWriteTime
        if ($lockAge.TotalSeconds -gt $orphanThresholdSeconds) {
          Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
        }
      }
      try {
        $lockContent = @{ pid = $PID; host = $env:COMPUTERNAME; timestamp = (Get-Date -Format "o") } | ConvertTo-Json -Compress
        $lockStream = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($lockContent)
        $lockStream.Write($bytes, 0, $bytes.Length)
        $lockStream.Close()
        break
      } catch [System.IO.IOException] { Start-Sleep -Milliseconds 200 }
    }
    if ((Get-Date) -ge $deadline -and (Test-Path $lockFile)) {
      Write-Error "[filelock] Timeout: $lockFile"
      throw "[filelock] Timeout: $lockFile"
    }
    try { & $Script } finally { Remove-Item $lockFile -Force -ErrorAction SilentlyContinue }
  }
}
