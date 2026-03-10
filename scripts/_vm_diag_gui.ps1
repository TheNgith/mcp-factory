# Diagnostic: check session context, bridge health, and what /analyze returns for calc.exe
$secret = "BridgeSecret2026xVM01"
$url    = "http://localhost:8090"

Write-Host "=== Session / Identity ==="
whoami
[System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$sid = (Get-Process -Id $PID).SessionId
Write-Host "Current session ID: $sid"

Write-Host ""
Write-Host "=== Bridge health ==="
try {
    $h = Invoke-RestMethod -Uri "$url/health" -Method GET
    $h | ConvertTo-Json
} catch { Write-Host "Health check failed: $_" }

Write-Host ""
Write-Host "=== Bridge process session ==="
$bridgeProc = Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($bridgeProc) {
    $proc = Get-Process -Id $bridgeProc.OwningProcess -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "Bridge PID: $($proc.Id)  Name: $($proc.Name)"
        $wmiProc = Get-WmiObject Win32_Process -Filter "ProcessId=$($proc.Id)"
        Write-Host "Bridge session ID: $($wmiProc.SessionId)"
    }
} else { Write-Host "No process listening on 8090" }

Write-Host ""
Write-Host "=== POST /analyze for calc.exe (gui+cli only) ==="
$body = @{
    path    = "C:\Windows\System32\calc.exe"
    hints   = "calculator"
    types   = @("gui","cli")
} | ConvertTo-Json

try {
    $resp = Invoke-RestMethod -Uri "$url/analyze" -Method POST -Body $body `
        -ContentType "application/json" `
        -Headers @{ "X-Bridge-Key" = $secret } `
        -TimeoutSec 90
    Write-Host "Total invocables: $($resp.count)"
    Write-Host "Errors: $($resp.errors | ConvertTo-Json)"
    $resp.invocables | ForEach-Object { Write-Host "  $($_.source_type)  $($_.name)" }
} catch { Write-Host "Analyze failed: $_" }

Write-Host ""
Write-Host "=== Desktop window station ==="
try {
    $ws = [System.Environment]::GetEnvironmentVariable("SESSIONNAME")
    Write-Host "SESSIONNAME: $ws"
    $desktop = Get-WmiObject -Class Win32_Desktop -ErrorAction SilentlyContinue | Select-Object -First 3
    $desktop | ForEach-Object { Write-Host "  Desktop: $($_.Name)" }
} catch {}
