$url    = "http://localhost:8090/execute"
$secret = "BridgeSecret2026xVM01"

$body = @{
    invocable = @{
        name = "calc"
        execution = @{
            method          = "cli"
            executable_path = "C:\Windows\System32\calc.exe"
        }
    }
    args = @{}
} | ConvertTo-Json -Depth 10

Write-Host "POST $url"
Write-Host "Body: $body"

try {
    $resp = Invoke-RestMethod -Uri $url -Method POST -Body $body `
        -ContentType "application/json" `
        -Headers @{ "X-Bridge-Key" = $secret }
    Write-Host "SUCCESS: $($resp | ConvertTo-Json)"
} catch {
    Write-Host "ERROR: $_"
    Write-Host $_.Exception.Response
}
