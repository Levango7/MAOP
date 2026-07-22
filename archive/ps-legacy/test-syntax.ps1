$files = @(
    'src/MAOP-execute.ps1',
    'src/guardrail.ps1',
    'src/delegate.ps1',
    'src/delegate-plugin.ps1'
)
foreach ($f in $files) {
    $t=$null;$e=$null
    [System.Management.Automation.Language.Parser]::ParseFile($f,[ref]$t,[ref]$e)
    "$f : errors=$($e.Count)"
}
