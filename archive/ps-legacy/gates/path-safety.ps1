#path-safety.ps1 — Gate: path traversal safety check
param([string]$ResultJson, [string]$PlanJson, [string]$WorkDir)
$result = $ResultJson | ConvertFrom-Json
$stdout = $result.stdout
$dangerous = @('\.\.\\', '\.\./', '(?i)/etc/passwd', '(?i)C:\\Windows\\System32')
$found = @()
foreach ($p in $dangerous) { if ($stdout -match $p) { $found += $p } }
$passed = ($found.Count -eq 0)
$detail = if ($passed) { "Path safety OK: no traversal patterns" } else { "Dangerous paths: " + ($found -join ", ") }
@{ passed = $passed; detail = $detail } | ConvertTo-Json -Compress
