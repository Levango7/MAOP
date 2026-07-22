#connectivity-check.ps1 — Gate: connectivity verification
param([string]$ResultJson, [string]$PlanJson, [string]$WorkDir)
$result = $ResultJson | ConvertFrom-Json
$passed = ($result.exit_code -eq 0 -and -not [string]::IsNullOrEmpty($result.stdout))
$detail = if ($passed) { "Connectivity OK" } else { "Connectivity FAIL: exit_code=$($result.exit_code)" }
@{ passed = $passed; detail = $detail } | ConvertTo-Json -Compress
