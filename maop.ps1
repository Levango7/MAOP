<#
.SYNOPSIS
    MAOP (Plan-Execute-Verify) Main Entry Point
.DESCRIPTION
    统一入口：start/stop/status/restart dashboard，以及 run 任务执行
.PARAMETER Action
    start|stop|status|restart|run
.PARAMETER Task
    任务描述（Action=run 时必需）
.PARAMETER Mode
    执行模式: standard|pipeline（默认 standard）
.PARAMETER PlanAgent
    Plan 阶段 agent（默认 openclaw）
.PARAMETER WorkerAgent
    Execute 阶段 agent（默认按 routing 解析）
.PARAMETER EvalAgent
    Verify 阶段 agent（默认 kimi）
.PARAMETER TimeoutSeconds
    超时秒数
.EXAMPLE
    .\MAOP.ps1 -Action run -Task "生成 Python 异步编程教程" -Mode pipeline
    .\MAOP.ps1 -Action run -Task "重构 utils.py" -Mode standard
    .\MAOP.ps1 -Action status
#>

param(
  [ValidateSet("start","stop","status","restart","run","validate","doctor")]
  [string]$Action = "status",
  
  [string]$Task = "",
  
  [ValidateSet("standard","pipeline")]
  [string]$Mode = "standard",
  
  [string]$PlanAgent = "openclaw",
  [string]$WorkerAgent = "",
  [string]$EvalAgent = "kimi",
  [int]$TimeoutSeconds = 120
)

$MAOP = Split-Path $PSCommandPath -Parent
$logDir = "$MAOP\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

# -- Python-first routing + deprecation notice --
$pyEntry = Join-Path $MAOP "py\MAOP\cli.py"
if (Test-Path $pyEntry) {
    if ($Action -eq "run") {
        Write-Host "[MAOP] Python engine (PS layer deprecated, EOL: v4.0)" -ForegroundColor DarkYellow
        $pyDir = Join-Path $MAOP "py"
        $pyArgs = @("-m", "MAOP.cli", "run", "--task", $Task)
        if ($Mode -eq "pipeline") { $pyArgs += "--mode"; $pyArgs += "pipeline" }
        if ($PlanAgent) { $pyArgs += "--plan-agent"; $pyArgs += $PlanAgent }
        if ($WorkerAgent) { $pyArgs += "--worker-agent"; $pyArgs += $WorkerAgent }
        if ($EvalAgent) { $pyArgs += "--eval-agent"; $pyArgs += $EvalAgent }
        $pyArgs += "--timeout"; $pyArgs += $TimeoutSeconds
        Push-Location $pyDir
        & python @pyArgs
        Pop-Location
        return
    }
    if ($Action -eq "validate") {
        Write-Host "[MAOP] Python config validation" -ForegroundColor DarkYellow
        Push-Location (Join-Path $MAOP "py")
        & python -m MAOP.cli validate-config
        Pop-Location
        return
    }
    if ($Action -eq "doctor") {
        Write-Host "[MAOP] Python health check" -ForegroundColor DarkYellow
        Push-Location (Join-Path $MAOP "py")
        & python -m MAOP.cli health-check
        Pop-Location
        return
    }
} else {
    Write-Host "[MAOP] ERROR: Python engine not found at $pyEntry. PS scripts have been archived to archive/ps-legacy/." -ForegroundColor Red
    Write-Host "[MAOP] Install Python 3.10+ and ensure py/MAOP/ is accessible." -ForegroundColor Red
    exit 1
}

switch ($Action) {
  "start" {
    Write-Host "[MAOP] Starting dashboard..."
    $dashLogOut = "$logDir\dashboard.out.log"
    $dashLogErr = "$logDir\dashboard.err.log"
    # Canonical entry: python -m maop.dashboard.server (from py/ dir)
    $pyDir = Join-Path $MAOP "py"
    $env:MAOP_DASH_PORT = "9079"
    Start-Process -NoNewWindow -FilePath "python" -ArgumentList "-m","maop.dashboard.server" -WorkingDirectory $pyDir -RedirectStandardOutput $dashLogOut -RedirectStandardError $dashLogErr
    Write-Host "[MAOP] Dashboard -> http://localhost:9079"
  }
  "stop" {
    Write-Host "[MAOP] Stopping..."
    Get-WmiObject Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match "MAOP\.dashboard\.server" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Host "[MAOP] Stopped"
  }
  "status" {
    $dash = Get-WmiObject Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match "MAOP\.dashboard\.server" }
    if ($dash) { Write-Host "[MAOP] Dashboard: RUNNING (PID $($dash.ProcessId))" } else { Write-Host "[MAOP] Dashboard: STOPPED" }
    $files = @("config\agents.yaml","py\MAOP\dashboard\server.py","py\MAOP\maop_loop.py","py\MAOP\cli.py")
    $missing = @(); foreach ($f in $files) { if (-not (Test-Path "$MAOP\$f")) { $missing += $f } }
    if ($missing.Count -eq 0) { Write-Host "[MAOP] Files: ALL PRESENT" } else { Write-Host "[MAOP] Files: MISSING $($missing -join ', ')" }
  }
  "restart" { & $MyInvocation.MyCommand.Path -Action stop; Start-Sleep 2; & $MyInvocation.MyCommand.Path -Action start }
  "validate" {
    Write-Host "[MAOP] PS validate-config.ps1 archived. Use: python -m MAOP.cli validate-config" -ForegroundColor Yellow
  }
  "doctor" {
    Write-Host "[MAOP] PS doctor.ps1 archived. Use: python -m MAOP.cli health-check" -ForegroundColor Yellow
  }
  "run" {
    if (-not $Task) { Write-Error "Task is required for run action"; exit 1 }
    Write-Host "[MAOP] PS MAOP-loop.ps1 archived. Use: python -m MAOP.cli run --task `"$Task`"" -ForegroundColor Yellow
  }
}