$UserName   = "azureuser"
$Password   = "McpBridge@2026!"
$WinLogon   = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
$TaskName   = "MCP-Factory-GUI-Bridge"
$WrapperBat = "C:\mcp-factory\scripts\_bridge_launcher.bat"

# Configure auto-logon
Set-ItemProperty $WinLogon -Name AutoAdminLogon   -Value "1"
Set-ItemProperty $WinLogon -Name DefaultUserName  -Value $UserName
Set-ItemProperty $WinLogon -Name DefaultPassword  -Value $Password
Set-ItemProperty $WinLogon -Name DefaultDomainName -Value $env:COMPUTERNAME
Write-Host "AutoLogon configured for $UserName"

# Remove stale task
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed old task"
}

# Register task to run as azureuser with password (interactive session)
$action = New-ScheduledTaskAction -Execute $WrapperBat

$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $UserName
$trigger2 = New-ScheduledTaskTrigger -AtStartup

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable $false

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:COMPUTERNAME\$UserName" `
    -LogonType Password `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $action `
    -Trigger   $trigger `
    -Settings  $settings `
    -Principal $principal `
    -Password  $Password `
    -Force | Out-Null

Write-Host "Task registered as $UserName (interactive logon)"

# Kill old bridge on port 8090
$stale = Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue
if ($stale) {
    $stale | ForEach-Object { try { Stop-Process -Id $_.OwningProcess -Force } catch {} }
    Start-Sleep -Seconds 2
    Write-Host "Killed old bridge"
}

# Start task now
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 8
Write-Host "Task started"

# Health check
try {
    $r = Invoke-RestMethod -Uri "http://localhost:8090/health" -TimeoutSec 10
    Write-Host "HEALTH_OK: $($r | ConvertTo-Json -Compress)"
} catch {
    Write-Host "HEALTH_FAIL: $($_.Exception.Message)"
}

Write-Host "NOTE: Full interactive session (for UWP) requires a reboot to trigger auto-logon."
