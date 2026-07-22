#security-scan.ps1 — Gate: basic security pattern check on output
param(
  [string]$ResultJson,
  [string]$PlanJson,
  [string]$WorkDir
)
$result = $ResultJson | ConvertFrom-Json
$stdout = $result.stdout

$securityPatterns = @(
  '(?i)password\s*=\s*["\]'']',
  '(?i)api[_-]?key\s*=\s*["\]'']',
  '(?i)secret\s*=\s*["\]'']',
  '(?i)token\s*=\s*["\]'']',
  '(?i)eval\s*\(',
  '(?i)exec\s*\(',
  '(?i)os\.system\s*\(',
  '(?i)subprocess\.call\s*\(',
  '(?i)rm\s+-rf\s+/',
  '(?i)del\s+/[fqs]\s+'
)

$found = @()
foreach ($pattern in $securityPatterns) {
  if ($stdout -match $pattern) { $found += $pattern }
}

$passed = ($found.Count -eq 0)
$detail = if ($passed) { "No security-sensitive patterns detected" } else { "Security patterns found: " + ($found -join ", ") }

@{ passed = $passed; detail = $detail } | ConvertTo-Json -Compress
