$qsOutput = (query session 2>&1) -join "|"
Write-Host "QS:$qsOutput"
$alEnabled = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -ErrorAction SilentlyContinue).AutoAdminLogon
Write-Host "AutoLogon=$alEnabled"
