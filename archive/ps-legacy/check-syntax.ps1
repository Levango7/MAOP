$errors = $null
$tokens = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('F:\Nexus\MAOP\src\doctor.ps1', [ref]$tokens, [ref]$errors)
if ($errors.Count -eq 0) {
  Write-Host "Syntax OK"
} else {
  foreach ($e in $errors) { Write-Host $e.Message }
}
