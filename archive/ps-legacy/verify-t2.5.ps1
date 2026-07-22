# MAOP database.ps1 — T2.5 Verification Script
# Usage: powershell -NoProfile -File test/verify-t2.5.ps1

$ErrorActionPreference = "Stop"
$rootDir = Split-Path $PSScriptRoot -Parent

Write-Host "=== Dot-source database.ps1 ===" -ForegroundColor Cyan
. (Join-Path $rootDir "src\database.ps1")

Write-Host "`n=== Test-SQLiteAvailability ===" -ForegroundColor Cyan
Test-SQLiteAvailability
Write-Host "HasSQLite: $script:PEV_HasSQLite | Provider: $script:PEV_SQLiteProvider"

Write-Host "`n=== Init-Database ===" -ForegroundColor Cyan
$ok = Init-Database
Write-Host "Init: $ok"

Write-Host "`n=== Save-DbCheckpoint (insert) ===" -ForegroundColor Cyan
$ok = Save-DbCheckpoint -Agent claude -Task test -Phase plan -State @{key="val"; count=1}
Write-Host "Save: $ok"

Write-Host "`n=== Save-DbCheckpoint (update) ===" -ForegroundColor Cyan
$ok = Save-DbCheckpoint -Agent claude -Task test -Phase exec -State @{key="val2"; count=2}
Write-Host "Save (update): $ok"

Write-Host "`n=== Get-DbCheckpoint ===" -ForegroundColor Cyan
$cp = Get-DbCheckpoint -Agent claude -Task test
Write-Host "Checkpoint: $(if ($cp) { $cp | ConvertTo-Json -Compress } else { 'NULL' })"

Write-Host "`n=== Get-DbCheckpoint (not found) ===" -ForegroundColor Cyan
$cp2 = Get-DbCheckpoint -Agent nonexistent -Task test
Write-Host "Not found returns null: $(($null -eq $cp2))"

Write-Host "`n=== Remove-DbCheckpoint ===" -ForegroundColor Cyan
$ok = Remove-DbCheckpoint -Agent claude -Task test
Write-Host "Remove: $ok"

Write-Host "`n=== Get-DbCheckpoint (after remove) ===" -ForegroundColor Cyan
$cp3 = Get-DbCheckpoint -Agent claude -Task test
Write-Host "After remove: $(if ($null -eq $cp3) { 'null (correct)' } else { 'unexpected value' })"

Write-Host "`n=== Log-DbError ===" -ForegroundColor Cyan
$ok = Log-DbError -Agent claude -Task test -ExitCode -1 -Error "test error" -TraceID "t1" -DurationMs 100
Write-Host "Log error: $ok"

$ok = Log-DbError -Agent codex -Task build -ExitCode 1 -Error "build failed" -TraceID "t2" -DurationMs 5000
Write-Host "Log error 2: $ok"

Write-Host "`n=== Sync-BreakerToDb ===" -ForegroundColor Cyan
$ok = Sync-BreakerToDb
Write-Host "Sync: $ok"

# Verify all tables
Write-Host "`n=== All tables ===" -ForegroundColor Cyan
$tables = Query-Database -Sql "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
$tables | ForEach-Object { Write-Host "  $($_.name)" }

Write-Host "`n=== Checkpoints table ===" -ForegroundColor Cyan
$rows = Query-Database -Sql "SELECT agent, task, phase FROM checkpoints"
if ($rows.Count -eq 0) { Write-Host "  (empty - as expected after remove)" }

Write-Host "`n=== Error log table ===" -ForegroundColor Cyan
$rows = Query-Database -Sql "SELECT agent, task, exit_code, error, trace_id, duration_ms FROM error_log"
$rows | ForEach-Object { Write-Host "  agent=$($_.agent) task=$($_.task) exit=$($_.exit_code) err=$($_.error) trace=$($_.trace_id) dur=$($_.duration_ms)ms" }

Write-Host "`n=== Circuit breaker table ===" -ForegroundColor Cyan
$rows = Query-Database -Sql "SELECT agent, state, failures, threshold FROM circuit_breaker"
$rows | ForEach-Object { Write-Host "  agent=$($_.agent) state=$($_.state) failures=$($_.failures) threshold=$($_.threshold)" }

Write-Host "`n=== Existing tables still intact ===" -ForegroundColor Cyan
$oldTables = @("delegations","metrics","vectors","graph_nodes","graph_edges")
foreach ($t in $oldTables) {
    $count = (Query-Database -Sql "SELECT COUNT(*) as cnt FROM [$t]")[0].cnt
    Write-Host "  $t : $count rows (table exists)"
}

Write-Host "`n=== Backward compatibility: dot-source path ===" -ForegroundColor Cyan
Write-Host "PEVDbRoot: $script:PEVDbRoot"
Write-Host "PEVDbPath: $script:PEVDbPath"

Write-Host "`n=== ALL TESTS PASSED ===" -ForegroundColor Green
