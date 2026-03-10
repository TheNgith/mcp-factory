$Port = 8090
$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $pid = $conn.OwningProcess
    $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
    Write-Host "Bridge PID=$pid, Name=$($proc.Name)"
    # Get owner and session
    $wmi = Get-WmiObject Win32_Process -Filter "ProcessId=$pid"
    $owner = $wmi.GetOwner()
    Write-Host "Owner=$($owner.Domain)\$($owner.User)"
    Write-Host "SessionId=$($proc.SessionId)"
}

# Query sessions
$sessions = query session 2>&1
$sessions | ForEach-Object { Write-Host $_ }

# Test auth
$headers = @{"X-Bridge-Key" = "BridgeSecret2026xVM01"}
try {
    $r = Invoke-RestMethod -Uri "http://localhost:$Port/analyze" -Method POST `
        -Headers $headers `
        -Body '{"path":"C:\\Windows\\System32\\calc.exe","types":["cli"]}' `
        -ContentType "application/json" -TimeoutSec 30
    Write-Host "AUTH_OK: $($r.count) invocables"
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    Write-Host "AUTH_FAIL: HTTP $code"
}
