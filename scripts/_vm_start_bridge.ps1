#Requires -RunAsAdministrator
# _vm_start_bridge.ps1
# THE definitive bridge setup — run once on the VM as Administrator.
# After this, azureuser auto-logs in on every boot and the bridge is ALWAYS
# running in Session 1 (interactive desktop), never Session 0 (SYSTEM/service).
#
# What this script does:
#   1. Writes the bridge .bat launcher with env vars baked in
#   2. Configures Windows AutoLogon so azureuser logs in automatically on boot
#   3. Disables lock screen / sleep so the session never disappears
#   4. Registers the scheduled task as azureuser (AtLogon) — guaranteed Session 1
#   5. Opens the firewall
#   6. Kills any stale Session-0 bridge and starts the bridge right now
#   7. Verifies everything end-to-end

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PythonExe  = "C:\Program Files\Python311\python.exe"
$BridgePy   = "C:\mcp-factory\scripts\gui_bridge.py"
$WrapperBat = "C:\mcp-factory\scripts\_bridge_launcher.bat"
$Port       = 8090
$TaskName   = "MCP-Factory-GUI-Bridge"
$Secret     = "BridgeSecret2026xVM01"
$BridgeUser = "azureuser"

# ── 1. Write the .bat launcher ────────────────────────────────────────────────
$batContent = "@echo off`r`nset BRIDGE_SECRET=$Secret`r`nset BRIDGE_PORT=$Port`r`n`"$PythonExe`" `"$BridgePy`"`r`n"
[System.IO.File]::WriteAllText($WrapperBat, $batContent, [System.Text.Encoding]::ASCII)
Write-Host "[OK] Launcher bat written: $WrapperBat"

# ── 2. Configure AutoLogon ────────────────────────────────────────────────────
# This makes azureuser log in automatically on every boot/reboot so the VM
# ALWAYS has an interactive Session 1 desktop — even after Azure restarts it.
$WinLogonKey = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
$UserPass = Read-Host "Enter the Windows password for '$BridgeUser' (stored in registry for AutoLogon)"

Set-ItemProperty -Path $WinLogonKey -Name "AutoAdminLogon"    -Value "1"          -Type String
Set-ItemProperty -Path $WinLogonKey -Name "DefaultUserName"   -Value $BridgeUser  -Type String
Set-ItemProperty -Path $WinLogonKey -Name "DefaultPassword"   -Value $UserPass    -Type String
Set-ItemProperty -Path $WinLogonKey -Name "DefaultDomainName" -Value "."          -Type String
# Prevent "legal notice" dialogs from blocking autologon
Remove-ItemProperty -Path $WinLogonKey -Name "LegalNoticeCaption" -ErrorAction SilentlyContinue
Remove-ItemProperty -Path $WinLogonKey -Name "LegalNoticeText"    -ErrorAction SilentlyContinue
Write-Host "[OK] AutoLogon configured for $BridgeUser"

# ── 3. Disable lock screen / sleep / screensaver ──────────────────────────────
# A locked/sleeping session is effectively gone — pywinauto can't interact with it.
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
# Turn off screensaver for azureuser (loaded at their next logon)
$ScrnSavKey = "HKCU:\Control Panel\Desktop"
if (Test-Path $ScrnSavKey) {
    Set-ItemProperty -Path $ScrnSavKey -Name "ScreenSaveActive" -Value "0" -Type String
}
# Disable the lock screen via group policy key (machine-wide)
$LockKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization"
if (-not (Test-Path $LockKey)) { New-Item -Path $LockKey -Force | Out-Null }
Set-ItemProperty -Path $LockKey -Name "NoLockScreen" -Value 1 -Type DWord
Write-Host "[OK] Lock screen / sleep / screensaver disabled"

# ── 4. Register scheduled task as azureuser (AtLogon, Session 1) ──────────────
# Remove whatever task exists (SYSTEM or otherwise)
schtasks /delete /tn $TaskName /f 2>&1 | Out-Null

