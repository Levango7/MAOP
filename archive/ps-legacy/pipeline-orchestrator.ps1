<#
.SYNOPSIS
    MAOP Pipeline Orchestrator — 专门调用 doc-pipeline 的 Plan→Execute→Verify
.DESCRIPTION
    用于生成技术文档、文章等需要 doc-pipeline 完整流水线的任务。
    Plan: 识别任务类型，准备参数
    Execute: 调用 pipeline-wrapper.ps1 运行 5-agent 11-step 流水线
    Verify: 解析 JSON 输出，检查 output_path 存在、quality.pass=true

.PARAMETER Task
    任务描述（自然语言），如 "生成一份 Python 异步编程基础教程"
.PARAMETER PlanAgent
    Plan 阶段使用的 agent（默认知
.PARAMETER TimeoutSeconds
    单步超时
.PARAMETER LogFile
    日志文件路径
.OUTPUTS
    JSON 到 stdout，含 passed/verification/output_path
#>

param(
  [Parameter(Mandatory=$true, Position=0)]
  [string]$Task,

  [string]$PlanAgent = "openclaw",
  [int]$TimeoutSeconds = 300,
  [string]$LogFile = ""
)

$ScriptDir = Split-Path $PSCommandPath -Parent
$Delegate = Join-Path (Split-Path $ScriptDir -Parent) "delegate.ps1"
$ObsScript = Join-Path (Split-Path $ScriptDir -Parent) "src\observability.ps1"
if (-not $LogFile) { $LogFile = Join-Path (Split-Path $ScriptDir -Parent) "logs\pipeline-orchestrator.jsonl" }
$LogDir = Split-Path $LogFile -Parent; if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }

function Log-Event($phase, $agent, $status, $detail, $ms) {
  $entry = @{
    timestamp = (Get-Date -Format "o"); phase = $phase; agent = $agent
    task = $Task; status = $status; detail = $detail; duration_ms = $ms
  }
  Add-Content -Path $LogFile -Value ($entry | ConvertTo-Json -Compress) -Encoding utf8
}

function Invoke-Agent($agent, $prompt, $timeout) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  try {
    $json = & powershell -NoProfile -File $Delegate -Agent $agent -Task $prompt -TimeoutSeconds $timeout 2>&1 | Out-String
    $result = $json | ConvertFrom-Json
    $sw.Stop()
    $ok = $result.exit_code -eq 0 -and -not [string]::IsNullOrWhiteSpace($result.stdout)
    return @{ ok = $ok; output = $result.stdout; ms = $sw.ElapsedMilliseconds; raw = $result }
  } catch {
    $sw.Stop()
    return @{ ok = $false; output = $_.Exception.Message; ms = $sw.ElapsedMilliseconds; raw = $null }
  }
}

# ── Plan: 分析任务，确定是否需要 doc-pipeline，准备参数 ──
$planPrompt = @"
You are a pipeline planner. Analyze this task and determine if it requires the doc-pipeline (technical document generation workflow).
Task: $Task

If it needs doc-pipeline, output ONLY a JSON object with:
{
  "use_pipeline": true,
  "topic": "extracted topic for doc-pipeline",
  "pipeline": "doc",
  "queries": ["additional search queries if any"],
  "output_hint": "suggested output filename.md"
}

If NOT doc-pipeline task, output:
{
  "use_pipeline": false,
  "reason": "why not needed"
}
"@

$planResult = Invoke-Agent $PlanAgent $planPrompt $TimeoutSeconds
Log-Event "plan" $PlanAgent $(if ($planResult.ok) { "ok" } else { "fail" }) $planResult.output $planResult.ms
if (-not $planResult.ok) { Write-Output ($planResult | ConvertTo-Json); exit 1 }

$planJson = $planResult.output | ConvertFrom-Json
if (-not $planJson.use_pipeline) {
  Write-Host "Task does not require doc-pipeline: $($planJson.reason)" -ForegroundColor Yellow
  $result = @{
    task = $Task; plan_agent = $PlanAgent; worker = "none"; eval_agent = "none"
    plan = $planResult.output; output = ""; verification = "SKIPPED: not a doc-pipeline task"
    passed = $false; attempts = 0; max_attempts = 1
    plan_ms = $planResult.ms; exec_ms = 0; verify_ms = 0
    total_ms = $planResult.ms; output_path = ""
  }
  Write-Output ($result | ConvertTo-Json -Depth 3)
  exit 0
}

