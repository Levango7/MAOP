<#
.SYNOPSIS
  MAOP Verify — 三层验证器
.DESCRIPTION
  验证执行结果是否通过 Plan 中定义的 gates：
    1. Gate "exit_code" — result.exit_code == 0
    2. Gate "output"    — result.stdout 非空
    3. Custom gates     — 从 plan.gates 读取，调用 gates/<gate>.ps1 执行自定义验证
.PARAMETER PlanJson
  Plan 阶段的 JSON 输出（含 .gates 数组）
.PARAMETER ResultJson
  执行阶段的 JSON 输出（含 .exit_code, .stdout, .stderr）
.PARAMETER WorkDir
  工作目录（可选，传递给 gate 脚本的环境变量）
.EXAMPLE
  powershell -NoProfile -File F:\Nexus\MAOP\src\MAOP-verify.ps1 -PlanJson '{"gates":[]}' -ResultJson '{"exit_code":0,"stdout":"hello"}'
#>

param(
  [Parameter(Mandatory = $true)]
  [string]$PlanJson,

  [Parameter(Mandatory = $true)]
  [string]$ResultJson,

  [Parameter(Mandatory = $false)]
  [string]$WorkDir = ""
)

$ScriptDir = Split-Path $PSCommandPath -Parent
$GatesDir = Join-Path $ScriptDir "gates"

# ── SQLite checkpoint support ──
$PEV_HasSQLite = $false
$dbScript = Join-Path $ScriptDir "database.ps1"
if (Test-Path $dbScript) {
  $__saved_DbAction = $global:DbAction
  $__saved_Sql = $global:Sql
  $__saved_DbPath = $global:DbPath
  try {
    . $dbScript
    $PEV_HasSQLite = $script:PEV_HasSQLite
  } catch {
    Write-Verbose "[verify] SQLite not available: $_"
  } finally {
    $global:DbAction = $__saved_DbAction
    $global:Sql = $__saved_Sql
    $global:DbPath = $__saved_DbPath
    Remove-Variable -Name __saved_DbAction,__saved_Sql,__saved_DbPath -ErrorAction SilentlyContinue
  }
}

# ── 解析输入 ──
try {
  $plan = $PlanJson | ConvertFrom-Json
} catch {
  Write-Error "Failed to parse PlanJson: $_"
  $result = @{ phase = "verify"; passed = $false; summary = "PlanJson parse error: $($_.Exception.Message)"; gates = @() }
  $result | ConvertTo-Json -Depth 3
  exit 1
}

try {
  $result = $ResultJson | ConvertFrom-Json
} catch {
  Write-Error "Failed to parse ResultJson: $_"
  $result = @{ phase = "verify"; passed = $false; summary = "ResultJson parse error: $($_.Exception.Message)"; gates = @() }
  $result | ConvertTo-Json -Depth 3
  exit 1
}

# ── 收集所有需要执行的 gate ──
$gatesToRun = @()

# 内置 gate: exit_code
$gatesToRun += @{
  gate   = "exit_code"
  script = $null   # 内置，无需脚本文件
}

# 内置 gate: output
$gatesToRun += @{
  gate   = "output"
  script = $null
}

# 自定义 gates — 从 plan.gates 数组读取
if ($plan.gates -and ($plan.gates -is [array]) -and $plan.gates.Count -gt 0) {
  foreach ($g in $plan.gates) {
    # 跳过已内置的 gate
    if ($g -in @("exit_code", "output")) { continue }

    # 路径安全：校验 gate name 不含路径穿越字符
    if ($g -notmatch '^[a-zA-Z0-9_-]+$') {
      Write-Warning "[verify] Invalid gate name rejected: $g"; continue
    }

    # 构造 gate 脚本路径
    $gateScript = Join-Path $GatesDir "$g.ps1"

    $gatesToRun += @{
      gate   = $g
      script = $gateScript
    }
  }
}

# ── 执行验证 ──
$gateResults = @()
$allPassed = $true

foreach ($gt in $gatesToRun) {
  $gateName = $gt.gate
  $gateFile = $gt.script

  $gatePassed = $false
  $gateDetail = ""

  if ($null -eq $gateFile) {
    # ── 内置 gate ──
    switch ($gateName) {
      "exit_code" {
        $ec = if ($null -ne $result.exit_code) { [int]$result.exit_code } else { -1 }
        $gatePassed = ($ec -eq 0)
        $gateDetail = if ($gatePassed) { "exit_code=0" } else { "exit_code=$ec, expected 0" }
      }
      "output" {
        $hasStdout = (-not [string]::IsNullOrEmpty($result.stdout))
        $gatePassed = $hasStdout
        $gateDetail = if ($gatePassed) { "stdout is non-empty" } else { "stdout is empty or null" }
      }
    }
  } else {
    # ── 自定义 gate: 调用 gates/<gate>.ps1 ──
    if (-not (Test-Path $gateFile)) {
      Write-Warning "[verify] Gate script NOT FOUND for '$gateName': $gateFile — treating as FAILED (not silently passed)"
      $gatePassed = $false
      $gateDetail = "FAILED — gate script not found: $gateFile"
    } else {
      try {
        # 将结果对象转为 JSON 传给 gate 脚本
        $resultJsonParam = $ResultJson
        $planJsonParam = $PlanJson

        # 执行 gate 脚本并捕获输出
        $gateOutput = & $gateFile -ResultJson $resultJsonParam -PlanJson $planJsonParam -WorkDir $WorkDir 2>&1

        # 解析 gate 输出：优先尝试 JSON，否则视为文本
        $parsed = $null
        try { $parsed = $gateOutput | Out-String | ConvertFrom-Json } catch {}

        if ($parsed -and ($null -ne $parsed.passed)) {
          $gatePassed = [bool]$parsed.passed
          $gateDetail = if ($parsed.detail) { $parsed.detail } else { $gateOutput | Out-String }
        } else {
          # 非 JSON 输出：按退出码判断（exit code from script）
          $gatePassed = ($LASTEXITCODE -eq 0)
          $gateDetail = $gateOutput | Out-String
        }
      } catch {
        $gatePassed = $false
        $gateDetail = "ERROR: $($_.Exception.Message)"
      }
    }
  }

  $gateResults += @(@{ gate = $gateName; passed = $gatePassed; detail = $gateDetail })
  if (-not $gatePassed) { $allPassed = $false }
}

# ── 构造输出 ──
$verifyOutput = @{
  phase   = "verify"
  passed  = $allPassed
  summary = if ($allPassed) { "ALL PASS" } else { "SOME FAILED" }
  gates   = $gateResults
}

# ── Save verify checkpoint to SQLite ──
if ($PEV_HasSQLite) {
  $verifyPlanId = if ($plan.id) { $plan.id } else { "verify-$(Get-Date -Format 'yyyyMMddHHmmss')" }
  try {
    Save-DbCheckpoint -Agent "verify" -Task $verifyPlanId -Phase "verify-done" -State @{
      passed = $allPassed
      summary = $verifyOutput.summary
      gates = @($gateResults)
      timestamp = (Get-Date -Format "o")
    } | Out-Null
  } catch {
    Write-Warning "SQLite verify checkpoint save failed: $_"
  }
}

$verifyOutput | ConvertTo-Json -Depth 3
