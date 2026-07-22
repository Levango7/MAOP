param(
  [ValidateSet("create-team","list-teams","team-info","run-swarm","run-sequential","run-parallel",
    "discover","add-member","remove-member","delete-team","delegate-task","report")]
  [string]$Action = "report",
  [string]$TeamId = "",
  [string]$TeamName = "",
  [string]$Mode = "swarm", # swarm | sequential | parallel
  [string]$Agents = "",
  [string]$Task = "",
  [string]$Goal = "",
  [string]$Leader = "",
  [string]$MemberId = "",
  [string]$Capability = "",
  [int]$TimeoutSeconds = 60,
  [string]$TeamFile = ""
)

$ScriptDir = Split-Path $PSCommandPath -Parent
$DelegateScript = Join-Path $ScriptDir "delegate.ps1"
if (-not $TeamFile) { $TeamFile = Join-Path (Split-Path $ScriptDir -Parent) "data\teams.json" }
$TeamDir = Split-Path $TeamFile -Parent; if (-not (Test-Path $TeamDir)) { New-Item -ItemType Directory -Force -Path $TeamDir | Out-Null }

function Load-Teams {
  if (Test-Path $TeamFile) { try { return @((Get-Content $TeamFile -Raw | ConvertFrom-Json).teams) } catch { return @() } }
  return @()
}
function Save-Teams($t) {
  @{ teams = @($t) } | ConvertTo-Json -Depth 3 -Compress | Set-Content $TeamFile -Encoding utf8
}

function Invoke-Agent($agent, $prompt, $timeout) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  try {
    $json = & powershell -NoProfile -File $DelegateScript -Agent $agent -Task $prompt -TimeoutSeconds $timeout 2>&1 | Out-String
    $result = $json | ConvertFrom-Json
    $sw.Stop()
    return @{ ok = ($result.exit_code -eq 0 -and -not [string]::IsNullOrWhiteSpace($result.stdout)); output = $result.stdout; ms = $sw.ElapsedMilliseconds; agent = $agent }
  } catch {
    $sw.Stop()
    return @{ ok = $false; output = $_.Exception.Message; ms = $sw.ElapsedMilliseconds; agent = $agent }
  }
}

