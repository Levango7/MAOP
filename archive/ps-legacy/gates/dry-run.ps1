#dry-run.ps1 — Gate: dry-run validation (no side effects)
param([string]$ResultJson, [string]$PlanJson, [string]$WorkDir)
$result = $ResultJson | ConvertFrom-Json
$passed = ($result.exit_code -eq 0)
$detail = if ($passed) { "Dry-run OK: no errors" } else { "Dry-run FAIL: exit_code=$($result.exit_code)" }
@{ passed = $passed; detail = $detail } | ConvertTo-Json -Compress
