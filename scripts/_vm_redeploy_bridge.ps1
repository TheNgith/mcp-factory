Set-Location C:\mcp-factory
git pull origin main *>&1 | Write-Host

$port = 8090
$stale = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($stale) {
    $stale | ForEach-Object { try { Stop-Process -Id $_.OwningProcess -Force } catch {} }
    Start-Sleep -Seconds 2
    Write-Host "Killed old bridge process"
} else {
    Write-Host "No existing bridge on port $port"
}

# Reboot so azureuser autologs in and the InteractiveToken scheduled task
# starts the bridge in session 1 (required for UIA/pywinauto GUI access).
Write-Host "Rebooting VM to restart bridge in interactive session..."
Restart-Computer -Force
