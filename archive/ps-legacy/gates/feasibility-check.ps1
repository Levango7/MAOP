#feasibility-check.ps1 — Gate: feasibility assessment
param([string]$ResultJson, [string]$PlanJson, [string]$WorkDir)
$result = $ResultJson | ConvertFrom-Json
$stdout = $result.stdout
$passed = ($result.exit_code -eq 0 -and $stdout.Length -gt 20)
$detail = if ($passed) { "Feasible: exit_code=0, output length=$($stdout.Length)" } else { "Feasibility FAIL: exit_code=$($result.exit_code), length=$($stdout.Length)" }
@{ passed = $passed; detail = $detail } | ConvertTo-Json -Compress