# 提取参数
$topic = $planJson.topic
$pipeline = $planJson.pipeline
$queries = $planJson.queries
$outputHint = $planJson.output_hint

# 默认输出路径
$pevOutput = Join-Path (Split-Path $ScriptDir -Parent) "output"
if (-not (Test-Path $pevOutput)) { New-Item -ItemType Directory -Path $pevOutput | Out-Null }
$outputFile = if ($outputHint) { Join-Path $pevOutput ([System.IO.Path]::GetFileName($outputHint)) } else { Join-Path $pevOutput "doc-output-$(Get-Date -Format 'yyyyMMdd-HHmmss').md" }

# ── Execute: 调用 pipeline-wrapper.ps1 ──
$execPrompt = "生成技术文档: $topic"
$pipelineWrapper = Join-Path $ScriptDir "pipeline-wrapper.ps1"
$sw = [Diagnostics.Stopwatch]::StartNew()
try {
  $execArgs = @(
    "-Task", $execPrompt,
    "-Output", $outputFile,
    "-Pipeline", $pipeline,
    "-Queries", $queries,
    "-JsonOutput"
  )
  $json = & powershell -NoProfile -File $pipelineWrapper @execArgs 2>&1 | Out-String
  $execResult = $json | ConvertFrom-Json
  $sw.Stop()
  $execMs = $sw.ElapsedMilliseconds
  $ok = $execResult.exit_code -eq 0 -and ($execResult.status -eq "completed" -or $execResult.status -eq "success")
} catch {
  $sw.Stop()
  $execResult = @{ exit_code = -1; stdout = ""; stderr = $_.Exception.Message; output_path = ""; status = "error"; steps = @() }
  $execMs = $sw.ElapsedMilliseconds
  $ok = $false
}
Log-Event "execute" "doc-pipeline" $(if ($ok) { "ok" } else { "fail" }) ($execResult.stdout + "`n" + $execResult.stderr) $execMs

# ── Verify: 检查输出文件存在、大小、质量 ──
$verifyPrompt = @"
Verify the doc-pipeline execution result:
Task: $Topic
Pipeline output: $($execResult.stdout)
Exit code: $($execResult.exit_code)
Status: $($execResult.status)
Output path: $($execResult.output_path)
Steps: $($execResult.steps | ConvertTo-Json)

Reply ONLY 'PASS' or 'FAIL' with a one-line reason.
Criteria: exit_code=0, status in [completed,success], output_path file exists and > 1KB.
"@
$verifyResult = Invoke-Agent $PlanAgent $verifyPrompt $TimeoutSeconds
Log-Event "verify" $PlanAgent $(if ($verifyResult.ok) { "ok" } else { "fail" }) $verifyResult.output $verifyResult.ms
$passed = $verifyResult.ok -and $verifyResult.output -match 'PASS'

# ── 额外的客观验证：文件是否真实存在 ──
$fileExists = $execResult.output_path -and (Test-Path $execResult.output_path)
$fileSize = if ($fileExists) { (Get-Item $execResult.output_path).Length } else { 0 }
$sizeOk = $fileSize -gt 1024  # > 1KB

$finalPassed = $passed -and $fileExists -and $sizeOk

# ── Report ──
$result = @{
  task = $Task; plan_agent = $PlanAgent; worker = "doc-pipeline"; eval_agent = $PlanAgent
  plan = $planResult.output; output = $execResult.stdout; verification = $verifyResult.output
  passed = $finalPassed; attempts = 1; max_attempts = 1
  plan_ms = $planResult.ms; exec_ms = $execMs; verify_ms = $verifyResult.ms
  total_ms = $planResult.ms + $execMs + $verifyResult.ms
  output_path = $execResult.output_path
  pipeline_status = $execResult.status
  pipeline_steps = $execResult.steps
  file_size_bytes = $fileSize
}
Write-Output ($result | ConvertTo-Json -Depth 3)

# Log to observability
if (Test-Path $ObsScript) {
  $passCode = if ($finalPassed) { 0 } else { 1 }
  $r = @{ exit_code = $passCode; stdout = if ($finalPassed) { "PASS: $($execResult.output_path)" } else { "FAIL: $($verifyResult.output)" }; duration_ms = $result.total_ms }
  & $ObsScript -Action "log" -Agent "pipeline-orchestrator" -Task "run:$Task" -ResultJson ($r | ConvertTo-Json) -RoutingKey "orchestrator" 2>&1 | Out-Null
}

exit (if ($finalPassed) { 0 } else { 1 })