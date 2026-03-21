# Check auto-logon config
$alUser = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -ErrorAction SilentlyContinue).DefaultUserName
$alEnabled = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -ErrorAction SilentlyContinue).AutoAdminLogon
Write-Host "AutoLogon=$alEnabled  DefaultUser=$alUser"

# Check if azureuser is currently logged in
$sessions = query session 2>&1
Write-Host "Sessions:"
$sessions | ForEach-Object { Write-Host "  $_" }

# Check running processes that are under azureuser session
try {
    $userProcs = Get-WmiObject Win32_Process | Where-Object {
        try {
            $owner = $_.GetOwner()
            $owner.User -eq "azureuser"
        } catch { $false }
    }
    Write-Host "azureuser processes: $($userProcs.Count)"
    Write-Host "Sample: $(($userProcs | Select-Object -First 3).Name -join ',')"
} catch {
    Write-Host "Could not enumerate user processes: $_"
}
