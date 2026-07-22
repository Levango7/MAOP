#consistency-check.ps1 — Gate: verify output logical consistency
param(
  [string]$ResultJson,
  [string]$PlanJson,
  [string]$WorkDir
)
$result = $ResultJson | ConvertFrom-Json
$stdout = $result.stdout
$exitCode = $result.exit_code

$issues = @()
if ($exitCode -ne 0) { $issues += "exit_code=$exitCode (expected 0)" }
if ([string]::IsNullOrEmpty($stdout)) { $issues += "stdout is empty" }
if ($stdout -match '(?i)error|exception|traceback|fail') {
  $issues += "output contains error indicators"
}

$passed = ($issues.Count -eq 0)
$detail = if ($passed) { "Consistency OK: exit_code=0, output non-empty, no error patterns" } else { ($issues -join "; ") }

@{ passed = $passed; detail = $detail } | ConvertTo-Json -Compress
