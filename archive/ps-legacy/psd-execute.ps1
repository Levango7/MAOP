param(
  [string]$Agent = "",
  [string]$Task = "",
  [string]$RoutingKey = "codegen",
  [string]$WorkDir = (Get-Location).Path,
  [int]$TimeoutSeconds = 120,
  [string]$TraceID = ""
)
$execScript = Join-Path (Split-Path $PSCommandPath -Parent) "MAOP-execute.ps1"
& $execScript @PSBoundParameters
