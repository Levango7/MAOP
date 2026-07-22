param(
  [ValidateSet("init","query","execute")]
  [string]$DbAction = "",
  [string]$Sql = "",
  [string]$DbPath = ""
)

# MAOP SQLite Database Module
# Dot-source:  . (Join-Path $PSScriptRoot 'database.ps1')
# Direct run:  powershell -File database.ps1 -Action init
#              powershell -File database.ps1 -Action query -Sql "SELECT ..."

$script:PEVDbRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path $PSCommandPath -Parent }
$script:PEVDbPath = Join-Path (Split-Path $script:PEVDbRoot -Parent) "data\MAOP.db"
$script:PEV_HasSQLite = $false
$script:PEV_SQLiteProvider = ""

# ─── Availability Detection ────────────────────────────────────────────
function Test-SQLiteAvailability {
  <#
    .SYNOPSIS
      Check if System.Data.SQLite or PSSQLite is available.
    .DESCRIPTION
      Sets $script:PEV_HasSQLite and $script:PEV_SQLiteProvider.
      Called automatically on module load.
  #>
  $script:PEV_HasSQLite = $false
  $script:PEV_SQLiteProvider = ""

  # 1) Try System.Data.SQLite via Add-Type
  try {
    Add-Type -AssemblyName System.Data.SQLite -ErrorAction Stop
    $script:PEV_HasSQLite = $true
    $script:PEV_SQLiteProvider = "System.Data.SQLite"
    return
  } catch { }

  # 2) Try PSSQLite module
  try {
    Import-Module PSSQLite -ErrorAction Stop
    $script:PEV_HasSQLite = $true
    $script:PEV_SQLiteProvider = "PSSQLite"
    return
  } catch { }

  # 3) Search common locations for System.Data.SQLite.dll
  $searchDirs = @(
    "C:\Program Files\System.Data.SQLite\",
    "C:\Program Files (x86)\System.Data.SQLite\",
    [System.IO.Path]::Combine($env:USERPROFILE, ".nuget", "packages", "System.Data.SQLite.Core"),
    "C:\Windows\assembly\GAC_MSIL\System.Data.SQLite\",
    "C:\Windows\Microsoft.NET\assembly\GAC_MSIL\System.Data.SQLite\"
  )
  foreach ($dir in $searchDirs) {
    if (Test-Path $dir) {
      $dll = Get-ChildItem -Recurse -Filter "System.Data.SQLite.dll" -Path $dir -ErrorAction SilentlyContinue |
             Select-Object -First 1
      if ($dll) {
        try {
          Add-Type -Path $dll.FullName -ErrorAction Stop
          $script:PEV_HasSQLite = $true
          $script:PEV_SQLiteProvider = "System.Data.SQLite"
          return
        } catch { }
      }
    }
  }
}

# ─── Schema ────────────────────────────────────────────────────────────
$script:PEV_Schema = @"
CREATE TABLE IF NOT EXISTS delegations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT,
  agent TEXT,
  task TEXT,
  routing_key TEXT,
  exit_code INT,
  stdout TEXT,
  stderr TEXT,
  duration_ms INT,
  trace_id TEXT
);
CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT,
  agent TEXT,
  metric_name TEXT,
  metric_value REAL,
  tags TEXT
);
CREATE TABLE IF NOT EXISTS vectors (
  id TEXT PRIMARY KEY,
  text TEXT,
  embedding BLOB,
  metadata TEXT,
  created TEXT
);
CREATE TABLE IF NOT EXISTS graph_nodes (
  id TEXT PRIMARY KEY,
  type TEXT,
  label TEXT,
  properties TEXT,
  created TEXT
);
CREATE TABLE IF NOT EXISTS graph_edges (
  id TEXT PRIMARY KEY,
  source TEXT,
  target TEXT,
  relation TEXT,
  weight REAL,
  created TEXT
);
CREATE TABLE IF NOT EXISTS checkpoints (
  id TEXT PRIMARY KEY,
  agent TEXT,
  task TEXT,
  phase TEXT,
  state_json TEXT,
  created TEXT,
  updated TEXT
);
CREATE TABLE IF NOT EXISTS circuit_breaker (
  agent TEXT PRIMARY KEY,
  state TEXT DEFAULT 'closed',
  failures INT DEFAULT 0,
  threshold INT DEFAULT 3,
  last_failure TEXT,
  cooldown_s INT DEFAULT 60,
  updated TEXT
);
CREATE TABLE IF NOT EXISTS error_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT,
  agent TEXT,
  task TEXT,
  exit_code INT,
  error TEXT,
  trace_id TEXT,
  duration_ms INT
);
"@

