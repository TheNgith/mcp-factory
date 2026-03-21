Write-Host "=== Check bat file ==="
if (Test-Path "C:\mcp-factory\scripts\_bridge_launcher.bat") {
    Write-Host "EXISTS"
    Get-Content "C:\mcp-factory\scripts\_bridge_launcher.bat"
} else {
    Write-Host "NOT FOUND: C:\mcp-factory\scripts\_bridge_launcher.bat"
}

Write-Host "=== Task action ==="
$t = Get-ScheduledTask -TaskName "MCP-Factory-GUI-Bridge" -ErrorAction SilentlyContinue
if ($t) {
    $t.Actions | Format-List Execute, Arguments, WorkingDirectory
} else {
    Write-Host "Task not found"
}
