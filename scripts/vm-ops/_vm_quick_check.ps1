query user 2>&1 | ForEach-Object { Write-Host $_ }

Write-Host "---AUTOLOGON---"
$al = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
Write-Host "AutoAdminLogon: $($al.AutoAdminLogon)"
Write-Host "DefaultUserName: $($al.DefaultUserName)"

Write-Host "---BRIDGE---"
$c = Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue
if ($c) {
    $w = Get-WmiObject Win32_Process -Filter "ProcessId=$($c.OwningProcess)"
    $o = $w.GetOwner()
    Write-Host "PID=$($c.OwningProcess) Session=$($w.SessionId) Owner=$($o.Domain)\$($o.User)"
} else {
    Write-Host "Bridge not listening on 8090"
}
