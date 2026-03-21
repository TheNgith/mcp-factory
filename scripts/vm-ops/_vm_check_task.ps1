Write-Host "=== All MCP/Bridge tasks ==="
Get-ScheduledTask | Where-Object { $_.TaskName -like "*Bridge*" -or $_.TaskName -like "*MCP*" } | Format-List TaskName, TaskPath, State

Write-Host "=== Bridge task info ==="
$t = Get-ScheduledTask -TaskName "MCP-Factory-GUI-Bridge" -ErrorAction SilentlyContinue
if ($t) {
    $t | Format-List
    $t.Actions | Format-List
} else {
    Write-Host "Task 'MCP-Factory-GUI-Bridge' not found"
    Write-Host "All tasks:"
    Get-ScheduledTask | Select-Object TaskName | Format-Table
}
