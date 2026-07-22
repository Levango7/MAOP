# MAOP error-schema — thin wrapper for backward compatibility
# Now uses error-schema.psm1 as the canonical module
$modulePath = Join-Path $PSScriptRoot "error-schema.psm1"
if (Test-Path $modulePath) {
  Import-Module $modulePath -Force
} else {
  # Fallback: inline the module content
  function New-ResultObject {
    param([Parameter(Mandatory=$true)][string]$Agent, [Parameter(Mandatory=$true)][string]$Task, [int]$ExitCode=0, [string]$Stdout="", [string]$Stderr="", $ErrMsg=$null, [int]$DurationMs=0, [string]$TraceID="", [string]$RoutingKey="", [string]$Driver=$null, [string]$Model=$null)
    return @{ ok=($ExitCode -eq 0 -and -not $ErrMsg); exit_code=$ExitCode; stdout=$Stdout; stderr=$Stderr; error=$ErrMsg; duration_ms=$DurationMs; agent=$Agent; task=$Task; trace_id=$TraceID; routing_key=$RoutingKey; driver=$Driver; start_time=(Get-Date -Format "o"); model=$Model }
  }
  function Test-ResultSuccess { param([Parameter(Mandatory=$true)][System.Collections.Hashtable]$Result) return ($Result.exit_code -eq 0 -and -not $Result.error) }
  function Format-ResultError { param([Parameter(Mandatory=$true)][System.Collections.Hashtable]$Result, [switch]$IncludeDetails)
    $ec = if ($null -ne $Result.exit_code) { $Result.exit_code } else { "?" }
    $msg = "[MAOP-$ec] Agent='$($Result.agent)' Task='$($Result.task)'"
    if ($Result.error) { $msg += " — $($Result.error)" }
    if ($Result.duration_ms -ge 0) { $msg += " ($($Result.duration_ms)ms)" }
    if ($IncludeDetails) { if ($Result.stderr) { $msg += "`nstderr: $($Result.stderr)" }; if ($Result.stdout) { $msg += "`nstdout: $($Result.stdout)" } }
    return $msg
  }
}
