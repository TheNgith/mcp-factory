$UserName   = "azureuser"
$Password   = "McpBridge@2026!"
$TaskName   = "MCP-Factory-GUI-Bridge"
$WrapperBat = "C:\mcp-factory\scripts\_bridge_launcher.bat"

# Remove stale task
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed old task"
}

$action = New-ScheduledTaskAction -Execute $WrapperBat

$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:COMPUTERNAME\$UserName"

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable

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

Write-Host "Task registered as $UserName"

# Also start it immediately (it will run as azureuser but from SYSTEM context via schtasks)
schtasks /run /tn $TaskName
Start-Sleep -Seconds 8

$conn = Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    Write-Host "PORT_LISTENING pid=$($conn.OwningProcess)"
    try {
        $r = Invoke-RestMethod -Uri "http://localhost:8090/health" -TimeoutSec 5
        Write-Host "HEALTH_OK: $($r | ConvertTo-Json -Compress)"
    } catch {
        Write-Host "HEALTH_FAIL: $_"
    }
} else {
    Write-Host "PORT_NOT_LISTENING - bridge will start on next logon"
}
