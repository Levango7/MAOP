#lint.ps1 — Gate: basic lint check on output
param([string]$ResultJson, [string]$PlanJson, [string]$WorkDir)
$result = $ResultJson | ConvertFrom-Json
$stdout = $result.stdout
$issues = @()
if ($stdout -match '(?i)\t\s+\t') { $issues += "mixed tabs and spaces" }
if ($stdout -match '(?i)\s{80,}') { $issues += "excessive trailing whitespace" }
$passed = ($issues.Count -eq 0)
$detail = if ($passed) { "Lint OK" } else { ($issues -join "; ") }
@{ passed = $passed; detail = $detail } | ConvertTo-Json -Compress