# ─── Core Functions ────────────────────────────────────────────────────
function Init-Database {
  <#
    .SYNOPSIS
      Create database file and all tables if they don't exist.
    .PARAMETER Path
      Full path to the SQLite database file. Defaults to data\MAOP.db.
    .OUTPUTS
      [bool]  $true on success, $false if SQLite unavailable or init fails.
  #>
  param(
    [string]$Path = $script:PEVDbPath
  )

  if (-not $script:PEV_HasSQLite) {
    Write-Verbose "[db] SQLite not available — skipping init"
    return $false
  }

  $dir = Split-Path $Path -Parent
  if (-not (Test-Path $dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
  }

  try {
    if ($script:PEV_SQLiteProvider -eq "PSSQLite") {
      Invoke-SqliteQuery -DataSource $Path -Query $script:PEV_Schema
    } else {
      $conn = New-Object System.Data.SQLite.SQLiteConnection("Data Source=$Path")
      $conn.Open()
      $cmd = $conn.CreateCommand()
      $cmd.CommandText = $script:PEV_Schema
      $cmd.ExecuteNonQuery() | Out-Null
      $conn.Close()
    }
    Write-Verbose "[db] Database initialized at $Path"
    return $true
  } catch {
    Write-Warning "[db] Failed to initialize SQLite: $_"
    return $false
  }
}

function Execute-Database {
  <#
    .SYNOPSIS
      Execute a non-query SQL statement (INSERT, UPDATE, DELETE, CREATE).
    .PARAMETER Path
      Full path to the SQLite database file.
    .PARAMETER Sql
      The SQL statement to execute.
    .PARAMETER Parameters
      Optional hashtable of parameter name-value pairs (e.g. @{"@id"="abc"}).
    .OUTPUTS
      [bool]  $true on success, $false if SQLite unavailable or execution fails.
  #>
  param(
    [string]$Path = $script:PEVDbPath,
    [string]$Sql,
    [hashtable]$Parameters = @{}
  )

  if (-not $script:PEV_HasSQLite) { return $false }

  try {
    if ($script:PEV_SQLiteProvider -eq "PSSQLite") {
      if ($Parameters.Count -gt 0) {
        Invoke-SqliteQuery -DataSource $Path -Query $Sql -SqlParameters $Parameters
      } else {
        Invoke-SqliteQuery -DataSource $Path -Query $Sql
      }
    } else {
      $conn = New-Object System.Data.SQLite.SQLiteConnection("Data Source=$Path")
      $conn.Open()
      $cmd = $conn.CreateCommand()
      $cmd.CommandText = $Sql
      foreach ($kv in $Parameters.GetEnumerator()) {
        [void]$cmd.Parameters.AddWithValue($kv.Key, $kv.Value)
      }
      [void]$cmd.ExecuteNonQuery()
      $conn.Close()
    }
    return $true
  } catch {
    Write-Warning "[db] Execute failed: $_"
    return $false
  }
}

function Query-Database {
  <#
    .SYNOPSIS
      Execute a query SQL statement (SELECT) and return results.
    .PARAMETER Path
      Full path to the SQLite database file.
    .PARAMETER Sql
      The SELECT statement to execute.
    .PARAMETER Parameters
      Optional hashtable of parameter name-value pairs.
    .OUTPUTS
      Returns an array of PSObjects (one per row) when SQLite is available.
      Returns $null when SQLite is unavailable (caller should fallback to JSON).
      Returns an empty array when the query returns no rows.
  #>
  param(
    [string]$Path = $script:PEVDbPath,
    [string]$Sql,
    [hashtable]$Parameters = @{}
  )

  if (-not $script:PEV_HasSQLite) { return $null }

  try {
    $rows = @()
    if ($script:PEV_SQLiteProvider -eq "PSSQLite") {
      if ($Parameters.Count -gt 0) {
        $rows = @(Invoke-SqliteQuery -DataSource $Path -Query $Sql -SqlParameters $Parameters)
      } else {
        $rows = @(Invoke-SqliteQuery -DataSource $Path -Query $Sql)
      }
    } else {
      $conn = New-Object System.Data.SQLite.SQLiteConnection("Data Source=$Path")
      $conn.Open()
      $cmd = $conn.CreateCommand()
      $cmd.CommandText = $Sql
      foreach ($kv in $Parameters.GetEnumerator()) {
        [void]$cmd.Parameters.AddWithValue($kv.Key, $kv.Value)
      }
      $adapter = New-Object System.Data.SQLite.SQLiteDataAdapter($cmd)
      $table = New-Object System.Data.DataTable
      [void]$adapter.Fill($table)
      $conn.Close()

      $rows = foreach ($row in $table.Rows) {
        $obj = @{}
        foreach ($col in $table.Columns) {
          $obj[$col.ColumnName] = $row[$col.ColumnName]
        }
        [PSCustomObject]$obj
      }
    }
    return @($rows)
  } catch {
    Write-Warning "[db] Query failed: $_"
    return $null
  }
}

# ─── Checkpoint / Error / Breaker Functions ──────────────────────────────
function Get-DbCheckpoint {
  <#
    .SYNOPSIS
      Retrieve a saved checkpoint state for a given agent+task.
    .PARAMETER Agent
      Agent name (e.g. "claude", "codex").
    .PARAMETER Task
      Task identifier.
    .OUTPUTS
      [hashtable]  Deserialized state from state_json, or $null if not found
                   or SQLite unavailable.
  #>
  param(
    [string]$Agent,
    [string]$Task
  )

  if (-not $script:PEV_HasSQLite) { return $null }

  $sql = "SELECT state_json FROM checkpoints WHERE agent=@agent AND task=@task LIMIT 1"
  $rows = Query-Database -Sql $sql -Parameters @{agent=$Agent; task=$Task}
  if ($null -eq $rows -or $rows.Count -eq 0) { return $null }

  try {
    return ($rows[0].state_json | ConvertFrom-Json)
  } catch {
    return $null
  }
}

function Save-DbCheckpoint {
  <#
    .SYNOPSIS
      Save or update a checkpoint for a given agent+task.
    .PARAMETER Agent
      Agent name.
    .PARAMETER Task
      Task identifier.
    .PARAMETER Phase
      Current phase (e.g. "plan", "exec", "review").
    .PARAMETER State
      Hashtable of state data to persist.
    .OUTPUTS
      [bool]  $true on success, $false on failure or SQLite unavailable.
  #>
  param(
    [string]$Agent,
    [string]$Task,
    [string]$Phase,
    [hashtable]$State
  )

  if (-not $script:PEV_HasSQLite) { return $false }

  $now = (Get-Date -Format "o")
  $json = $State | ConvertTo-Json -Depth 5 -Compress
  $id = "$Agent`_$Task`_$Phase`_$(Get-Random -Maximum 99999)"

  # Two-step: delete existing checkpoints for this agent+task, then insert new one
  # This avoids ON CONFLICT / UPSERT which requires SQLite ≥ 3.24.0
  [void](Execute-Database -Sql "DELETE FROM checkpoints WHERE agent=@agent AND task=@task" -Parameters @{agent=$Agent; task=$Task})

  $sql = "INSERT INTO checkpoints (id, agent, task, phase, state_json, created, updated) VALUES (@id, @agent, @task, @phase, @json, @now, @now)"
  return (Execute-Database -Sql $sql -Parameters @{
    id=$id; agent=$Agent; task=$Task; phase=$Phase
    json=$json; now=$now
  })
}

function Remove-DbCheckpoint {
  <#
    .SYNOPSIS
      Delete all checkpoints for a given agent+task.
    .PARAMETER Agent
      Agent name.
    .PARAMETER Task
      Task identifier.
    .OUTPUTS
      [bool]  $true on success, $false on failure or SQLite unavailable.
  #>
  param(
    [string]$Agent,
    [string]$Task
  )

  if (-not $script:PEV_HasSQLite) { return $false }

  $sql = "DELETE FROM checkpoints WHERE agent=@agent AND task=@task"
  return (Execute-Database -Sql $sql -Parameters @{agent=$Agent; task=$Task})
}

function Log-DbError {
  <#
    .SYNOPSIS
      Log an execution error to the error_log table.
    .PARAMETER Agent
      Agent name.
    .PARAMETER Task
      Task identifier.
    .PARAMETER ExitCode
      Process exit code (0 for success, -1 for crash, etc.).
    .PARAMETER Error
      Error message or description.
    .PARAMETER TraceID
      Trace or correlation identifier.
    .PARAMETER DurationMs
      Duration of the failing operation in milliseconds.
    .OUTPUTS
      [bool]  $true on success, $false on failure or SQLite unavailable.
  #>
  param(
    [string]$Agent,
    [string]$Task,
    [int]$ExitCode,
    [string]$Error,
    [string]$TraceID,
    [int]$DurationMs
  )

  if (-not $script:PEV_HasSQLite) { return $false }

  $timestamp = (Get-Date -Format "o")

  $sql = @"
INSERT INTO error_log (timestamp, agent, task, exit_code, error, trace_id, duration_ms)
VALUES (@ts, @agent, @task, @exit, @err, @trace, @dur)
"@

  return (Execute-Database -Sql $sql -Parameters @{
    ts=$timestamp; agent=$Agent; task=$Task
    exit=$ExitCode; err=$Error; trace=$TraceID; dur=$DurationMs
  })
}

function Sync-BreakerToDb {
  <#
    .SYNOPSIS
      Read circuit-breaker state from data/circuit-breaker.json and
      upsert into the circuit_breaker table. Creates the JSON file
      with defaults if it doesn't exist.
    .OUTPUTS
      [bool]  $true on success, $false if SQLite unavailable or operation fails.
  #>
  if (-not $script:PEV_HasSQLite) { return $false }

  $breakerPath = Join-Path (Split-Path $script:PEVDbRoot -Parent) "data\circuit-breaker.json"
  $now = (Get-Date -Format "o")

  # Create default file if missing
  if (-not (Test-Path $breakerPath)) {
    $dir = Split-Path $breakerPath -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $default = @{
      claude = @{state="closed"; failures=0; threshold=3; last_failure=$null; cooldown_s=60; updated=$now}
      codex  = @{state="closed"; failures=0; threshold=3; last_failure=$null; cooldown_s=60; updated=$now}
    }
    $default | ConvertTo-Json -Depth 3 | Set-Content -Path $breakerPath
  }

  try {
    $breakers = Get-Content -Path $breakerPath -Raw | ConvertFrom-Json
  } catch {
    Write-Warning "[db] Failed to read circuit-breaker.json: $_"
    return $false
  }

  $success = $true
  foreach ($agent in $breakers.PSObject.Properties) {
    $b = $agent.Value
    $sql = "INSERT OR REPLACE INTO circuit_breaker (agent, state, failures, threshold, last_failure, cooldown_s, updated) VALUES (@agent, @state, @failures, @threshold, @last_failure, @cooldown, @updated)"
    $ok = Execute-Database -Sql $sql -Parameters @{
      agent=$agent.Name
      state=$b.state
      failures=[int]$b.failures
      threshold=[int]$b.threshold
      last_failure=if ($b.last_failure) { $b.last_failure } else { "" }
      cooldown=[int]$b.cooldown_s
      updated=$now
    }
    if (-not $ok) { $success = $false }
  }

  return $success
}

# ─── Auto-detect availability on load ─────────────────────────────────
Test-SQLiteAvailability | Out-Null

# ─── Direct Execution (when run via -File, not dot-sourced) ───────────
if ($DbAction) {
  switch ($DbAction) {
    "init" {
      if ($DbPath) { $result = Init-Database -Path $DbPath }
      else { $result = Init-Database }
      if ($result) {
        Write-Host "[db] Database initialized successfully"
      } else {
        if ($script:PEV_HasSQLite) {
          Write-Host "[db] Database initialization failed"
        } else {
          Write-Host "[db] SQLite unavailable — using JSON fallback"
        }
      }
    }

    "query" {
      if (-not $Sql) { Write-Error "[db] -Sql is required for query action"; exit 1 }
      if ($DbPath) { $result = Query-Database -Path $DbPath -Sql $Sql }
      else { $result = Query-Database -Sql $Sql }

      if ($null -eq $result) {
        if ($script:PEV_HasSQLite) {
          Write-Host "[db] Query returned no results"
        } else {
          Write-Host "[db] SQLite unavailable — JSON fallback active"
        }
      } elseif ($result.Count -eq 0) {
        Write-Host "[db] Query returned 0 rows"
      } else {
        $result | ConvertTo-Json -Depth 2
      }
    }

    "execute" {
      if (-not $Sql) { Write-Error "[db] -Sql is required for execute action"; exit 1 }
      if ($DbPath) { $result = Execute-Database -Path $DbPath -Sql $Sql }
      else { $result = Execute-Database -Sql $Sql }
      if ($result) {
        Write-Host "[db] Execute succeeded"
      } else {
        Write-Host "[db] Execute failed or SQLite unavailable"
      }
    }
  }
}
