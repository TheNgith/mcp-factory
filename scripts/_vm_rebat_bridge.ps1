$TaskName  = "MCP-Factory-GUI-Bridge"
$PythonExe = "C:\Program Files\Python311\python.exe"
$BridgePy  = "C:\mcp-factory\scripts\gui_bridge.py"
$WrapperBat = "C:\mcp-factory\scripts\_bridge_launcher.bat"
$Port       = 8090
$Secret     = "BridgeSecret2026xVM01"

# Write a .bat launcher that sets env vars then runs Python
$batContent = "@echo off`r`nset BRIDGE_SECRET=$Secret`r`nset BRIDGE_PORT=$Port`r`n`"$PythonExe`" `"$BridgePy`"`r`n"
[System.IO.File]::WriteAllText($WrapperBat, $batContent, [System.Text.Encoding]::ASCII)
Write-Host "Launcher bat written: $WrapperBat"
Get-Content $WrapperBat | ForEach-Object { Write-Host "  $_" }

# Unregister and re-register task to use the bat file
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed old task"
}

$action = New-ScheduledTaskAction -Execute $WrapperBat

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

Write-Host "Task registered with .bat launcher"

# Kill old bridge
$stale = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($stale) {
    $stale | ForEach-Object { try { Stop-Process -Id $_.OwningProcess -Force } catch {} }
    Start-Sleep -Seconds 2
    Write-Host "Killed old bridge"
}

# Start
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 8

# Health check
try {
    $r = Invoke-RestMethod -Uri "http://localhost:$Port/health" -TimeoutSec 10
    Write-Host "HEALTH_OK: $($r | ConvertTo-Json -Compress)"
} catch {
    Write-Host "HEALTH_FAIL: $($_.Exception.Message)"
}

# Auth test via /analyze with a tiny payload (expect 200 or 422, not 401)
$headers = @{"X-Bridge-Key" = $Secret}
try {
    $r2 = Invoke-RestMethod -Uri "http://localhost:$Port/analyze" -Method POST `
        -Headers $headers `
        -Body '{"path":"C:\\Windows\\System32\\calc.exe","types":["cli"]}' `
        -ContentType "application/json" -TimeoutSec 30
    Write-Host "AUTH_TEST_OK: got response with $($r2.invocables.Count) invocables"
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    Write-Host "AUTH_TEST: HTTP $code $($_.Exception.Message)"
    Write-Host "AUTH_DETAIL: $($_.ErrorDetails.Message)"
}
