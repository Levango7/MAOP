param(
  [Parameter(Mandatory=$true)]
  [string]$Agent,
  [Parameter(Mandatory=$true)]
  [string]$Task,
  [string]$RoutingKey = "codegen",
  [string]$WorkDir,
  [int]$TimeoutSeconds = 120,
  [string]$TraceID = ""
)

# ════════════════════════════════════════════════════════════
# T2.7: 统一 Error Schema
# ════════════════════════════════════════════════════════════
. (Join-Path (Split-Path $PSCommandPath -Parent) "error-schema.ps1")

$ScriptDir = Split-Path $PSCommandPath -Parent
$GuardrailScript = Join-Path $ScriptDir "guardrail.ps1"
$DelegateScript  = Join-Path $ScriptDir "delegate.ps1"
$ObservabilityScript = Join-Path $ScriptDir "observability.ps1"

# ── Step 1: Guardrail check ──
$guardrailTaskFile = [System.IO.Path]::GetTempFileName()
try {
  [System.IO.File]::WriteAllText($guardrailTaskFile, $Task, [System.Text.Encoding]::UTF8)
  $guardrailResult = & powershell -NoProfile -File $GuardrailScript -Action check -ContentFile $guardrailTaskFile -Agent $Agent -TaskFile $guardrailTaskFile 2>&1 | Out-String
} finally {
  Remove-Item $guardrailTaskFile -Force -ErrorAction SilentlyContinue
}
$guardrail = $guardrailResult | ConvertFrom-Json

if (-not $guardrail.passed) {
  $result = New-ResultObject -Agent $Agent -Task $Task -ExitCode -2 -RoutingKey $RoutingKey -Stderr "Guardrail check failed: $($guardrail.summary)" -TraceID $TraceID
  $result | ConvertTo-Json -Depth 3
  exit -2
}

# ── Step 2: Execute via delegate ──
$sw = [System.Diagnostics.Stopwatch]::StartNew()

$delegateTaskFile = [System.IO.Path]::GetTempFileName()
try {
  [System.IO.File]::WriteAllText($delegateTaskFile, $Task, [System.Text.Encoding]::UTF8)

  $delegateArgs = @("-Agent", $Agent, "-TaskFile", $delegateTaskFile, "-TimeoutSeconds", $TimeoutSeconds)
  if ($RoutingKey) { $delegateArgs += "-RoutingKey"; $delegateArgs += $RoutingKey }
  if ($WorkDir)    { $delegateArgs += "-WorkDir";    $delegateArgs += $WorkDir }

  $rawOutput = & powershell -NoProfile -File $DelegateScript @delegateArgs 2>&1 | Out-String
  $exitCode = $LASTEXITCODE
  $stdout = $rawOutput
  $stderr = ""
}
catch {
  $exitCode = -1
  $stdout = ""
  $stderr = "Delegate execution threw exception: $_"
}
finally {
  Remove-Item $delegateTaskFile -Force -ErrorAction SilentlyContinue
  $sw.Stop()
}

$durationMs = [math]::Round($sw.Elapsed.TotalMilliseconds)

# ── Step 3: Build result object (unified schema) ──
$result = New-ResultObject -Agent $Agent -Task $Task -ExitCode $exitCode -Stdout $stdout -Stderr $stderr -DurationMs $durationMs -TraceID $TraceID -RoutingKey $RoutingKey

# ── Step 4: Log to observability ──
$resultJson = ($result | ConvertTo-Json -Depth 3 -Compress)
$obsTaskFile = [System.IO.Path]::GetTempFileName()
try {
  [System.IO.File]::WriteAllText($obsTaskFile, $Task, [System.Text.Encoding]::UTF8)
  & powershell -NoProfile -File $ObservabilityScript -Action log -Agent $Agent -TaskFile $obsTaskFile -ResultJson $resultJson -RoutingKey $RoutingKey 2>&1 | Out-Null
} finally {
  Remove-Item $obsTaskFile -Force -ErrorAction SilentlyContinue
}

# ── Step 5: Output result JSON ──
$result | ConvertTo-Json -Depth 3
