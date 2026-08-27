<#
.SYNOPSIS
    48 小时长稳（Soak）测试一键启动 / 状态查看 / 停止脚本。

.DESCRIPTION
    在后台启动 48h soak 测试（py/tests/soak/soak_test.py --duration 172800），
    将 stdout / stderr 重定向到日志文件，记录 PID 便于后续监控与停止。
    支持 -Status 查看运行状态并打印日志尾部，支持 -Stop 停止正在运行的测试。

.USAGE
    启动 48h 测试：  powershell -ExecutionPolicy Bypass -File .\scripts\run_soak_48h.ps1
    查看运行状态：  powershell -ExecutionPolicy Bypass -File .\scripts\run_soak_48h.ps1 -Status
    停止测试：      powershell -ExecutionPolicy Bypass -File .\scripts\run_soak_48h.ps1 -Stop

.NOTES
    日志目录：deliverables/soak-48h-logs/
    PID 文件：deliverables/soak-48h-logs/soak_48h.pid
    预计耗时：48 小时（172800 秒）
#>

param(
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = "Stop"

# ===================== 路径与参数配置 =====================
$ProjectRoot = "F:\Nexus\MAOP"
$SoakScript  = Join-Path $ProjectRoot "py\tests\soak\soak_test.py"
$LogDir      = Join-Path $ProjectRoot "deliverables\soak-48h-logs"
$PidFile     = Join-Path $LogDir "soak_48h.pid"
$Duration    = 172800   # 48 小时 = 172800 秒

# 确保日志目录存在
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# 校验 soak 脚本是否存在
if (-not (Test-Path $SoakScript)) {
    Write-Host "[ERROR] 未找到 soak 测试脚本：$SoakScript" -ForegroundColor Red
    exit 1
}

# ===================== 辅助函数 =====================
function Read-PidFromFile {
    if (-not (Test-Path $PidFile)) { return $null }
    $raw = (Get-Content $PidFile -Raw).Trim()
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
    try { return [int]$raw } catch { return $null }
}

function Test-PidAlive($targetPid) {
    if (-not $targetPid) { return $false }
    $p = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
    return ($null -ne $p)
}

# ===================== Status 模式 =====================
if ($Status) {
    Write-Host "========== 48h Soak 测试状态 ==========" -ForegroundColor Cyan
    $currentPid = Read-PidFromFile
    if (-not $currentPid) {
        Write-Host "[INFO] 未找到 PID 文件，测试尚未启动或已清理。" -ForegroundColor Yellow
    }
    elseif (Test-PidAlive $currentPid) {
        $proc = Get-Process -Id $currentPid -ErrorAction SilentlyContinue
        Write-Host "[RUNNING] 测试正在运行" -ForegroundColor Green
        Write-Host ("  PID          : {0}" -f $currentPid)
        Write-Host ("  进程启动时间 : {0}" -f $proc.StartTime.ToString('yyyy-MM-dd HH:mm:ss'))
        $elapsed = (Get-Date) - $proc.StartTime
        Write-Host ("  已运行       : {0} 天 {1} 时 {2} 分 {3} 秒" -f $elapsed.Days, $elapsed.Hours, $elapsed.Minutes, $elapsed.Seconds)
        $remainSec = [Math]::Max(0, $Duration - $elapsed.TotalSeconds)
        $remainTs = [TimeSpan]::FromSeconds($remainSec)
        Write-Host ("  预计剩余     : {0} 天 {1} 时 {2} 分 {3} 秒" -f $remainTs.Days, $remainTs.Hours, $remainTs.Minutes, $remainTs.Seconds)
        Write-Host ("  预计完成     : {0}" -f (Get-Date).AddSeconds($remainSec).ToString('yyyy-MM-dd HH:mm:ss'))
    }
    else {
        Write-Host "[STOPPED] PID $currentPid 进程不存在，测试可能已结束或被终止。" -ForegroundColor Yellow
    }

    # 显示最新日志最后 20 行
    $latestLog = Get-ChildItem -Path $LogDir -Filter "soak_*.log" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latestLog) {
        Write-Host ""
        Write-Host ("--- 日志尾部（{0}）最后 20 行 ---" -f $latestLog.Name) -ForegroundColor Cyan
        Get-Content $latestLog.FullName -Tail 20 -ErrorAction SilentlyContinue
    }
    else {
        Write-Host "[INFO] 暂无日志文件。" -ForegroundColor Yellow
    }
    return
}

