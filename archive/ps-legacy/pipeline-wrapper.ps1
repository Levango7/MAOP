<#
.SYNOPSIS
    MAOP Doc-Pipeline Wrapper
    供 MAOP orchestrator 调用 doc-pipeline 流水线
.PARAMETER Task
    任务描述（自然语言），将作为输入传给 doc-pipeline
.PARAMETER Output
    输出文件路径（可选，默认自动生成到 MAOP output 目录）
.PARAMETER Pipeline
    流水线名称（默认 doc）
.PARAMETER Queries
    额外检索查询词（数组）
.PARAMETER JsonOutput
    是否输出 JSON 到 stdout（供 MAOP 解析）
.OUTPUTS
    输出 JSON 到 stdout:
    {
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "output_path": "F:/path/to/output.md",
        "status": "completed",
        "steps": [...]
    }
#>
param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$Task,
    
    [Parameter(Mandatory=$false)]
    [string]$Output = "",
    
    [Parameter(Mandatory=$false)]
    [string]$Pipeline = "doc",
    
    [Parameter(Mandatory=$false)]
    [string[]]$Queries = @(),
    
    [Parameter(Mandatory=$false)]
    [switch]$JsonOutput
)

$ErrorActionPreference = "Stop"

# Doc-Pipeline 根目录
$PIPELINE_ROOT = "F:\Nexus\Workflow\doc-pipeline"
$PYTHON = "python"

# 生成临时输入文件
$inputFile = [IO.Path]::Combine([IO.Path]::GetTempPath(), "MAOP-doc-input-$(Get-Random).md")
$taskContent = @"
# MAOP Task: $Task

$Task

"@
if ($Queries.Count -gt 0) {
    $taskContent += "`n## Additional Queries`n"
    $taskContent += ($Queries | ForEach-Object { "- $_" }) -join "`n"
}
[IO.File]::WriteAllText($inputFile, $taskContent, [System.Text.Encoding]::UTF8)

# 确定输出路径
if (-not $Output) {
    $pevOutput = Join-Path (Split-Path (Split-Path $ScriptDir -Parent) -Parent) "output"
    if (-not (Test-Path $pevOutput)) { New-Item -ItemType Directory -Path $pevOutput | Out-Null }
    $Output = Join-Path $pevOutput "doc-output-$(Get-Date -Format 'yyyyMMdd-HHmmss').md"
}

# 构建参数
$args = @(
    $inputFile,
    "--pipeline", $Pipeline,
    "--output", $Output,
    "--task-id", "MAOP-$(Get-Date -Format 'yyyyMMddHHmmss')"
)
if ($Queries.Count -gt 0) {
    $args += "--queries"
    $args += $Queries
}
if ($JsonOutput) {
    $args += "--json-output"
}

try {
    # 不要 2>&1，让 stderr 直接输出到控制台（不作为错误）
    $result = & $PYTHON "$PIPELINE_ROOT\run.py" @args
    $exitCode = $LASTEXITCODE
    
    if ($JsonOutput) {
        # 尝试从输出中提取最后一行有效 JSON
        $lines = $result.Trim().Split("`n")
        $jsonObj = $null
        for ($i = $lines.Count - 1; $i -ge 0; $i--) {
            $line = $lines[$i].Trim()
            if ($line -match '^\{.*\}$') {
                try {
                    $jsonObj = $line | ConvertFrom-Json
                    break
                } catch { }
            }
        }
        
        if ($jsonObj) {
            # 确保输出路径正确
            if ($jsonObj.output_path -and (Test-Path $jsonObj.output_path)) {
                $jsonObj.output_path = $Output
            }
            $jsonObj | ConvertTo-Json -Depth 10
        }
        catch {
            # 回退：构造结果
            @{
                exit_code = $exitCode
                stdout = $result
                stderr = if ($exitCode -ne 0) { $result } else { "" }
                output_path = $Output
                status = if ($exitCode -eq 0) { "completed" } else { "failed" }
                steps = @()
            } | ConvertTo-Json -Depth 10
        }
    } else {
        Write-Host $result
    }
}
finally {
    if (Test-Path $inputFile) { Remove-Item $inputFile -Force }
}

exit $exitCode