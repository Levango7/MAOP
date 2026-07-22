param(
  [ValidateSet("check","allow","block","config","report","reset")]
  [string]$Action = "check",
  [string]$Content = "",
  [string]$ContentFile = "",
  [string]$Agent = "",
  [string]$Task = "",
  [string]$TaskFile = "",
  [string]$Rule = "",
  [string]$ConfigFile = ""
)

# ── Python guardrail bridge (preferred) ──
if ($Action -eq "check") {
  $ProjectRoot = Split-Path (Split-Path $PSCommandPath -Parent) -Parent
  $pyGuardrail = Join-Path $ProjectRoot "py\MAOP\core\guardrail.py"
  if (Test-Path $pyGuardrail) {
    $python = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "python3" }
    $env:PYTHONPATH = Join-Path $ProjectRoot "py"
    $safeContent = $Content -replace '"', '""'
    try {
      $out = & $python $pyGuardrail check --content "$safeContent" --agent "$Agent" --task "$Task" 2>&1 | Out-String
      $result = try { $out | ConvertFrom-Json } catch { $null }
      if ($result) {
        Write-Output ($result | ConvertTo-Json -Depth 3 -Compress)
        exit $(if ($result.passed) { 0 } else { 1 })
      }
    } catch { Write-Warning "[guardrail] Python bridge failed, falling back to PS (non-critical)" }
  }
}

# Load Content/Task from file if file params provided (to avoid command-line length limits)
if ($ContentFile -and -not $Content) {
  $Content = Get-Content $ContentFile -Raw -ErrorAction Stop
}
if ($TaskFile -and -not $Task) {
  $Task = Get-Content $TaskFile -Raw -ErrorAction Stop
}

$ScriptDir = Split-Path $PSCommandPath -Parent
if (-not $ConfigFile) { $ConfigFile = Join-Path (Split-Path $ScriptDir -Parent) "data\guardrails.json" }
$ConfigDir = Split-Path $ConfigFile -Parent; if (-not (Test-Path $ConfigDir)) { New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null }

$DefaultConfig = @{
  rules = @(
    @{ id = "max-task-length"; type = "input"; enabled = $true; limit = 5000; action = "warn"; description = "任务文本最长5000字符" }
    @{ id = "blocked-agents"; type = "agent"; enabled = $true; blocklist = @(); action = "block"; description = "禁止使用的 agent 列表" }
    @{ id = "rate-limit"; type = "rate"; enabled = $false; max_per_minute = 30; action = "block"; description = "每分钟最大请求数" }
    @{ id = "sensitive-patterns"; type = "content"; enabled = $true; patterns = @("sk-[a-zA-Z0-9]{20,}", "AKIA[0-9A-Z]{16}", "-----BEGIN (RSA |EC )?PRIVATE KEY-----"); action = "block"; description = "敏感信息泄露防护" }
    @{ id = "allowed-tasks"; type = "task"; enabled = $false; allowlist = @("*"); action = "block"; description = "允许执行的任务白名单" }
    @{ id = "max-output-size"; type = "output"; enabled = $true; limit = 100000; action = "truncate"; description = "输出最大100KB" }
  )
}

function Load-Config {
  if (Test-Path $ConfigFile) {
    try { return Get-Content $ConfigFile -Raw -ErrorAction Stop | ConvertFrom-Json }
    catch { return $DefaultConfig | ConvertTo-Json -Depth 10 | ConvertFrom-Json }
  }
  return $DefaultConfig | ConvertTo-Json -Depth 10 | ConvertFrom-Json
}
function Save-Config($c) { $c | ConvertTo-Json -Depth 3 -Compress | Set-Content $ConfigFile -Encoding utf8 }

switch ($Action) {
  "check" {
    $violations = @()
    $cfg = Load-Config
    if ($cfg -and $cfg.rules) {
      $rulesRaw = $cfg.rules
      for ($i = 0; $i -lt $rulesRaw.Count; $i++) {
        $r = $rulesRaw[$i]
        $rid = "$($r.id)"; $rtype = "$($r.type)"; $renabled = "$($r.enabled)" -eq "True"
        if (-not $renabled) { continue }
        if ($rtype -eq "content") {
          $pArr = $r.patterns
          if ($pArr -and $pArr.Count -gt 0) {
            for ($j = 0; $j -lt $pArr.Count; $j++) {
              if ([regex]::IsMatch($Content, "$($pArr[$j])")) {
                $violations += @{ rule = $rid; severity = "block"; message = "sensitive content detected"; action = "block" }
              }
            }
          }
        } elseif ($rtype -eq "input") {
          if ($Content.Length -gt [int]"$($r.limit)") {
            $violations += @{ rule = $rid; severity = "warn"; message = "content exceeds limit: $($Content.Length) chars"; action = "$($r.action)" }
          }
        } elseif ($rtype -eq "agent") {
          $bl = $r.blocklist
          if ($bl -and $bl.Count -gt 0) {
            for ($j = 0; $j -lt $bl.Count; $j++) { if ("$($bl[$j])" -eq $Agent) { $violations += @{ rule = $rid; severity = "block"; message = "agent blocked: $Agent"; action = "block" } } }
          }
        } elseif ($rtype -eq "task") {
          $al = $r.allowlist
          if ($al -and $al.Count -gt 0 -and "$($al[0])" -ne "*") {
            $matched = $false
            for ($j = 0; $j -lt $al.Count; $j++) { if ($Task -like "$($al[$j])") { $matched = $true; break } }
            if (-not $matched) { $violations += @{ rule = $rid; severity = "block"; message = "task not in allowlist"; action = "block" } }
          }
        }
      }
    }
    $blocked = @($violations | Where-Object { $_.action -eq "block" })
    Write-Output (@{ passed = ($blocked.Count -eq 0); violations = $violations; summary = if ($blocked.Count -gt 0) { "BLOCKED" } elseif ($violations.Count -gt 0) { "WARN" } else { "PASS" } } | ConvertTo-Json -Depth 3)
  }

  "allow" {
    $check = & $MyInvocation.MyCommand.Path -Action check -Content $Content -Agent $Agent -Task $Task -ConfigFile $ConfigFile 2>&1 | Out-String
    $result = $check | ConvertFrom-Json
    if ($result.passed) { exit 0 } else { Write-Output ($result | ConvertTo-Json -Depth 3); exit 1 }
  }

  "block" {
    Write-Output (@{ action = "blocked"; message = "$Agent blocked: $Task" } | ConvertTo-Json)
  }

  "config" {
    $config = Load-Config
    if ($Rule) { Write-Output ($config.rules | Where-Object { "$($_.id)" -eq $Rule } | ConvertTo-Json -Depth 3) }
    else { Write-Output ($config | ConvertTo-Json -Depth 3) }
  }

  "report" {
    $cfg = Load-Config
    $summary = @()
    if ($cfg -and $cfg.rules) {
      for ($i = 0; $i -lt $cfg.rules.Count; $i++) {
        $r = $cfg.rules[$i]
        $summary += @{ id = "$($r.id)"; type = "$($r.type)"; enabled = "$($r.enabled)" -eq "True"; action = "$($r.action)"; description = "$($r.description)" }
      }
    }
    Write-Output (@{ total = $summary.Count; enabled = @($summary | Where-Object { $_.enabled }).Count; rules = $summary } | ConvertTo-Json -Depth 2)
  }

  "reset" {
    Save-Config ($DefaultConfig | ConvertTo-Json -Depth 10 | ConvertFrom-Json)
    Write-Output "guardrails reset to defaults"
  }
}
