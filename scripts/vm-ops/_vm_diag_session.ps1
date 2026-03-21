# Check who is configured for autologon and what interactive sessions exist
Write-Host "=== Autologon user ==="
$al = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -ErrorAction SilentlyContinue
Write-Host "DefaultUserName : $($al.DefaultUserName)"
Write-Host "DefaultDomainName: $($al.DefaultDomainName)"
Write-Host "AutoAdminLogon  : $($al.AutoAdminLogon)"

Write-Host ""
Write-Host "=== Active sessions (query user) ==="
query user 2>&1 | ForEach-Object { Write-Host $_ }

Write-Host ""
Write-Host "=== Scheduled task principal ==="
$task = Get-ScheduledTask -TaskName "MCP-Factory-GUI-Bridge" -ErrorAction SilentlyContinue
if ($task) {
    $task.Principal | Select-Object UserId, LogonType, RunLevel | Format-List
} else { Write-Host "Task not found" }
