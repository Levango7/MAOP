$t=$null;$e=$null;[System.Management.Automation.Language.Parser]::ParseFile('$PSScriptRoot\delegate.ps1',[ref]$t,[ref]$e)
Write-Host "delegate.ps1 errors: $($e.Count)"
$t=$null;$e=$null;[System.Management.Automation.Language.Parser]::ParseFile('$PSScriptRoot\delegate-plugin.ps1',[ref]$t,[ref]$e)
Write-Host "delegate-plugin.ps1 errors: $($e.Count)"
