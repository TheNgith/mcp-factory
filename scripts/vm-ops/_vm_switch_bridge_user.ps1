# Kill old SYSTEM bridge, then start the user-session task cleanly
$port = 8090

$stale = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($stale) {
    $stale | ForEach-Object {
        try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction Stop }
        catch { Write-Host "Could not kill PID $($_.OwningProcess): $_" }
    }
    Start-Sleep -Seconds 3
    Write-Host "Killed old bridge"
} else {
    Write-Host "No bridge on port $port"
}

# Confirm port is free
$still = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($still) {
    Write-Host "WARNING: port $port still occupied by PID $($still.OwningProcess)"
} else {
    Write-Host "Port $port is free"
}

# Start the user-session scheduled task
$result = schtasks /run /tn "MCP-Factory-GUI-Bridge" 2>&1
Write-Host "Task start: $result"
Start-Sleep -Seconds 6

# Verify
$conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    $wmi  = Get-WmiObject Win32_Process -Filter "ProcessId=$($conn.OwningProcess)"
    Write-Host "Bridge listening: PID=$($conn.OwningProcess) Session=$($wmi.SessionId) User=$($proc.Name)"
    $Owner = $wmi.GetOwner()
    Write-Host "Owner: $($Owner.Domain)\$($Owner.User)"
    $h = Invoke-RestMethod -Uri "http://localhost:8090/health" -TimeoutSec 5
    Write-Host "Health: $($h | ConvertTo-Json -Compress)"
} else {
    Write-Host "ERROR: bridge not listening after restart"
}
