param(
  [ValidateSet("get", "list")]
  [string]$Action = "get",
  [string]$Name = "dashboard",
  [int]$Lines = 50
)

$ScriptDir = Split-Path $PSCommandPath -Parent
$RootDir = Split-Path $ScriptDir -Parent
$LogDir = Join-Path $RootDir "logs"

switch ($Action) {
  "get" {
    $logFile = Join-Path $LogDir "$Name.log"
    if (-not (Test-Path $LogDir)) { Write-Output '{"error":"logs dir not found"}'; exit 0 }
    
    # Check for exact file or try known files
    if (Test-Path $logFile) {
      $content = Get-Content $logFile -Tail $Lines -Encoding utf8 -ErrorAction SilentlyContinue
      $total = @(Get-Content $logFile -Encoding utf8 -ErrorAction SilentlyContinue).Count
      $result = @{ file = "$Name.log"; lines = $total; display = $Lines; content = @($content) }
      Write-Output ($result | ConvertTo-Json -Depth 2 -Compress)
    } elseif ($Name -eq "delegations") {
      $logFile = Join-Path $LogDir "delegations.json"
      if (Test-Path $logFile) {
        $raw = Get-Content $logFile -Raw -Encoding utf8 -ErrorAction SilentlyContinue
        $total = 0
        try { $total = @($raw | ConvertFrom-Json).Count } catch { $total = ($raw -split "`n").Count }
        $result = @{ file = "delegations.json"; lines = $total; display = $Lines; content = @($raw.Substring(0, [Math]::Min($raw.Length, 2000))) }
        Write-Output ($result | ConvertTo-Json -Depth 2 -Compress)
      } else {
        Write-Output '{"error":"delegations.json not found"}'
      }
    } else {
      # List available logs
      $files = Get-ChildItem $LogDir -Filter "*.log" -ErrorAction SilentlyContinue | Select-Object Name
      Write-Output (@{ error = "file not found: $Name.log"; available = @($files.Name) } | ConvertTo-Json -Compress)
    }
  }
  "list" {
    $files = Get-ChildItem $LogDir -Filter "*.log" -ErrorAction SilentlyContinue | Select-Object Name, Length, LastWriteTime
    Write-Output ($files | ConvertTo-Json -Depth 1)
  }
}