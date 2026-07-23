# MAOP Dashboard 启动脚本 (Python FastAPI)
$ErrorActionPreference = 'Stop'

param(
    [Parameter(Mandatory=$false)]
    [int]$Port = 9079,

    [Parameter(Mandatory=$false)]
    [switch]$ForceRestart
)

$pevRoot = "F:/Nexus/MAOP"
$python = "C:\Users\winge\.workbuddy\binaries\python\versions\3.13.12\python.exe"

if (-not (Test-Path $python)) {
    Write-Error "Python not found: $python"
    exit 1
}

# Kill existing dashboard process if ForceRestart
if ($ForceRestart) {
    Get-Process -Name python -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'MAOP.dashboard' } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

$env:PYTHONPATH = "$pevRoot\py"
$env:PEV_DASH_PORT = "$Port"

Write-Host "MAOP Dashboard v3.2 -> http://localhost:$Port" -ForegroundColor Cyan

& $python -c "import sys; sys.path.insert(0, r'$pevRoot\py'); from MAOP.dashboard.server import app; import uvicorn; uvicorn.run(app, host='0.0.0.0', port=$Port, log_level='info')"
