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

$pyDir       = 'C:\Python310'
$pyExe       = "$pyDir\python.exe"
$pyInstaller = Join-Path $env:TEMP 'python310-amd64.exe'
$pyUrl       = 'https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe'

if (-not (Test-Path $pyExe)) {
    Write-Host "Downloading Python 3.10 installer..."
    Invoke-WebRequest -Uri $pyUrl -OutFile $pyInstaller -UseBasicParsing
    Write-Host "Running Python installer (TargetDir=$pyDir)..."
    $proc = Start-Process $pyInstaller `
        -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 TargetDir=`"$pyDir`"" `
        -Wait -PassThru
    Write-Host "Python installer exit code: $($proc.ExitCode)"
    Remove-Item $pyInstaller -ErrorAction SilentlyContinue
    if (-not (Test-Path $pyExe)) { throw "Python installation failed - $pyExe not found" }
}

# Prepend the known install dir to PATH for this session (registry refresh is unreliable under SYSTEM)
$env:PATH = "$pyDir;$pyDir\Scripts;$env:PATH"

& $pyExe --version

# ---------------------------------------------------------------------------
# 2. Install Python packages
# ---------------------------------------------------------------------------
Write-Step 'Installing Python packages'

& $pyExe -m pip install --quiet --upgrade pip 2>&1 | Out-Null
& $pyExe -m pip install --quiet `
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

& 'C:\Python310\python.exe' '$bridgePy'
"@ | Set-Content $bridgeScript -Encoding UTF8

# ── Configure Windows auto-logon so the VM logs in on reboot without RDP ──
# This is required for pywinauto / UIA — they need an interactive desktop
# (Session 1). A SYSTEM/Session 0 task cannot launch visible windows or read
# the UIA accessibility tree of apps like Calculator.
$_user     = 'azureuser'
$_password = (Get-Content 'C:\mcp-bridge\.env' -ErrorAction SilentlyContinue |
              Where-Object { $_ -match '^VM_PASSWORD=' } |
              ForEach-Object { $_.Split('=', 2)[1] }) -join ''
if (-not $_password) { $_password = 'McpBridge@2026!' }  # safe fallback

$_wl = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty $_wl -Name AutoAdminLogon    -Value '1'
Set-ItemProperty $_wl -Name DefaultUserName   -Value $_user
Set-ItemProperty $_wl -Name DefaultPassword   -Value $_password
Set-ItemProperty $_wl -Name DefaultDomainName -Value $env:COMPUTERNAME
Write-Host "Auto-logon configured for $_user — VM will sign in automatically on reboot." -ForegroundColor Green

# ── Register the bridge as an AtLogOn task running as azureuser ───────────
# AtLogOn fires when azureuser's interactive session starts (auto-logon or
# manual RDP).  The task runs in Session 1 with a real desktop, so pywinauto
# and UIA work correctly.  RestartCount 99 means "always restart" after a crash.
$action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
               -Argument "-NonInteractive -WindowStyle Hidden -File `"$bridgeScript`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $_user
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 99 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:COMPUTERNAME\$_user" `
    -LogonType Password `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName   'MCPFactoryGUIBridge' `
    -Action     $action `
    -Trigger    $trigger `
    -Settings   $settings `
    -Principal  $principal `
    -Password   $_password `
    -Force | Out-Null

# Start immediately (user is already logged on during provisioning)
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
