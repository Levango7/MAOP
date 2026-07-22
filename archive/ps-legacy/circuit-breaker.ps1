# MAOP circuit-breaker — thin wrapper for backward compatibility
# Now uses circuit-breaker.psm1 as the canonical module
<#
  P0-3 状态源统一（遗留标注）：
    熔断状态唯一真源已迁移至 MAOP.db 的 circuit_breaker_state 表
    （Python 的 MAOP.core.circuit_breaker.CircuitBreaker）。
    circuit-breaker.json 仅为兼容 PS 侧读取的【遗留镜像】，不再作为权威写入方。
#>
$modulePath = Join-Path $PSScriptRoot "circuit-breaker.psm1"
if (Test-Path $modulePath) {
  Import-Module $modulePath -Force
} else {
  # Fallback: inline the module content
  $script:PEV_Root = Split-Path $PSScriptRoot
  $script:BreakerFile = $script:PEV_Root + "\data\circuit-breaker.json"

  function Initialize-BreakerFile {
    if (-not (Test-Path $script:BreakerFile)) {
      $dir = Split-Path $script:BreakerFile -Parent
      if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
      $agents = @("claude","kimi","codewhale","autoclaw","codex","qwenpaw","qoder","openclaw","mimo","cursor","hermes","kilo","mavis","doc-pipeline")
      $init = @{}
      foreach ($a in $agents) { $init[$a] = @{ state = "closed"; failures = 0; threshold = 3; last_failure = $null; cooldown_s = 60 } }
      $init | ConvertTo-Json -Depth 3 | Set-Content $script:BreakerFile
    }
  }
  function Get-BreakerState { param([Parameter(Mandatory=$true)][string]$AgentName)
    Initialize-BreakerFile
    if (-not (Test-Path $script:BreakerFile)) { return $null }
    $content = Get-Content $script:BreakerFile -Raw -ErrorAction SilentlyContinue
    if ($content) { $data = $content | ConvertFrom-Json; if ($data.PSObject.Properties.Name -contains $AgentName) { return $data.$AgentName } }
    return $null
  }
  function Set-BreakerState { param([Parameter(Mandatory=$true)][string]$AgentName, [Parameter(Mandatory=$true)][ValidateSet("closed","open","half-open")][string]$State, [int]$Failures=-1, [string]$LastFailure="", [int]$Threshold=-1)
    Initialize-BreakerFile
    $data = @{}
    if (Test-Path $script:BreakerFile) { $content = Get-Content $script:BreakerFile -Raw -ErrorAction SilentlyContinue; if ($content) { $parsed = $content | ConvertFrom-Json; foreach ($prop in $parsed.PSObject.Properties) { $data[$prop.Name] = @{}; foreach ($ip in $prop.Value.PSObject.Properties) { $data[$prop.Name][$ip.Name] = $ip.Value } } } }
    if (-not $data.ContainsKey($AgentName)) { $data[$AgentName] = @{ state="closed"; failures=0; threshold=3; last_failure=$null; cooldown_s=60 } }
    $data[$AgentName].state = $State
    if ($Failures -ge 0) { $data[$AgentName].failures = $Failures }
    if ($Threshold -ge 0) { $data[$AgentName].threshold = $Threshold }
    if ($LastFailure -eq "") { $data[$AgentName].last_failure = $null } elseif ($LastFailure) { $data[$AgentName].last_failure = $LastFailure }
    $data | ConvertTo-Json -Depth 3 | Set-Content $script:BreakerFile -Force
  }
  Initialize-BreakerFile
}
