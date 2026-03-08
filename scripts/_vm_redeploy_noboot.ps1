# Kill old bridge
$port = 8090
$stale = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($stale) {
    $stale | ForEach-Object { try { Stop-Process -Id $_.OwningProcess -Force } catch {} }
    Start-Sleep -Seconds 2
    Write-Host "Killed old bridge process"
} else {
    Write-Host "No existing bridge on port $port"
}

# Pull latest code
Set-Location C:\mcp-factory
git pull origin main *>&1 | Write-Host

# Re-trigger the task in session 1 (InteractiveToken task auto-selects active session)
schtasks /Run /TN "MCP-Factory-GUI-Bridge" 2>&1 | Write-Host
Start-Sleep -Seconds 5

# Verify
$c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($c) {
    $w = Get-WmiObject Win32_Process -Filter "ProcessId=$($c.OwningProcess)"
    $o = $w.GetOwner()
    Write-Host "PID=$($c.OwningProcess) Session=$($w.SessionId) Owner=$($o.Domain)\$($o.User)"
} else {
    Write-Host "Bridge not running after task trigger"
}