# schtasks /RU <user> /RP <pass> /SC ONLOGON runs in the user's interactive
# session — this is the ONLY reliable way to guarantee Session 1.
# /DELAY 0:10 gives the desktop 10 seconds to settle before the bridge starts.
$result = schtasks /Create `
    /TN  $TaskName `
    /TR  "`"$WrapperBat`"" `
    /SC  ONLOGON `
    /RU  "$env:COMPUTERNAME\$BridgeUser" `
    /RP  $UserPass `
    /RL  HIGHEST `
    /DELAY 0:10 `
    /F 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "[FAIL] schtasks /Create failed: $result"
}
Write-Host "[OK] Scheduled task registered: $TaskName (runs as $BridgeUser at logon)"

# Also enable auto-restart on failure via XML tweak (schtasks CLI can't do this)
$xml = (schtasks /Query /TN $TaskName /XML ONE 2>&1) -join "`n"
if ($xml -notmatch "RestartOnFailure") {
    $xml = $xml -replace '</Settings>', @"
  <RestartOnFailure>
    <Interval>PT1M</Interval>
    <Count>99</Count>
  </RestartOnFailure>
</Settings>
"@
    $tmpXml = [System.IO.Path]::GetTempFileName() + ".xml"
    [System.IO.File]::WriteAllText($tmpXml, $xml, [System.Text.Encoding]::Unicode)
    schtasks /Create /TN $TaskName /XML $tmpXml /RU "$env:COMPUTERNAME\$BridgeUser" /RP $UserPass /F 2>&1 | Out-Null
    Remove-Item $tmpXml -ErrorAction SilentlyContinue
    Write-Host "[OK] Auto-restart on failure configured (every 60s, up to 99 times)"
}

# ── 5. Firewall ───────────────────────────────────────────────────────────────
$fwRule = Get-NetFirewallRule -DisplayName "MCP Bridge $Port" -ErrorAction SilentlyContinue
if (-not $fwRule) {
    New-NetFirewallRule -DisplayName "MCP Bridge $Port" -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
    Write-Host "[OK] Firewall rule created for port $Port"
} else {
    Write-Host "[INFO] Firewall rule already exists for port $Port"
}

# ── 6. Kill any stale bridge and start now ────────────────────────────────────
$stale = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($stale) {
    $stale | ForEach-Object { try { Stop-Process -Id $_.OwningProcess -Force } catch {} }
    Start-Sleep -Seconds 2
    Write-Host "[OK] Killed stale bridge process"
}

# Run the task immediately (azureuser IS logged in right now so this lands in Session 1)
schtasks /Run /TN $TaskName 2>&1 | Out-Null
Write-Host "[OK] Task triggered — waiting for bridge to start..."
Start-Sleep -Seconds 12

# ── 7. Verify ─────────────────────────────────────────────────────────────────
$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $wmi   = Get-WmiObject Win32_Process -Filter "ProcessId=$($conn.OwningProcess)"
    $owner = $wmi.GetOwner()
    Write-Host ""
    Write-Host "Bridge PID     : $($conn.OwningProcess)"
    Write-Host "Session ID     : $($wmi.SessionId)  $(if ($wmi.SessionId -eq 1) { '<-- GOOD: interactive' } else { '<-- BAD: Session 0!' })"
    Write-Host "Running as     : $($owner.Domain)\$($owner.User)"
    try {
        $h = Invoke-RestMethod -Uri "http://localhost:$Port/health" -TimeoutSec 8
        Write-Host "Health check   : OK — $($h | ConvertTo-Json -Compress)"
    } catch {
        Write-Warning "Health check failed (bridge may still be starting): $_"
    }
    Write-Host ""
    if ($wmi.SessionId -ne 1) {
        Write-Warning "Still Session 0 — reboot the VM now. AutoLogon will kick in and place the bridge in Session 1 permanently."
    } else {
        Write-Host "[SUCCESS] Bridge is running in Session 1 and will survive reboots via AutoLogon."
    }
} else {
    Write-Warning "Bridge is not listening on port $Port yet. Check: Get-NetTCPConnection -LocalPort $Port"
}
