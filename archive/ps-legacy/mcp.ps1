param(
  [ValidateSet("add-server","remove-server","list-servers","list-tools","call-tool","server-info","refresh")]
  [string]$Action = "list-servers",
  [string]$ServerId = "",
  [string]$Name = "",
  [string]$Command = "",
  [string]$Args = "",
  [string]$Transport = "stdio", # stdio | sse
  [string]$Endpoint = "", # SSE endpoint URL
  [string]$ToolName = "",
  [string]$ToolArgs = "{}",
  [int]$TimeoutSeconds = 30,
  [string]$MCPFile = ""
)

$ScriptDir = Split-Path $PSCommandPath -Parent
if (-not $MCPFile) { $MCPFile = Join-Path (Split-Path $ScriptDir -Parent) "data\mcp-servers.json" }
$MCPDir = Split-Path $MCPFile -Parent; if (-not (Test-Path $MCPDir)) { New-Item -ItemType Directory -Force -Path $MCPDir | Out-Null }

function Load-Servers {
  if (Test-Path $MCPFile) { try { return @((Get-Content $MCPFile -Raw | ConvertFrom-Json).servers) } catch { return @() } }
  return @()
}
function Save-Servers($s) {
  @{ servers = @($s) } | ConvertTo-Json -Depth 3 -Compress | Set-Content $MCPFile -Encoding utf8
}

$mcpExe = "npx.cmd"

switch ($Action) {
  "add-server" {
    if (-not $ServerId -or -not $Command) { Write-Error "add-server requires -ServerId and -Command"; exit 1 }
    $servers = New-Object System.Collections.ArrayList
    foreach ($s in @(Load-Servers)) { if ($s.id -ne $ServerId) { $null = $servers.Add($s) } }
    $null = $servers.Add(@{
      id = $ServerId; name = if ($Name) { $Name } else { $ServerId }
      command = $Command; args = $Args; transport = $Transport
      endpoint = $Endpoint; status = "registered"; added = (Get-Date -Format "o")
    })
    Save-Servers $servers
    Write-Output ("mcp server added: " + $ServerId)
  }

  "remove-server" {
    if (-not $ServerId) { Write-Error "remove-server requires -ServerId"; exit 1 }
    $servers = New-Object System.Collections.ArrayList
    foreach ($s in @(Load-Servers)) { if ($s.id -ne $ServerId) { $null = $servers.Add($s) } }
    Save-Servers $servers
    Write-Output ("removed: " + $ServerId)
  }

  "list-servers" {
    $servers = @(Load-Servers)
    $result = $servers | Select-Object id, name, transport, status, @{N="tools";E={if ($_.tools) { @($_.tools).Count } else { 0 }}}
    Write-Output ($result | ConvertTo-Json -Depth 2)
  }

  "list-tools" {
    $servers = @(Load-Servers)
    $allTools = @()
    foreach ($s in $servers) {
      if ($s.tools) {
        foreach ($t in @($s.tools)) {
          $allTools += @{ server = $s.id; name = $t.name; description = $t.description }
        }
      }
    }
    Write-Output ($allTools | ConvertTo-Json -Depth 2)
  }

  "call-tool" {
    if (-not $ServerId -or -not $ToolName) { Write-Error "call-tool requires -ServerId and -ToolName"; exit 1 }
    $server = @(Load-Servers) | Where-Object { $_.id -eq $ServerId }
    if (-not $server) { Write-Error "server not found: $ServerId"; exit 1 }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
      $args = @($server.command) + @($server.args -split ' ') + @("--tool", $ToolName, "--args", $ToolArgs)
      $output = & $args 2>&1 | Out-String
      $sw.Stop()
      Write-Output (@{ ok = $true; server = $ServerId; tool = $ToolName; output = $output.Trim(); duration_ms = $sw.ElapsedMilliseconds } | ConvertTo-Json)
    } catch {
      $sw.Stop()
      Write-Output (@{ ok = $false; error = $_.Exception.Message; duration_ms = $sw.ElapsedMilliseconds } | ConvertTo-Json)
    }
  }

  "server-info" {
    if (-not $ServerId) { Write-Error "server-info requires -ServerId"; exit 1 }
    $s = @(Load-Servers) | Where-Object { $_.id -eq $ServerId }
    Write-Output ($s | ConvertTo-Json -Depth 3)
  }

  "refresh" {
    Write-Output "mcp refresh: scanning servers..."
    $servers = New-Object System.Collections.ArrayList
    foreach ($s in @(Load-Servers)) {
      $null = $servers.Add(@{id=$s.id;name=$s.name;command=$s.command;args=$s.args;transport=$s.transport;endpoint=$s.endpoint;status="active";tools=$s.tools;added=$s.added})
    }
    Save-Servers $servers
    Write-Output ("refreshed " + $servers.Count + " servers")
  }
}