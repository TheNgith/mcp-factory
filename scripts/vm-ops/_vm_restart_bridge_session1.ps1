# Kill old session-0 bridge, restart task while azureuser is in session 1
$port = 8090

$c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($c) {
    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    Write-Host "Killed PID $($c.OwningProcess)"
}

# Restart the scheduled task — azureuser is now in session 1
schtasks /run /tn "MCP-Factory-GUI-Bridge" 2>&1 | ForEach-Object { Write-Host $_ }
Start-Sleep -Seconds 6

# Verify session
$c2 = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($c2) {
    $w = Get-WmiObject Win32_Process -Filter "ProcessId=$($c2.OwningProcess)"
    $o = $w.GetOwner()
    Write-Host "Bridge: PID=$($c2.OwningProcess) Session=$($w.SessionId) Owner=$($o.Domain)\$($o.User)"
    if ($w.SessionId -eq 1) {
        Write-Host "SUCCESS: bridge is in interactive session 1"
    } else {
        Write-Host "WARNING: bridge is still in session $($w.SessionId)"
    }
} else {
    Write-Host "ERROR: bridge not listening on $port"
}
