# Kill old bridge
$port = 8090
$stale = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($stale) {
    $stale | ForEach-Object { try { Stop-Process -Id $_.OwningProcess -Force } catch {} }
    Start-Sleep -Seconds 2
    Write-Host "Killed old bridge process"
}

# Pull latest code
Set-Location C:\mcp-factory
git pull origin main *>&1 | Write-Host

# Re-register the task with InteractiveToken (in case it was lost)
$TaskName   = "MCP-Factory-GUI-Bridge"
$WrapperBat = "C:\mcp-factory\scripts\_bridge_launcher.bat"
$UserName   = "azureuser"

$action   = New-ScheduledTaskAction -Execute $WrapperBat
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $UserName
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:COMPUTERNAME\$UserName" `
    -LogonType InteractiveToken `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $action `
    -Trigger   $trigger `
    -Settings  $settings `
    -Principal $principal `
    -Force | Out-Null
Write-Host "Task re-registered"

# Trigger with PowerShell cmdlet (handles InteractiveToken better than schtasks /Run)
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 6

# Verify
$c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($c) {
    $w = Get-WmiObject Win32_Process -Filter "ProcessId=$($c.OwningProcess)"
    $o = $w.GetOwner()
    Write-Host "PID=$($c.OwningProcess) Session=$($w.SessionId) Owner=$($o.Domain)\$($o.User)"
    if ([int]$w.SessionId -ge 1) {
        Write-Host "SUCCESS: bridge in interactive session"
    } else {
        Write-Host "WARNING: bridge in session 0 - reboot required for session 1"
    }
} else {
    Write-Host "Bridge not running - reboot required"
}
