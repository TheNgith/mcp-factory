# install-bridge-service.ps1
#
# Run this ONCE on the Windows runner VM (as Administrator) to:
#   1. Generate a BRIDGE_SECRET and print it (copy it — you need it for ACA)
#   2. Persist the secret to a local .env file the bridge reads at startup
#   3. Register a Windows Scheduled Task that starts the bridge at boot
#   4. Open Windows Firewall for port 8090
#   5. Start it immediately
#
# Usage (on the VM, in the repo root):
#   .\scripts\install-bridge-service.ps1
#
# After running, copy the printed BRIDGE_SECRET and run:
#   .\scripts\wire-bridge-to-aca.ps1 -BridgeSecret "<secret>" -BridgeIP "<vm-public-ip>"

#Requires -RunAsAdministrator

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot   = Split-Path $PSScriptRoot -Parent
$BridgePy   = Join-Path $RepoRoot "scripts\gui_bridge.py"
$PythonExe  = (Get-Command python).Source
$EnvFile    = Join-Path $RepoRoot "scripts\.bridge.env"
$TaskName   = "MCP-Factory-GUI-Bridge"
$BridgePort = 8090

# ── 1. Generate secret ────────────────────────────────────────────────────────
if (Test-Path $EnvFile) {
    Write-Host "[INFO] Reusing existing bridge env at $EnvFile"
    $existing = Get-Content $EnvFile | Where-Object { $_ -match '^BRIDGE_SECRET=' }
    $BridgeSecret = ($existing -split '=', 2)[1]
} else {
    Add-Type -AssemblyName System.Security
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $BridgeSecret = [Convert]::ToBase64String($bytes) -replace '[+/=]', ''
    "BRIDGE_SECRET=$BridgeSecret`nBRIDGE_PORT=$BridgePort" | Set-Content $EnvFile
    Write-Host "[OK] Generated new BRIDGE_SECRET and saved to $EnvFile"
}

Write-Host ""
Write-Host "========================================="
Write-Host "  BRIDGE_SECRET = $BridgeSecret"
Write-Host "  Copy this — you need it for wire-bridge-to-aca.ps1"
Write-Host "========================================="
Write-Host ""

# ── 2. Remove stale task if it exists ─────────────────────────────────────────
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[OK] Removed stale task $TaskName"
}

# ── 3. Build the startup command ──────────────────────────────────────────────
# Reads BRIDGE_SECRET from .bridge.env, then launches uvicorn via gui_bridge.py
$StartCmd = @"
cmd /c "for /f `"tokens=1,2 delims==`" %A in ($EnvFile) do (set %A=%B) && $PythonExe $BridgePy"
"@

$action  = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c for /f `"tokens=1,2 delims==`" %A in ($EnvFile) do set %A=%B && `"$PythonExe`" `"$BridgePy`""
$trigger = New-ScheduledTaskTrigger -AtStartup
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

Write-Host "[OK] Scheduled task '$TaskName' registered (runs at boot as SYSTEM)"

# ── 4. Open firewall ──────────────────────────────────────────────────────────
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

# ── 5. Start it now without rebooting ────────────────────────────────────────
# Kill any stale bridge process on this port first
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
Start-Process -FilePath $PythonExe -ArgumentList "`"$BridgePy`"" -NoNewWindow
Start-Sleep -Seconds 3

# ── 6. Smoke test ─────────────────────────────────────────────────────────────
try {
    $resp = Invoke-RestMethod -Uri "http://localhost:$BridgePort/health" -TimeoutSec 5
    Write-Host "[OK] Bridge is UP: $($resp | ConvertTo-Json -Compress)"
} catch {
    Write-Warning "Bridge health check failed — it may still be starting. Check with: Invoke-RestMethod http://localhost:$BridgePort/health"
}

Write-Host ""
Write-Host "Next step: run wire-bridge-to-aca.ps1 with the BRIDGE_SECRET above and this VM's public IP."
