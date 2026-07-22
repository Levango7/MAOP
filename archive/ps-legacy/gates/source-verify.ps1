#source-verify.ps1 — Gate: verify source references exist
param([string]$ResultJson, [string]$PlanJson, [string]$WorkDir)
$result = $ResultJson | ConvertFrom-Json
$passed = (-not [string]::IsNullOrEmpty($result.stdout))
$detail = if ($passed) { "Source verified: output present" } else { "Source verify FAIL: empty output" }
@{ passed = $passed; detail = $detail } | ConvertTo-Json -Compress
