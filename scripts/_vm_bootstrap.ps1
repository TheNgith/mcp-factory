$f = "C:\mcp-factory\scripts\.bridge.env"
$s = "BridgeSecret2026xVM01"
Set-Content $f "BRIDGE_SECRET=$s"
Add-Content $f "BRIDGE_PORT=8090"
Write-Host "ENV_WRITTEN"
Write-Host "SECRET=$s"
Get-Content $f