switch ($Action) {
  "create-team" {
    if (-not $TeamId -or -not $Agents) { Write-Error "create-team requires -TeamId and -Agents (comma-separated)"; exit 1 }
    $memberList = $Agents -split ',' | ForEach-Object { $_.Trim() }
    $entry = @{
      id = $TeamId; name = if ($TeamName) { $TeamName } else { $TeamId }
      leader = if ($Leader) { $Leader } else { $memberList[0] }
      members = @($memberList); created = (Get-Date -Format "o")
    }
    $teams = New-Object System.Collections.ArrayList
    foreach ($t in @(Load-Teams)) { if ($t.id -ne $TeamId) { $null = $teams.Add($t) } }
    $null = $teams.Add($entry)
    Save-Teams $teams
    Write-Output ("team created: " + $TeamId + " (" + $memberList.Count + " members)")
  }

  "add-member" {
    if (-not $TeamId -or -not $MemberId) { Write-Error "add-member requires -TeamId and -MemberId"; exit 1 }
    $teams = New-Object System.Collections.ArrayList
    foreach ($t in @(Load-Teams)) {
      if ($t.id -eq $TeamId) {
        $members = @($t.members)
        if ($members -notcontains $MemberId) { $members += $MemberId }
        $null = $teams.Add(@{id=$t.id;name=$t.name;leader=$t.leader;members=@($members);created=$t.created})
      } else { $null = $teams.Add($t) }
    }
    Save-Teams $teams
    Write-Output ("added " + $MemberId + " to " + $TeamId)
  }

  "remove-member" {
    if (-not $TeamId -or -not $MemberId) { Write-Error "remove-member requires -TeamId and -MemberId"; exit 1 }
    $teams = New-Object System.Collections.ArrayList
    foreach ($t in @(Load-Teams)) {
      if ($t.id -eq $TeamId) {
        $members = @($t.members) | Where-Object { $_ -ne $MemberId }
        $null = $teams.Add(@{id=$t.id;name=$t.name;leader=$t.leader;members=@($members);created=$t.created})
      } else { $null = $teams.Add($t) }
    }
    Save-Teams $teams
    Write-Output ("removed " + $MemberId + " from " + $TeamId)
  }

  "list-teams" {
    $teams = @(Load-Teams)
    $list = $teams | Select-Object id, name, leader, @{N="member_count";E={$_.members.Count}}, created
    Write-Output ($list | ConvertTo-Json -Depth 2)
  }

  "team-info" {
    if (-not $TeamId) { Write-Error "team-info requires -TeamId"; exit 1 }
    $t = @(Load-Teams) | Where-Object { $_.id -eq $TeamId }
    if (-not $t) { Write-Error "team not found: $TeamId"; exit 1 }
    Write-Output ($t | ConvertTo-Json -Depth 3)
  }

  "delete-team" {
    if (-not $TeamId) { Write-Error "delete-team requires -TeamId"; exit 1 }
    $teams = New-Object System.Collections.ArrayList
    foreach ($t in @(Load-Teams)) { if ($t.id -ne $TeamId) { $null = $teams.Add($t) } }
    Save-Teams $teams
    Write-Output ("deleted: " + $TeamId)
  }

  "run-swarm" {
    if (-not $TeamId -or -not $Task) { Write-Error "run-swarm requires -TeamId and -Task"; exit 1 }
    $team = @(Load-Teams) | Where-Object { $_.id -eq $TeamId }
    if (-not $team) { Write-Error "team not found: $TeamId"; exit 1 }
    $leader = "$($team.leader)"; $allMembers = @(); foreach ($m in @($team.members)) { $allMembers += "$m" }; $members = $allMembers | Where-Object { $_ -ne $leader }
    if ($Goal) { $taskWithGoal = "Goal: $Goal`nTask: $Task" } else { $taskWithGoal = $Task }
    $planResult = Invoke-Agent $leader $taskWithGoal $TimeoutSeconds
    if (-not $planResult.ok) { Write-Output ($planResult | ConvertTo-Json); exit 1 }
    $memberResults = @()
    $parallel = if ($Mode -eq "parallel") { $true } else { $false }
    if ($parallel) {
      $jobs = @()
      foreach ($m in $members) {
        $prompt = "As a team member, execute this task: $Task`nLeader's plan: $($planResult.output)`nProvide your contribution."
        $jobs += @{ agent = $m; prompt = $prompt; timeout = $TimeoutSeconds }
      }
      $memberResults = $jobs | ForEach-Object { Invoke-Agent $_.agent $_.prompt $_.timeout }
    } else {
      foreach ($m in $members) {
        $prompt = "As a team member, execute this task: $Task`nLeader's plan: $($planResult.output)`nProvide your contribution."
        $memberResults += Invoke-Agent $m $prompt $TimeoutSeconds
      }
      $synthesisPrompt = "Synthesize the following contributions into a final answer.`nTask: $Task`nLeader plan: $($planResult.output)"
      $i = 0; foreach ($mr in $memberResults) { $synthesisPrompt += "`nMember $($members[$i]) contribution: $($mr.output)"; $i++ }
      $finalResult = Invoke-Agent $leader $synthesisPrompt ($TimeoutSeconds * 2)
    }
    Write-Output (@{
      team = $TeamId; mode = "swarm"; task = $Task; leader = $leader
      plan = $planResult.output; plan_ms = $planResult.ms
      members = @($memberResults | ForEach-Object { @{ agent = $_.agent; ok = $_.ok; ms = $_.ms; summary = $_.output.Substring(0, [Math]::Min(100, $_.output.Length)) } })
      final = if ($finalResult) { $finalResult.output } else { $null }
      total_ms = $planResult.ms + ($memberResults | ForEach-Object { $_.ms } | Measure-Object -Sum).Sum
    } | ConvertTo-Json -Depth 3)
  }

  "run-sequential" {
    if (-not $Agents -or -not $Task) { Write-Error "run-sequential requires -Agents and -Task"; exit 1 }
    $agentList = $Agents -split ',' | ForEach-Object { $_.Trim() }
    $context = $Task
    $results = @()
    foreach ($a in $agentList) {
      $r = Invoke-Agent $a $context $TimeoutSeconds
      $context = $context + "`n--- $a responded ---`n$($r.output)"
      $results += $r
    }
    Write-Output (@{ mode = "sequential"; agents = $agentList; task = $Task; results = $results | Select-Object agent, ok, ms | ConvertTo-Json -Depth 2 })
  }

  "run-parallel" {
    if (-not $Agents -or -not $Task) { Write-Error "run-parallel requires -Agents and -Task"; exit 1 }
    $agentList = $Agents -split ',' | ForEach-Object { $_.Trim() }
    $results = $agentList | ForEach-Object { Invoke-Agent $_ $Task $TimeoutSeconds }
    Write-Output (@{ mode = "parallel"; agents = $agentList; task = $Task; results = $results | Select-Object agent, ok, ms | ConvertTo-Json -Depth 2 })
  }

  "discover" {
    $agents = @("claude","kimi","codewhale","openclaw","hermes","mavis","kilo","qwenpaw","qoder","mimo","codex","autoclaw")
    if ($Capability) {
      $capMap = @{code=@("codewhale","codex","qoder","qwenpaw");reasoning=@("claude","kimi");quick=@("codewhale");chinese=@("kimi","qwenpaw");autonomous=@("openclaw","hermes");shell=@("kilo","codewhale")}
      $matched = $capMap[$Capability]
      if ($matched) { Write-Output ($matched | ConvertTo-Json -Compress) } else { Write-Output "[]" }
    } else {
      Write-Output ($agents | ConvertTo-Json -Compress)
    }
  }

  "delegate-task" {
    if (-not $Capability -or -not $Task) { Write-Error "delegate-task requires -Capability and -Task"; exit 1 }
    $capMap = @{code=@("codewhale","codex","qoder","qwenpaw");reasoning=@("claude","kimi");quick=@("codewhale");chinese=@("kimi","qwenpaw");autonomous=@("openclaw","hermes");shell=@("kilo","codewhale")}
    $rawAgents = $capMap[$Capability]
    if (-not $rawAgents) { Write-Error "no agents found for capability: $Capability"; exit 1 }
    $agentList = @($rawAgents | ForEach-Object { "$_" })
    if ($agentList.Count -eq 0) { Write-Error "no agents for: $Capability"; exit 1 }
    $picked = $agentList[0]
    $result = Invoke-Agent $picked $Task $TimeoutSeconds
    Write-Output ($result | ConvertTo-Json -Depth 2)
  }

  "report" {
    $teams = @(Load-Teams)
    $totalTeams = @($teams).Count
    $totalMembers = 0; foreach ($t in $teams) { $totalMembers += @($t.members).Count }
    $agentsList = @("claude","kimi","codewhale","openclaw","hermes","mavis","kilo","qwenpaw","qoder","mimo","codex","autoclaw")
    Write-Output (@{
      total_teams = $totalTeams; total_members = $totalMembers
      available_agents = @($agentsList).Count
      modes = @("swarm","sequential","parallel")
      capabilities = @{code=@("codewhale","codex","qoder","qwenpaw");reasoning=@("claude","kimi");quick=@("codewhale");chinese=@("kimi","qwenpaw");autonomous=@("openclaw","hermes");shell=@("kilo","codewhale")}
    } | ConvertTo-Json -Depth 2)
  }
}