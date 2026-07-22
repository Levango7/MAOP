param(
  [string]$Task = "",
  [string]$WorkDir = (Get-Location).Path,
  [string]$RoutingKey = ""
)
$planScript = Join-Path (Split-Path $PSCommandPath -Parent) "MAOP-plan.ps1"
& $planScript @PSBoundParameters
