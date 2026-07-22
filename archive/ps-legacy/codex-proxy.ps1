param(
  [ValidateSet("start","stop","status","install")]
  [string]$Action = "status"
)

# Codex StepFun Proxy — Management Script
# Port 18080, converts Codex Responses API ↔ StepFun Chat Completions API

$proxyFile = "$env:TEMP\codex-proxy.js"
$logFile   = "$env:TEMP\codex-proxy.log"
$pidFile   = "$env:TEMP\codex-proxy.pid"
$port      = 18080

function Get-ProxyProcess {
  $processes = Get-Process -Name "node" -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -match "codex-proxy" -or $_.Id -eq (Get-Content $pidFile -ErrorAction SilentlyContinue)
  }
  return $processes
}

switch ($Action) {
  "start" {
    $existing = Get-ProxyProcess
    if ($existing) {
      Write-Host "[proxy] Already running (PID $($existing.Id)) on port $port"
      return
    }
    if (-not (Test-Path $proxyFile)) {
      Write-Error "[proxy] Proxy script not found: $proxyFile"
      exit 1
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "node"
    $psi.Arguments = "`"$proxyFile`""
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    Start-Sleep -Seconds 2
    if ($p -and -not $p.HasExited) {
      $p.Id | Out-File -FilePath $pidFile -Encoding ascii
      Write-Host "[proxy] Started (PID $($p.Id)) on port $port"
      Write-Host "[proxy] Log: $logFile"
    } else {
      Write-Error "[proxy] Failed to start"
    }
  }

  "stop" {
    $existing = Get-ProxyProcess
    if ($existing) {
      $existing | Stop-Process -Force
      Write-Host "[proxy] Stopped (PID $($existing.Id))"
    } else {
      Write-Host "[proxy] Not running"
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
  }

  "status" {
    $existing = Get-ProxyProcess
    if ($existing) {
      $url = "http://127.0.0.1:$port/v1/models"
      try {
        $resp = Invoke-RestMethod -Uri $url -TimeoutSec 3 -ErrorAction Stop
        $models = ($resp.data | ForEach-Object { $_.id }) -join ", "
        Write-Host "[proxy] RUNNING (PID $($existing.Id), port $port)"
        Write-Host "[proxy] Models: $models"
      } catch {
        Write-Host "[proxy] RUNNING (PID $($existing.Id), port $port) — but not responding"
      }
    } else {
      Write-Host "[proxy] STOPPED"
    }
  }

  "install" {
    $shortcut = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\codex-proxy.bat"
    $content = "@echo off`r`nstart /B node `"$proxyFile`""
    $content | Out-File -FilePath $shortcut -Encoding ascii
    Write-Host "[proxy] Startup shortcut installed: $shortcut"
    Write-Host "[proxy] Will auto-start on next login"
    & $PSCommandPath -Action start
  }
}