# ===================== Stop 模式 =====================
if ($Stop) {
    Write-Host "========== 停止 48h Soak 测试 ==========" -ForegroundColor Cyan
    $currentPid = Read-PidFromFile
    if (-not $currentPid) {
        Write-Host "[INFO] 未找到 PID 文件，无需停止。" -ForegroundColor Yellow
        return
    }
    if (Test-PidAlive $currentPid) {
        # 终止进程树（先尝试子进程 python，再终止父进程）
        try {
            $cimProc = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                Where-Object { $_.ParentProcessId -eq $currentPid }
            foreach ($child in $cimProc) {
                Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
                Write-Host ("  已终止子进程 PID: {0}" -f $child.ProcessId) -ForegroundColor DarkGray
            }
        } catch { }
        Stop-Process -Id $currentPid -Force -ErrorAction SilentlyContinue
        Write-Host ("[OK] 已停止测试进程 PID: {0}" -f $currentPid) -ForegroundColor Green
    }
    else {
        Write-Host ("[INFO] PID {0} 进程不存在，仅清理 PID 文件。" -f $currentPid) -ForegroundColor Yellow
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] PID 文件已清理。" -ForegroundColor Green
    return
}

# ===================== 启动模式 =====================
Write-Host "========== 启动 48h Soak 长稳测试 ==========" -ForegroundColor Cyan

# 防止重复启动
$existingPid = Read-PidFromFile
if ($existingPid -and (Test-PidAlive $existingPid)) {
    Write-Host ("[WARN] 测试已在运行（PID: {0}），请先执行 -Stop 再启动。" -f $existingPid) -ForegroundColor Yellow
    return
}
# 清理失效的 PID 文件
if (Test-Path $PidFile) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }

# 时间戳与日志路径
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile   = Join-Path $LogDir "soak_$timestamp.log"
$ErrFile   = Join-Path $LogDir "soak_$timestamp.err"

$startTime = Get-Date
$endTime   = $startTime.AddSeconds($Duration)

# 启动后台进程：stdout -> .log，stderr -> .err
try {
    $proc = Start-Process -FilePath "python" `
        -ArgumentList "`"$SoakScript`" --duration $Duration" `
        -RedirectStandardOutput $LogFile `
        -RedirectStandardError $ErrFile `
        -PassThru -NoNewWindow
}
catch {
    Write-Host "[ERROR] 启动 soak 测试失败：$($_.Exception.Message)" -ForegroundColor Red
    exit 2
}

# 记入 PID 文件
"$($proc.Id)" | Out-File -FilePath $PidFile -Encoding ascii -Force

Write-Host "[OK] 48h 长稳测试已在后台启动" -ForegroundColor Green
Write-Host ("  脚本路径     : {0}" -f $SoakScript)
Write-Host ("  PID          : {0}" -f $proc.Id)
Write-Host ("  日志(stdout) : {0}" -f $LogFile)
Write-Host ("  日志(stderr) : {0}" -f $ErrFile)
Write-Host ("  PID 文件     : {0}" -f $PidFile)
Write-Host ("  测试时长     : 48 小时（172800 秒）")
Write-Host ("  开始时间     : {0}" -f $startTime.ToString('yyyy-MM-dd HH:mm:ss'))
Write-Host ("  预计完成     : {0}" -f $endTime.ToString('yyyy-MM-dd HH:mm:ss'))
Write-Host ""
Write-Host "提示：使用 -Status 查看进度，使用 -Stop 停止测试。" -ForegroundColor DarkGray