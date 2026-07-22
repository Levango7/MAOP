#quality-gate.ps1 — Gate: quality metrics check
param(
  [string]$ResultJson,
  [string]$PlanJson,
  [string]$WorkDir
)
$result = $ResultJson | ConvertFrom-Json
$stdout = $result.stdout
$exitCode = $result.exit_code

$issues = @()
$minLength = 50
if ($stdout.Length -lt $minLength) { $issues += "output too short ($($stdout.Length) < $minLength chars)" }
if ($exitCode -ne 0) { $issues += "non-zero exit code: $exitCode" }
if ($stdout -match '(?i)^(todo|fixme|hack|placeholder)') { $issues += "output starts with placeholder marker" }
if ($stdout -match '(?i)\bundefined\b|\bnull\b' -and $stdout.Length -lt 200) {
  $issues += "output contains undefined/null in short response"
}

$passed = ($issues.Count -eq 0)
$detail = if ($passed) { "Quality OK: length=$($stdout.Length), exit_code=0, no placeholders" } else { ($issues -join "; ") }

@{ passed = $passed; detail = $detail } | ConvertTo-Json -Compress
