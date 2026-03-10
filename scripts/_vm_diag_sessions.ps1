# Check all sessions and whether azureuser is logged in interactively
Write-Host "=== query user ==="
query user 2>&1 | ForEach-Object { Write-Host $_ }

Write-Host ""
Write-Host "=== All sessions (qwinsta) ==="
qwinsta 2>&1 | ForEach-Object { Write-Host $_ }

Write-Host ""
Write-Host "=== Autologon registry ==="
$al = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
Write-Host "AutoAdminLogon  : $($al.AutoAdminLogon)"
Write-Host "DefaultUserName : $($al.DefaultUserName)"
Write-Host "ForceAutoLogon  : $($al.ForceAutoLogon)"

Write-Host ""
Write-Host "=== All python processes and their sessions ==="
Get-WmiObject Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
    $owner = $_.GetOwner()
    Write-Host "PID=$($_.ProcessId) Session=$($_.SessionId) Owner=$($owner.Domain)\$($owner.User) CMD=$($_.CommandLine)"
}

Write-Host ""
Write-Host "=== Scheduled task state ==="
schtasks /query /tn "MCP-Factory-GUI-Bridge" /fo list 2>&1 | ForEach-Object { Write-Host $_ }
