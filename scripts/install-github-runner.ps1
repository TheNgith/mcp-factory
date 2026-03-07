<#
.SYNOPSIS
    Bootstraps a GitHub Actions self-hosted runner on a fresh Windows Server VM.
    Invoked automatically by the Azure CustomScriptExtension on first boot.

.PARAMETER RepoUrl
    Full GitHub repository URL.
    Example: https://github.com/evanking12/mcp-factory

.PARAMETER RunnerToken
    Ephemeral runner registration token (1 hour TTL).
    Generate with: gh api repos/<owner>/<repo>/actions/runners/registration-token --method POST --jq .token

.PARAMETER RunnerName
    Display name shown in GitHub Actions UI → Settings → Runners.
    Defaults to the machine hostname.

.NOTES
    • Installs Python 3.10 system-wide (PrependPath=1).
    • Installs all Python packages needed for GUI automation and MCP tests.
    • Downloads the latest GitHub Actions runner release from GitHub.
    • Configures the runner with labels: self-hosted, windows, x64
    • Installs the runner as a Windows service (starts on boot).
#>

[CmdletBinding()]
param (
    [string] $RepoUrl    = 'https://github.com/evanking12/mcp-factory',
    [string] $RunnerToken = '',
    [string] $RunnerName  = $env:COMPUTERNAME
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Step { param([string]$Message) Write-Host "`n==> $Message" -ForegroundColor Cyan }

# ---------------------------------------------------------------------------
# 1. Install Python 3.10
# ---------------------------------------------------------------------------
Write-Step 'Installing Python 3.10'

$pyInstaller = Join-Path $env:TEMP 'python310-amd64.exe'
$pyUrl       = 'https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe'

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Invoke-WebRequest -Uri $pyUrl -OutFile $pyInstaller -UseBasicParsing
    Start-Process $pyInstaller `
        -ArgumentList '/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1' `
        -Wait
    Remove-Item $pyInstaller -ErrorAction SilentlyContinue
}

# Reload PATH so python/pip binaries are visible in this session
$machinePath = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
$userPath    = [System.Environment]::GetEnvironmentVariable('Path', 'User')
$env:PATH    = "$machinePath;$userPath"

python --version

# ---------------------------------------------------------------------------
# 2. Install Python packages
# ---------------------------------------------------------------------------
Write-Step 'Installing Python packages'

pip install --quiet --upgrade pip
pip install --quiet `
    flask>=3.0 `
    openai>=1.0 `
    python-dotenv>=1.0 `
    mcp>=1.0 `
    pywinauto `
    comtypes `
    pywin32 `
    pytest `
    fastapi `
    uvicorn `
    httpx `
    azure-storage-blob `
    azure-identity `
    opencensus-ext-azure

# ---------------------------------------------------------------------------
# 3. Install and start the GUI bridge as a Windows service
# ---------------------------------------------------------------------------
Write-Step 'Installing GUI bridge service'

# ── Write a dedicated startup script ──────────────────────────────────────
$bridgeDir    = 'C:\mcp-bridge'
$bridgeScript = Join-Path $bridgeDir 'start_bridge.ps1'

New-Item -ItemType Directory -Force -Path $bridgeDir | Out-Null

# Copy gui_bridge.py from the repo checkout into the bridge directory so it
# survives future runner re-registrations (runner dir is routinely wiped).
$repoRoot = 'C:\actions-runner\_work\mcp-factory\mcp-factory'
$bridgePy  = Join-Path $bridgeDir 'gui_bridge.py'

# Write the startup wrapper — reads BRIDGE_SECRET from a local env file
@"
`$env:BRIDGE_SECRET = (Get-Content 'C:\mcp-bridge\.env' -ErrorAction SilentlyContinue | Where-Object { `$_ -match '^BRIDGE_SECRET=' } | ForEach-Object { `$_.Split('=',2)[1] }) -join ''
`$env:BRIDGE_PORT   = '8090'
`$env:PYTHONPATH    = 'C:\actions-runner\_work\mcp-factory\mcp-factory\src\discovery'

# Copy latest gui_bridge.py from the repo on each start
`$src = 'C:\actions-runner\_work\mcp-factory\mcp-factory\scripts\gui_bridge.py'
if (Test-Path `$src) { Copy-Item `$src -Destination '$bridgePy' -Force }

python '$bridgePy'
"@ | Set-Content $bridgeScript -Encoding UTF8

# Register as a scheduled task that starts at system boot (survives reboots)
$action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
               -Argument "-NonInteractive -WindowStyle Hidden -File `"$bridgeScript`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask `
    -TaskName   'MCPFactoryGUIBridge' `
    -Action     $action `
    -Trigger    $trigger `
    -Settings   $settings `
    -Principal  $principal `
    -Force | Out-Null

# Start immediately so the bridge is available without a reboot
Start-ScheduledTask -TaskName 'MCPFactoryGUIBridge'
Write-Host "GUI bridge service registered and started on port 8090." -ForegroundColor Green
Write-Host "Set BRIDGE_SECRET in C:\mcp-bridge\.env  (BRIDGE_SECRET=<value>)"

# ---------------------------------------------------------------------------
# 4. Download & configure the GitHub Actions runner
# ---------------------------------------------------------------------------
Write-Step 'Setting up GitHub Actions runner'

$runnerDir = 'C:\actions-runner'
New-Item -ItemType Directory -Force -Path $runnerDir | Out-Null
Set-Location $runnerDir

# Find the latest runner release
$release     = Invoke-RestMethod -Uri 'https://api.github.com/repos/actions/runner/releases/latest'
$version     = $release.tag_name.TrimStart('v')
$downloadUrl = "https://github.com/actions/runner/releases/download/v${version}/actions-runner-win-x64-${version}.zip"

Write-Host "Downloading GitHub Actions runner v${version}..."
Invoke-WebRequest -Uri $downloadUrl -OutFile 'actions-runner.zip' -UseBasicParsing
Expand-Archive -Path 'actions-runner.zip' -DestinationPath $runnerDir -Force
Remove-Item 'actions-runner.zip'

# Configure — connects to GitHub and registers the runner
Write-Host "Configuring runner for $RepoUrl ..."
.\config.cmd `
    --url          $RepoUrl `
    --token        $RunnerToken `
    --name         $RunnerName `
    --labels       'self-hosted,windows,x64' `
    --runasservice `
    --unattended

# Install and start the Windows service
Write-Step 'Installing and starting runner service'
.\svc.cmd install
.\svc.cmd start

Write-Host "`nRunner '$RunnerName' is registered and running." -ForegroundColor Green
Write-Host "Verify at: $RepoUrl/settings/actions/runners"
