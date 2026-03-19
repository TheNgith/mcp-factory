# watch-and-run.ps1
# Keeps running on your desktop. Watches for new git commits on main and
# automatically triggers ci-run.ps1 after every push when the bridge is open.
#
# Usage:
#   .\sessions\watch-and-run.ps1 -ApiUrl "https://mcp-factory-ui.icycoast-8ddfa278.eastus.azurecontainerapps.io" -DllPath "C:\path\to\contoso_cs.dll"
#
# Leave this running in a terminal. Every time you push and the bridge VM is up,
# it runs the full pipeline automatically and saves the session to sessions/.
#
# Press Ctrl+C to stop.

param(
    [Parameter(Mandatory=$true)][string]$ApiUrl,
    [Parameter(Mandatory=$true)][string]$DllPath,
    [string]$ApiKey        = "",
    [string]$SessionsRoot  = $PSScriptRoot,
    [string]$RepoRoot      = (Resolve-Path "$PSScriptRoot\.."),
    [int]   $PollSec       = 30,    # how often to check for new commits
    [switch]$RunOnce               # run immediately then exit (useful for testing)
)

$ErrorActionPreference = "Stop"

if (-not $ApiKey -and $env:PIPELINE_API_KEY) { $ApiKey = $env:PIPELINE_API_KEY }

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  MCP Factory -- Local CI Watcher" -ForegroundColor Cyan
Write-Host "  Polling every ${PollSec}s for new commits" -ForegroundColor DarkCyan
Write-Host "  Repo: $RepoRoot" -ForegroundColor DarkCyan
Write-Host "  DLL:  $DllPath" -ForegroundColor DarkCyan
Write-Host "  Press Ctrl+C to stop" -ForegroundColor DarkGray
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# -- Get current HEAD ----------------------------------------------------------
Push-Location $RepoRoot
$lastKnownCommit = git rev-parse HEAD 2>$null
if (-not $lastKnownCommit) { $lastKnownCommit = "" }
Write-Host "  Watching from commit: $($lastKnownCommit.Substring(0,[Math]::Min(8,$lastKnownCommit.Length)))" -ForegroundColor DarkGray

$ciScript = Join-Path $SessionsRoot "ci-run.ps1"
if (-not (Test-Path $ciScript)) {
    Write-Host "  ERROR: ci-run.ps1 not found at $ciScript" -ForegroundColor Red
    Pop-Location; exit 1
}
if (-not (Test-Path $DllPath)) {
    Write-Host "  ERROR: DLL not found at $DllPath" -ForegroundColor Red
    Pop-Location; exit 1
}

function Run-Pipeline {
    param([string]$Commit)
    Write-Host ""
    Write-Host "  *** New commit detected: $($Commit.Substring(0,8)) ***" -ForegroundColor Yellow
    Write-Host "  Starting E2E pipeline at $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Yellow
    Write-Host ""

    $ciArgs = @{ ApiUrl = $ApiUrl; DllPath = $DllPath; SessionsRoot = $SessionsRoot }
    if ($ApiKey) { $ciArgs['ApiKey'] = $ApiKey }

    try {
        & $ciScript @ciArgs
        $exitCode = $LASTEXITCODE
        if ($exitCode -eq 0) {
            Write-Host "  Pipeline completed successfully" -ForegroundColor Green
        } else {
            Write-Host "  Pipeline completed with exit code $exitCode" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  Pipeline threw an exception: $_" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "  Resuming watch..." -ForegroundColor DarkGray
    Write-Host ""
}

# -- Run immediately if -RunOnce -----------------------------------------------
if ($RunOnce) {
    Run-Pipeline -Commit $lastKnownCommit
    Pop-Location; exit 0
}

# -- Watch loop ----------------------------------------------------------------
Write-Host "  Watching... (last commit: $($lastKnownCommit.Substring(0,[Math]::Min(8,$lastKnownCommit.Length))))" -ForegroundColor DarkGray

while ($true) {
    Start-Sleep -Seconds $PollSec

    # Fetch latest from origin silently
    git fetch origin main --quiet 2>$null | Out-Null

    $remoteCommit = git rev-parse origin/main 2>$null
    if (-not $remoteCommit) {
        Write-Host "  [watch] Could not read origin/main -- is internet connected?" -ForegroundColor DarkYellow
        continue
    }

    if ($remoteCommit -ne $lastKnownCommit) {
        $msg = git log -1 --format="%s" origin/main 2>$null
        Write-Host "  [watch] New commit: $($remoteCommit.Substring(0,8)) -- $msg" -ForegroundColor Cyan

        # Pull the new code before running
        git pull origin main --quiet 2>$null | Out-Null

        $lastKnownCommit = $remoteCommit
        Run-Pipeline -Commit $remoteCommit

        Write-Host "  [watch] Waiting for next push..." -ForegroundColor DarkGray
    }
}

Pop-Location
