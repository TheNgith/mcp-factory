# Re-register bridge task with InteractiveToken so it runs in azureuser's session 1
$UserName   = "azureuser"
$TaskName   = "MCP-Factory-GUI-Bridge"
$WrapperBat = "C:\mcp-factory\scripts\_bridge_launcher.bat"
$Port       = 8090

# Kill current bridge
$c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($c) {
    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Write-Host "Killed old bridge PID=$($c.OwningProcess)"
}

# Remove old task
schtasks /delete /tn $TaskName /f 2>&1 | Out-Null
Write-Host "Deleted old task"

# Register with InteractiveToken — uses the user's live interactive session token
$action = New-ScheduledTaskAction -Execute $WrapperBat

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserName

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable

# InteractiveToken = use the user's interactive desktop session (session 1)
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

Write-Host "Task registered with InteractiveToken"

# Trigger it now — azureuser IS logged in so this should land in session 1
schtasks /run /tn $TaskName 2>&1 | ForEach-Object { Write-Host $_ }
Start-Sleep -Seconds 8

# Verify
$c2 = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($c2) {
    $w = Get-WmiObject Win32_Process -Filter "ProcessId=$($c2.OwningProcess)"
    $o = $w.GetOwner()
    Write-Host "Bridge: PID=$($c2.OwningProcess) Session=$($w.SessionId) Owner=$($o.Domain)\$($o.User)"
    if ($w.SessionId -eq 1) {
        Write-Host "SUCCESS: bridge is in interactive session 1"
    } else {
        Write-Host "STILL session $($w.SessionId) - InteractiveToken requires user to trigger via logon event"
    }
} else {
    Write-Host "Bridge not listening - InteractiveToken task only starts at logon, not via /run from session 0"
    Write-Host "Bridge will auto-start at next azureuser logon."
}
