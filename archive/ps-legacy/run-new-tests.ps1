Import-Module Pester -ErrorAction SilentlyContinue
if (-not (Get-Module Pester -ErrorAction SilentlyContinue)) {
  Write-Host "Installing Pester..."; Install-Module Pester -Force -Scope CurrentUser -SkipPublisherCheck
  Import-Module Pester
}

$results = @()

# Test memory
Write-Host "`n=== memory.tests.ps1 ==="
$r1 = Invoke-Pester -Path "F:\Nexus\MAOP\tests\memory.tests.ps1" -Output Normal -PassThru
$results += @{ Name = "memory"; Passed = $r1.PassedCount; Failed = $r1.FailedCount }

# Test log-rotate
Write-Host "`n=== log-rotate.tests.ps1 ==="
$r2 = Invoke-Pester -Path "F:\Nexus\MAOP\tests\log-rotate.tests.ps1" -Output Normal -PassThru
$results += @{ Name = "log-rotate"; Passed = $r2.PassedCount; Failed = $r2.FailedCount }

Write-Host "`n=== SUMMARY ==="
foreach ($r in $results) {
  Write-Host "  $($r.Name): $($r.Passed) passed, $($r.Failed) failed"
}
