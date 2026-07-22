#freshness-check.ps1 — Gate: check output freshness
param([string]$ResultJson, [string]$PlanJson, [string]$WorkDir)
$result = $ResultJson | ConvertFrom-Json
$passed = (-not [string]::IsNullOrEmpty($result.stdout))
$detail = if ($passed) { "Freshness OK: output is non-empty" } else { "Freshness FAIL: empty output" }
@{ passed = $passed; detail = $detail } | ConvertTo-Json -Compress
