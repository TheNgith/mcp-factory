$Port = 8090
# Check if process is running
$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    Write-Host "PORT_LISTENING pid=$($conn.OwningProcess)"
} else {
    Write-Host "PORT_NOT_LISTENING"
}

# Try health check
try {
    $r = Invoke-RestMethod -Uri "http://localhost:$Port/health" -TimeoutSec 5
    Write-Host "HEALTH_OK: $($r | ConvertTo-Json -Compress)"
} catch {
    Write-Host "HEALTH_FAIL: $($_.Exception.Message)"
}
