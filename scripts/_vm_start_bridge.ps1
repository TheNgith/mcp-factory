$PythonExe = "C:\Program Files\Python311\python.exe"
$BridgePy  = "C:\mcp-factory\scripts\gui_bridge.py"
$EnvFile   = "C:\mcp-factory\scripts\.bridge.env"
$Port      = 8090
$TaskName  = "MCP-Factory-GUI-Bridge"
$Secret    = "BridgeSecret2026xVM01"

# Ensure .bridge.env exists
Set-Content $EnvFile "BRIDGE_SECRET=$Secret"
Add-Content $EnvFile "BRIDGE_PORT=$Port"
Write-Host "Env file written"

# Open firewall
$fwRule = Get-NetFirewallRule -DisplayName "MCP Bridge $Port" -ErrorAction SilentlyContinue
if (-not $fwRule) {
    New-NetFirewallRule -DisplayName "MCP Bridge $Port" -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
    Write-Host "Firewall rule created"
} else {
    Write-Host "Firewall rule already exists"
}

# Remove stale scheduled task
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed stale task"
}

# Register scheduled task - uses cmd /c to set env vars from file then run Python
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c for /f `"tokens=1,2 delims=`=`" %A in ($EnvFile) do set %A=%B && `"$PythonExe`" `"$BridgePy`""

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

Write-Host "Scheduled task registered: $TaskName"

# Start the task now (runs detached under SYSTEM, fully separate from this session)
Start-ScheduledTask -TaskName $TaskName
Write-Host "Task started"

# Wait for bridge to come up
Start-Sleep -Seconds 8
Write-Host "Checking health..."

try {
    $r = Invoke-RestMethod -Uri "http://localhost:$Port/health" -TimeoutSec 10
    Write-Host "HEALTH_OK: $($r | ConvertTo-Json -Compress)"
} catch {
    Write-Host "HEALTH_FAIL: $($_.Exception.Message)"
    # Check if port is listening
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        Write-Host "PORT_IS_LISTENING (bridge may still be starting)"
    } else {
        Write-Host "PORT_NOT_LISTENING"
    }
}
