$StartupDir = "C:\Users\azureuser\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup"
$VbsPath    = Join-Path $StartupDir "StartMCPBridge.vbs"
$BatPath    = "C:\mcp-factory\scripts\_bridge_launcher.bat"

# Ensure startup dir exists
if (-not (Test-Path $StartupDir)) {
    Write-Host "Startup dir missing - azureuser profile may not exist yet"
    exit 1
}

# Write VBS launcher (hidden window, non-blocking)
$vbsContent = @"
Set oShell = CreateObject("Wscript.Shell")
oShell.Run "$BatPath", 1, False
"@
[System.IO.File]::WriteAllText($VbsPath, $vbsContent, [System.Text.Encoding]::ASCII)
Write-Host "VBS launcher written to: $VbsPath"
Get-Content $VbsPath | ForEach-Object { Write-Host "  $_" }

# Kill current bridge (SYSTEM session bridge)
$stale = Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue
if ($stale) {
    $stale | ForEach-Object { try { Stop-Process -Id $_.OwningProcess -Force } catch {} }
    Start-Sleep -Seconds 2
    Write-Host "Killed existing bridge"
}

# Run the VBS NOW in azureuser's session using schtasks /create + /run as one-shot
# We'll use a temporary at-startup task that runs as azureuser via Interactive logon
$TmpTask = "MCPBridgeTmpStart"
schtasks /delete /tn $TmpTask /f 2>&1 | Out-Null
$createResult = schtasks /create /tn $TmpTask /tr $VbsPath /sc once /st 00:00 /sd 01/01/1970 /ru $env:COMPUTERNAME\azureuser /rp "McpBridge@2026!" /it /f 2>&1
Write-Host "TmpTask create: $createResult"
$runResult = schtasks /run /tn $TmpTask 2>&1
Write-Host "TmpTask run: $runResult"

Start-Sleep -Seconds 12

$conn = Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    Write-Host "PORT_LISTENING"
    $r = Invoke-RestMethod -Uri "http://localhost:8090/health" -TimeoutSec 5 -ErrorAction SilentlyContinue
    Write-Host "HEALTH: $($r | ConvertTo-Json -Compress)"
} else {
    Write-Host "PORT_NOT_LISTENING"
}

schtasks /delete /tn $TmpTask /f 2>&1 | Out-Null
Write-Host "Cleaned up tmp task"
