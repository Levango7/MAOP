$totalPass = 0; $totalFail = 0
Get-ChildItem "F:\Nexus\MAOP\tests\*.tests.ps1" | ForEach-Object {
    $r = Invoke-Pester -Path $_.FullName -Output Minimal -PassThru 2>&1
    Write-Host ("{0}: {1} passed, {2} failed" -f $_.Name, $r.PassedCount, $r.FailedCount)
    $totalPass += $r.PassedCount
    $totalFail += $r.FailedCount
}
Write-Host "`nTOTAL: $totalPass passed, $totalFail failed"
