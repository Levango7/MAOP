param(
  [ValidateSet("request","approve","reject","list","pending","resolve","notify","config")]
  [string]$Action = "pending",
  [string]$RequestId = "",
  [string]$Task = "",
  [string]$Agent = "",
  [string]$Reason = "",
  [string]$Requester = "system",
  [string]$Priority = "medium",
  [string[]]$NotifyMethods = @(),
  [string]$QueueFile = ""
)

$ScriptDir = Split-Path $PSCommandPath -Parent
$dataDir = Join-Path (Split-Path $ScriptDir -Parent) "data"
$dbPath = Join-Path $dataDir "human_queue.db"

<#
  MIGRATION COMPLETE: human-queue.json has been removed.
  All queue operations now go through SQLite (human_queue.db),
  owned by Python's MAOP.core.human_proxy.HumanProxy.

  This PS script is a legacy compatibility stub. It reads from
  SQLite directly for list/pending/notify actions. Write actions
  (request/approve/reject/resolve) are deprecated — use the Python
  API instead (MAOP.core.human_proxy.HumanProxy).
#>

function Invoke-SqliteQuery($sql, $params = @()) {
  if (-not (Test-Path $dbPath)) { return @() }
  try {
    $conn = New-Object System.Data.SQLite.SQLiteConnection("Data Source=$dbPath;Version=3;")
    $conn.Open()
    $cmd = $conn.CreateCommand()
    $cmd.CommandText = $sql
    for ($i = 0; $i -lt $params.Count; $i++) {
      $p = $cmd.Parameters.AddWithValue("p$i", $params[$i])
    }
    $reader = $cmd.ExecuteReader()
    $results = @()
    while ($reader.Read()) {
      $row = @{}
      for ($i = 0; $i -lt $reader.FieldCount; $i++) {
        $row[$reader.GetName($i)] = $reader.GetValue($i)
      }
      $results += $row
    }
    $conn.Close()
    return $results
  } catch {
    return @()
  }
}

switch ($Action) {
  "list" {
    $rows = Invoke-SqliteQuery "SELECT * FROM approval_requests ORDER BY created DESC"
    Write-Output ($rows | ConvertTo-Json -Depth 2)
  }

  "pending" {
    $rows = Invoke-SqliteQuery "SELECT * FROM approval_requests WHERE status='pending' ORDER BY created DESC"
    $byPriority = $rows | Group-Object priority | ForEach-Object {
      @{ priority = $_.Name; count = $_.Count; requests = $_.Group | Select-Object id, task, agent, requester, reason, created }
    } | Sort-Object { @{high=0;medium=1;low=2}[$_.priority] }
    Write-Output (@{ total_pending = $rows.Count; by_priority = @($byPriority) } | ConvertTo-Json -Depth 3)
  }

  "notify" {
    $rows = Invoke-SqliteQuery "SELECT id, task, agent, priority, created FROM approval_requests WHERE status='pending'"
    if ($rows.Count -eq 0) { Write-Output "no pending requests"; exit 0 }
    Write-Output "pending approvals: $($rows.Count)"
    $rows | ConvertTo-Json
  }

  "config" {
    Write-Output (@{ db_path = $dbPath; auto_approve_patterns = @(); max_pending = 50 } | ConvertTo-Json)
  }

  default {
    Write-Warning "[human-proxy] Action '$Action' is deprecated. Use Python API: MAOP.core.human_proxy.HumanProxy"
    Write-Output "deprecated: use Python API for write operations"
  }
}
