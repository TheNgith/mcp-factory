# install-bridge-service.ps1
# Run this ONCE on the Windows runner VM (as Administrator).
# It generates a BRIDGE_SECRET, installs the bridge as a Scheduled Task,
# opens the firewall, and starts the bridge immediately.
#
# Usage (in repo root, as Admin):
#   .\scripts\install-bridge-service.ps1

#Requires -RunAsAdministrator

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot   = Split-Path $PSScriptRoot -Parent
$BridgePy   = Join-Path $RepoRoot "scripts\gui_bridge.py"
$PythonExe  = (Get-Command python).Source
$EnvFile    = Join-Path $RepoRoot "scripts\.bridge.env"
$TaskName   = "MCP-Factory-GUI-Bridge"
$BridgePort = 8090

# 1. Generate or reuse secret
if (Test-Path $EnvFile) {
    Write-Host "[INFO] Reusing existing bridge env at $EnvFile"
    $line = Get-Content $EnvFile | Where-Object { $_ -match '^BRIDGE_SECRET=' }
    $BridgeSecret = ($line -split '=', 2)[1]
} else {
    Add-Type -AssemblyName System.Security
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $BridgeSecret = [Convert]::ToBase64String($bytes) -replace '[+/=]', ''
    "BRIDGE_SECRET=$BridgeSecret`nBRIDGE_PORT=$BridgePort" | Set-Content $EnvFile
    Write-Host "[OK] Generated BRIDGE_SECRET and saved to $EnvFile"
}

Write-Host ""
Write-Host "========================================="
Write-Host "  BRIDGE_SECRET = $BridgeSecret"
Write-Host "  Copy this for wire-bridge-to-aca.ps1"
Write-Host "========================================="
Write-Host ""

# 2. Remove stale task
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[OK] Removed stale task $TaskName"
}

# 3. Register Scheduled Task
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c for /f ""tokens=1,2 delims=="" %A in ($EnvFile) do set %A=%B && ""$PythonExe"" ""$BridgePy"""

$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $action `
    -Trigger   $trigger `
    -Settings  $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "[OK] Scheduled task registered: $TaskName"

# 4. Open firewall
$fwRule = Get-NetFirewallRule -DisplayName "MCP Bridge $BridgePort" -ErrorAction SilentlyContinue
if (-not $fwRule) {
    New-NetFirewallRule `
        -DisplayName "MCP Bridge $BridgePort" `
        -Direction   Inbound `
        -Protocol    TCP `
        -LocalPort   $BridgePort `
        -Action      Allow | Out-Null
    Write-Host "[OK] Firewall rule created for port $BridgePort"
} else {
    Write-Host "[INFO] Firewall rule already exists for port $BridgePort"
}

# 5. Kill stale process and start bridge now
$stale = Get-NetTCPConnection -LocalPort $BridgePort -ErrorAction SilentlyContinue
if ($stale) {
    $stale | ForEach-Object {
        try { Stop-Process -Id $_.OwningProcess -Force } catch {}
    }
    Start-Sleep -Seconds 1
    Write-Host "[OK] Cleared stale process on port $BridgePort"
}

$env:BRIDGE_SECRET = $BridgeSecret
$env:BRIDGE_PORT   = "$BridgePort"
Start-Process -FilePath $PythonExe -ArgumentList """$BridgePy""" -NoNewWindow
Start-Sleep -Seconds 3

# 6. Health check
try {
    $resp = Invoke-RestMethod -Uri "http://localhost:$BridgePort/health" -TimeoutSec 5
    Write-Host "[OK] Bridge is UP: $($resp | ConvertTo-Json -Compress)"
} catch {
    Write-Warning "Bridge health check failed - may still be starting. Run: Invoke-RestMethod http://localhost:$BridgePort/health"
}

# 7. Auto-pull scheduled task (git pull + restart bridge every 5 minutes)
$AutoPullTaskName = "MCP-Factory-Bridge-AutoPull"
$AutoPullScript   = Join-Path $RepoRoot "scripts\_bridge_autopull.ps1"

# Write the auto-pull helper script next to this one
@"
# _bridge_autopull.ps1 — runs every 5 minutes via Scheduled Task.
# Pulls latest code from GitHub and restarts the bridge if anything changed.
Set-StrictMode -Version Latest
`$ErrorActionPreference = "SilentlyContinue"

`$RepoRoot = "$RepoRoot"
`$EnvFile  = "$EnvFile"
`$BridgePy = "$BridgePy"
`$Python   = "$PythonExe"
`$Port     = $BridgePort
`$TaskName = "$TaskName"

# Resolve git (try common install paths if not in PATH)
`$git = Get-Command git -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
if (-not `$git) {
    foreach (`$p in @(
        "`$env:ProgramFiles\Git\bin\git.exe",
        "`$env:ProgramFiles\Git\cmd\git.exe",
        "C:\Program Files\Git\bin\git.exe",
        "C:\Program Files\Git\cmd\git.exe"
    )) { if (Test-Path `$p) { `$git = `$p; break } }
}
if (-not `$git) { Write-Host "[autopull] git not found, skipping."; exit 0 }

Push-Location `$RepoRoot
`$before = & "`$git" rev-parse HEAD 2>`$null
& "`$git" fetch origin main --quiet 2>`$null
& "`$git" reset --hard origin/main --quiet 2>`$null
`$after  = & "`$git" rev-parse HEAD 2>`$null
Pop-Location

if (`$before -ne `$after) {
    Write-Host "[autopull] Updated `$before -> `$after. Restarting bridge…"
    # Kill process on bridge port then restart via Scheduled Task
    `$conn = Get-NetTCPConnection -LocalPort `$Port -ErrorAction SilentlyContinue
    if (`$conn) { `$conn | ForEach-Object { try { Stop-Process -Id `$_.OwningProcess -Force } catch {} } }
    Start-Sleep 2
    # Load env and restart
    `$line = Get-Content `$EnvFile | Where-Object { `$_ -match '^BRIDGE_SECRET=' }
    `$env:BRIDGE_SECRET = (`$line -split '=', 2)[1]
    `$env:BRIDGE_PORT   = "`$Port"
    Start-Process -FilePath `$Python -ArgumentList """"`$BridgePy"""" -NoNewWindow
    Write-Host "[autopull] Bridge restarted."
} else {
    Write-Host "[autopull] No changes ($((`$after).Substring(0,7))). Bridge unchanged."
}
"@ | Set-Content -Encoding UTF8 $AutoPullScript
Write-Host "[OK] Wrote auto-pull script to $AutoPullScript"

# Register the 5-minute auto-pull task
$existingAP = Get-ScheduledTask -TaskName $AutoPullTaskName -ErrorAction SilentlyContinue
if ($existingAP) {
    Unregister-ScheduledTask -TaskName $AutoPullTaskName -Confirm:$false
}

$apAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -ExecutionPolicy Bypass -File `"$AutoPullScript`""
$apTrigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 5) -Once -At (Get-Date)
$apSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 4) `
    -StartWhenAvailable
$apPrincipal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask `
    -TaskName  $AutoPullTaskName `
    -Action    $apAction `
    -Trigger   $apTrigger `
    -Settings  $apSettings `
    -Principal $apPrincipal `
    -Force | Out-Null
Write-Host "[OK] Auto-pull task registered: $AutoPullTaskName (every 5 minutes)"

Write-Host ""
Write-Host "Next step: run wire-bridge-to-aca.ps1 with the BRIDGE_SECRET above and the VM public IP."
Write-Host "Auto-pull: the bridge will update itself from GitHub every 5 minutes automatically."
