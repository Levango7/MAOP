param(
  [Parameter(Mandatory=$true)]
  [string]$Agent,
  [Parameter(Mandatory=$true)]
  [string]$Task,
  [string]$TaskFile = "",
  [string]$RoutingKey,
  [string]$WorkDir,
  [int]$TimeoutSeconds = 180,
  [string]$TraceID = ""
)

# Load Task from file if TaskFile provided (to avoid command-line length limits)
if ($TaskFile -and -not $Task) {
  $Task = Get-Content $TaskFile -Raw -ErrorAction Stop
}

# ════════════════════════════════════════════════════════════
# T2.7: 统一 Error Schema
# ════════════════════════════════════════════════════════════
. (Join-Path (Split-Path $PSCommandPath -Parent) "error-schema.ps1")

# ════════════════════════════════════════════════════════════
# T2.1: 进程隔离 — 直连 delegate-plugin.ps1，由 -JobMode 在内部实现隔离
# ════════════════════════════════════════════════════════════

# 生成 trace_id
if (-not $TraceID) {
  $TraceID = [System.Guid]::NewGuid().ToString().Substring(0, 8)
}

$pluginScript = Join-Path (Split-Path $PSCommandPath -Parent) "delegate-plugin.ps1"

$startTime = Get-Date
$stdout = ""
$stderr = ""
$errorMsg = $null
$exitCode = 0

try {
  # 将 Task 写入临时文件（避免命令行过长）
  $pluginTaskFile = [System.IO.Path]::GetTempFileName()
  try {
    [System.IO.File]::WriteAllText($pluginTaskFile, $Task, [System.Text.Encoding]::UTF8)

    # ════════════════════════════════════════════════════════════
    # T2.1: 直连 delegate-plugin.ps1，由 -JobMode 实现进程隔离
    # ════════════════════════════════════════════════════════════
    $rawOutput = & $pluginScript -Agent $Agent -TaskFile $pluginTaskFile -RoutingKey $RoutingKey -WorkDir $WorkDir -TimeoutSeconds $TimeoutSeconds -TraceID $TraceID -JobMode 2>&1 | Out-String

    # 解析 JSON 输出
    if ($rawOutput) {
      $jsonStart = $rawOutput.IndexOf('{')
      $jsonText = if ($jsonStart -ge 0) { $rawOutput.Substring($jsonStart) } else { $rawOutput }
      try {
        $parsed = $jsonText | ConvertFrom-Json -ErrorAction Stop
        $stdout = if ($parsed.stdout) { $parsed.stdout } else { "" }
        $stderr = if ($parsed.stderr) { $parsed.stderr } else { "" }
        $errorMsg = if ($parsed.error -and $parsed.error -ne "null") { $parsed.error } else { $null }
        $exitCode = if ($null -ne $parsed.exit_code) { [int]$parsed.exit_code } else { 0 }
      } catch {
        # 不是有效 JSON，视为原始 stdout
        $stdout = $rawOutput
      }
    }

  } finally {
    Remove-Item $pluginTaskFile -Force -ErrorAction SilentlyContinue
  }

} catch {
  $errorMsg = "DELEGATE_CRASH: $($_.Exception.Message)"
  $stderr = $errorMsg
  $exitCode = -2
}

$endTime = Get-Date
$duration = [math]::Round(($endTime - $startTime).TotalMilliseconds)

# ════════════════════════════════════════════════════════════
# T2.7: 统一 Error Schema 输出
# ════════════════════════════════════════════════════════════
$result = New-ResultObject -Agent $Agent -Task $Task -ExitCode $exitCode -Stdout $stdout -Stderr $stderr -ErrMsg $errorMsg -DurationMs $duration -TraceID $TraceID
$result | ConvertTo-Json -Depth 3 -Compress
