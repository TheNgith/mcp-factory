param(
    [Parameter(Mandatory = $true)][string]$ApiUrl,
    [string]$ApiKey = "",
    [int]$PollSeconds = 300,
    [int]$MaxCases = 4,
    [string]$Model = "gpt-4o-mini",
    [switch]$Once
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}

$runner = Join-Path $repoRoot "scripts\run_transition_isolation_matrix.py"
if (-not (Test-Path $runner)) {
    throw "Missing runner script: $runner"
}

$logDir = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir "autopilot-transition.log"

function Write-LoopLog {
    param([string]$Message)
    $ts = (Get-Date).ToString("o")
    $line = "[$ts] $Message"
    Write-Host $line
    Add-Content -Path $logPath -Value $line
}

Write-LoopLog "Autopilot loop starting. ApiUrl=$ApiUrl PollSeconds=$PollSeconds MaxCases=$MaxCases Model=$Model Once=$Once"

while ($true) {
    try {
        $effectiveKey = $ApiKey
        if (-not $effectiveKey) {
            $effectiveKey = [Environment]::GetEnvironmentVariable("MCP_FACTORY_API_KEY")
        }

        if (-not $effectiveKey) {
            Write-LoopLog "No API key available (MCP_FACTORY_API_KEY missing). Waiting."
        }
        else {
            Write-LoopLog "Launching isolation matrix run"
            $cmdArgs = @(
                $runner,
                "--api-url", $ApiUrl,
                "--api-key", $effectiveKey,
                "--max-cases", $MaxCases,
                "--model", $Model
            )
            & $pythonExe @cmdArgs
            $exitCode = $LASTEXITCODE
            Write-LoopLog "Isolation matrix run finished with exit code $exitCode"
        }
    }
    catch {
        Write-LoopLog "Loop error: $($_.Exception.Message)"
    }

    if ($Once) {
        Write-LoopLog "Autopilot loop exiting after single cycle (--Once)."
        break
    }

    Start-Sleep -Seconds $PollSeconds
}
