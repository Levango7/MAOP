#syntax-check.ps1 — Gate: basic syntax validation
param([string]$ResultJson, [string]$PlanJson, [string]$WorkDir)
$result = $ResultJson | ConvertFrom-Json
$stdout = $result.stdout
$exitCode = $result.exit_code
$passed = ($exitCode -eq 0 -and -not [string]::IsNullOrEmpty($stdout))
$detail = if ($passed) { "Syntax OK: exit_code=0, output present" } else { "Syntax FAIL: exit_code=$exitCode or empty output" }
@{ passed = $passed; detail = $detail } | ConvertTo-Json -Compress
