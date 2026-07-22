#benchmark-compare.ps1 — Gate: benchmark comparison (pass if exit_code=0)
param([string]$ResultJson, [string]$PlanJson, [string]$WorkDir)
$result = $ResultJson | ConvertFrom-Json
$passed = ($result.exit_code -eq 0)
$detail = if ($passed) { "Benchmark OK" } else { "Benchmark FAIL: exit_code=$($result.exit_code)" }
@{ passed = $passed; detail = $detail } | ConvertTo-Json -Compress
