#Requires -RunAsAdministrator
# _vm_start_bridge.ps1 -- Run once on the VM as Administrator.
# Configures AutoLogon + Session 1 scheduled task so the bridge is ALWAYS up.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PythonExe  = "C:\Program Files\Python311\python.exe"
$BridgePy   = "C:\mcp-factory\scripts\gui_bridge.py"
$WrapperBat = "C:\mcp-factory\scripts\_bridge_launcher.bat"
$Port       = 8090
$TaskName   = "MCP-Factory-GUI-Bridge"
$Secret     = "BridgeSecret2026xVM01"
$BridgeUser = "azureuser"

# ---- 1. Write the .bat launcher
# The bat kills anything on port 8090 before starting Python so there is
# never a port collision on restart or reboot.
# It also runs `git pull` first so the bridge always runs the latest code
# without any manual update step.
$batContent = "@echo off`r`nset BRIDGE_SECRET=$Secret`r`nset BRIDGE_PORT=$Port`r`necho [bridge] Syncing latest code from GitHub...`r`ngit -C C:\mcp-factory fetch origin`r`ngit -C C:\mcp-factory reset --hard origin/main`r`nfor /f `"tokens=5`" %%a in ('netstat -ano ^| findstr :$Port ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1`r`ntimeout /t 2 /nobreak >nul`r`n`"$PythonExe`" `"$BridgePy`"`r`n"
[System.IO.File]::WriteAllText($WrapperBat, $batContent, [System.Text.Encoding]::ASCII)
Write-Host "[OK] Launcher bat written: $WrapperBat"

# ---- 2. Configure AutoLogon
$WinLogonKey = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
$UserPass = Read-Host "Enter the Windows password for $BridgeUser (stored for AutoLogon)"

Set-ItemProperty -Path $WinLogonKey -Name "AutoAdminLogon"    -Value "1"         -Type String
Set-ItemProperty -Path $WinLogonKey -Name "DefaultUserName"   -Value $BridgeUser -Type String
Set-ItemProperty -Path $WinLogonKey -Name "DefaultPassword"   -Value $UserPass   -Type String
Set-ItemProperty -Path $WinLogonKey -Name "DefaultDomainName" -Value "."         -Type String
Remove-ItemProperty -Path $WinLogonKey -Name "LegalNoticeCaption" -ErrorAction SilentlyContinue
Remove-ItemProperty -Path $WinLogonKey -Name "LegalNoticeText"    -ErrorAction SilentlyContinue
Write-Host "[OK] AutoLogon configured for $BridgeUser"

# ---- 3. Disable lock screen / sleep / screensaver
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /change hibernate-timeout-ac 0

$ScrnSavKey = "HKCU:\Control Panel\Desktop"
if (Test-Path $ScrnSavKey) {
    Set-ItemProperty -Path $ScrnSavKey -Name "ScreenSaveActive" -Value "0" -Type String
}
$LockKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization"
if (-not (Test-Path $LockKey)) { New-Item -Path $LockKey -Force | Out-Null }
Set-ItemProperty -Path $LockKey -Name "NoLockScreen" -Value 1 -Type DWord
Write-Host "[OK] Lock screen / sleep / screensaver disabled"

# ---- 4. Register scheduled task as azureuser (ONLOGON = Session 1)
# /delete fails with exit 1 if the task does not exist yet -- that is fine.
# Temporarily silence $ErrorActionPreference so PS5.1 does not throw on it.
$ErrorActionPreference = "SilentlyContinue"
schtasks /delete /tn $TaskName /f 2>&1 | Out-Null
$ErrorActionPreference = "Stop"

$createResult = schtasks /Create /TN $TaskName /TR "`"$WrapperBat`"" /SC ONLOGON /RU "$env:COMPUTERNAME\$BridgeUser" /RP $UserPass /RL HIGHEST /F 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "[FAIL] schtasks /Create failed: $createResult"
}
Write-Host "[OK] Scheduled task registered as $BridgeUser (ONLOGON)"

# Patch XML to add RestartOnFailure
$xml = (schtasks /Query /TN $TaskName /XML ONE 2>&1) -join "`n"
if ($xml -notmatch "RestartOnFailure") {
    $restartBlock = "  <RestartOnFailure><Interval>PT1M</Interval><Count>99</Count></RestartOnFailure></Settings>"
    $xml = $xml -replace '</Settings>', $restartBlock
    $tmpXml = [System.IO.Path]::GetTempFileName() + ".xml"
    [System.IO.File]::WriteAllText($tmpXml, $xml, [System.Text.Encoding]::Unicode)
    schtasks /Create /TN $TaskName /XML $tmpXml /RU "$env:COMPUTERNAME\$BridgeUser" /RP $UserPass /F 2>&1 | Out-Null
    Remove-Item $tmpXml -ErrorAction SilentlyContinue
    Write-Host "[OK] RestartOnFailure configured (every 60s, up to 99 retries)"
}

# ---- 5. Firewall
$fwRule = Get-NetFirewallRule -DisplayName "MCP Bridge $Port" -ErrorAction SilentlyContinue
if (-not $fwRule) {
    New-NetFirewallRule -DisplayName "MCP Bridge $Port" -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
    Write-Host "[OK] Firewall rule created for port $Port"
} else {
    Write-Host "[INFO] Firewall rule already exists for port $Port"
}

# ---- 6. Kill stale bridge and start now
$stale = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($stale) {
    $stale | ForEach-Object {
        try { Stop-Process -Id $_.OwningProcess -Force } catch { }
    }
    Start-Sleep -Seconds 2
    Write-Host "[OK] Killed stale bridge process"
}

schtasks /Run /TN $TaskName 2>&1 | Out-Null
Write-Host "[OK] Task triggered - waiting 12s for bridge to start..."
Start-Sleep -Seconds 12

# ---- 7. Verify
$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $wmi   = Get-WmiObject Win32_Process -Filter "ProcessId=$($conn.OwningProcess)"
    $owner = $wmi.GetOwner()
    $sid   = $wmi.SessionId

    Write-Host ""
    Write-Host "Bridge PID  : $($conn.OwningProcess)"
    Write-Host "Session ID  : $sid"
    Write-Host "Running as  : $($owner.Domain)\$($owner.User)"

    try {
        $h = Invoke-RestMethod -Uri "http://localhost:$Port/health" -TimeoutSec 8
        Write-Host "Health      : OK"
    } catch {
        Write-Warning "Health check failed (bridge may still be starting): $_"
    }

    Write-Host ""
    if ($sid -ne 1) {
        Write-Warning "Still Session 0. Reboot the VM - AutoLogon will place the bridge in Session 1 permanently."
    } else {
        Write-Host "[SUCCESS] Bridge is in Session 1 and will survive reboots via AutoLogon."
    }
} else {
    Write-Warning "Bridge is not listening on port $Port. Check: Get-NetTCPConnection -LocalPort $Port"
}