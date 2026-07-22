<# MAOP shared YAML bridge
   Dot-source this script to get Invoke-ConfigBridge, replacing hand-rolled regex parsers.
   Usage: . (Join-Path $ProjectRoot "tools\MAOP-bridge.ps1")

   Python resolution strategy (in priority order):
   1. $env:PEV_PYTHON — explicit override
   2. py -3 launcher (Windows Python Launcher, bypasses broken cmd wrappers)
   3. System python.exe (skip any .cmd/.bat wrappers in PATH that may have space bugs)
   4. python / python3 (last resort, may hit wrapper issues)
#>
$ParseConfigPy = Join-Path $PSScriptRoot "parse-config.py"

# ── Resolve a working Python executable (returns .exe path or $null) ──
function Resolve-PevPython {
  # 1. Explicit override
  if ($env:PEV_PYTHON -and (Test-Path $env:PEV_PYTHON)) {
    return $env:PEV_PYTHON
  }

  # 2. Try py launcher to find the real .exe path
  $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    $realExe = & py -3 -c "import sys; print(sys.executable)" 2>$null
    if ($realExe -and $realExe.Trim() -and (Test-Path $realExe.Trim())) {
      return $realExe.Trim()
    }
  }

  # 3. Find a real python.exe in PATH (skip .cmd/.bat wrappers)
  $pythonCmds = Get-Command python, python3 -ErrorAction SilentlyContinue
  foreach ($pc in $pythonCmds) {
    $src = $pc.Source
    if ($src -match '\.(cmd|bat)$') {
      # Wrapper script — try to get the real .exe it forwards to
      $realExe = & $src -c "import sys; print(sys.executable)" 2>$null
      if ($realExe -and $realExe.Trim() -and (Test-Path $realExe.Trim())) {
        return $realExe.Trim()
      }
      continue
    }
    if ($src -match '\.exe$') {
      $testResult = & $src -c "import sys; print('OK')" 2>&1
      if ($testResult -match 'OK') { return $src }
    }
  }

  # 4. Common install locations as fallback
  $commonPaths = @(
    "C:\Python*\python.exe",
    "$env:LocalAppData\Programs\Python\Python*\python.exe",
    "C:\Program Files\Python*\python.exe",
    "C:\Program Files (x86)\Python*\python.exe"
  )
  foreach ($pattern in $commonPaths) {
    $found = Get-ChildItem $pattern -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) {
      $testResult = & $found.FullName -c "import sys; print('OK')" 2>&1
      if ($testResult -match 'OK') { return $found.FullName }
    }
  }

  Write-Warning "MAOP: No working Python found. Set `$env:PEV_PYTHON to a valid python.exe path."
  return $null
}

$script:PevPython = Resolve-PevPython

function Invoke-ConfigBridge {
  param(
    [string]$BridgeArgs,
    [switch]$Critical
  )
  if (-not $script:PevPython) {
    $msg = "ConfigBridge: Python not available. Set `$env:PEV_PYTHON to a python.exe path."
    if ($Critical) {
      throw "[FAIL-FAST] $msg Bridge call '$BridgeArgs' is on a critical path and cannot degrade."
    }
    Write-Warning $msg
    return $null
  }
  try {
    $raw = & $script:PevPython $ParseConfigPy @($BridgeArgs.Split(' ')) 2>&1 | Out-String
    # Find JSON start — could be '{' (object) or '[' (array)
    $jsonStart = -1
    for ($i = 0; $i -lt $raw.Length; $i++) {
      $ch = $raw[$i]
      if ($ch -eq '{' -or $ch -eq '[') { $jsonStart = $i; break }
    }
    if ($jsonStart -ge 0) { $jsonText = $raw.Substring($jsonStart) } else { $jsonText = $raw }
    $result = $jsonText | ConvertFrom-Json -ErrorAction Stop
    # Check for error returned by parse-config.py itself
    if ($result -and $result.error -and $Critical) {
      throw "[FAIL-FAST] ConfigBridge returned error for '$BridgeArgs': $($result.error)"
    }
    return $result
  } catch {
    if ($Critical) {
      throw "[FAIL-FAST] ConfigBridge failed for '$BridgeArgs' (critical path): $($_.Exception.Message)"
    }
    Write-Warning "ConfigBridge failed for '$BridgeArgs': $($_.Exception.Message)"
    return $null
  }
}
