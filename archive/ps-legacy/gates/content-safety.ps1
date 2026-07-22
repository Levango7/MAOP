#content-safety.ps1 — Gate: content safety check
param([string]$ResultJson, [string]$PlanJson, [string]$WorkDir)
$result = $ResultJson | ConvertFrom-Json
$stdout = $result.stdout
$unsafePatterns = @('(?i)<script', '(?i)javascript:', '(?i)onerror\s*=', '(?i)<iframe')
$found = @()
foreach ($p in $unsafePatterns) { if ($stdout -match $p) { $found += $p } }
$passed = ($found.Count -eq 0)
$detail = if ($passed) { "Content safety OK" } else { "Unsafe patterns: " + ($found -join ", ") }
@{ passed = $passed; detail = $detail } | ConvertTo-Json -Compress
