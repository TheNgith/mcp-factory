$PythonExe = "C:\Program Files\Python311\python.exe"
$BridgePy  = "C:\mcp-factory\scripts\gui_bridge.py"
$EnvFile   = "C:\mcp-factory\scripts\.bridge.env"
$Port      = 8090
$TaskName  = "MCP-Factory-GUI-Bridge"
$Secret    = "BridgeSecret2026xVM01"

# Write env file as UTF-8 (no BOM) so cmd.exe for/f can parse it
[System.IO.File]::WriteAllText($EnvFile, "BRIDGE_SECRET=$Secret`nBRIDGE_PORT=$Port`n", [System.Text.Encoding]::UTF8)
Write-Host "Env file rewritten (UTF-8)"
Get-Content $EnvFile | ForEach-Object { Write-Host "  $_" }

# Unregister old task and re-register with env vars set directly (no file parsing)
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    if ((Get-ScheduledTask -TaskName $TaskName).State -ne "Ready") {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed old task"
}

# Embed secret directly as env var in the cmd action
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c set BRIDGE_SECRET=$Secret && set BRIDGE_PORT=$Port && `"$PythonExe`" `"$BridgePy`""

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

Write-Host "Task re-registered with inline env vars"

# Kill old bridge process
$stale = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($stale) {
    $stale | ForEach-Object { try { Stop-Process -Id $_.OwningProcess -Force } catch {} }
    Start-Sleep -Seconds 2
    Write-Host "Killed old bridge process"
}

# Start task (runs detached under SYSTEM)
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 8
Write-Host "Task started"

# Health check
try {
    $r = Invoke-RestMethod -Uri "http://localhost:$Port/health" -TimeoutSec 10
    Write-Host "HEALTH_OK: $($r | ConvertTo-Json -Compress)"
} catch {
    Write-Host "HEALTH_FAIL: $($_.Exception.Message)"
}

# Auth check
$headers = @{"X-Bridge-Key" = $Secret}
try {
    $r2 = Invoke-RestMethod -Uri "http://localhost:$Port/health" -Headers $headers -TimeoutSec 5
    Write-Host "AUTH_CHECK: health with key = OK"
} catch {
    Write-Host "AUTH_CHECK_FAIL: $($_.Exception.Message)"
}
