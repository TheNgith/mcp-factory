$UserName   = "azureuser"
$Password   = "McpBridge@2026!"
$TaskName   = "MCP-Factory-GUI-Bridge"
$WrapperBat = "C:\mcp-factory\scripts\_bridge_launcher.bat"

# Delete old task
schtasks /delete /tn $TaskName /f 2>&1 | Out-Null
Write-Host "Deleted old task"

# Create task via schtasks.exe with user credentials
$result = schtasks /create /tn $TaskName /tr $WrapperBat /sc onlogon /ru $UserName /rp $Password /rl Highest /f 2>&1
Write-Host "Create result: $result"

# Start it immediately
$startResult = schtasks /run /tn $TaskName 2>&1
Write-Host "Start result: $startResult"

Start-Sleep -Seconds 10

# Check port
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
    Write-Host "PORT_NOT_LISTENING"
}

# Show task state
schtasks /query /tn $TaskName /fo list 2>&1 | ForEach-Object { Write-Host $_ }
