Write-Host "=== Test 1: Dot-source guard ==="
. $PSScriptRoot\delegate-plugin.ps1
Write-Host "Test 1 PASS: Dot-source loaded OK (no Agent/Task, defines functions only)"

Write-Host "`n=== Test 2: Unknown agent via Direct mode (should return error JSON) ==="
$result = & $PSScriptRoot\delegate-plugin.ps1 -Agent nonexistent -Task "hello" -Direct 2>&1 | Out-String
$result = $result.Trim()
Write-Host "Output:"
$result
Write-Host "`nIs valid JSON? "
try { $null = $result | ConvertFrom-Json; Write-Host "YES" } catch { Write-Host "NO - $($_.Exception.Message)" }

Write-Host "`n=== Test 3: Unknown agent via JobMode (should return error JSON via job) ==="
$result2 = & $PSScriptRoot\delegate-plugin.ps1 -Agent nonexistent -Task "hello" -JobMode 2>&1 | Out-String
$result2 = $result2.Trim()
Write-Host "Output:"
$result2
Write-Host "`nIs valid JSON? "
try { $null = $result2 | ConvertFrom-Json; Write-Host "YES" } catch { Write-Host "NO - $($_.Exception.Message)" }

Write-Host "`n=== All tests complete ==="
